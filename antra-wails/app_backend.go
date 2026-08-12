package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"time"

	wailsRuntime "github.com/wailsapp/wails/v2/pkg/runtime"
)

const currentConfigSchemaVersion = 2

type UIConfig struct {
	Scale                     float64 `json:"scale"`
	Density                   string  `json:"density"`
	SidebarWidth              int     `json:"sidebar_width"`
	ArtworkSize               int     `json:"artwork_size"`
	Motion                    string  `json:"motion"`
	PlayerVolume              float64 `json:"player_volume"`
	StartupDestination        string  `json:"startup_destination"`
	RememberLastPage          bool    `json:"remember_last_page"`
	OpenDownloadsOnAdd        bool    `json:"open_downloads_on_add"`
	CompletionNotifications   bool    `json:"completion_notifications"`
	DeviceNotifications       bool    `json:"device_notifications"`
	CompletedHistoryRetention int     `json:"completed_history_retention"`
}

type Config struct {
	ConfigSchemaVersion         int           `json:"config_schema_version"`
	UI                          *UIConfig     `json:"ui,omitempty"`
	DownloadPath                string        `json:"download_path"`
	DownloadPathIsLibraryRoot   bool          `json:"download_path_is_library_root,omitempty"`
	AppleEnabled                bool          `json:"apple_enabled"`
	AppleAuthorizationToken     string        `json:"apple_authorization_token,omitempty"`
	AppleMusicUserToken         string        `json:"apple_music_user_token,omitempty"`
	AppleStorefront             string        `json:"apple_storefront,omitempty"`
	AppleWVDPath                string        `json:"apple_wvd_path,omitempty"`
	AmazonEnabled               bool          `json:"amazon_enabled"`
	AmazonDirectCredsJSON       string        `json:"amazon_direct_creds_json,omitempty"`
	AmazonWVDPath               string        `json:"amazon_wvd_path,omitempty"`
	AmazonRegion                string        `json:"amazon_region,omitempty"`
	QobuzEnabled                bool          `json:"qobuz_enabled"`
	QobuzEmail                  string        `json:"qobuz_email,omitempty"`
	QobuzPassword               string        `json:"qobuz_password,omitempty"`
	QobuzAppID                  string        `json:"qobuz_app_id,omitempty"`
	QobuzAppSecret              string        `json:"qobuz_app_secret,omitempty"`
	QobuzUserAuthToken          string        `json:"qobuz_user_auth_token,omitempty"`
	DeezerARLToken              string        `json:"deezer_arl_token,omitempty"`
	DeezerBFSecret              string        `json:"deezer_bf_secret,omitempty"`
	SourcesEnabled              []string      `json:"sources_enabled,omitempty"`
	FirstRunComplete            bool          `json:"first_run_complete"`
	OutputFormat                string        `json:"output_format,omitempty"`
	MaxRetries                  int           `json:"max_retries,omitempty"`
	MaxConcurrentJobs           int           `json:"max_concurrent_jobs,omitempty"`
	LibraryMode                 string        `json:"library_mode,omitempty"`
	PreferExplicit              *bool         `json:"prefer_explicit,omitempty"`
	StrictMatching              bool          `json:"strict_matching"`
	FolderStructure             string        `json:"folder_structure,omitempty"`
	AlbumFolderStructure        string        `json:"album_folder_structure,omitempty"`
	PlaylistFolderStructure     string        `json:"playlist_folder_structure,omitempty"`
	SingleTrackStructure        string        `json:"single_track_structure,omitempty"`
	FilenameFormat              string        `json:"filename_format,omitempty"`
	SingleTrackFilenameTemplate string        `json:"single_track_filename_template,omitempty"`
	AlbumZipNameTemplate        string        `json:"album_zip_name_template,omitempty"`
	AlbumTrackFilenameTemplate  string        `json:"album_track_filename_template,omitempty"`
	FolderStructureTemplate     string        `json:"folder_structure_template,omitempty"`
	MultiDiscHandling           string        `json:"multi_disc_handling,omitempty"`
	TrackNumberPadding          int           `json:"track_number_padding,omitempty"`
	IllegalCharacterReplacement string        `json:"illegal_character_replacement,omitempty"`
	WhitespaceHandling          string        `json:"whitespace_handling,omitempty"`
	FilenameConflictBehavior    string        `json:"filename_conflict_behavior,omitempty"`
	FetchLyrics                 bool          `json:"fetch_lyrics"`
	SpotifySpDc                 string        `json:"spotify_sp_dc,omitempty"`
	TidalEnabled                bool          `json:"tidal_enabled"`
	TidalAuthMode               string        `json:"tidal_auth_mode,omitempty"`
	TidalSessionJSON            string        `json:"tidal_session_json,omitempty"`
	TidalAccessToken            string        `json:"tidal_access_token,omitempty"`
	TidalRefreshToken           string        `json:"tidal_refresh_token,omitempty"`
	TidalSessionID              string        `json:"tidal_session_id,omitempty"`
	TidalTokenType              string        `json:"tidal_token_type,omitempty"`
	TidalCountryCode            string        `json:"tidal_country_code,omitempty"`
	AntraApiKey                 string        `json:"antra_api_key,omitempty"`
	Theme                       string        `json:"theme,omitempty"`
	DownloadSource              string        `json:"download_source,omitempty"`
	DownloadSources             []string      `json:"download_sources,omitempty"`
	SaveCoverArtSidecar         bool          `json:"save_cover_art_sidecar"`
	AutoSyncEnabled             bool          `json:"auto_sync_enabled"`
	AutoSyncHour                int           `json:"auto_sync_hour"`
	AutoSyncMinute              int           `json:"auto_sync_minute"`
	AutoSyncDays                int           `json:"auto_sync_days"` // bitmask Mon=0…Sun=6
	TrackedPlaylists            []interface{} `json:"tracked_playlists,omitempty"`
}

func defaultUIConfig() UIConfig {
	return UIConfig{
		Scale:                     1,
		Density:                   "comfortable",
		SidebarWidth:              240,
		ArtworkSize:               170,
		Motion:                    "system",
		PlayerVolume:              0.8,
		StartupDestination:        "recently-added",
		RememberLastPage:          true,
		OpenDownloadsOnAdd:        true,
		CompletionNotifications:   true,
		DeviceNotifications:       true,
		CompletedHistoryRetention: 100,
	}
}

func clampFloat(value, min, max float64) float64 {
	if value < min {
		return min
	}
	if value > max {
		return max
	}
	return value
}

func clampInt(value, min, max int) int {
	if value < min {
		return min
	}
	if value > max {
		return max
	}
	return value
}

func oneOf(value string, fallback string, allowed ...string) string {
	for _, candidate := range allowed {
		if value == candidate {
			return value
		}
	}
	return fallback
}

// normalizeConfig is the single migration/default/clamping path used for both
// reads and writes. The raw payload is retained only to distinguish absent
// legacy booleans from explicit false values.
func normalizeConfig(cfg Config, raw []byte) Config {
	defaults := defaultUIConfig()
	if cfg.UI == nil {
		cfg.UI = &defaults
	} else {
		ui := *cfg.UI
		if ui.Scale == 0 {
			ui.Scale = defaults.Scale
		}
		ui.Scale = clampFloat(ui.Scale, 0.85, 1.25)
		ui.Density = oneOf(ui.Density, defaults.Density, "compact", "comfortable", "spacious")
		if ui.SidebarWidth == 0 {
			ui.SidebarWidth = defaults.SidebarWidth
		}
		ui.SidebarWidth = clampInt(ui.SidebarWidth, 210, 300)
		if ui.ArtworkSize == 0 {
			ui.ArtworkSize = defaults.ArtworkSize
		}
		ui.ArtworkSize = clampInt(ui.ArtworkSize, 130, 210)
		ui.Motion = oneOf(ui.Motion, defaults.Motion, "system", "reduced", "full")
		if !bytes.Contains(raw, []byte(`"player_volume"`)) {
			ui.PlayerVolume = defaults.PlayerVolume
		} else if ui.PlayerVolume < 0 || ui.PlayerVolume > 1 {
			ui.PlayerVolume = clampFloat(ui.PlayerVolume, 0, 1)
		}
		ui.StartupDestination = oneOf(
			ui.StartupDestination,
			defaults.StartupDestination,
			"recently-added", "albums", "playlists", "favourites", "artists", "downloaded", "downloads",
		)
		if !bytes.Contains(raw, []byte(`"remember_last_page"`)) {
			ui.RememberLastPage = defaults.RememberLastPage
		}
		if !bytes.Contains(raw, []byte(`"open_downloads_on_add"`)) {
			ui.OpenDownloadsOnAdd = defaults.OpenDownloadsOnAdd
		}
		if !bytes.Contains(raw, []byte(`"completion_notifications"`)) {
			ui.CompletionNotifications = defaults.CompletionNotifications
		}
		if !bytes.Contains(raw, []byte(`"device_notifications"`)) {
			ui.DeviceNotifications = defaults.DeviceNotifications
		}
		if ui.CompletedHistoryRetention == 0 {
			ui.CompletedHistoryRetention = defaults.CompletedHistoryRetention
		}
		ui.CompletedHistoryRetention = clampInt(ui.CompletedHistoryRetention, 10, 1000)
		cfg.UI = &ui
	}

	cfg.Theme = oneOf(strings.ToLower(strings.TrimSpace(cfg.Theme)), "system", "system", "light", "dark")
	cfg.ConfigSchemaVersion = currentConfigSchemaVersion
	return cfg
}

