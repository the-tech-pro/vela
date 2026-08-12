package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	wailsRuntime "github.com/wailsapp/wails/v2/pkg/runtime"
)

const downloadedMetadataWorkerCount = 2

type downloadedIndexProgress struct {
	mu        sync.Mutex
	total     int
	completed int
	last      int
	emit      func(int, string)
}

type downloadedReleaseBuildResult struct {
	candidate downloadedReleaseCandidate
	record    downloadedReleaseRecord
	err       error
	persist   bool
}

func sameDownloadedLibraryRoot(first, second string) bool {
	if first == "" || second == "" {
		return false
	}
	return normalizedCompletePath(first) == normalizedCompletePath(second)
}

func (a *App) refreshDownloadedLibraryIndex(root string, cfg Config) {
	a.libraryIndexCoordinator().schedule(backgroundIndexDownloaded, func(ctx context.Context) {
		a.runDownloadedLibraryIndex(ctx, root, cfg)
	})
}

func (a *App) runDownloadedLibraryIndex(ctx context.Context, root string, cfg Config) {
	a.mu.Lock()
	if a.downloadIndexing {
		a.mu.Unlock()
		return
	}
	a.downloadIndexing = true
	a.mu.Unlock()

	indexSpan := a.beginPerf("downloaded_index")
	startingProbeCount := a.perfCounter("ffprobe")
	startingFFmpegCount := a.perfCounter("ffmpeg")
	var indexErr error
	payloadBytes := 0
	defer func() {
		recovered := recover()
		a.mu.Lock()
		a.downloadIndexing = false
		a.mu.Unlock()
		if recovered != nil {
			indexErr = fmt.Errorf("panic: %v", recovered)
			wailsRuntime.EventsEmit(a.ctx, "downloaded-index-event", map[string]interface{}{
				"type": "error", "message": fmt.Sprintf("Downloaded music indexing failed: %v", recovered),
			})
		}
		indexSpan.finish(
			payloadBytes,
			indexErr,
			perfCount{name: "ffmpeg_count", value: a.perfCounter("ffmpeg") - startingFFmpegCount},
			perfCount{name: "ffprobe_count", value: a.perfCounter("ffprobe") - startingProbeCount},
		)
	}()

	wailsRuntime.EventsEmit(a.ctx, "downloaded-index-event", map[string]interface{}{
		"type": "progress", "percent": 0, "label": "Finding downloads",
	})
	library, indexErrors, err := a.rebuildDownloadedLibraryIndexContext(ctx, root, cfg, func(percent int, label string) {
		wailsRuntime.EventsEmit(a.ctx, "downloaded-index-event", map[string]interface{}{
			"type": "progress", "percent": percent, "label": label,
		})
	})
	if err != nil {
		indexErr = err
		if errors.Is(err, context.Canceled) {
			wailsRuntime.EventsEmit(a.ctx, "downloaded-index-event", map[string]interface{}{
				"type": "paused", "label": "Downloaded indexing paused for a higher-priority operation",
			})
			return
		}
		wailsRuntime.EventsEmit(a.ctx, "downloaded-index-event", map[string]interface{}{
			"type": "error", "message": err.Error(),
		})
		return
	}
	if indexSpan != nil {
		if encoded, marshalErr := json.Marshal(library); marshalErr == nil {
			payloadBytes = len(encoded)
		}
	}
	if len(indexErrors) > 0 {
		wailsRuntime.EventsEmit(a.ctx, "downloaded-index-event", map[string]interface{}{
			"type":    "warning",
			"message": fmt.Sprintf("%d downloaded release(s) could not be indexed", len(indexErrors)),
			"errors":  indexErrors,
		})
	}
	wailsRuntime.EventsEmit(a.ctx, "downloaded-index-event", map[string]interface{}{
		"type": "complete", "percent": 100, "library": library,
	})
}

func (a *App) rebuildDownloadedLibraryIndex(
	root string,
	cfg Config,
	progress func(int, string),
) (libraryPayload, []string, error) {
	return a.rebuildDownloadedLibraryIndexContext(context.Background(), root, cfg, progress)
}

