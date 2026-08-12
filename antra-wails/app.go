package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"os/exec"
	"sync"
	"sync/atomic"
	"time"

	wailsRuntime "github.com/wailsapp/wails/v2/pkg/runtime"
)

// App struct
type App struct {
	ctx                       context.Context
	runtimeReady              atomic.Bool
	mu                        sync.Mutex
	configWriteMu             sync.Mutex
	configMu                  sync.RWMutex
	configCache               Config
	configCacheReady          bool
	readOnlyHelperMu          sync.Mutex
	readOnlyHelper            *readOnlyHelperClient
	readOnlyHelperClosed      bool
	perf                      *perfRecorder
	indexCoordinator          *indexResourceCoordinator
	downloadedStore           *downloadedLibraryStore
	downloadedCacheWriteMu    sync.Mutex
	trackProbeHook            func(context.Context, *libraryReleaseTrack)
	embeddedArtworkHook       func(context.Context, string) (string, error)
	embeddedArtworkFlights    embeddedArtworkSingleflight
	cancelDownload            context.CancelFunc
	activeCmd                 *exec.Cmd
	indexCmd                  *exec.Cmd
	isStopping                bool
	downloadPaused            bool
	downloadStopReason        string
	downloadIndexing          bool
	indexRestartAfterDownload bool
	autoSyncCmd               *exec.Cmd
	autoSyncCancel            context.CancelFunc
	autoSyncStarting          bool
	autoSyncStopRequested     bool
	autoSyncRunID             uint64
	autoSyncCommandFactory    func(context.Context) (*exec.Cmd, error)
	autoSyncEventHook         func(string, interface{})
	ipodWatcherCmd            *exec.Cmd
	ipodWatcherStarting       bool
	ipodWatcherCancel         context.CancelFunc
	ipodWatcherGeneration     uint64
	ipodCommandFactory        ipodCommandFactory
	ipodMutationCmd           *exec.Cmd
	ipodMutationStarting      bool
	ipodCancelPath            string
	ipodMutationRunID         string
	ipodMutationOperationID   string
	ipodMutationOperation     string
	ipodMutationKind          string
	ipodMutationPhase         string
	ipodMutationCanCancel     bool
	ffmpegExe                 string // absolute path to bundled ffmpeg for local playback helpers
	ffprobeExe                string // absolute path to bundled ffprobe for local library metadata
	mediaServer               *http.Server
	mediaBaseURL              string
	mediaToken                string
}

// NewApp creates a new App application struct
func NewApp() *App {
	return &App{
		perf:             newPerfRecorderFromEnv(),
		indexCoordinator: newIndexResourceCoordinator(backgroundIndexStageDelay),
	}
}

// startup is called when the app starts. The context is saved
// so we can call the runtime methods
func (a *App) startup(ctx context.Context) {
	a.ctx = ctx
	a.runtimeReady.Store(true)
	a.markPerf("startup")
}

func (a *App) logWarningf(format string, args ...interface{}) {
	if a != nil && a.runtimeReady.Load() && a.ctx != nil {
		wailsRuntime.LogWarningf(a.ctx, format, args...)
		return
	}
	log.Printf("[WARN] "+format, args...)
}

// domReady is called after the frontend DOM has finished loading.
// We reveal the window here to avoid the white/unstyled flash that
// occurs when the window is shown before the Svelte app has mounted.
func (a *App) domReady(ctx context.Context) {
	a.markPerf("dom_ready")
	wailsRuntime.WindowShow(ctx)
	go a.cacheFfmpegPaths()
	go a.startAutoSyncTicker(ctx)
	go func() {
		if err := a.StartIPodWatcher(); err != nil {
			wailsRuntime.EventsEmit(a.ctx, "ipod-event", map[string]interface{}{
				"type": "ipod_watch_error", "message": err.Error(), "protocol_version": 1,
			})
		}
	}()
}

// startAutoSyncTicker checks every minute whether the auto-sync schedule has
// been met and, if so, spawns the Python backend with --auto-sync.
// Successful SaveConfig calls replace the cached schedule immediately.
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