func downloadWorkerCeiling() int {
	return downloadWorkerCeilingForCPU(runtime.NumCPU())
}

func downloadWorkerCeilingForCPU(cpus int) int {
	if cpus <= 4 {
		return 8
	}
	if cpus <= 8 {
		return 12
	}
	return 16
}

func (a *App) GetDownloadWorkerCapacity() int {
	return downloadWorkerCeiling()
}

type HistoryItem struct {
	Date           string         `json:"date"`
	URL            string         `json:"url"`
	Title          string         `json:"title,omitempty"`
	ArtworkUrl     string         `json:"artwork_url,omitempty"`
	Total          int            `json:"total"`
	Downloaded     int            `json:"downloaded"`
	Failed         int            `json:"failed"`
	Skipped        int            `json:"skipped"`
	Error          string         `json:"error,omitempty"`
	Sources        map[string]int `json:"sources"`
	CompletedFiles []string       `json:"completed_files,omitempty"`
}

func getAppDataDir() string {
	switch runtime.GOOS {
	case "windows":
		localAppData := os.Getenv("LOCALAPPDATA")
		return filepath.Join(localAppData, "Antra")
	case "darwin":
		home := os.Getenv("HOME")
		return filepath.Join(home, "Library", "Application Support", "Antra")
	default:
		home := os.Getenv("HOME")
		return filepath.Join(home, ".local", "share", "Antra")
	}
}

func getConfigPath() string {
	return filepath.Join(getAppDataDir(), "config.json")
}

func getHistoryPath() string {
	return filepath.Join(getAppDataDir(), "history.json")
}

func defaultVelaMusicPath() string {
	home, err := os.UserHomeDir()
	if err != nil || strings.TrimSpace(home) == "" {
		home = "."
	}
	return filepath.Join(home, "Music", "Vela")
}

// GetSuggestedDownloadLocation provides explicit, local presets for Settings.
// The returned folder is the actual library root, not a parent that later gains
// a hidden product-specific suffix.
func (a *App) GetSuggestedDownloadLocation(kind string) string {
	home, err := os.UserHomeDir()
	if err != nil || strings.TrimSpace(home) == "" {
		home = "."
	}
	if strings.EqualFold(strings.TrimSpace(kind), "downloads") {
		return filepath.Join(home, "Downloads", "Vela")
	}
	return filepath.Join(home, "Music", "Vela")
}

func cloneConfig(cfg Config) Config {
	cloned := cfg
	if cfg.UI != nil {
		ui := *cfg.UI
		cloned.UI = &ui
	}
	cloned.SourcesEnabled = append([]string(nil), cfg.SourcesEnabled...)
	cloned.DownloadSources = append([]string(nil), cfg.DownloadSources...)
	if cfg.TrackedPlaylists != nil {
		cloned.TrackedPlaylists = make([]interface{}, len(cfg.TrackedPlaylists))
		for index, entry := range cfg.TrackedPlaylists {
			cloned.TrackedPlaylists[index] = cloneConfigValue(entry)
		}
	}
	return cloned
}

func cloneConfigValue(value interface{}) interface{} {
	switch typed := value.(type) {
	case map[string]interface{}:
		cloned := make(map[string]interface{}, len(typed))
		for key, item := range typed {
			cloned[key] = cloneConfigValue(item)
		}
		return cloned
	case []interface{}:
		cloned := make([]interface{}, len(typed))
		for index, item := range typed {
			cloned[index] = cloneConfigValue(item)
		}
		return cloned
	case []string:
		return append([]string(nil), typed...)
	default:
		return value
	}
}

// GetConfig returns a detached copy of the lazily cached, normalized
// application configuration.
func (a *App) GetConfig() Config {
	a.configMu.RLock()
	if a.configCacheReady {
		cfg := cloneConfig(a.configCache)
		a.configMu.RUnlock()
		return cfg
	}
	a.configMu.RUnlock()

	a.configMu.Lock()
	defer a.configMu.Unlock()
	if !a.configCacheReady {
		a.configCache = cloneConfig(a.loadConfigFromDisk())
		a.configCacheReady = true
	}
	return cloneConfig(a.configCache)
}

func (a *App) replaceConfigCache(cfg Config) {
	a.configMu.Lock()
	a.configCache = cloneConfig(cfg)
	a.configCacheReady = true
	a.configMu.Unlock()
}

func (a *App) invalidateConfigCache() {
	a.configMu.Lock()
	a.configCache = Config{}
	a.configCacheReady = false
	a.configMu.Unlock()
}

func (a *App) loadConfigFromDisk() Config {
	var cfg Config
	cfgPath := getConfigPath()
	if _, err := os.Stat(cfgPath); os.IsNotExist(err) {
		userProfile := os.Getenv("USERPROFILE")
		if userProfile == "" {
			userProfile = os.Getenv("HOME")
		}
		cfg.DownloadPath = filepath.Join(userProfile, "Music", "Vela")
		cfg.DownloadPathIsLibraryRoot = true
		cfg.MaxRetries = 3
		cfg.MaxConcurrentJobs = 2
		cfg.AppleStorefront = "gb"
		cfg.QobuzAppID = "285473059"
		cfg.DeezerBFSecret = "g4el58wc0zvf9na1"
		cfg.TidalAuthMode = "session_json"
		cfg.TidalTokenType = "Bearer"
		cfg.FolderStructure = "standard"
		cfg.AlbumFolderStructure = "standard"
		cfg.PlaylistFolderStructure = "standard"
		cfg.SingleTrackStructure = "album_numbered"
		cfg.MultiDiscHandling = "track_only"
		cfg.TrackNumberPadding = 2
		cfg.IllegalCharacterReplacement = "_"
		cfg.WhitespaceHandling = "preserve"
		cfg.FilenameConflictBehavior = "skip"
		cfg.FetchLyrics = true
		cfg.SaveCoverArtSidecar = true
		cfg.DownloadSource = "auto"
		cfg.DownloadSources = []string{"auto"}
		return normalizeConfig(cfg, nil)
	}
	if err := ensurePrivateConfigPermissions(cfgPath); err != nil {
		a.logWarningf("Failed to restrict config permissions: %v", err)
	}

	data, err := os.ReadFile(cfgPath)
	if err != nil {
		wailsRuntime.LogErrorf(a.ctx, "Failed to read config: %v", err)
		cfg.DownloadPath = defaultVelaMusicPath()
		return normalizeConfig(cfg, nil)
	}

	json.Unmarshal(data, &cfg)
	if !bytes.Contains(data, []byte(`"fetch_lyrics"`)) {
		cfg.FetchLyrics = true
	}
	if !bytes.Contains(data, []byte(`"save_cover_art_sidecar"`)) {
		cfg.SaveCoverArtSidecar = true
	}
	if cfg.DownloadPath == "" {
		userProfile := os.Getenv("USERPROFILE")
		if userProfile == "" {
			userProfile = os.Getenv("HOME")
		}
		cfg.DownloadPath = filepath.Join(userProfile, "Music", "Vela")
	}
	if !bytes.Contains(data, []byte(`"download_path_is_library_root"`)) {
		// Older builds stored a parent and silently appended "Apple Music" in
		// Python. Preserve an existing legacy library; otherwise migrate the
		// parent to the new Vela-named root without moving user files.
		legacy := filepath.Join(cfg.DownloadPath, "Apple Music")
		if info, err := os.Stat(legacy); err == nil && info.IsDir() {
			cfg.DownloadPath = legacy
		} else if !strings.EqualFold(filepath.Base(cfg.DownloadPath), "Vela") && !strings.EqualFold(filepath.Base(cfg.DownloadPath), "Apple Music") {
			cfg.DownloadPath = filepath.Join(cfg.DownloadPath, "Vela")
		}
		cfg.DownloadPathIsLibraryRoot = true
	}
	if cfg.MaxRetries <= 0 {
		cfg.MaxRetries = 3
	}
	if cfg.MaxConcurrentJobs < 1 {
		cfg.MaxConcurrentJobs = 2
	}
	if cfg.MaxConcurrentJobs > downloadWorkerCeiling() {
		cfg.MaxConcurrentJobs = downloadWorkerCeiling()
	}
	if cfg.AppleStorefront == "" {
		cfg.AppleStorefront = "gb"
	}
	if cfg.QobuzAppID == "" {
		cfg.QobuzAppID = "285473059"
	}
	if cfg.DeezerBFSecret == "" {
		cfg.DeezerBFSecret = "g4el58wc0zvf9na1"
	}
	if cfg.TidalAuthMode == "" {
		cfg.TidalAuthMode = "session_json"
	}
	if cfg.TidalTokenType == "" {
		cfg.TidalTokenType = "Bearer"
	}
	if cfg.FolderStructure == "" {
		cfg.FolderStructure = "standard"
	}
	if cfg.AlbumFolderStructure == "" {
		cfg.AlbumFolderStructure = cfg.FolderStructure
	}
	if cfg.PlaylistFolderStructure == "" {
		cfg.PlaylistFolderStructure = cfg.FolderStructure
	}
	if cfg.SingleTrackStructure == "" {
		cfg.SingleTrackStructure = "album_numbered"
	}
	if cfg.MultiDiscHandling == "" {
		cfg.MultiDiscHandling = "track_only"
	}
	if cfg.TrackNumberPadding <= 0 {
		cfg.TrackNumberPadding = 2
	}
	if cfg.IllegalCharacterReplacement == "" {
		cfg.IllegalCharacterReplacement = "_"
	}
	if cfg.WhitespaceHandling == "" {
		cfg.WhitespaceHandling = "preserve"
	}
	if cfg.FilenameConflictBehavior == "" {
		cfg.FilenameConflictBehavior = "skip"
	}
	if cfg.DownloadSource == "" {
		cfg.DownloadSource = "auto"
	}
	if len(cfg.DownloadSources) == 0 {
		cfg.DownloadSources = []string{cfg.DownloadSource}
	}
	return normalizeConfig(cfg, data)
}