func (a *App) rebuildDownloadedLibraryIndexContext(
	ctx context.Context,
	root string,
	cfg Config,
	progress func(int, string),
) (libraryPayload, []string, error) {
	if info, err := os.Stat(root); err != nil || !info.IsDir() {
		if err == nil {
			err = fmt.Errorf("download path is not a folder")
		}
		return libraryPayload{}, nil, fmt.Errorf("could not index downloaded music: %w", err)
	}
	if err := ctx.Err(); err != nil {
		return libraryPayload{}, nil, err
	}

	albumStructure := cfg.AlbumFolderStructure
	if albumStructure == "" {
		albumStructure = cfg.FolderStructure
	}
	if progress != nil {
		progress(2, "Finding downloaded albums")
	}
	albums, albumErrors, err := a.scanDownloadedReleaseCandidates(ctx, root, "album", albumStructure)
	if err != nil {
		return libraryPayload{}, albumErrors, err
	}
	if progress != nil {
		progress(6, "Finding downloaded playlists")
	}
	playlists, playlistErrors, err := a.scanDownloadedReleaseCandidates(
		ctx,
		root,
		"playlist",
		cfg.PlaylistFolderStructure,
	)
	indexErrors := append(albumErrors, playlistErrors...)
	if err != nil {
		return libraryPayload{}, indexErrors, err
	}

	payload := libraryPayload{
		Albums:    summariesFromDownloadedCandidates(albums),
		Playlists: summariesFromDownloadedCandidates(playlists),
	}
	if progress != nil {
		progress(10, "Reading cached metadata")
	}
	store := a.downloadedLibraryStore()
	cache, _ := store.readSummary()
	if !sameDownloadedLibraryRoot(cache.Root, root) {
		cache = newDownloadedLibraryCache()
	}

	allCandidates := append(append([]downloadedReleaseCandidate{}, albums...), playlists...)
	totalUnits := 1
	for _, candidate := range allCandidates {
		totalUnits += max(1, len(candidate.Tracks)) + 1
	}
	tracker := &downloadedIndexProgress{
		total: totalUnits,
		last:  10,
		emit:  progress,
	}
	records := make(map[string]downloadedReleaseRecord, len(allCandidates))
	changed := make([]downloadedReleaseCandidate, 0, len(allCandidates))
	previous := make(map[string]downloadedReleaseRecord, len(allCandidates))

	for _, candidate := range allCandidates {
		cacheKey := filepath.ToSlash(candidate.Summary.RelativePath)
		record, exact := cache.Records[cacheKey]
		exact = exact &&
			record.Fingerprint == candidate.ReleaseFingerprint &&
			store.recordFilePresent(record)
		if !exact {
			record, exact = store.readRecord(cacheKey, candidate.ReleaseFingerprint)
		}
		if exact {
			records[cacheKey] = record
			tracker.advance(max(1, len(candidate.Tracks))+1, candidate.Summary.Title)
			continue
		}
		if old, ok := cache.Records[cacheKey]; ok {
			if old.Shard != "" {
				if full, loadErr := store.readRecordFile(old.Shard); loadErr == nil {
					old = full
				}
			}
			if len(old.Tracks) > 0 {
				previous[cacheKey] = old
			}
		}
		changed = append(changed, candidate)
	}

	if len(changed) > 0 {
		jobs := make(chan downloadedReleaseCandidate, len(changed))
		results := make(chan downloadedReleaseBuildResult, len(changed))
		workers := min(downloadedMetadataWorkerCount, len(changed))
		var workersDone sync.WaitGroup
		workersDone.Add(workers)
		for worker := 0; worker < workers; worker++ {
			go func() {
				defer workersDone.Done()
				for candidate := range jobs {
					cacheKey := filepath.ToSlash(candidate.Summary.RelativePath)
					record, buildErr := a.buildDownloadedReleaseRecord(
						ctx,
						candidate,
						previous[cacheKey],
						func() { tracker.advance(1, candidate.Summary.Title) },
					)
					result := downloadedReleaseBuildResult{
						candidate: candidate,
						record:    record,
						err:       buildErr,
					}
					if buildErr == nil {
						result.record, result.err = store.writeRecord(record)
						result.persist = true
					}
					tracker.advance(1, candidate.Summary.Title)
					results <- result
				}
			}()
		}
		for _, candidate := range changed {
			jobs <- candidate
		}
		close(jobs)
		go func() {
			workersDone.Wait()
			close(results)
		}()

		var persistenceErrors []error
		for result := range results {
			cacheKey := filepath.ToSlash(result.candidate.Summary.RelativePath)
			if result.err != nil {
				if errors.Is(result.err, context.Canceled) {
					return libraryPayload{}, indexErrors, result.err
				}
				if result.persist {
					persistenceErrors = append(
						persistenceErrors,
						fmt.Errorf("%s: %w", result.candidate.Summary.Title, result.err),
					)
				} else {
					indexErrors = append(
						indexErrors,
						fmt.Sprintf("%s: %v", result.candidate.Summary.Title, result.err),
					)
				}
				continue
			}
			records[cacheKey] = result.record
		}
		if err := errors.Join(persistenceErrors...); err != nil {
			return libraryPayload{}, indexErrors, fmt.Errorf("could not save downloaded release metadata: %w", err)
		}
	}

	if err := ctx.Err(); err != nil {
		return libraryPayload{}, indexErrors, err
	}
	now := time.Now().Unix()
	manifestCurrent := downloadedManifestMatches(cache, root, records)
	recentlyIndexed := cache.IndexedAt > 0 && now-cache.IndexedAt >= 0 && now-cache.IndexedAt <= 300
	if manifestCurrent && recentlyIndexed {
		if progress != nil {
			progress(99, "Download index is current")
		}
		return payload, indexErrors, nil
	}
	if progress != nil {
		progress(99, "Saving download index")
	}
	cache.SchemaVersion = downloadedLibraryIndexSchemaVersion
	cache.Root = root
	cache.Payload = payload
	cache.Records = records
	cache.Details = nil
	cache.IndexedAt = now
	a.downloadedCacheWriteMu.Lock()
	err = store.writeManifest(cache)
	a.downloadedCacheWriteMu.Unlock()
	if err != nil {
		return libraryPayload{}, indexErrors, fmt.Errorf("could not save downloaded music index: %w", err)
	}
	return payload, indexErrors, nil
}

