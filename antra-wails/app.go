package main

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"

	wailsRuntime "github.com/wailsapp/wails/v2/pkg/runtime"
)

// App struct
type App struct {
	ctx                       context.Context
	mu                        sync.Mutex
	cancelDownload            context.CancelFunc
	activeCmd                 *exec.Cmd
	indexCmd                  *exec.Cmd
	isStopping                bool
	downloadPaused            bool
	downloadStopReason        string
	downloadIndexing          bool
	indexRestartAfterDownload bool
	ffmpegExe                 string // absolute path to bundled ffmpeg for local playback helpers
	ffprobeExe                string // absolute path to bundled ffprobe for local library metadata
	mediaServer               *http.Server
	mediaBaseURL              string
	mediaToken                string
}

// NewApp creates a new App application struct
func NewApp() *App {
	return &App{}
}

// startup is called when the app starts. The context is saved
// so we can call the runtime methods
func (a *App) startup(ctx context.Context) {
	a.ctx = ctx
}

// domReady is called after the frontend DOM has finished loading.
// We reveal the window here to avoid the white/unstyled flash that
// occurs when the window is shown before the Svelte app has mounted.
func (a *App) domReady(ctx context.Context) {
	wailsRuntime.WindowShow(ctx)
	go a.cacheFfmpegPaths()
	go a.startAutoSyncTicker(ctx)
}

// startAutoSyncTicker checks every minute whether the auto-sync schedule has
// been met and, if so, spawns the Python backend with --auto-sync.
// Schedule is read from config.json each tick so changes take effect without restart.
func (a *App) startAutoSyncTicker(ctx context.Context) {
	ticker := time.NewTicker(60 * time.Second)
	defer ticker.Stop()

	// Align to the next whole minute so we don't double-fire near startup.
	time.Sleep(time.Until(time.Now().Truncate(time.Minute).Add(time.Minute)))

	for {
		select {
		case <-ctx.Done():
			return
		case now := <-ticker.C:
			a.maybeRunAutoSync(now)
		}
	}
}

// maybeRunAutoSync reads config.json, checks whether the current time matches
// the configured auto-sync schedule, and spawns the backend if it does.
func (a *App) maybeRunAutoSync(now time.Time) {
	cfgPath := getConfigPath()
	data, err := os.ReadFile(cfgPath)
	if err != nil {
		return
	}
	var cfg struct {
		AutoSyncEnabled  bool          `json:"auto_sync_enabled"`
		AutoSyncHour     int           `json:"auto_sync_hour"`
		AutoSyncMinute   int           `json:"auto_sync_minute"`
		AutoSyncDays     int           `json:"auto_sync_days"` // bitmask: Mon=bit0 … Sun=bit6
		TrackedPlaylists []interface{} `json:"tracked_playlists"`
	}
	if err := json.Unmarshal(data, &cfg); err != nil {
		return
	}
	if !cfg.AutoSyncEnabled || len(cfg.TrackedPlaylists) == 0 {
		return
	}

	// Check day-of-week bitmask (Go: Sunday=0, but we use Monday=bit0)
	dow := int(now.Weekday()) // 0=Sunday … 6=Saturday
	bit := (dow + 6) % 7      // Monday=0 … Sunday=6
	if cfg.AutoSyncDays&(1<<bit) == 0 {
		return
	}

	// Check hour and minute
	if now.Hour() != cfg.AutoSyncHour || now.Minute() != cfg.AutoSyncMinute {
		return
	}

	// Schedule matched — spawn auto-sync in background
	go func() {
		backend, err := ensureBundledBackend()
		var cmd *exec.Cmd
		if err != nil {
			return // no backend available in dev mode; RunAutoSync() can be called manually
		}
		cmd = exec.Command(backend, "--auto-sync", "--config", cfgPath)
		hideProcess(cmd)
		out, _ := cmd.Output()
		_ = out
		wailsRuntime.EventsEmit(a.ctx, "auto_sync_complete", string(out))
	}()
}

// cacheFfmpegPaths asks the bundled Python backend where its media tools live
// so local playback and library metadata do not depend on system PATH.
func (a *App) cacheFfmpegPaths() {
	backend, err := ensureBundledBackend()
	if err != nil {
		return
	}
	cmd := exec.Command(backend, "--get-ffmpeg-dir")
	hideProcess(cmd)
	out, err := cmd.Output()
	if err != nil {
		return
	}
	// Output is two lines: ffmpeg path, ffprobe path (either may be empty)
	lines := strings.SplitN(strings.ReplaceAll(strings.TrimSpace(string(out)), "\r\n", "\n"), "\n", 2)
	ffmpegPath := ""
	ffprobePath := ""
	if len(lines) >= 1 {
		ffmpegPath = strings.TrimSpace(lines[0])
	}
	if len(lines) >= 2 {
		ffprobePath = strings.TrimSpace(lines[1])
	}

	a.mu.Lock()
	defer a.mu.Unlock()
	if ffmpegPath != "" {
		if _, err := os.Stat(ffmpegPath); err == nil {
			a.ffmpegExe = ffmpegPath
		}
	}
	if ffprobePath != "" {
		if _, err := os.Stat(ffprobePath); err == nil {
			a.ffprobeExe = ffprobePath
		}
	}
}

// shutdown is called when the application is closing.
// Clean up any running backend processes so we don't leave orphans.
func (a *App) shutdown(ctx context.Context) {
	_, cmd := a.detachActiveDownload()
	if cmd != nil {
		_ = killCommandTree(cmd)
	}
	a.mu.Lock()
	indexCmd := a.indexCmd
	a.indexCmd = nil
	a.mu.Unlock()
	if indexCmd != nil {
		_ = killCommandTree(indexCmd)
	}

	a.mu.Lock()
	mediaServer := a.mediaServer
	a.mediaServer = nil
	a.mediaBaseURL = ""
	a.mediaToken = ""
	a.mu.Unlock()
	if mediaServer != nil {
		_ = mediaServer.Close()
	}

}