// SaveConfig saves the configuration and marks first run as complete
func (a *App) SaveConfig(cfg Config) error {
	a.configWriteMu.Lock()
	defer a.configWriteMu.Unlock()

	cfg.FirstRunComplete = true
	cfg.DownloadPathIsLibraryRoot = true
	raw, _ := json.Marshal(cfg)
	cfg = normalizeConfig(cfg, raw)
	if cfg.MaxRetries <= 0 {
		cfg.MaxRetries = 3
	}
	if cfg.MaxConcurrentJobs < 1 {
		cfg.MaxConcurrentJobs = 2
	}
	if cfg.MaxConcurrentJobs > downloadWorkerCeiling() {
		cfg.MaxConcurrentJobs = downloadWorkerCeiling()
	}
	if cfg.AppleStorefront == "" {
		cfg.AppleStorefront = "gb"
	}
	if cfg.QobuzAppID == "" {
		cfg.QobuzAppID = "285473059"
	}
	if cfg.DeezerBFSecret == "" {
		cfg.DeezerBFSecret = "g4el58wc0zvf9na1"
	}
	if cfg.TidalAuthMode == "" {
		cfg.TidalAuthMode = "session_json"
	}
	if cfg.TidalTokenType == "" {
		cfg.TidalTokenType = "Bearer"
	}
	if cfg.FolderStructure == "" {
		cfg.FolderStructure = "standard"
	}
	if cfg.AlbumFolderStructure == "" {
		cfg.AlbumFolderStructure = cfg.FolderStructure
	}
	if cfg.PlaylistFolderStructure == "" {
		cfg.PlaylistFolderStructure = cfg.FolderStructure
	}
	if cfg.SingleTrackStructure == "" {
		cfg.SingleTrackStructure = "album_numbered"
	}
	if cfg.MultiDiscHandling == "" {
		cfg.MultiDiscHandling = "track_only"
	}
	if cfg.TrackNumberPadding <= 0 {
		cfg.TrackNumberPadding = 2
	}
	if cfg.IllegalCharacterReplacement == "" {
		cfg.IllegalCharacterReplacement = "_"
	}
	if cfg.WhitespaceHandling == "" {
		cfg.WhitespaceHandling = "preserve"
	}
	if cfg.FilenameConflictBehavior == "" {
		cfg.FilenameConflictBehavior = "skip"
	}
	if cfg.DownloadSource == "" {
		cfg.DownloadSource = "auto"
	}
	if len(cfg.DownloadSources) == 0 {
		cfg.DownloadSources = []string{cfg.DownloadSource}
	}
	dir := getAppDataDir()
	if err := os.MkdirAll(dir, 0755); err != nil {
		return err
	}

	data, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}

	temp, err := os.CreateTemp(dir, "config-*.tmp")
	if err != nil {
		return err
	}
	tempName := temp.Name()
	defer os.Remove(tempName)
	if err := temp.Chmod(0600); err != nil {
		temp.Close()
		return err
	}
	if _, err := temp.Write(data); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Sync(); err != nil {
		temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	if err := os.Rename(tempName, getConfigPath()); err != nil {
		return err
	}
	if err := ensurePrivateConfigPermissions(getConfigPath()); err != nil {
		a.invalidateConfigCache()
		return err
	}
	a.replaceConfigCache(cfg)
	return nil
}

// GetHistory returns the application history
func (a *App) GetHistory() []HistoryItem {
	var history []HistoryItem
	historyPath := getHistoryPath()

	if _, err := os.Stat(historyPath); os.IsNotExist(err) {
		return history
	}

	data, err := os.ReadFile(historyPath)
	if err != nil {
		wailsRuntime.LogErrorf(a.ctx, "Failed to read history: %v", err)
		return history
	}

	json.Unmarshal(data, &history)
	return history
}

// AddHistory appends a new run to the history file
func (a *App) AddHistory(item HistoryItem) error {
	item.CompletedFiles = validatedCompletedLibraryFiles(
		item.CompletedFiles,
		a.GetConfig().DownloadPath,
	)
	history := a.GetHistory()
	history = append([]HistoryItem{item}, history...) // prepend

	retention := defaultUIConfig().CompletedHistoryRetention
	if ui := a.GetConfig().UI; ui != nil {
		retention = ui.CompletedHistoryRetention
	}
	if len(history) > retention {
		history = history[:retention]
	}
	data, err := json.MarshalIndent(history, "", "  ")
	if err != nil {
		return err
	}

	dir := getAppDataDir()
	os.MkdirAll(dir, 0755)
	return os.WriteFile(getHistoryPath(), data, 0644)
}

func validatedCompletedLibraryFiles(paths []string, libraryRoot string) []string {
	root, err := filepath.Abs(strings.TrimSpace(libraryRoot))
	if err != nil || root == "" {
		return nil
	}
	if resolved, resolveErr := filepath.EvalSymlinks(root); resolveErr == nil {
		root = resolved
	}
	root = filepath.Clean(root)
	result := make([]string, 0, len(paths))
	seen := make(map[string]bool)
	for _, candidate := range paths {
		path, pathErr := filepath.Abs(strings.TrimSpace(candidate))
		if pathErr != nil {
			continue
		}
		if resolved, resolveErr := filepath.EvalSymlinks(path); resolveErr == nil {
			path = resolved
		}
		path = filepath.Clean(path)
		relative, relativeErr := filepath.Rel(root, path)
		if relativeErr != nil || relative == "." || relative == ".." ||
			strings.HasPrefix(relative, ".."+string(os.PathSeparator)) {
			continue
		}
		info, statErr := os.Stat(path)
		if statErr != nil || !info.Mode().IsRegular() {
			continue
		}
		key := path
		if runtime.GOOS == "windows" {
			key = strings.ToLower(key)
		}
		if !seen[key] {
			seen[key] = true
			result = append(result, path)
		}
	}
	return result
}

// ClearHistory deletes history
func (a *App) ClearHistory() error {
	path := getHistoryPath()
	if _, err := os.Stat(path); err == nil {
		return os.Remove(path)
	}
	return nil
}

// PickDirectory opens a folder selection dialog for the user
func (a *App) PickDirectory() string {
	dir, err := wailsRuntime.OpenDirectoryDialog(a.ctx, wailsRuntime.OpenDialogOptions{
		Title: "Select Download Folder",
	})
	if err != nil {
		return ""
	}
	return dir
}

// CancelDownload cancels the active download session
func (a *App) CancelDownload() {
	a.mu.Lock()
	a.isStopping = true
	a.downloadStopReason = "cancelled"
	a.mu.Unlock()

	cancel, cmd := a.detachActiveDownload()
	// Kill the process tree BEFORE cancelling the context.
	// If we cancel() first, Go kills the parent PID, which breaks the
	// tree relationship and taskkill /T can no longer find children.
	if err := killCommandTree(cmd); err != nil {
		wailsRuntime.LogErrorf(a.ctx, "Failed to stop library engine: %v", err)
	}
	if cancel != nil {
		cancel()
	}
	wailsRuntime.LogInfof(a.ctx, "Download cancelled by user")
}

type downloadControlState struct {
	Paused     bool `json:"paused"`
	MaxWorkers int  `json:"max_workers"`
}

func getDownloadControlPath() string {
	return filepath.Join(getAppDataDir(), "download-control.json")
}

func (a *App) writeDownloadControl(paused bool, workers int) error {
	if workers < 1 {
		workers = 1
	}
	if workers > downloadWorkerCeiling() {
		workers = downloadWorkerCeiling()
	}
	if err := os.MkdirAll(getAppDataDir(), 0755); err != nil {
		return err
	}
	data, err := json.Marshal(downloadControlState{Paused: paused, MaxWorkers: workers})
	if err != nil {
		return err
	}
	if err := os.WriteFile(getDownloadControlPath(), data, 0644); err != nil {
		return err
	}
	a.mu.Lock()
	a.downloadPaused = paused
	a.mu.Unlock()
	return nil
}