func downloadedManifestMatches(
	cache downloadedLibraryCache,
	root string,
	records map[string]downloadedReleaseRecord,
) bool {
	if cache.SchemaVersion != downloadedLibraryIndexSchemaVersion ||
		!sameDownloadedLibraryRoot(cache.Root, root) ||
		len(cache.Records) != len(records) {
		return false
	}
	for cacheKey, record := range records {
		cached, ok := cache.Records[cacheKey]
		if !ok || cached.Fingerprint != record.Fingerprint || cached.Shard != record.Shard {
			return false
		}
	}
	return true
}

func (p *downloadedIndexProgress) advance(units int, label string) {
	if p == nil || units <= 0 {
		return
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	p.completed = min(p.total-1, p.completed+units)
	percent := 10 + (p.completed * 88 / max(1, p.total))
	percent = min(98, percent)
	if p.emit != nil && percent > p.last {
		p.emit(percent, label)
		p.last = percent
	}
}

func (a *App) scanDownloadedReleaseCandidates(
	ctx context.Context,
	root string,
	kind string,
	folderStructure string,
) ([]downloadedReleaseCandidate, []string, error) {
	var releaseDirs []string
	var err error
	if kind == "playlist" {
		releaseDirs, err = collectPlaylistReleaseDirsContext(ctx, root)
	} else {
		releaseDirs, err = collectAlbumReleaseDirsContext(ctx, root, folderStructure)
	}
	if err != nil {
		return nil, nil, err
	}
	candidates := make([]downloadedReleaseCandidate, 0, len(releaseDirs))
	var scanErrors []string
	for _, releaseDir := range releaseDirs {
		if err := ctx.Err(); err != nil {
			return nil, scanErrors, err
		}
		candidate, err := a.buildDownloadedReleaseCandidateContext(ctx, root, kind, releaseDir)
		if err != nil {
			scanErrors = append(scanErrors, fmt.Sprintf("%s: %v", releaseDir, err))
			continue
		}
		if len(candidate.Tracks) == 0 {
			continue
		}
		candidates = append(candidates, candidate)
	}
	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].Summary.Artist != candidates[j].Summary.Artist {
			return candidates[i].Summary.Artist < candidates[j].Summary.Artist
		}
		return candidates[i].Summary.Title < candidates[j].Summary.Title
	})
	return candidates, scanErrors, nil
}