// maybeRunAutoSync checks whether the current time matches the configured
// auto-sync schedule and spawns the backend if it does.
func (a *App) maybeRunAutoSync(now time.Time) {
	cfg := a.GetConfig()
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

	// The shared guard prevents a schedule tick from overlapping a manual run.
	_ = a.startAutoSync("scheduled")
}

// cacheFfmpegPaths asks the bundled Python backend where its media tools live
// so local playback and library metadata do not depend on system PATH.
func (a *App) cacheFfmpegPaths() {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	out, err := a.callReadOnlyHelper(ctx, "ffmpeg_paths", map[string]interface{}{})
	if err != nil {
		return
	}
	var paths struct {
		FFmpeg  string `json:"ffmpeg"`
		FFprobe string `json:"ffprobe"`
	}
	if err := json.Unmarshal(out, &paths); err != nil {
		return
	}

	a.mu.Lock()
	defer a.mu.Unlock()
	if paths.FFmpeg != "" {
		if _, err := os.Stat(paths.FFmpeg); err == nil {
			a.ffmpegExe = paths.FFmpeg
		}
	}
	if paths.FFprobe != "" {
		if _, err := os.Stat(paths.FFprobe); err == nil {
			a.ffprobeExe = paths.FFprobe
		}
	}
}

// beforeClose prevents a normal application close from interrupting the
// non-cancellable portion of an iPod write.
func (a *App) beforeClose(ctx context.Context) bool {
	a.mu.Lock()
	running := a.ipodMutationCmd != nil || a.ipodMutationStarting
	runID := a.ipodMutationRunID
	operationID := a.ipodMutationOperationID
	operation := a.ipodMutationOperation
	kind := a.ipodMutationKind
	phase := a.ipodMutationPhase
	prevent := shouldPreventIPodClose(running, phase)
	a.mu.Unlock()
	if !prevent {
		return false
	}

	if a.ctx != nil {
		event := map[string]interface{}{
			"type":             "ipod_close_blocked",
			"status":           "running",
			"operation":        operation,
			"operation_id":     operationID,
			"kind":             kind,
			"phase":            phase,
			"can_cancel":       false,
			"message":          "Vela must remain open while the iPod operation commits its changes.",
			"protocol_version": 1,
		}
		if runID != "" && runID != operationID {
			event["bridge_operation_id"] = runID
		}
		wailsRuntime.EventsEmit(a.ctx, "ipod-event", event)
	}
	return true
}

func shouldPreventIPodClose(running bool, phase string) bool {
	return running && isProtectedIPodMutationPhase(phase)
}

// shutdown is called when the application is closing.
// Clean up any running backend processes so we don't leave orphans.
func (a *App) shutdown(ctx context.Context) {
	defer a.logPerfSummary()
	a.runtimeReady.Store(false)
	a.closeLibraryIndexCoordinator()
	a.closeReadOnlyHelper()
	a.CancelAutoSync()
	a.StopIPodWatcher()
	_, cmd := a.detachActiveDownload()
	if cmd != nil {
		_ = killCommandTree(cmd)
	}
	a.mu.Lock()
	indexCmd := a.indexCmd
	a.indexCmd = nil
	a.indexRestartAfterDownload = false
	ipodMutationCmd := a.ipodMutationCmd
	protectedIPodCommit := shouldPreventIPodClose(
		a.ipodMutationCmd != nil || a.ipodMutationStarting,
		a.ipodMutationPhase,
	)
	if protectedIPodCommit {
		// A forced shutdown must not explicitly terminate a process while it is
		// committing or flushing device state.
		ipodMutationCmd = nil
	} else {
		a.clearIPodMutationLocked()
	}
	a.mu.Unlock()
	if indexCmd != nil {
		_ = killCommandTree(indexCmd)
	}
	if ipodMutationCmd != nil {
		_ = killCommandTree(ipodMutationCmd)
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
