package main

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func newDownloadedLibraryTestApp(tb testing.TB) (*App, string) {
	tb.Helper()
	stateRoot := tb.TempDir()
	app := NewApp()
	app.downloadedStore = newDownloadedLibraryStore(
		filepath.Join(stateRoot, "downloaded-library-index.json"),
		filepath.Join(stateRoot, "downloaded-library-index-v2"),
	)
	app.mediaBaseURL = "http://127.0.0.1:43210"
	app.mediaToken = "test-token"
	return app, tb.TempDir()
}

func writeDownloadedTestTrack(tb testing.TB, releaseDir, name, contents string) string {
	tb.Helper()
	if err := os.MkdirAll(releaseDir, 0755); err != nil {
		tb.Fatal(err)
	}
	path := filepath.Join(releaseDir, name)
	if err := os.WriteFile(path, []byte(contents), 0644); err != nil {
		tb.Fatal(err)
	}
	return path
}

func TestDownloadedFingerprintsUseFullPathSizeMtimeAndSchema(t *testing.T) {
	root := t.TempDir()
	first := writeDownloadedTestTrack(t, filepath.Join(root, "First"), "01.flac", "same")
	second := writeDownloadedTestTrack(t, filepath.Join(root, "Second"), "01.flac", "same")
	stamp := time.Unix(1_700_000_000, 123_456_789)
	for _, path := range []string{first, second} {
		if err := os.Chtimes(path, stamp, stamp); err != nil {
			t.Fatal(err)
		}
	}
	firstSnapshot, err := snapshotDownloadedFile(first, downloadedTrackFingerprintSchema)
	if err != nil {
		t.Fatal(err)
	}
	firstAgain, err := snapshotDownloadedFile(first, downloadedTrackFingerprintSchema)
	if err != nil {
		t.Fatal(err)
	}
	secondSnapshot, err := snapshotDownloadedFile(second, downloadedTrackFingerprintSchema)
	if err != nil {
		t.Fatal(err)
	}
	otherSchema, err := snapshotDownloadedFile(first, "different-schema")
	if err != nil {
		t.Fatal(err)
	}
	if firstSnapshot.Fingerprint != firstAgain.Fingerprint {
		t.Fatal("unchanged track fingerprint was not stable")
	}
	if firstSnapshot.Fingerprint == secondSnapshot.Fingerprint {
		t.Fatal("same-named tracks in different complete paths collided")
	}
	if firstSnapshot.Fingerprint == otherSchema.Fingerprint {
		t.Fatal("track fingerprint omitted its schema version")
	}

	if err := os.WriteFile(first, []byte("different-size"), 0644); err != nil {
		t.Fatal(err)
	}
	sizeChanged, err := snapshotDownloadedFile(first, downloadedTrackFingerprintSchema)
	if err != nil {
		t.Fatal(err)
	}
	if sizeChanged.Fingerprint == firstSnapshot.Fingerprint {
		t.Fatal("track size change did not alter its fingerprint")
	}
	if err := os.WriteFile(first, []byte("same"), 0644); err != nil {
		t.Fatal(err)
	}
	later := stamp.Add(time.Second)
	if err := os.Chtimes(first, later, later); err != nil {
		t.Fatal(err)
	}
	mtimeChanged, err := snapshotDownloadedFile(first, downloadedTrackFingerprintSchema)
	if err != nil {
		t.Fatal(err)
	}
	if mtimeChanged.Fingerprint == firstSnapshot.Fingerprint {
		t.Fatal("track modification time change did not alter its fingerprint")
	}
}