func (a *App) buildDownloadedReleaseCandidate(
	root string,
	kind string,
	releaseDir string,
) (downloadedReleaseCandidate, error) {
	return a.buildDownloadedReleaseCandidateContext(context.Background(), root, kind, releaseDir)
}

func (a *App) buildDownloadedReleaseCandidateContext(
	ctx context.Context,
	root string,
	kind string,
	releaseDir string,
) (downloadedReleaseCandidate, error) {
	if err := ctx.Err(); err != nil {
		return downloadedReleaseCandidate{}, err
	}
	info, err := os.Stat(releaseDir)
	if err != nil || !info.IsDir() {
		return downloadedReleaseCandidate{}, fmt.Errorf("release folder not found")
	}
	relativePath, err := filepath.Rel(root, releaseDir)
	if err != nil {
		return downloadedReleaseCandidate{}, err
	}
	title, artist, year := inferReleaseNames(releaseDir, kind, root)
	trackPaths, err := collectAudioFilesContext(ctx, releaseDir)
	if err != nil {
		return downloadedReleaseCandidate{}, err
	}
	sort.Slice(trackPaths, func(i, j int) bool {
		return normalizedCompletePath(trackPaths[i]) < normalizedCompletePath(trackPaths[j])
	})
	tracks := make([]downloadedFileSnapshot, 0, len(trackPaths))
	for _, trackPath := range trackPaths {
		if err := ctx.Err(); err != nil {
			return downloadedReleaseCandidate{}, err
		}
		snapshot, snapshotErr := snapshotDownloadedFile(trackPath, downloadedTrackFingerprintSchema)
		if snapshotErr != nil {
			return downloadedReleaseCandidate{}, snapshotErr
		}
		tracks = append(tracks, snapshot)
	}

	var artwork *downloadedFileSnapshot
	artworkFingerprint := ""
	if artworkPath := findArtworkFile(releaseDir); artworkPath != "" {
		snapshot, snapshotErr := snapshotDownloadedFile(artworkPath, downloadedArtworkFingerprintSchema)
		if snapshotErr != nil {
			return downloadedReleaseCandidate{}, snapshotErr
		}
		artwork = &snapshot
		artworkFingerprint = snapshot.Fingerprint
	} else if len(tracks) > 0 {
		artworkFingerprint = "embedded:" + tracks[0].Fingerprint
	}
	releaseFingerprint, err := downloadedReleaseFingerprint(
		releaseDir,
		kind,
		tracks,
		artworkFingerprint,
	)
	if err != nil {
		return downloadedReleaseCandidate{}, err
	}
	summary := libraryReleaseSummary{
		Kind:         kind,
		RelativePath: filepath.ToSlash(relativePath),
		Title:        title,
		Artist:       artist,
		Year:         year,
		TrackCount:   len(tracks),
	}
	summary.ArtworkURL = a.artworkURLForDownloadedCandidate(artwork, tracks)
	return downloadedReleaseCandidate{
		Summary:            summary,
		Tracks:             tracks,
		Artwork:            artwork,
		ArtworkFingerprint: artworkFingerprint,
		ReleaseFingerprint: releaseFingerprint,
	}, nil
}