func (a *App) PauseDownload() error {
	cfg := a.GetConfig()
	if err := a.writeDownloadControl(true, cfg.MaxConcurrentJobs); err != nil {
		return err
	}

	// Stop the process tree instead of waiting for every in-flight provider
	// request to finish. Completed files remain in the library and are skipped
	// when the checkpointed job resumes; provider-owned partials are retained
	// when that provider supports resuming them.
	a.mu.Lock()
	a.isStopping = true
	a.downloadStopReason = "paused"
	a.mu.Unlock()
	cancel, cmd := a.detachActiveDownload()
	if cmd != nil {
		if err := killCommandTree(cmd); err != nil {
			a.logWarningf("Failed to pause library engine immediately: %v", err)
		}
	}
	if cancel != nil {
		cancel()
	}
	return nil
}

func (a *App) ResumeDownload() error {
	cfg := a.GetConfig()
	return a.writeDownloadControl(false, cfg.MaxConcurrentJobs)
}

func (a *App) SetDownloadWorkerCount(workers int) error {
	a.mu.Lock()
	paused := a.downloadPaused
	a.mu.Unlock()
	return a.writeDownloadControl(paused, workers)
}

// StartDownload starts the Python backend process and streams output
func (a *App) StartDownload(playlists []string) error {
	wailsRuntime.LogInfof(a.ctx, "Starting download for: %v", playlists)
	releaseLibraryResources := a.beginExplicitLibraryWork()

	if cancel, cmd := a.detachActiveDownload(); cancel != nil || cmd != nil {
		if cancel != nil {
			cancel()
		}
		if err := killCommandTree(cmd); err != nil {
			a.logWarningf("Failed to stop previous library engine: %v", err)
		}
	}

	a.mu.Lock()
	a.isStopping = false
	a.downloadPaused = false
	a.downloadStopReason = ""
	a.mu.Unlock()
	_ = a.writeDownloadControl(false, a.GetConfig().MaxConcurrentJobs)

	ctx, cancel := context.WithCancel(a.ctx)

	command, args, workDir, env, err := a.resolveBackendCommand(playlists)
	if err != nil {
		cancel()
		releaseLibraryResources()
		wailsRuntime.LogErrorf(a.ctx, err.Error())
		return err
	}

	if err := a.startBackendProcess(
		ctx,
		cancel,
		command,
		args,
		workDir,
		env,
		releaseLibraryResources,
	); err != nil {
		releaseLibraryResources()
		return err
	}
	return nil
}

func (a *App) resumeDeferredAppleIndex() {
	a.mu.Lock()
	restart := a.indexRestartAfterDownload
	a.indexRestartAfterDownload = false
	a.mu.Unlock()
	if restart {
		go func() {
			if err := a.StartAppleMusicIndex(); err != nil {
				wailsRuntime.EventsEmit(a.ctx, "apple-index-event", map[string]interface{}{
					"type": "apple_index_error", "message": fmt.Sprintf("Could not resume library indexing: %v", err),
				})
			}
		}()
	}
}

func (a *App) startBackendProcess(
	ctx context.Context,
	cancel context.CancelFunc,
	command string,
	args []string,
	workDir string,
	env []string,
	releaseLibraryResources func(),
) error {
	processSpan := a.beginBackendPerf("download")
	cmd := exec.CommandContext(ctx, command, args...)
	hideProcess(cmd)
	cmd.Dir = workDir
	cmd.Env = env

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		cancel()
		processSpan.finish(0, err)
		return err
	}
	cmd.Stderr = cmd.Stdout // merge stderr into stdout for parsing

	spawnSpan := a.beginPerf("backend.download_spawn")
	if err := cmd.Start(); err != nil {
		cancel()
		spawnSpan.finish(0, err)
		processSpan.finish(0, err)
		return err
	}
	a.incrementPerf("backend_spawns")
	spawnSpan.finish(0, nil)
	a.attachActiveDownload(cancel, cmd)

	go func() {
		defer releaseLibraryResources()
		scanner := bufio.NewScanner(stdout)
		outputBytes := 0
		// Large playlist metadata events can exceed Scanner's default 64 KiB cap.
		// Raise it so 1000+ track payloads still reach the frontend intact.
		scanner.Buffer(make([]byte, 64*1024), 16*1024*1024)
		for scanner.Scan() {
			outputBytes += len(scanner.Bytes()) + 1
			a.mu.Lock()
			stopping := a.isStopping
			a.mu.Unlock()

			// Stop emitting events once the context has been cancelled or a stop was requested.
			// Use break (not return) so we still fall through to cmd.Wait() and process_ended.
			if ctx.Err() != nil || stopping {
				break
			}
			line := scanner.Text()

			// Filter out noisy yt-dlp warnings and progress updates
			lowerLine := strings.ToLower(line)
			if strings.Contains(line, "No supported JavaScript runtime") ||
				strings.Contains(line, "YouTube extraction without a JS runtime") ||
				strings.Contains(lowerLine, "deno is enabled by default") ||
				strings.Contains(lowerLine, "js-runtimes") ||
				strings.HasPrefix(line, "[download]") ||
				strings.Contains(line, "% of ") {
				continue
			}

			// Try to parse as JSON first — apply message-level filtering only to plain log messages
			if json.Valid([]byte(line)) {
				var probe map[string]interface{}
				if json.Unmarshal([]byte(line), &probe) == nil && probe["type"] == "log" {
					msg, _ := probe["message"].(string)
					if shouldHideLogMessage(msg) {
						continue
					}
				}
			}

			// Parse JSON line and re-emit via Wails
			var payload map[string]interface{}
			if err := json.Unmarshal([]byte(line), &payload); err == nil {
				wailsRuntime.EventsEmit(a.ctx, "backend-event", payload)
			} else {
				// If it's not JSON, just send it as a raw log
				fallback := map[string]interface{}{
					"type":    "log",
					"level":   "info",
					"message": line,
				}
				wailsRuntime.EventsEmit(a.ctx, "backend-event", fallback)
			}
		}

		scanErr := scanner.Err()
		err := cmd.Wait()
		processSpan.finish(outputBytes, errors.Join(scanErr, err))
		a.clearActiveDownload(cmd)

		status := "completed"
		a.mu.Lock()
		stopReason := a.downloadStopReason
		if stopReason != "" {
			a.downloadStopReason = ""
		}
		a.isStopping = false
		restartIndex := a.indexRestartAfterDownload
		if restartIndex {
			a.indexRestartAfterDownload = false
		}
		a.mu.Unlock()
		if ctx.Err() == context.Canceled {
			if stopReason == "paused" {
				status = "paused"
			} else {
				status = "cancelled"
			}
		} else if scanErr != nil || err != nil {
			status = "failed"
		}

		if scanErr != nil && ctx.Err() != context.Canceled {
			wailsRuntime.EventsEmit(a.ctx, "backend-event", map[string]interface{}{
				"type":    "log",
				"level":   "error",
				"message": fmt.Sprintf("Library engine stream failed: %v", scanErr),
			})
		}
		if err != nil && ctx.Err() != context.Canceled {
			wailsRuntime.EventsEmit(a.ctx, "backend-event", map[string]interface{}{
				"type":    "log",
				"level":   "error",
				"message": fmt.Sprintf("Library engine exited with error: %v", err),
			})
		}
		wailsRuntime.EventsEmit(a.ctx, "backend-event", map[string]interface{}{
			"type":   "process_ended",
			"status": status,
		})
		if restartIndex {
			go func() {
				time.Sleep(350 * time.Millisecond)
				if startErr := a.StartAppleMusicIndex(); startErr != nil {
					wailsRuntime.EventsEmit(a.ctx, "apple-index-event", map[string]interface{}{
						"type": "apple_index_error", "message": fmt.Sprintf("Could not resume library indexing: %v", startErr),
					})
				}
			}()
		}
	}()

	return nil
}

func (a *App) attachActiveDownload(cancel context.CancelFunc, cmd *exec.Cmd) {
	a.mu.Lock()
	defer a.mu.Unlock()
	a.cancelDownload = cancel
	a.activeCmd = cmd
}

func (a *App) detachActiveDownload() (context.CancelFunc, *exec.Cmd) {
	a.mu.Lock()
	defer a.mu.Unlock()

	cancel := a.cancelDownload
	cmd := a.activeCmd
	a.cancelDownload = nil
	a.activeCmd = nil
	return cancel, cmd
}

func (a *App) clearActiveDownload(cmd *exec.Cmd) {
	a.mu.Lock()
	defer a.mu.Unlock()

	if a.activeCmd == cmd {
		a.activeCmd = nil
		a.cancelDownload = nil
	}
}

// shouldHideLogMessage returns true for internal/noisy log lines that the
// desktop UI should not surface to the user.
func shouldHideLogMessage(msg string) bool {
	noisePrefixes := []string{
		"[OK] HiFi adapter",
		"[OK] Amazon adapter",
		"[OK] Apple Music adapter",
		"[OK] JioSaavn adapter",
		"[OK] Qobuz adapter",
		"[OK] Deezer adapter",
		"[OK] Tidal adapter",
		"[OK] YAMS adapter",
		"[OK] Source preference",
		"[Gate]",
		"[HiFi]",
		"[Resolver]",
		"[DL]",
		"[OK] Done:",
		"[Qobuz]",
		"[Yams]",
		"[Apple]",
		"[Amazon]",
		"[LinkResolver]",
		"[Songwhip]",
		"[Odesli]",
		"[QobuzCreds]",
	}
	for _, prefix := range noisePrefixes {
		if strings.HasPrefix(msg, prefix) {
			return true
		}
	}
	return false
}