func TestUnchangedDownloadedIndexLaunchesNoProbeOrArtworkProcess(t *testing.T) {
	app, root := newDownloadedLibraryTestApp(t)
	t.Setenv("LOCALAPPDATA", t.TempDir())
	t.Setenv("HOME", t.TempDir())
	writeDownloadedTestTrack(t, filepath.Join(root, "First"), "01.flac", "first-audio")
	writeDownloadedTestTrack(t, filepath.Join(root, "Second"), "01.flac", "second-audio")

	var probes atomic.Int32
	var artworkExtractions atomic.Int32
	app.trackProbeHook = func(_ context.Context, track *libraryReleaseTrack) {
		probes.Add(1)
		track.Codec = "TEST"
	}
	app.embeddedArtworkHook = func(_ context.Context, _ string) (string, error) {
		artworkExtractions.Add(1)
		return "", nil
	}

	if _, indexErrors, err := app.rebuildDownloadedLibraryIndex(root, Config{}, nil); err != nil {
		t.Fatal(err)
	} else if len(indexErrors) != 0 {
		t.Fatalf("unexpected initial indexing warnings: %v", indexErrors)
	}
	if probes.Load() != 2 || artworkExtractions.Load() != 2 {
		t.Fatalf(
			"initial index launched probes=%d artwork=%d, want 2 each",
			probes.Load(),
			artworkExtractions.Load(),
		)
	}
	probes.Store(0)
	artworkExtractions.Store(0)
	manifestBefore, err := os.Stat(app.downloadedLibraryStore().manifestPath)
	if err != nil {
		t.Fatal(err)
	}
	time.Sleep(20 * time.Millisecond)

	if _, indexErrors, err := app.rebuildDownloadedLibraryIndex(root, Config{}, nil); err != nil {
		t.Fatal(err)
	} else if len(indexErrors) != 0 {
		t.Fatalf("unexpected unchanged indexing warnings: %v", indexErrors)
	}
	if probes.Load() != 0 || artworkExtractions.Load() != 0 {
		t.Fatalf(
			"unchanged reconciliation launched probes=%d artwork=%d",
			probes.Load(),
			artworkExtractions.Load(),
		)
	}
	manifestAfter, err := os.Stat(app.downloadedLibraryStore().manifestPath)
	if err != nil {
		t.Fatal(err)
	}
	if !manifestAfter.ModTime().Equal(manifestBefore.ModTime()) {
		t.Fatal("unchanged reconciliation rewrote the complete manifest")
	}
}

func TestChangedTrackInvalidatesOnlyItsReleaseAndReusesSiblingMetadata(t *testing.T) {
	app, root := newDownloadedLibraryTestApp(t)
	t.Setenv("LOCALAPPDATA", t.TempDir())
	t.Setenv("HOME", t.TempDir())
	firstRelease := filepath.Join(root, "First")
	changedTrack := writeDownloadedTestTrack(t, firstRelease, "02.flac", "before")
	writeDownloadedTestTrack(t, firstRelease, "01.flac", "stable-first-track")
	writeDownloadedTestTrack(t, filepath.Join(root, "Second"), "01.flac", "stable-release")

	var probes atomic.Int32
	var artworkExtractions atomic.Int32
	app.trackProbeHook = func(_ context.Context, track *libraryReleaseTrack) {
		probes.Add(1)
		track.Codec = "TEST"
	}
	app.embeddedArtworkHook = func(_ context.Context, _ string) (string, error) {
		artworkExtractions.Add(1)
		return "", nil
	}
	if _, _, err := app.rebuildDownloadedLibraryIndex(root, Config{}, nil); err != nil {
		t.Fatal(err)
	}
	before, err := app.downloadedLibraryStore().read()
	if err != nil {
		t.Fatal(err)
	}
	firstBefore := before.Records["First"]
	secondBefore := before.Records["Second"]

	if err := os.WriteFile(changedTrack, []byte("after-with-a-different-size"), 0644); err != nil {
		t.Fatal(err)
	}
	future := time.Now().Add(2 * time.Second)
	if err := os.Chtimes(changedTrack, future, future); err != nil {
		t.Fatal(err)
	}
	probes.Store(0)
	artworkExtractions.Store(0)

	if _, indexErrors, err := app.rebuildDownloadedLibraryIndex(root, Config{}, nil); err != nil {
		t.Fatal(err)
	} else if len(indexErrors) != 0 {
		t.Fatalf("unexpected changed indexing warnings: %v", indexErrors)
	}
	if probes.Load() != 1 {
		t.Fatalf("changed track launched %d probes, want exactly 1", probes.Load())
	}
	if artworkExtractions.Load() != 0 {
		t.Fatalf("unchanged first-track artwork launched %d extractions", artworkExtractions.Load())
	}
	after, err := app.downloadedLibraryStore().read()
	if err != nil {
		t.Fatal(err)
	}
	if after.Records["First"].Fingerprint == firstBefore.Fingerprint {
		t.Fatal("changed release retained its old release fingerprint")
	}
	if after.Records["Second"].Fingerprint != secondBefore.Fingerprint {
		t.Fatal("unchanged release was invalidated by a sibling release change")
	}
}

