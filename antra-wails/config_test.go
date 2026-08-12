package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
	"testing"
)

func TestNormalizeConfigMigratesLegacyThemeAndPreservesData(t *testing.T) {
	raw := []byte(`{
		"theme":"midnight",
		"download_path":"D:/Music",
		"apple_music_user_token":"secret",
		"tracked_playlists":[{"id":"p1"}]
	}`)
	var cfg Config
	if err := json.Unmarshal(raw, &cfg); err != nil {
		t.Fatal(err)
	}

	got := normalizeConfig(cfg, raw)
	if got.ConfigSchemaVersion != currentConfigSchemaVersion {
		t.Fatalf("schema version = %d", got.ConfigSchemaVersion)
	}
	if got.Theme != "system" {
		t.Fatalf("legacy theme should migrate to system, got %q", got.Theme)
	}
	if got.UI == nil || got.UI.Scale != 1 || got.UI.PlayerVolume != 0.8 {
		t.Fatalf("UI defaults not applied: %#v", got.UI)
	}
	if got.AppleMusicUserToken != "secret" || len(got.TrackedPlaylists) != 1 {
		t.Fatal("migration discarded credentials or tracked playlists")
	}
}

func TestNormalizeConfigClampsAndValidatesUI(t *testing.T) {
	raw := []byte(`{"theme":"DARK","ui":{
		"scale":9,
		"density":"dense",
		"sidebar_width":500,
		"artwork_size":20,
		"motion":"fast",
		"player_volume":-2,
		"startup_destination":"device",
		"remember_last_page":false,
		"open_downloads_on_add":false,
		"completion_notifications":false,
		"device_notifications":false,
		"completed_history_retention":5000
	}}`)
	var cfg Config
	if err := json.Unmarshal(raw, &cfg); err != nil {
		t.Fatal(err)
	}

	got := normalizeConfig(cfg, raw)
	ui := got.UI
	if got.Theme != "dark" {
		t.Fatalf("theme = %q", got.Theme)
	}
	if ui.Scale != 1.25 || ui.SidebarWidth != 300 || ui.ArtworkSize != 130 {
		t.Fatalf("numeric clamping failed: %#v", ui)
	}
	if ui.PlayerVolume != 0 || ui.CompletedHistoryRetention != 1000 {
		t.Fatalf("general clamping failed: %#v", ui)
	}
	if ui.Density != "comfortable" || ui.Motion != "system" || ui.StartupDestination != "recently-added" {
		t.Fatalf("enum migration failed: %#v", ui)
	}
	if ui.RememberLastPage || ui.OpenDownloadsOnAdd || ui.CompletionNotifications || ui.DeviceNotifications {
		t.Fatal("explicit false values must survive normalization")
	}
}

func TestSaveConfigRoundTripPreservesCredentialsAndUI(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	app := NewApp()
	cfg := Config{
		AppleAuthorizationToken: "authorization-secret",
		AppleMusicUserToken:     "music-secret",
		TrackedPlaylists:        []interface{}{map[string]interface{}{"id": "playlist-1"}},
		Theme:                   "dark",
		UI: &UIConfig{
			Scale:                     1.1,
			Density:                   "compact",
			SidebarWidth:              260,
			ArtworkSize:               180,
			Motion:                    "reduced",
			PlayerVolume:              0.35,
			StartupDestination:        "albums",
			RememberLastPage:          false,
			OpenDownloadsOnAdd:        false,
			CompletionNotifications:   false,
			DeviceNotifications:       false,
			CompletedHistoryRetention: 50,
		},
	}
	if err := app.SaveConfig(cfg); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(getConfigPath()); err != nil {
		t.Fatal(err)
	}
	got := app.GetConfig()
	if got.ConfigSchemaVersion != currentConfigSchemaVersion || got.Theme != "dark" {
		t.Fatalf("schema/theme did not round trip: %#v", got)
	}
	if got.AppleAuthorizationToken != cfg.AppleAuthorizationToken ||
		got.AppleMusicUserToken != cfg.AppleMusicUserToken ||
		len(got.TrackedPlaylists) != 1 {
		t.Fatal("credential or playlist data was lost")
	}
	if got.UI == nil || got.UI.PlayerVolume != 0.35 || got.UI.RememberLastPage {
		t.Fatalf("UI settings did not round trip: %#v", got.UI)
	}
}