func (a *App) backendOutput(name string, cmd *exec.Cmd) ([]byte, error) {
	span := a.beginBackendPerf(name)
	output, err := cmd.Output()
	if cmd.Process != nil {
		a.incrementPerf("backend_spawns")
	}
	span.finish(len(output), err)
	return output, err
}

func (a *App) backendCombinedOutput(name string, cmd *exec.Cmd) ([]byte, error) {
	span := a.beginBackendPerf(name)
	output, err := cmd.CombinedOutput()
	if cmd.Process != nil {
		a.incrementPerf("backend_spawns")
	}
	span.finish(len(output), err)
	return output, err
}

func (a *App) runPythonCommand(args []string) (string, error) {
	pythonExe, _, workDir, env, err := a.resolveBackendCommand([]string{})
	if err != nil {
		return "", err
	}

	// We want to run python -m antra <args>
	// resolveBackendCommand returns ['json_cli.py', '--config', '...']
	// We need to swap json_cli.py with -m antra

	finalArgs := []string{"-m", "antra"}
	finalArgs = append(finalArgs, args...)
	finalArgs = append(finalArgs, "--config", getConfigPath())

	cmd := exec.Command(pythonExe, finalArgs...)
	cmd.Dir = workDir
	cmd.Env = env
	hideProcess(cmd)

	output, err := a.backendCombinedOutput("python_command", cmd)
	if err != nil {
		return string(output), err
	}
	return string(output), nil
}

func (a *App) ValidateTidalAuth() string {
	output, err := a.runPythonCommand([]string{"--tidal-validate"})
	if err != nil {
		msg := strings.TrimSpace(output)
		if msg == "" {
			msg = err.Error()
		}
		resp := map[string]interface{}{
			"ok":      false,
			"message": msg,
		}
		if b, marshalErr := json.Marshal(resp); marshalErr == nil {
			return string(b)
		}
		return `{"ok":false,"message":"Internal error marshaling validation failure"}`
	}
	return strings.TrimSpace(output)
}

// StartTidalOAuthLogin initiates the TIDAL OAuth device-code login flow.
// It spawns the Python backend with --tidal-oauth-login and streams all JSON events
// to the frontend via "tidal-oauth-event" events. The flow is long-running (waits
// for user to visit URL in browser), so it runs asynchronously.
func (a *App) StartTidalOAuthLogin() error {
	command, baseArgs, workDir, env, err := a.resolveBackendCommand([]string{})
	if err != nil {
		return err
	}

	// Build args: insert --tidal-oauth-login after the script/module args
	args := append([]string{}, baseArgs...)
	// If dev mode (python json_cli.py ...), insert after the script path
	// If bundled mode (exe ...), just append
	oauthArgs := []string{}
	for _, arg := range args {
		oauthArgs = append(oauthArgs, arg)
		if strings.HasSuffix(arg, "json_cli.py") {
			// After script, insert our flag
			oauthArgs = append(oauthArgs, "--tidal-oauth-login")
		}
	}
	// Bundled backend: just append if not already added
	if !containsStr(oauthArgs, "--tidal-oauth-login") {
		// Find where --config starts and insert before it
		newArgs := []string{}
		inserted := false
		for _, arg := range oauthArgs {
			if arg == "--config" && !inserted {
				newArgs = append(newArgs, "--tidal-oauth-login")
				inserted = true
			}
			newArgs = append(newArgs, arg)
		}
		if !inserted {
			newArgs = append(newArgs, "--tidal-oauth-login")
		}
		oauthArgs = newArgs
	}

	ctx, cancel := context.WithTimeout(a.ctx, 10*time.Minute)

	cmd := exec.CommandContext(ctx, command, oauthArgs...)
	hideProcess(cmd)
	cmd.Dir = workDir
	cmd.Env = env

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		cancel()
		return err
	}
	cmd.Stderr = cmd.Stdout

	if err := cmd.Start(); err != nil {
		cancel()
		return err
	}

	go func() {
		defer cancel()
		scanner := bufio.NewScanner(stdout)
		for scanner.Scan() {
			line := scanner.Text()
			var payload map[string]interface{}
			if err := json.Unmarshal([]byte(line), &payload); err == nil {
				wailsRuntime.EventsEmit(a.ctx, "tidal-oauth-event", payload)
			}
		}
		cmd.Wait()
		wailsRuntime.EventsEmit(a.ctx, "tidal-oauth-event", map[string]interface{}{
			"type": "tidal_oauth_done",
		})
	}()

	return nil
}

func containsStr(slice []string, s string) bool {
	for _, v := range slice {
		if v == s {
			return true
		}
	}
	return false
}

func (a *App) startBrowserLoginFlow(flag string, eventName string, doneType string) error {
	command, baseArgs, workDir, env, err := a.resolveBackendCommand([]string{})
	if err != nil {
		return err
	}

	args := append([]string{}, baseArgs...)
	loginArgs := []string{}
	for _, arg := range args {
		loginArgs = append(loginArgs, arg)
		if strings.HasSuffix(arg, "json_cli.py") {
			loginArgs = append(loginArgs, flag)
		}
	}
	if !containsStr(loginArgs, flag) {
		newArgs := []string{}
		inserted := false
		for _, arg := range loginArgs {
			if arg == "--config" && !inserted {
				newArgs = append(newArgs, flag)
				inserted = true
			}
			newArgs = append(newArgs, arg)
		}
		if !inserted {
			newArgs = append(newArgs, flag)
		}
		loginArgs = newArgs
	}

	ctx, cancel := context.WithTimeout(a.ctx, 10*time.Minute)
	cmd := exec.CommandContext(ctx, command, loginArgs...)
	hideProcess(cmd)
	cmd.Dir = workDir
	cmd.Env = env

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		cancel()
		return err
	}
	cmd.Stderr = cmd.Stdout

	if err := cmd.Start(); err != nil {
		cancel()
		return err
	}

	go func() {
		defer cancel()
		scanner := bufio.NewScanner(stdout)
		for scanner.Scan() {
			line := scanner.Text()
			var payload map[string]interface{}
			if err := json.Unmarshal([]byte(line), &payload); err == nil {
				wailsRuntime.EventsEmit(a.ctx, eventName, payload)
				if eventType, _ := payload["type"].(string); strings.HasSuffix(eventType, "_success") {
					wailsRuntime.WindowShow(a.ctx)
				}
			}
		}
		cmd.Wait()
		wailsRuntime.EventsEmit(a.ctx, eventName, map[string]interface{}{"type": doneType})
	}()

	return nil
}

func (a *App) StartAppleBrowserLogin() error {
	return a.startBrowserLoginFlow("--apple-browser-login", "apple-login-event", "apple_login_done")
}

func (a *App) StartAmazonBrowserLogin() error {
	return a.startBrowserLoginFlow("--amazon-browser-login", "amazon-login-event", "amazon_login_done")
}

func (a *App) CaptureSpDC() error {
	return a.startBrowserLoginFlow("--capture-sp-dc", "sp-dc-event", "sp_dc_done")
}

// ConfirmAmazonLogin is called by the frontend when the user has signed into
// Amazon Music in their real browser and is ready for Antra to capture the session.
// It writes a sentinel file that the Python --amazon-browser-login process polls for.
func (a *App) ConfirmAmazonLogin() error {
	sentinelPath := filepath.Join(os.TempDir(), "antra_amazon_login_confirm.tmp")
	return os.WriteFile(sentinelPath, []byte("ok"), 0644)
}

// Spotify Auth & Management

// GetArtistDiscography fetches the full release list for an artist URL.
// Returns a JSON string: {"artist_id","artist_name","artwork_url","albums":[...]}
// On error returns: {"error":"..."}
func (a *App) GetArtistDiscography(artistUrl string) string {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	out, err := a.callReadOnlyHelper(ctx, "artist_discography", map[string]interface{}{
		"artist_url": artistUrl,
	})
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) {
			return `{"error":"timed out fetching discography (60s)"}`
		}
		return jsonError(err)
	}
	return string(out)
}

// GetSpotifyLibrary returns the user's Spotify library (Liked Songs + playlists).
// Requires spotify_sp_dc to be configured.
// Returns a JSON string: the library object or {"error":"..."}.
// GetAppleMusicLibrary returns the user's Apple Music library (saved songs + playlists).
// Requires the Apple Music web session fields to be configured.
// Returns a JSON string: the library object or {"error":"..."}.
func (a *App) GetAppleMusicLibrary() string {
	return a.getAppleMusicLibrary(false)
}

// RefreshAppleMusicLibrary bypasses the on-disk index and replaces it with a
// fresh snapshot from Apple Music. Normal library opens always use the cache.
func (a *App) RefreshAppleMusicLibrary() string {
	return a.getAppleMusicLibrary(true)
}