func (a *App) buildDownloadedReleaseRecord(
	ctx context.Context,
	candidate downloadedReleaseCandidate,
	previous downloadedReleaseRecord,
	trackIndexed func(),
) (downloadedReleaseRecord, error) {
	previousTracks := make(map[string]downloadedTrackCacheRecord, len(previous.Tracks))
	for _, track := range previous.Tracks {
		previousTracks[track.NormalizedPath] = track
	}
	tracks := make([]libraryReleaseTrack, 0, len(candidate.Tracks))
	cachedTracks := make([]downloadedTrackCacheRecord, 0, len(candidate.Tracks))
	for _, snapshot := range candidate.Tracks {
		if err := ctx.Err(); err != nil {
			return downloadedReleaseRecord{}, err
		}
		cached, reusable := previousTracks[snapshot.NormalizedPath]
		reusable = reusable && cached.Fingerprint == snapshot.Fingerprint
		var track libraryReleaseTrack
		if reusable {
			track = cached.Track
			track.FileName = filepath.Base(snapshot.Path)
			track.FilePath = filepath.ToSlash(snapshot.Path)
			track.AudioURL = a.mediaURL("audio", snapshot.Path)
		} else {
			track = libraryReleaseTrack{
				FileName: filepath.Base(snapshot.Path),
				FilePath: filepath.ToSlash(snapshot.Path),
				AudioURL: a.mediaURL("audio", snapshot.Path),
				Artist:   candidate.Summary.Artist,
				Album:    candidate.Summary.Title,
			}
			applyTrackFallbackMetadata(&track)
			if err := a.applyTrackProbeMetadataContext(ctx, &track); err != nil {
				return downloadedReleaseRecord{}, err
			}
		}
		tracks = append(tracks, track)
		cachedTracks = append(cachedTracks, downloadedTrackCacheRecord{
			NormalizedPath: snapshot.NormalizedPath,
			Fingerprint:    snapshot.Fingerprint,
			Track:          track,
		})
		if trackIndexed != nil {
			trackIndexed()
		}
	}

	if candidate.Artwork == nil && len(candidate.Tracks) > 0 &&
		previous.ArtworkFingerprint != candidate.ArtworkFingerprint {
		_, _ = a.extractEmbeddedArtworkContext(ctx, candidate.Tracks[0].Path)
	}
	detail := libraryReleaseDetail{
		Kind:         candidate.Summary.Kind,
		RelativePath: candidate.Summary.RelativePath,
		Title:        candidate.Summary.Title,
		Artist:       candidate.Summary.Artist,
		Year:         candidate.Summary.Year,
		TrackCount:   len(tracks),
		Tracks:       tracks,
		ArtworkURL:   a.artworkURLForDownloadedCandidate(candidate.Artwork, candidate.Tracks),
	}
	cacheKey := filepath.ToSlash(candidate.Summary.RelativePath)
	return downloadedReleaseRecord{
		SchemaVersion:      downloadedLibraryIndexSchemaVersion,
		CacheKey:           cacheKey,
		Fingerprint:        candidate.ReleaseFingerprint,
		ArtworkFingerprint: candidate.ArtworkFingerprint,
		Detail:             detail,
		Tracks:             cachedTracks,
	}, nil
}

func (a *App) buildDownloadedReleaseDetail(
	root string,
	summary libraryReleaseSummary,
	trackIndexed func(),
) (libraryReleaseDetail, error) {
	absolutePath, err := resolveLibraryPath(root, summary.RelativePath)
	if err != nil {
		return libraryReleaseDetail{}, err
	}
	candidate, err := a.buildDownloadedReleaseCandidate(root, summary.Kind, absolutePath)
	if err != nil {
		return libraryReleaseDetail{}, err
	}
	record, err := a.buildDownloadedReleaseRecord(context.Background(), candidate, downloadedReleaseRecord{}, trackIndexed)
	return record.Detail, err
}