func TestDownloadedMetadataWorkersAreBoundedAndProgressIsMonotonic(t *testing.T) {
	app, root := newDownloadedLibraryTestApp(t)
	for release := 0; release < 6; release++ {
		releaseDir := filepath.Join(root, "Release-"+string(rune('A'+release)))
		writeDownloadedTestTrack(t, releaseDir, "01.flac", "audio")
		if err := os.WriteFile(filepath.Join(releaseDir, "cover.jpg"), []byte("cover"), 0644); err != nil {
			t.Fatal(err)
		}
	}
	var active atomic.Int32
	var maximum atomic.Int32
	app.trackProbeHook = func(_ context.Context, _ *libraryReleaseTrack) {
		now := active.Add(1)
		for {
			old := maximum.Load()
			if now <= old || maximum.CompareAndSwap(old, now) {
				break
			}
		}
		time.Sleep(10 * time.Millisecond)
		active.Add(-1)
	}
	var percentages []int
	if _, _, err := app.rebuildDownloadedLibraryIndex(root, Config{}, func(percent int, _ string) {
		percentages = append(percentages, percent)
	}); err != nil {
		t.Fatal(err)
	}
	if maximum.Load() < 2 || maximum.Load() > downloadedMetadataWorkerCount {
		t.Fatalf(
			"metadata concurrency=%d, want 2 and never above %d",
			maximum.Load(),
			downloadedMetadataWorkerCount,
		)
	}
	if len(percentages) == 0 || percentages[len(percentages)-1] != 99 {
		t.Fatalf("progress did not end at the determinate save boundary: %v", percentages)
	}
	for index := 1; index < len(percentages); index++ {
		if percentages[index] < percentages[index-1] {
			t.Fatalf("progress moved backwards: %v", percentages)
		}
	}
}

func TestDownloadedReconciliationCancellationStopsProbeWorkers(t *testing.T) {
	app, root := newDownloadedLibraryTestApp(t)
	for release := 0; release < 4; release++ {
		releaseDir := filepath.Join(root, "Release-"+string(rune('A'+release)))
		writeDownloadedTestTrack(t, releaseDir, "01.flac", "audio")
		if err := os.WriteFile(filepath.Join(releaseDir, "cover.jpg"), []byte("cover"), 0644); err != nil {
			t.Fatal(err)
		}
	}
	probeStarted := make(chan struct{})
	var startedOnce sync.Once
	app.trackProbeHook = func(ctx context.Context, _ *libraryReleaseTrack) {
		startedOnce.Do(func() { close(probeStarted) })
		<-ctx.Done()
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		_, _, err := app.rebuildDownloadedLibraryIndexContext(ctx, root, Config{}, nil)
		done <- err
	}()
	select {
	case <-probeStarted:
	case <-time.After(time.Second):
		t.Fatal("metadata probe worker did not start")
	}
	cancel()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("cancelled reconciliation returned %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("cancelled reconciliation did not stop")
	}
}

