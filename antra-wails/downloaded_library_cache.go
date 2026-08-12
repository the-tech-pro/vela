package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
)

const (
	downloadedLibraryIndexSchemaVersion = 2
	downloadedTrackFingerprintSchema    = "vela-downloaded-track-v2"
	downloadedArtworkFingerprintSchema  = "vela-downloaded-artwork-v2"
	downloadedReleaseFingerprintSchema  = "vela-downloaded-release-v2"
)

type downloadedLibraryCache struct {
	SchemaVersion int                                `json:"schema_version,omitempty"`
	Root          string                             `json:"root"`
	Payload       libraryPayload                     `json:"payload"`
	Details       map[string]libraryReleaseDetail    `json:"details,omitempty"`
	IndexedAt     int64                              `json:"indexed_at"`
	Records       map[string]downloadedReleaseRecord `json:"-"`
}

type downloadedFileSnapshot struct {
	Path            string
	NormalizedPath  string
	Size            int64
	ModTimeUnixNano int64
	Fingerprint     string
}

type downloadedReleaseCandidate struct {
	Summary            libraryReleaseSummary
	Tracks             []downloadedFileSnapshot
	Artwork            *downloadedFileSnapshot
	ArtworkFingerprint string
	ReleaseFingerprint string
}

type downloadedTrackCacheRecord struct {
	NormalizedPath string              `json:"normalized_path"`
	Fingerprint    string              `json:"fingerprint"`
	Track          libraryReleaseTrack `json:"track"`
}

type downloadedReleaseRecord struct {
	SchemaVersion      int                          `json:"schema_version"`
	CacheKey           string                       `json:"cache_key"`
	Fingerprint        string                       `json:"fingerprint"`
	ArtworkFingerprint string                       `json:"artwork_fingerprint,omitempty"`
	Detail             libraryReleaseDetail         `json:"detail"`
	Tracks             []downloadedTrackCacheRecord `json:"tracks"`
	Shard              string                       `json:"-"`
}

type downloadedLibraryManifestEntry struct {
	Fingerprint string `json:"fingerprint"`
	Shard       string `json:"shard"`
}

type downloadedLibraryManifest struct {
	SchemaVersion int                                       `json:"schema_version"`
	Root          string                                    `json:"root"`
	Payload       libraryPayload                            `json:"payload"`
	Releases      map[string]downloadedLibraryManifestEntry `json:"releases"`
	IndexedAt     int64                                     `json:"indexed_at"`
}

type downloadedLibraryStore struct {
	legacyPath       string
	directory        string
	manifestPath     string
	recordsDirectory string
}

func downloadedLibraryCachePath() string {
	return filepath.Join(getAppDataDir(), "downloaded-library-index.json")
}

func defaultDownloadedLibraryStore() *downloadedLibraryStore {
	base := getAppDataDir()
	return newDownloadedLibraryStore(
		filepath.Join(base, "downloaded-library-index.json"),
		filepath.Join(base, "downloaded-library-index-v2"),
	)
}

func (a *App) downloadedLibraryStore() *downloadedLibraryStore {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.downloadedStore == nil {
		a.downloadedStore = defaultDownloadedLibraryStore()
	}
	return a.downloadedStore
}

func newDownloadedLibraryStore(legacyPath, directory string) *downloadedLibraryStore {
	return &downloadedLibraryStore{
		legacyPath:       legacyPath,
		directory:        directory,
		manifestPath:     filepath.Join(directory, "manifest.json"),
		recordsDirectory: filepath.Join(directory, "releases"),
	}
}

func readDownloadedLibraryCache() downloadedLibraryCache {
	cache, _ := defaultDownloadedLibraryStore().read()
	return cache
}

func (s *downloadedLibraryStore) read() (downloadedLibraryCache, error) {
	cache, err := s.readSummary()
	if err != nil || cache.SchemaVersion != downloadedLibraryIndexSchemaVersion {
		return cache, err
	}
	for cacheKey, stub := range cache.Records {
		record, recordErr := s.readRecordFile(stub.Shard)
		if recordErr != nil || record.CacheKey != cacheKey || record.Fingerprint != stub.Fingerprint {
			delete(cache.Records, cacheKey)
			continue
		}
		cache.Records[cacheKey] = record
		cache.Details[cacheKey] = record.Detail
	}
	return cache, nil
}