func (a *App) refreshDownloadedDetailURLs(root, relativePath string, detail *libraryReleaseDetail) {
	if detail == nil {
		return
	}
	trackPaths := make([]string, 0, len(detail.Tracks))
	for index := range detail.Tracks {
		detail.Tracks[index].AudioURL = a.mediaURL("audio", detail.Tracks[index].FilePath)
		trackPaths = append(trackPaths, detail.Tracks[index].FilePath)
	}
	if absolutePath, err := resolveLibraryPath(root, relativePath); err == nil {
		detail.ArtworkURL = a.artworkURLForRelease(absolutePath, trackPaths)
	}
}

func collectAlbumReleaseDirsContext(
	ctx context.Context,
	albumsRoot string,
	folderStructure string,
) ([]string, error) {
	legacy, err := collectLegacyAlbumReleaseDirsContext(
		ctx,
		filepath.Join(albumsRoot, "Albums"),
		folderStructure,
	)
	if err != nil {
		return nil, err
	}
	root, err := collectRootAlbumReleaseDirsContext(ctx, albumsRoot)
	if err != nil {
		return nil, err
	}
	return orderedUniquePaths(legacy, root), nil
}

func collectLegacyAlbumReleaseDirsContext(
	ctx context.Context,
	albumsRoot string,
	folderStructure string,
) ([]string, error) {
	if !dirExists(albumsRoot) {
		return nil, nil
	}
	if folderStructure == "flat" {
		return collectTopLevelReleaseDirsContext(ctx, albumsRoot)
	}
	artistDirs, err := os.ReadDir(albumsRoot)
	if err != nil {
		return nil, err
	}
	var releases []string
	for _, artistDir := range artistDirs {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if !artistDir.IsDir() {
			continue
		}
		fullArtistDir := filepath.Join(albumsRoot, artistDir.Name())
		childReleases, err := collectTopLevelReleaseDirsContext(ctx, fullArtistDir)
		if err != nil {
			return nil, err
		}
		if len(childReleases) == 0 {
			hasAudio, err := hasAudioFilesRecursiveContext(ctx, fullArtistDir)
			if err != nil {
				return nil, err
			}
			if hasAudio {
				releases = append(releases, fullArtistDir)
			}
			continue
		}
		releases = append(releases, childReleases...)
	}
	return releases, nil
}

func collectRootAlbumReleaseDirsContext(ctx context.Context, root string) ([]string, error) {
	if !dirExists(root) {
		return nil, nil
	}
	entries, err := os.ReadDir(root)
	if err != nil {
		return nil, err
	}
	var releases []string
	for _, entry := range entries {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if !entry.IsDir() {
			continue
		}
		name := entry.Name()
		if strings.EqualFold(name, "Albums") || strings.EqualFold(name, "Playlists") {
			continue
		}
		fullPath := filepath.Join(root, name)
		hasDirectAudio, err := hasAudioFilesDirectContext(ctx, fullPath)
		if err != nil {
			return nil, err
		}
		if hasDirectAudio {
			if !hasPlaylistManifest(root, name) {
				releases = append(releases, fullPath)
			}
			continue
		}
		hasAudio, err := hasAudioFilesRecursiveContext(ctx, fullPath)
		if err != nil {
			return nil, err
		}
		if !hasAudio {
			continue
		}
		childReleases, err := collectTopLevelReleaseDirsContext(ctx, fullPath)
		if err != nil {
			return nil, err
		}
		if len(childReleases) == 0 {
			releases = append(releases, fullPath)
			continue
		}
		releases = append(releases, childReleases...)
	}
	return releases, nil
}

func collectPlaylistReleaseDirsContext(ctx context.Context, root string) ([]string, error) {
	legacy, err := collectTopLevelReleaseDirsContext(ctx, filepath.Join(root, "Playlists"))
	if err != nil {
		return nil, err
	}
	rootReleases, err := collectRootPlaylistReleaseDirsContext(ctx, root)
	if err != nil {
		return nil, err
	}
	return orderedUniquePaths(legacy, rootReleases), nil
}