func (a *App) getAppleMusicLibrary(forceRefresh bool) string {
	ctx, cancel := context.WithTimeout(context.Background(), 35*time.Second)
	defer cancel()
	if !forceRefresh {
		out, err := a.callReadOnlyHelper(ctx, "apple_library", map[string]interface{}{})
		if err != nil {
			if errors.Is(err, context.DeadlineExceeded) {
				return `{"error":"timed out fetching Apple Music library (35s)"}`
			}
			return jsonError(err)
		}
		return string(out)
	}

	flag := "--apple-library"
	if forceRefresh {
		flag = "--apple-library-refresh"
	}

	backend, err := ensureBundledBackend()
	if err != nil {
		return a.getAppleMusicLibraryViaPython(ctx, flag)
	}

	cmd := exec.CommandContext(ctx, backend, flag, "--config", getConfigPath())
	hideProcess(cmd)
	out, err := a.backendOutput("apple_library", cmd)
	if err != nil {
		if ctx.Err() == context.DeadlineExceeded {
			return `{"error":"timed out fetching Apple Music library (35s)"}`
		}
		return `{"error":"` + strings.ReplaceAll(err.Error(), `"`, `'`) + `"}`
	}
	return parseLibraryOutput(out, "apple_library")
}

func (a *App) getAppleMusicLibraryViaPython(ctx context.Context, flag string) string {
	pythonExe, _, workDir, env, err := a.resolveBackendCommand([]string{})
	if err != nil {
		return `{"error":"could not resolve backend"}`
	}
	cmd := exec.CommandContext(ctx, pythonExe, "-m", "antra.json_cli", flag, "--config", getConfigPath())
	cmd.Dir = workDir
	cmd.Env = env
	hideProcess(cmd)
	out, err := a.backendOutput("apple_library_dev", cmd)
	if err != nil {
		if ctx.Err() == context.DeadlineExceeded {
			return `{"error":"timed out fetching Apple Music library (35s)"}`
		}
		return `{"error":"` + strings.ReplaceAll(err.Error(), `"`, `'`) + `"}`
	}
	return parseLibraryOutput(out, "apple_library")
}

// GetAppleMusicPlaylistDetail returns a library playlist's tracks for the
// read-only detail sheet. It never starts the downloader.
func (a *App) GetAppleMusicPlaylistDetail(libraryURL string) string {
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()

	out, err := a.callReadOnlyHelper(ctx, "apple_library_detail", map[string]interface{}{
		"library_url": libraryURL,
	})
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) {
			return `{"error":"timed out loading playlist tracks"}`
		}
		return jsonError(err)
	}
	return string(out)
}

func (a *App) GetAppleMusicArtistDetail(artistName string) string {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	out, err := a.callReadOnlyHelper(ctx, "apple_library_artist", map[string]interface{}{
		"artist_name": artistName,
	})
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) {
			return `{"error":"timed out reading indexed artist"}`
		}
		return `{"error":"could not read indexed artist"}`
	}
	return string(out)
}

// StartAppleMusicIndex resumes a checkpointed, local-first index of every
// Apple Music release and track. Closing the app stops the process; cached
// releases are skipped when it starts again.
func (a *App) StartAppleMusicIndex() error {
	a.libraryIndexCoordinator().schedule(backgroundIndexApple, func(ctx context.Context) {
		if err := a.runAppleMusicIndex(ctx); err != nil && !errors.Is(err, context.Canceled) {
			wailsRuntime.EventsEmit(a.ctx, "apple-index-event", map[string]interface{}{
				"type": "apple_index_error", "message": fmt.Sprintf("Could not index Apple Music: %v", err),
			})
		}
	})
	return nil
}

func (a *App) runAppleMusicIndex(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	a.mu.Lock()
	if a.indexCmd != nil {
		a.mu.Unlock()
		return nil
	}
	a.mu.Unlock()

	command, args, workDir, env, err := a.resolveBackendCommand([]string{})
	if err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	args = append(args, "--apple-library-index")
	processSpan := a.beginBackendPerf("apple_index")
	cmd := exec.Command(command, args...)
	hideProcess(cmd)
	cmd.Dir = workDir
	cmd.Env = env
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		processSpan.finish(0, err)
		return err
	}
	cmd.Stderr = cmd.Stdout
	if err := cmd.Start(); err != nil {
		processSpan.finish(0, err)
		return err
	}
	a.incrementPerf("backend_spawns")
	a.mu.Lock()
	a.indexCmd = cmd
	a.mu.Unlock()
	watchDone := make(chan struct{})
	go func() {
		select {
		case <-ctx.Done():
			_ = killCommandTree(cmd)
		case <-watchDone:
		}
	}()
	wailsRuntime.EventsEmit(a.ctx, "apple-index-event", map[string]interface{}{
		"type": "apple_index_progress", "completed": 0, "total": 1,
		"percent": 0, "release_completed": 0, "release_total": 0,
		"label": "Reading local library index",
	})

	scanner := bufio.NewScanner(stdout)
	outputBytes := 0
	scanner.Buffer(make([]byte, 64*1024), 32*1024*1024)
	terminalSeen := false
	for scanner.Scan() {
		outputBytes += len(scanner.Bytes()) + 1
		var payload map[string]interface{}
		if json.Unmarshal([]byte(scanner.Text()), &payload) == nil {
			if eventType, _ := payload["type"].(string); eventType == "apple_index_complete" || eventType == "apple_index_incomplete" || eventType == "error" {
				terminalSeen = true
			}
			wailsRuntime.EventsEmit(a.ctx, "apple-index-event", payload)
		}
	}
	scanErr := scanner.Err()
	waitErr := cmd.Wait()
	close(watchDone)
	processErr := errors.Join(scanErr, waitErr)
	processSpan.finish(outputBytes, processErr)
	a.mu.Lock()
	wasCurrent := a.indexCmd == cmd
	if wasCurrent {
		a.indexCmd = nil
	}
	a.mu.Unlock()
	if err := ctx.Err(); err != nil {
		if wasCurrent {
			wailsRuntime.EventsEmit(a.ctx, "apple-index-event", map[string]interface{}{
				"type": "apple_index_paused", "label": "Index paused for a higher-priority operation",
			})
		}
		return err
	}
	if wasCurrent && !terminalSeen {
		message := "The library index stopped before completion. It will resume from cached releases."
		if scanErr != nil {
			message = fmt.Sprintf("Library indexing output failed: %v", scanErr)
		} else if waitErr != nil {
			message = fmt.Sprintf("Library indexing stopped: %v", waitErr)
		}
		wailsRuntime.EventsEmit(a.ctx, "apple-index-event", map[string]interface{}{
			"type": "apple_index_incomplete",
			"data": map[string]interface{}{"completed": 0, "total": 1, "percent": 0, "errors": []interface{}{message}},
		})
	}
	return processErr
}

// ResetAppleMusicIndex stops any active indexer and removes only Apple Music's
// derived local cache. Account credentials and downloaded files are untouched.
func (a *App) ResetAppleMusicIndex() error {
	a.libraryIndexCoordinator().cancelKind(backgroundIndexApple)
	a.mu.Lock()
	indexCmd := a.indexCmd
	a.indexCmd = nil
	a.mu.Unlock()
	if indexCmd != nil {
		_ = killCommandTree(indexCmd)
	}

	basePath := filepath.Join(getAppDataDir(), "apple_library_cache.sqlite3")
	for _, path := range []string{basePath, basePath + "-wal", basePath + "-shm"} {
		if err := os.Remove(path); err != nil && !errors.Is(err, os.ErrNotExist) {
			return err
		}
	}
	return nil
}

// GetIPodDevices scans mounted iPods through iOpenPod's read-only scanner.
func (a *App) GetIPodDevices() string {
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()

	out, err := a.callReadOnlyHelper(ctx, "ipod_scan", map[string]interface{}{})
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) {
			return `{"error":"iPod scan timed out"}`
		}
		return jsonError(err)
	}
	return string(out)
}

// parseLibraryOutput scans newline-delimited JSON output and extracts a single
// library event. Other lines (log events, etc.) are ignored so stray config
// messages do not break JSON parsing.
func parseLibraryOutput(out []byte, eventType string) string {
	for _, line := range bytes.Split(bytes.TrimSpace(out), []byte("\n")) {
		line = bytes.TrimSpace(line)
		if len(line) == 0 {
			continue
		}
		var wrapper map[string]interface{}
		if err := json.Unmarshal(line, &wrapper); err != nil {
			continue
		}
		switch wrapper["type"] {
		case eventType:
			result, _ := json.Marshal(wrapper["data"])
			return string(result)
		case "error":
			msg, _ := wrapper["message"].(string)
			return `{"error":"` + strings.ReplaceAll(msg, `"`, `'`) + `"}`
		}
	}
	return `{"error":"no ` + eventType + ` event in backend output"}`
}

// SearchArtists searches for artists by name using the given source ("spotify" or "apple").
// Returns a JSON string: {"type":"artist_search","data":[...]} or {"error":"..."}
func (a *App) SearchArtists(query string, source string) string {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	if source == "" {
		source = "spotify"
	}

	out, err := a.callReadOnlyHelper(ctx, "artist_search", map[string]interface{}{
		"query":  query,
		"source": source,
	})
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) {
			return `{"error":"artist search timed out"}`
		}
		return jsonError(err)
	}
	return string(out)
}