func TestIncrementalDownloadedCacheSurvivesInterruptedManifestPublish(t *testing.T) {
	app, root := newDownloadedLibraryTestApp(t)
	releaseDir := filepath.Join(root, "Release")
	trackPath := writeDownloadedTestTrack(t, releaseDir, "01.flac", "before")
	if err := os.WriteFile(filepath.Join(releaseDir, "cover.jpg"), []byte("cover"), 0644); err != nil {
		t.Fatal(err)
	}
	var probes atomic.Int32
	app.trackProbeHook = func(_ context.Context, track *libraryReleaseTrack) {
		probes.Add(1)
		track.Codec = "TEST"
	}
	if _, _, err := app.rebuildDownloadedLibraryIndex(root, Config{}, nil); err != nil {
		t.Fatal(err)
	}
	oldCache, err := app.downloadedLibraryStore().read()
	if err != nil {
		t.Fatal(err)
	}
	oldRecord := oldCache.Records["Release"]

	if err := os.WriteFile(trackPath, []byte("changed-and-longer"), 0644); err != nil {
		t.Fatal(err)
	}
	future := time.Now().Add(2 * time.Second)
	if err := os.Chtimes(trackPath, future, future); err != nil {
		t.Fatal(err)
	}
	candidate, err := app.buildDownloadedReleaseCandidate(root, "album", releaseDir)
	if err != nil {
		t.Fatal(err)
	}
	probes.Store(0)
	record, err := app.buildDownloadedReleaseRecord(context.Background(), candidate, oldRecord, nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := app.downloadedLibraryStore().writeRecord(record); err != nil {
		t.Fatal(err)
	}
	if probes.Load() != 1 {
		t.Fatalf("changed shard preparation launched %d probes, want 1", probes.Load())
	}

	// Simulate interruption before manifest publication: the old complete
	// snapshot remains readable while the content-addressed shard is durable.
	stillComplete, err := app.downloadedLibraryStore().read()
	if err != nil {
		t.Fatal(err)
	}
	if stillComplete.Records["Release"].Fingerprint != oldRecord.Fingerprint {
		t.Fatal("interrupted shard write replaced the last complete manifest")
	}
	probes.Store(0)
	if _, _, err := app.rebuildDownloadedLibraryIndex(root, Config{}, nil); err != nil {
		t.Fatal(err)
	}
	if probes.Load() != 0 {
		t.Fatalf("reconciliation failed to reuse interrupted shard; probes=%d", probes.Load())
	}
	published, err := app.downloadedLibraryStore().read()
	if err != nil {
		t.Fatal(err)
	}
	if published.Records["Release"].Fingerprint != candidate.ReleaseFingerprint {
		t.Fatal("reconciliation did not publish the durable changed shard")
	}
}

func TestDownloadedCacheReadsAndMigratesLegacyMonolith(t *testing.T) {
	app, root := newDownloadedLibraryTestApp(t)
	releaseDir := filepath.Join(root, "Legacy")
	trackPath := writeDownloadedTestTrack(t, releaseDir, "01.flac", "legacy")
	if err := os.WriteFile(filepath.Join(releaseDir, "cover.jpg"), []byte("cover"), 0644); err != nil {
		t.Fatal(err)
	}
	legacy := downloadedLibraryCache{
		Root: root,
		Payload: libraryPayload{Albums: []libraryReleaseSummary{{
			Kind: "album", RelativePath: "Legacy", Title: "Legacy", TrackCount: 1,
		}}},
		Details: map[string]libraryReleaseDetail{
			"Legacy": {
				Kind: "album", RelativePath: "Legacy", Title: "Legacy", TrackCount: 1,
				Tracks: []libraryReleaseTrack{{
					Title: "Legacy", FileName: "01.flac", FilePath: trackPath,
				}},
			},
		},
		IndexedAt: time.Now().Unix(),
	}
	data, err := json.Marshal(legacy)
	if err != nil {
		t.Fatal(err)
	}
	store := app.downloadedLibraryStore()
	if err := os.MkdirAll(filepath.Dir(store.legacyPath), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(store.legacyPath, data, 0644); err != nil {
		t.Fatal(err)
	}
	read, err := store.read()
	if err != nil {
		t.Fatal(err)
	}
	if read.Details["Legacy"].Title != "Legacy" || len(read.Payload.Albums) != 1 {
		t.Fatal("legacy cache-first summary/detail compatibility was lost")
	}

	app.trackProbeHook = func(_ context.Context, track *libraryReleaseTrack) {
		track.Codec = "TEST"
	}
	if _, _, err := app.rebuildDownloadedLibraryIndex(root, Config{}, nil); err != nil {
		t.Fatal(err)
	}
	migrated, err := store.read()
	if err != nil {
		t.Fatal(err)
	}
	if migrated.SchemaVersion != downloadedLibraryIndexSchemaVersion {
		t.Fatalf("migrated schema=%d, want %d", migrated.SchemaVersion, downloadedLibraryIndexSchemaVersion)
	}
	if _, ok := migrated.Records["Legacy"]; !ok {
		t.Fatal("legacy release was not migrated to a sharded record")
	}
	if _, err := os.Stat(store.legacyPath); err != nil {
		t.Fatalf("migration should preserve the readable legacy snapshot: %v", err)
	}
}

func BenchmarkDownloadedLibraryUnchangedReconciliation(b *testing.B) {
	app, root := newDownloadedLibraryTestApp(b)
	for release := 0; release < 4; release++ {
		releaseDir := filepath.Join(root, "Release-"+string(rune('A'+release)))
		for track := 1; track <= 4; track++ {
			writeDownloadedTestTrack(
				b,
				releaseDir,
				time.Date(2000, 1, track, 0, 0, 0, 0, time.UTC).Format("02")+".flac",
				"audio",
			)
		}
		if err := os.WriteFile(filepath.Join(releaseDir, "cover.jpg"), []byte("cover"), 0644); err != nil {
			b.Fatal(err)
		}
	}
	var probes atomic.Int32
	app.trackProbeHook = func(_ context.Context, _ *libraryReleaseTrack) {
		probes.Add(1)
	}
	if _, _, err := app.rebuildDownloadedLibraryIndex(root, Config{}, nil); err != nil {
		b.Fatal(err)
	}
	probes.Store(0)
	b.ReportAllocs()
	b.ResetTimer()
	for index := 0; index < b.N; index++ {
		if _, _, err := app.rebuildDownloadedLibraryIndex(root, Config{}, nil); err != nil {
			b.Fatal(err)
		}
	}
	b.StopTimer()
	if probes.Load() != 0 {
		b.Fatalf("unchanged benchmark launched %d probes", probes.Load())
	}
}