func (s *downloadedLibraryStore) readSummary() (downloadedLibraryCache, error) {
	cache, manifestErr := s.readManifestSummary()
	if manifestErr == nil {
		return cache, nil
	}

	legacy, legacyErr := s.readLegacy()
	if legacyErr == nil {
		return legacy, nil
	}
	if errors.Is(manifestErr, os.ErrNotExist) && errors.Is(legacyErr, os.ErrNotExist) {
		return newDownloadedLibraryCache(), nil
	}
	return newDownloadedLibraryCache(), errors.Join(manifestErr, legacyErr)
}

func (s *downloadedLibraryStore) readManifestSummary() (downloadedLibraryCache, error) {
	data, err := os.ReadFile(s.manifestPath)
	if err != nil {
		return newDownloadedLibraryCache(), err
	}
	var manifest downloadedLibraryManifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		return newDownloadedLibraryCache(), fmt.Errorf("decode downloaded library manifest: %w", err)
	}
	if manifest.SchemaVersion != downloadedLibraryIndexSchemaVersion {
		return newDownloadedLibraryCache(), fmt.Errorf(
			"unsupported downloaded library manifest schema %d",
			manifest.SchemaVersion,
		)
	}

	cache := newDownloadedLibraryCache()
	cache.SchemaVersion = manifest.SchemaVersion
	cache.Root = manifest.Root
	cache.Payload = manifest.Payload
	cache.IndexedAt = manifest.IndexedAt
	for cacheKey, entry := range manifest.Releases {
		cache.Records[cacheKey] = downloadedReleaseRecord{
			SchemaVersion: downloadedLibraryIndexSchemaVersion,
			CacheKey:      cacheKey,
			Fingerprint:   entry.Fingerprint,
			Shard:         entry.Shard,
		}
	}
	return cache, nil
}

func (s *downloadedLibraryStore) readLegacy() (downloadedLibraryCache, error) {
	data, err := os.ReadFile(s.legacyPath)
	if err != nil {
		return newDownloadedLibraryCache(), err
	}
	cache := newDownloadedLibraryCache()
	if err := json.Unmarshal(data, &cache); err != nil {
		return newDownloadedLibraryCache(), fmt.Errorf("decode legacy downloaded library index: %w", err)
	}
	cache.SchemaVersion = 0
	if cache.Details == nil {
		cache.Details = make(map[string]libraryReleaseDetail)
	}
	cache.Records = make(map[string]downloadedReleaseRecord)
	return cache, nil
}

func newDownloadedLibraryCache() downloadedLibraryCache {
	return downloadedLibraryCache{
		SchemaVersion: downloadedLibraryIndexSchemaVersion,
		Details:       make(map[string]libraryReleaseDetail),
		Records:       make(map[string]downloadedReleaseRecord),
	}
}

func (s *downloadedLibraryStore) readRecord(cacheKey, fingerprint string) (downloadedReleaseRecord, bool) {
	shard := downloadedReleaseShardName(cacheKey, fingerprint)
	record, err := s.readRecordFile(shard)
	if err != nil || record.CacheKey != cacheKey || record.Fingerprint != fingerprint {
		return downloadedReleaseRecord{}, false
	}
	return record, true
}

func (s *downloadedLibraryStore) recordFilePresent(record downloadedReleaseRecord) bool {
	if filepath.Base(record.Shard) != record.Shard || record.Shard == "." || record.Shard == "" {
		return false
	}
	info, err := os.Stat(filepath.Join(s.recordsDirectory, record.Shard))
	return err == nil && info.Mode().IsRegular() && info.Size() > 0
}

func (s *downloadedLibraryStore) readRecordFile(shard string) (downloadedReleaseRecord, error) {
	var record downloadedReleaseRecord
	if filepath.Base(shard) != shard || shard == "." || shard == "" {
		return record, fmt.Errorf("invalid downloaded release shard name")
	}
	data, err := os.ReadFile(filepath.Join(s.recordsDirectory, shard))
	if err != nil {
		return record, err
	}
	if err := json.Unmarshal(data, &record); err != nil {
		return record, fmt.Errorf("decode downloaded release shard: %w", err)
	}
	if record.SchemaVersion != downloadedLibraryIndexSchemaVersion {
		return record, fmt.Errorf("unsupported downloaded release schema %d", record.SchemaVersion)
	}
	record.Shard = shard
	return record, nil
}