func (a *App) GetDiscoveryData(region string, genreId string, genreName string) string {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	if region == "" {
		region = "us"
	}

	out, err := a.callReadOnlyHelper(ctx, "discovery", map[string]interface{}{
		"region":     region,
		"genre_id":   genreId,
		"genre_name": genreName,
	})
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) {
			return `{"error":"discovery fetch timed out"}`
		}
		return jsonError(err)
	}
	return string(out)
}

func (a *App) GetDiscoveryGenres(region string) string {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	if region == "" {
		region = "us"
	}

	out, err := a.callReadOnlyHelper(ctx, "discovery_genres", map[string]interface{}{
		"region": region,
	})
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) {
			return `{"error":"genres fetch timed out"}`
		}
		return jsonError(err)
	}
	return string(out)
}

func (a *App) GetSpotifyStatus() string {
	output, err := a.runPythonCommand([]string{"spotify", "status", "--json"})
	if err != nil {
		return `{"authenticated": false, "error": "` + err.Error() + `"}`
	}
	return output
}

func (a *App) LoginSpotify() string {
	// This opens the browser and waits for the automated capture
	output, err := a.runPythonCommand([]string{"spotify", "login"})
	if err != nil {
		return `{"success": false, "error": "` + err.Error() + `"}`
	}
	return output
}

func (a *App) LogoutSpotify() string {
	output, err := a.runPythonCommand([]string{"spotify", "logout", "--json"})
	if err != nil {
		return `{"success": false, "error": "` + err.Error() + `"}`
	}
	return output
}

func (a *App) SetSpotifyCookie(spDc string) string {
	output, err := a.runPythonCommand([]string{"spotify", "set-cookie", spDc})
	if err != nil {
		return `{"success": false, "error": "` + err.Error() + `"}`
	}
	return `{"success": true, "message": "` + strings.TrimSpace(output) + `"}`
}

func (a *App) SetSpotifyToken(token string) string {
	output, err := a.runPythonCommand([]string{"spotify", "set-token", token})
	if err != nil {
		return `{"success": false, "error": "` + err.Error() + `"}`
	}
	return `{"success": true, "message": "` + strings.TrimSpace(output) + `"}`
}

func (a *App) resolveBackendCommand(playlists []string) (string, []string, string, []string, error) {
	// Paid builds use the full two-worker engine without entitlement checks.
	workers := a.GetConfig().MaxConcurrentJobs
	if workers < 1 {
		workers = 2
	}
	extraEnv := []string{
		"PYTHONUTF8=1",
		fmt.Sprintf("ANTRA_MAX_WORKERS=%d", workers),
		fmt.Sprintf("ANTRA_WORKER_CEILING=%d", downloadWorkerCeiling()),
		"ANTRA_CONTROL_PATH=" + getDownloadControlPath(),
	}

	if bundledBackend, err := ensureBundledBackend(); err == nil {
		args := append([]string{}, playlists...)
		args = append(args, "--config", getConfigPath())
		return bundledBackend, args, filepath.Dir(bundledBackend), append(os.Environ(), extraEnv...), nil
	} else if !errors.Is(err, fs.ErrNotExist) {
		return "", nil, "", nil, fmt.Errorf("failed to prepare bundled backend: %w", err)
	}

	// Dev fallback: run the Python backend directly from source.
	pythonExe := "python"
	exePath, _ := os.Executable()
	exeDir := filepath.Dir(exePath)
	currentDir, _ := os.Getwd()

	candidates := uniqueCleanPaths([]string{
		exeDir,
		filepath.Join(exeDir, "resources"),
		filepath.Join(exeDir, ".."),
		filepath.Join(exeDir, "..", ".."),
		filepath.Join(exeDir, "..", "..", ".."),
		currentDir,
		filepath.Join(currentDir, ".."),
	})

	var parentDir string
	var jsonCliScript string
	for _, dir := range candidates {
		testPath := filepath.Join(dir, "antra", "json_cli.py")
		if _, err := os.Stat(testPath); err == nil {
			parentDir = dir
			jsonCliScript = testPath
			break
		}
	}

	if jsonCliScript == "" {
		return "", nil, "", nil, fmt.Errorf(
			"could not find bundled backend or antra/json_cli.py; checked: %s",
			strings.Join(candidates, ", "),
		)
	}

	args := []string{jsonCliScript}
	args = append(args, playlists...)
	args = append(args, "--config", getConfigPath())
	env := append(os.Environ(), fmt.Sprintf("PYTHONPATH=%s", parentDir), "PYTHONUTF8=1", fmt.Sprintf("ANTRA_MAX_WORKERS=%d", workers), "ANTRA_CONTROL_PATH="+getDownloadControlPath())
	return pythonExe, args, parentDir, env, nil
}

func uniqueCleanPaths(paths []string) []string {
	seen := make(map[string]struct{}, len(paths))
	result := make([]string, 0, len(paths))
	for _, path := range paths {
		clean := filepath.Clean(path)
		if _, ok := seen[clean]; ok {
			continue
		}
		seen[clean] = struct{}{}
		result = append(result, clean)
	}
	return result
}

// ── Source health check ───────────────────────────────────────────────────────

type EndpointStatus struct {
	URL       string `json:"url"`
	Alive     bool   `json:"alive"`
	LatencyMs int64  `json:"latency_ms"`
}

type SourceHealthResult struct {
	Source    string           `json:"source"`
	Total     int              `json:"total"`
	Live      int              `json:"live"`
	Endpoints []EndpointStatus `json:"endpoints"`
}

const defaultEndpointManifestURL = "https://gist.githubusercontent.com/anandprtp/fdc2c16b7bfdc2d337fbc86161b79371/raw"

var gistIDPattern = regexp.MustCompile(`(?i)([0-9a-f]{32})`)

type endpointManifestMirrors struct {
	Tidal  string `json:"tidal"`
	Qobuz  string `json:"qobuz"`
	Deezer string `json:"deezer"`
	Amazon string `json:"amazon"`
	Apple  string `json:"apple"`
}

type endpointManifest struct {
	Hifi    []string                `json:"hifi"`
	Amazon  []string                `json:"amazon"`
	Apple   []string                `json:"apple"`
	Mirrors endpointManifestMirrors `json:"mirrors"`
	ApiKey  string                  `json:"api_key"`
}

func getEndpointManifestCachePaths() []string {
	paths := []string{filepath.Join(getAppDataDir(), "endpoint_manifest_cache.json")}

	switch runtime.GOOS {
	case "windows":
		localAppData := os.Getenv("LOCALAPPDATA")
		if localAppData != "" {
			paths = append(paths, filepath.Join(localAppData, "Antra", "Antra", "endpoint_manifest_cache.json"))
		}
	case "darwin":
		home := os.Getenv("HOME")
		if home != "" {
			paths = append(paths, filepath.Join(home, "Library", "Application Support", "Antra", "Antra", "endpoint_manifest_cache.json"))
		}
	default:
		home := os.Getenv("HOME")
		if home != "" {
			paths = append(paths, filepath.Join(home, ".local", "share", "Antra", "Antra", "endpoint_manifest_cache.json"))
		}
	}

	return uniqueCleanPaths(paths)
}

func loadEndpointManifest() endpointManifest {
	manifestURL := strings.TrimSpace(os.Getenv("ANTRA_ENDPOINT_MANIFEST_URL"))
	if manifestURL == "" {
		manifestURL = defaultEndpointManifestURL
	}

	client := &http.Client{
		Timeout: 5 * time.Second,
		Transport: &http.Transport{
			Proxy: nil,
		},
	}
	if manifest, ok := fetchManifestFromURL(client, manifestURL); ok {
		manifest.normalize()
		writeEndpointManifestCache(manifest)
		return manifest
	}

	if gistID := extractGistID(manifestURL); gistID != "" {
		if manifest, ok := fetchManifestFromGistAPI(client, gistID); ok {
			manifest.normalize()
			writeEndpointManifestCache(manifest)
			return manifest
		}
	}

	if cached, ok := readEndpointManifestCache(); ok {
		return cached
	}
	return endpointManifest{}
}

func readEndpointManifestCache() (endpointManifest, bool) {
	for _, cachePath := range getEndpointManifestCachePaths() {
		data, err := os.ReadFile(cachePath)
		if err != nil {
			continue
		}
		var manifest endpointManifest
		if err := unmarshalEndpointManifest(data, &manifest); err != nil {
			continue
		}
		manifest.normalize()
		return manifest, true
	}
	return endpointManifest{}, false
}

func fetchManifestFromURL(client *http.Client, manifestURL string) (endpointManifest, bool) {
	req, err := http.NewRequest(http.MethodGet, manifestURL, nil)
	if err != nil {
		return endpointManifest{}, false
	}
	req.Header.Set("User-Agent", "Antra/1.0 (+https://github.com/anandprtp/Antra)")
	req.Header.Set("Accept", "application/json, text/plain, */*")
	req.Header.Set("Cache-Control", "no-cache")

	resp, err := client.Do(req)
	if err != nil {
		return endpointManifest{}, false
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return endpointManifest{}, false
	}

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return endpointManifest{}, false
	}

	var manifest endpointManifest
	if err := unmarshalEndpointManifest(data, &manifest); err != nil {
		return endpointManifest{}, false
	}
	return manifest, true
}