func TestConfigCacheIsLazyAndReplacedAfterAtomicSave(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	if err := os.MkdirAll(getAppDataDir(), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(
		getConfigPath(),
		[]byte(`{"download_path":"D:/First","apple_music_user_token":"first-secret"}`),
		0600,
	); err != nil {
		t.Fatal(err)
	}

	app := NewApp()
	first := app.GetConfig()
	if first.DownloadPath != filepath.Join(`D:/First`, "Vela") || first.AppleMusicUserToken != "first-secret" {
		t.Fatalf("unexpected initial config: %#v", first)
	}

	// An external write cannot mutate the in-process snapshot.
	if err := os.WriteFile(
		getConfigPath(),
		[]byte(`{"download_path":"D:/External","apple_music_user_token":"external-secret"}`),
		0600,
	); err != nil {
		t.Fatal(err)
	}
	cached := app.GetConfig()
	if cached.DownloadPath != first.DownloadPath || cached.AppleMusicUserToken != first.AppleMusicUserToken {
		t.Fatalf("cached config unexpectedly reread disk: %#v", cached)
	}

	replacement := first
	replacement.DownloadPath = `E:/Library`
	replacement.AppleMusicUserToken = "replacement-secret"
	if err := app.SaveConfig(replacement); err != nil {
		t.Fatal(err)
	}
	got := app.GetConfig()
	if got.DownloadPath != replacement.DownloadPath || got.AppleMusicUserToken != "replacement-secret" {
		t.Fatalf("successful save did not replace cache: %#v", got)
	}
}

func TestInvalidateConfigCacheReloadsExternalAtomicUpdate(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	app := NewApp()
	if err := app.SaveConfig(Config{
		DownloadPath:              `D:/Before`,
		AppleMusicUserToken:       "before-secret",
		DownloadPathIsLibraryRoot: true,
	}); err != nil {
		t.Fatal(err)
	}
	if got := app.GetConfig(); got.AppleMusicUserToken != "before-secret" {
		t.Fatalf("unexpected cached config: %#v", got)
	}

	replacement := []byte(`{
		"download_path":"E:/After",
		"download_path_is_library_root":true,
		"apple_music_user_token":"after-secret"
	}`)
	temp, err := os.CreateTemp(getAppDataDir(), "external-config-*.tmp")
	if err != nil {
		t.Fatal(err)
	}
	tempPath := temp.Name()
	defer os.Remove(tempPath)
	if _, err := temp.Write(replacement); err != nil {
		temp.Close()
		t.Fatal(err)
	}
	if err := temp.Close(); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(tempPath, getConfigPath()); err != nil {
		t.Fatal(err)
	}

	app.invalidateConfigCache()
	got := app.GetConfig()
	if got.DownloadPath != `E:/After` || got.AppleMusicUserToken != "after-secret" {
		t.Fatalf("cache invalidation did not reload config: %#v", got)
	}
}

func TestGetConfigReturnsDeeplyDetachedCopies(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	app := NewApp()
	cfg := Config{
		SourcesEnabled:   []string{"apple"},
		DownloadSources:  []string{"auto"},
		TrackedPlaylists: []interface{}{map[string]interface{}{"id": "original", "tracks": []interface{}{"one"}}},
	}
	if err := app.SaveConfig(cfg); err != nil {
		t.Fatal(err)
	}

	first := app.GetConfig()
	first.UI.Scale = 1.25
	first.SourcesEnabled[0] = "tidal"
	first.DownloadSources[0] = "qobuz"
	playlist := first.TrackedPlaylists[0].(map[string]interface{})
	playlist["id"] = "mutated"
	playlist["tracks"].([]interface{})[0] = "two"

	second := app.GetConfig()
	secondPlaylist := second.TrackedPlaylists[0].(map[string]interface{})
	if second.UI.Scale == 1.25 ||
		second.SourcesEnabled[0] != "apple" ||
		second.DownloadSources[0] != "auto" ||
		secondPlaylist["id"] != "original" ||
		secondPlaylist["tracks"].([]interface{})[0] != "one" {
		t.Fatalf("caller mutation leaked into config cache: %#v", second)
	}
}

func TestConfigCacheConcurrentReadsAndReplacement(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	app := NewApp()
	if err := app.SaveConfig(Config{DownloadPath: "initial"}); err != nil {
		t.Fatal(err)
	}

	var wait sync.WaitGroup
	for reader := range 8 {
		wait.Add(1)
		go func() {
			defer wait.Done()
			for range 100 {
				cfg := app.GetConfig()
				if cfg.ConfigSchemaVersion != currentConfigSchemaVersion {
					t.Errorf("reader %d saw unnormalized config: %#v", reader, cfg)
					return
				}
			}
		}()
	}
	for writer := range 4 {
		wait.Add(1)
		go func() {
			defer wait.Done()
			cfg := app.GetConfig()
			cfg.DownloadPath = string(rune('A' + writer))
			if err := app.SaveConfig(cfg); err != nil {
				t.Errorf("writer %d: %v", writer, err)
			}
		}()
	}
	wait.Wait()
}