func (s *downloadedLibraryStore) writeRecord(record downloadedReleaseRecord) (downloadedReleaseRecord, error) {
	record.SchemaVersion = downloadedLibraryIndexSchemaVersion
	record.Shard = downloadedReleaseShardName(record.CacheKey, record.Fingerprint)
	if existing, err := s.readRecordFile(record.Shard); err == nil &&
		existing.CacheKey == record.CacheKey &&
		existing.Fingerprint == record.Fingerprint {
		return existing, nil
	}
	if err := atomicWriteJSON(filepath.Join(s.recordsDirectory, record.Shard), record, 0644); err != nil {
		return downloadedReleaseRecord{}, err
	}
	return record, nil
}

func (s *downloadedLibraryStore) writeManifest(cache downloadedLibraryCache) error {
	manifest := downloadedLibraryManifest{
		SchemaVersion: downloadedLibraryIndexSchemaVersion,
		Root:          cache.Root,
		Payload:       cache.Payload,
		Releases:      make(map[string]downloadedLibraryManifestEntry, len(cache.Records)),
		IndexedAt:     cache.IndexedAt,
	}
	for cacheKey, record := range cache.Records {
		shard := record.Shard
		if shard == "" {
			shard = downloadedReleaseShardName(cacheKey, record.Fingerprint)
		}
		manifest.Releases[cacheKey] = downloadedLibraryManifestEntry{
			Fingerprint: record.Fingerprint,
			Shard:       shard,
		}
	}
	return atomicWriteJSON(s.manifestPath, manifest, 0644)
}

func atomicWriteJSON(path string, value interface{}, mode os.FileMode) error {
	data, err := json.Marshal(value)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return err
	}
	temp, err := os.CreateTemp(filepath.Dir(path), filepath.Base(path)+"-*.tmp")
	if err != nil {
		return err
	}
	tempPath := temp.Name()
	defer os.Remove(tempPath)
	if err := temp.Chmod(mode); err != nil {
		_ = temp.Close()
		return err
	}
	if _, err := temp.Write(data); err != nil {
		_ = temp.Close()
		return err
	}
	if err := temp.Sync(); err != nil {
		_ = temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	return replaceFileAtomic(tempPath, path)
}

func normalizedCompletePath(path string) string {
	absolute, err := filepath.Abs(path)
	if err != nil {
		absolute = path
	}
	normalized := filepath.ToSlash(filepath.Clean(absolute))
	if runtime.GOOS == "windows" {
		normalized = strings.ToLower(normalized)
	}
	return normalized
}

func snapshotDownloadedFile(path, schema string) (downloadedFileSnapshot, error) {
	info, err := os.Stat(path)
	if err != nil {
		return downloadedFileSnapshot{}, err
	}
	if !info.Mode().IsRegular() {
		return downloadedFileSnapshot{}, fmt.Errorf("%s is not a regular file", path)
	}
	normalized := normalizedCompletePath(path)
	identity := fmt.Sprintf(
		"%s\x00%s\x00%d\x00%d",
		schema,
		normalized,
		info.Size(),
		info.ModTime().UnixNano(),
	)
	sum := sha256.Sum256([]byte(identity))
	return downloadedFileSnapshot{
		Path:            filepath.Clean(path),
		NormalizedPath:  normalized,
		Size:            info.Size(),
		ModTimeUnixNano: info.ModTime().UnixNano(),
		Fingerprint:     hex.EncodeToString(sum[:]),
	}, nil
}

func downloadedReleaseFingerprint(
	releasePath string,
	kind string,
	tracks []downloadedFileSnapshot,
	artworkFingerprint string,
) (string, error) {
	info, err := os.Stat(releasePath)
	if err != nil {
		return "", err
	}
	trackFingerprints := make([]string, 0, len(tracks))
	for _, track := range tracks {
		trackFingerprints = append(trackFingerprints, track.Fingerprint)
	}
	sort.Strings(trackFingerprints)
	identity := fmt.Sprintf(
		"%s\x00%s\x00%s\x00%d\x00%d\x00%s\x00%s",
		downloadedReleaseFingerprintSchema,
		normalizedCompletePath(releasePath),
		kind,
		info.Size(),
		info.ModTime().UnixNano(),
		strings.Join(trackFingerprints, "\x00"),
		artworkFingerprint,
	)
	sum := sha256.Sum256([]byte(identity))
	return hex.EncodeToString(sum[:]), nil
}

func downloadedReleaseShardName(cacheKey, fingerprint string) string {
	sum := sha256.Sum256([]byte(
		fmt.Sprintf(
			"%d\x00%s\x00%s",
			downloadedLibraryIndexSchemaVersion,
			filepath.ToSlash(cacheKey),
			fingerprint,
		),
	))
	return hex.EncodeToString(sum[:]) + ".json"
}