func extractGistID(manifestURL string) string {
	match := gistIDPattern.FindStringSubmatch(manifestURL)
	if len(match) < 2 {
		return ""
	}
	return match[1]
}

func fetchManifestFromGistAPI(client *http.Client, gistID string) (endpointManifest, bool) {
	req, err := http.NewRequest(http.MethodGet, "https://api.github.com/gists/"+gistID, nil)
	if err != nil {
		return endpointManifest{}, false
	}
	req.Header.Set("User-Agent", "Antra/1.0 (+https://github.com/anandprtp/Antra)")
	req.Header.Set("Accept", "application/vnd.github+json")

	resp, err := client.Do(req)
	if err != nil {
		return endpointManifest{}, false
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return endpointManifest{}, false
	}

	var payload struct {
		Files map[string]struct {
			Content string `json:"content"`
		} `json:"files"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		return endpointManifest{}, false
	}

	for _, file := range payload.Files {
		if strings.TrimSpace(file.Content) == "" {
			continue
		}
		var manifest endpointManifest
		if err := unmarshalEndpointManifest([]byte(file.Content), &manifest); err == nil {
			return manifest, true
		}
	}

	return endpointManifest{}, false
}

func writeEndpointManifestCache(manifest endpointManifest) {
	data, err := json.MarshalIndent(manifest, "", "  ")
	if err != nil {
		return
	}
	for _, cachePath := range getEndpointManifestCachePaths() {
		if err := os.MkdirAll(filepath.Dir(cachePath), 0755); err != nil {
			continue
		}
		_ = os.WriteFile(cachePath, data, 0644)
	}
}

func unmarshalEndpointManifest(data []byte, manifest *endpointManifest) error {
	if err := json.Unmarshal(data, manifest); err == nil {
		return nil
	}
	var legacyHifi []string
	if err := json.Unmarshal(data, &legacyHifi); err == nil {
		manifest.Hifi = legacyHifi
		return nil
	}
	return fmt.Errorf("unsupported endpoint manifest payload")
}

func (m *endpointManifest) normalize() {
	m.Hifi = normalizeURLList(m.Hifi)
	m.Amazon = normalizeURLList(m.Amazon)
	m.Apple = normalizeURLList(m.Apple)
}

func normalizeURLList(urls []string) []string {
	seen := make(map[string]struct{}, len(urls))
	result := make([]string, 0, len(urls))
	for _, raw := range urls {
		clean := strings.TrimSpace(strings.TrimRight(raw, "/"))
		if clean == "" {
			continue
		}
		if _, ok := seen[clean]; ok {
			continue
		}
		seen[clean] = struct{}{}
		result = append(result, clean)
	}
	return result
}

func endpointsForHealthSource(manifest endpointManifest, source string) []string {
	switch source {
	case "hifi":
		eps := append([]string{}, manifest.Hifi...)
		if manifest.Mirrors.Tidal != "" {
			eps = append([]string{manifest.Mirrors.Tidal}, eps...)
		}
		return eps
	case "amazon":
		eps := append([]string{}, manifest.Amazon...)
		if manifest.Mirrors.Amazon != "" {
			eps = append([]string{manifest.Mirrors.Amazon}, eps...)
		}
		return eps
	case "apple":
		eps := append([]string{}, manifest.Apple...)
		if manifest.Mirrors.Apple != "" {
			eps = append([]string{manifest.Mirrors.Apple}, eps...)
		}
		return eps
	case "qobuz":
		if manifest.Mirrors.Qobuz != "" {
			return []string{manifest.Mirrors.Qobuz}
		}
		return nil
	case "deezer":
		if manifest.Mirrors.Deezer != "" {
			return []string{manifest.Mirrors.Deezer}
		}
		return nil
	default:
		return nil
	}
}

// CheckSourceHealth probes all known endpoints for a given source ("hifi", "amazon",
// "apple", "qobuz", "deezer") in parallel and returns a JSON-encoded
// SourceHealthResult.
//
// Health check URLs mirror the adapters' own liveness checks.
func probeHifiEndpoint(client *http.Client, base string) (bool, error) {
	// Use the public health endpoint (GET /) — no API key needed
	resp, err := client.Get(base + "/")
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()
	return resp.StatusCode == 200, nil
}

// - HiFi:   search + track manifest probe must both succeed
// - Amazon: GET {mirror}/           → 200 or 404 (server reachable)
// - Apple:  GET {mirror}/           → 200 or 404 (server reachable)
func (a *App) CheckSourceHealth(source string) string {
	manifest := loadEndpointManifest()
	endpoints := endpointsForHealthSource(manifest, source)
	if endpoints == nil {
		res := SourceHealthResult{Source: source, Total: 0, Live: 0, Endpoints: []EndpointStatus{}}
		b, _ := json.Marshal(res)
		return string(b)
	}

	type probeResult struct {
		alive     bool
		latencyMs int64
	}

	results := make([]probeResult, len(endpoints))
	client := &http.Client{Timeout: 7 * time.Second}

	var wg sync.WaitGroup
	for i, ep := range endpoints {
		wg.Add(1)
		go func(idx int, base string) {
			defer wg.Done()
			start := time.Now()
			alive := false
			switch source {
			case "hifi":
				ok, err := probeHifiEndpoint(client, base)
				alive = err == nil && ok
			default:
				var checkURL string
				switch source {
				case "amazon", "apple", "qobuz", "deezer":
					checkURL = base + "/"
				default:
					checkURL = base
				}
				resp, err := client.Get(checkURL)
				if err == nil {
					resp.Body.Close()
					switch source {
					case "amazon", "apple", "qobuz", "deezer":
						alive = resp.StatusCode == 200 || resp.StatusCode == 404
					default:
						alive = resp.StatusCode == 200
					}
				}
			}
			elapsed := time.Since(start).Milliseconds()
			results[idx] = probeResult{alive: alive, latencyMs: elapsed}
		}(i, ep)
	}
	wg.Wait()

	statuses := make([]EndpointStatus, len(endpoints))
	live := 0
	for i, ep := range endpoints {
		statuses[i] = EndpointStatus{
			URL:       ep,
			Alive:     results[i].alive,
			LatencyMs: results[i].latencyMs,
		}
		if results[i].alive {
			live++
		}
	}

	res := SourceHealthResult{
		Source:    source,
		Total:     len(endpoints),
		Live:      live,
		Endpoints: statuses,
	}
	b, _ := json.Marshal(res)
	return string(b)
}

// ── Key info ──────────────────────────────────────────────────────────────────

// SaveCoverArt downloads the album/playlist cover art at maximum resolution
// and saves it to the library root folder.
func (a *App) SaveCoverArt(artworkUrl string, title string) string {
	cfg := a.GetConfig()
	libraryRoot := strings.TrimSpace(cfg.DownloadPath)
	if libraryRoot == "" {
		return "error: no music library folder configured"
	}

	// Upgrade URL to 3000x3000 hi-res
	// Apple Music: {w}x{h}bb → 3000x3000bb
	// Spotify: no dimension tokens — use as-is (largest available)
	hiResURL := regexp.MustCompile(`\{\w\}x\{\w\}bb`).ReplaceAllString(artworkUrl, "3000x3000bb")
	hiResURL = regexp.MustCompile(`\d+x\d+bb`).ReplaceAllString(hiResURL, "3000x3000bb")

	// Sanitize title for filename
	safeTitle := strings.Map(func(r rune) rune {
		if strings.ContainsRune(`<>:"/\|?*`, r) {
			return '_'
		}
		return r
	}, strings.TrimSpace(title))
	if safeTitle == "" {
		safeTitle = "cover"
	}
	safeTitle = strings.TrimRight(safeTitle, ". ")

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Get(hiResURL)
	if err != nil {
		return fmt.Sprintf("error: failed to download cover art — %v", err)
	}

	if resp.StatusCode != 200 {
		resp.Body.Close()
		resp2, err2 := client.Get(artworkUrl)
		if err2 != nil {
			return fmt.Sprintf("error: failed to download cover art — %v", err2)
		}
		resp = resp2
		if resp.StatusCode != 200 {
			resp.Body.Close()
			return fmt.Sprintf("error: server returned %d", resp.StatusCode)
		}
		hiResURL = artworkUrl
	}
	defer resp.Body.Close()

	// Determine extension from content-type or URL
	contentType := resp.Header.Get("Content-Type")
	ext := ".jpg"
	if strings.Contains(contentType, "png") {
		ext = ".png"
	} else if strings.Contains(contentType, "webp") {
		ext = ".webp"
	}

	outPath := filepath.Join(libraryRoot, safeTitle+ext)
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Sprintf("error: failed to read cover art — %v", err)
	}

	if err := os.MkdirAll(filepath.Dir(outPath), 0755); err != nil {
		return fmt.Sprintf("error: %v", err)
	}
	if err := os.WriteFile(outPath, data, 0644); err != nil {
		return fmt.Sprintf("error: failed to save cover art — %v", err)
	}

	return fmt.Sprintf("ok: %s", outPath)
}