func collectRootPlaylistReleaseDirsContext(ctx context.Context, root string) ([]string, error) {
	if !dirExists(root) {
		return nil, nil
	}
	entries, err := os.ReadDir(root)
	if err != nil {
		return nil, err
	}
	var releases []string
	for _, entry := range entries {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if !entry.IsDir() {
			continue
		}
		name := entry.Name()
		if strings.EqualFold(name, "Albums") || strings.EqualFold(name, "Playlists") {
			continue
		}
		fullPath := filepath.Join(root, name)
		if !hasPlaylistManifest(root, name) {
			continue
		}
		hasAudio, err := hasAudioFilesRecursiveContext(ctx, fullPath)
		if err != nil {
			return nil, err
		}
		if hasAudio {
			releases = append(releases, fullPath)
		}
	}
	return releases, nil
}

func collectTopLevelReleaseDirsContext(ctx context.Context, root string) ([]string, error) {
	if !dirExists(root) {
		return nil, nil
	}
	entries, err := os.ReadDir(root)
	if err != nil {
		return nil, err
	}
	var releases []string
	for _, entry := range entries {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if !entry.IsDir() {
			continue
		}
		fullPath := filepath.Join(root, entry.Name())
		hasAudio, err := hasAudioFilesRecursiveContext(ctx, fullPath)
		if err != nil {
			return nil, err
		}
		if hasAudio {
			releases = append(releases, fullPath)
		}
	}
	return releases, nil
}

func collectAudioFilesContext(ctx context.Context, root string) ([]string, error) {
	var files []string
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if walkErr != nil || entry == nil || entry.IsDir() {
			return nil
		}
		if isAudioFile(path) {
			files = append(files, path)
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	return files, nil
}

func hasAudioFilesRecursiveContext(ctx context.Context, root string) (bool, error) {
	found := false
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if walkErr != nil || entry == nil || entry.IsDir() {
			return nil
		}
		if isAudioFile(path) {
			found = true
			return fs.SkipAll
		}
		return nil
	})
	if err != nil {
		return false, err
	}
	return found, nil
}

func hasAudioFilesDirectContext(ctx context.Context, root string) (bool, error) {
	if err := ctx.Err(); err != nil {
		return false, err
	}
	entries, err := os.ReadDir(root)
	if err != nil {
		return false, err
	}
	for _, entry := range entries {
		if err := ctx.Err(); err != nil {
			return false, err
		}
		if !entry.IsDir() && isAudioFile(entry.Name()) {
			return true, nil
		}
	}
	return false, nil
}

func summariesFromDownloadedCandidates(candidates []downloadedReleaseCandidate) []libraryReleaseSummary {
	summaries := make([]libraryReleaseSummary, 0, len(candidates))
	for _, candidate := range candidates {
		summaries = append(summaries, candidate.Summary)
	}
	return summaries
}

func (a *App) artworkURLForDownloadedCandidate(
	artwork *downloadedFileSnapshot,
	tracks []downloadedFileSnapshot,
) string {
	if artwork != nil {
		return a.mediaURL("art", artwork.Path)
	}
	if len(tracks) > 0 {
		return a.mediaURL("embedded-art", tracks[0].Path)
	}
	return ""
}

func upsertDownloadedReleaseSummary(payload *libraryPayload, summary libraryReleaseSummary) {
	if payload == nil {
		return
	}
	releases := &payload.Albums
	if summary.Kind == "playlist" {
		releases = &payload.Playlists
	}
	for index := range *releases {
		if filepath.ToSlash((*releases)[index].RelativePath) == filepath.ToSlash(summary.RelativePath) {
			(*releases)[index] = summary
			return
		}
	}
	*releases = append(*releases, summary)
	sort.Slice(*releases, func(i, j int) bool {
		if (*releases)[i].Artist != (*releases)[j].Artist {
			return (*releases)[i].Artist < (*releases)[j].Artist
		}
		return (*releases)[i].Title < (*releases)[j].Title
	})
}
