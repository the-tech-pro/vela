package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"os/exec"
	"time"

	wailsRuntime "github.com/wailsapp/wails/v2/pkg/runtime"
)

const autoSyncTimeout = 30 * time.Minute

var errAutoSyncRunning = errors.New("auto-sync is already running")

// RunAutoSync schedules an immediate run and returns without waiting for
// backend startup or downloads. Progress and the terminal result are events.
func (a *App) RunAutoSync() string {
	if err := a.startAutoSync("manual"); err != nil {
		return "error:" + err.Error()
	}
	return "Auto-sync started."
}

func (a *App) startAutoSync(trigger string) error {
	baseContext := a.ctx
	if baseContext == nil {
		baseContext = context.Background()
	}
	ctx, cancel := context.WithTimeout(baseContext, autoSyncTimeout)

	a.mu.Lock()
	if a.autoSyncStarting || a.autoSyncCmd != nil {
		a.mu.Unlock()
		cancel()
		return errAutoSyncRunning
	}
	a.autoSyncRunID++
	runID := a.autoSyncRunID
	a.autoSyncStarting = true
	a.autoSyncStopRequested = false
	a.autoSyncCancel = cancel
	a.mu.Unlock()

	go a.runAutoSyncProcess(ctx, runID, trigger)
	return nil
}

func (a *App) runAutoSyncProcess(ctx context.Context, runID uint64, trigger string) {
	processSpan := a.beginBackendPerf("auto_sync")
	a.emitAutoSyncEvent("auto_sync_started", map[string]interface{}{
		"status":  "starting",
		"trigger": trigger,
	})

	cmd, err := a.makeAutoSyncCommand(ctx)
	if err != nil {
		processSpan.finish(0, err)
		a.finishAutoSync(runID, trigger, a.autoSyncFailureStatus(runID, ctx), err, false)
		return
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		processSpan.finish(0, err)
		a.finishAutoSync(runID, trigger, a.autoSyncFailureStatus(runID, ctx), err, false)
		return
	}
	cmd.Stderr = cmd.Stdout
	if err := cmd.Start(); err != nil {
		processSpan.finish(0, err)
		status := a.autoSyncFailureStatus(runID, ctx)
		a.finishAutoSync(runID, trigger, status, err, false)
		return
	}
	a.incrementPerf("backend_spawns")

	a.mu.Lock()
	if a.autoSyncRunID != runID || !a.autoSyncStarting {
		a.mu.Unlock()
		_ = killCommandTree(cmd)
		processSpan.finish(0, context.Canceled)
		a.finishAutoSync(runID, trigger, "cancelled", context.Canceled, false)
		return
	}
	a.autoSyncStarting = false
	a.autoSyncCmd = cmd
	a.mu.Unlock()

	scanner := bufio.NewScanner(stdout)
	scanner.Buffer(make([]byte, 64*1024), 16*1024*1024)
	outputBytes := 0
	var reportedErr error
	for scanner.Scan() {
		line := scanner.Text()
		outputBytes += len(line) + 1
		a.emitAutoSyncEvent("auto_sync_event", line)

		var event struct {
			Type    string `json:"type"`
			Message string `json:"message"`
		}
		if json.Unmarshal(scanner.Bytes(), &event) == nil && event.Type == "error" {
			message := sanitizeHelperDiagnostic(event.Message)
			if message == "" {
				message = "The auto-sync backend reported an error."
			}
			reportedErr = errors.New(message)
		}
	}
	scanErr := scanner.Err()
	waitErr := cmd.Wait()
	runErr := errors.Join(scanErr, waitErr, reportedErr)
	processSpan.finish(outputBytes, runErr)

	status := "completed"
	if a.autoSyncWasStopped(runID) {
		status = "cancelled"
	} else if ctx.Err() != nil {
		status = autoSyncContextStatus(ctx)
	} else if runErr != nil {
		status = "failed"
	}
	a.finishAutoSync(runID, trigger, status, runErr, true)
}

func (a *App) makeAutoSyncCommand(ctx context.Context) (*exec.Cmd, error) {
	a.mu.Lock()
	factory := a.autoSyncCommandFactory
	a.mu.Unlock()
	if factory != nil {
		return factory(ctx)
	}

	command, args, workDir, env, err := a.resolveBackendCommand(nil)
	if err != nil {
		return nil, err
	}
	args = append(append([]string(nil), args...), "--auto-sync")
	cmd := exec.CommandContext(ctx, command, args...)
	hideProcess(cmd)
	cmd.Dir = workDir
	cmd.Env = env
	return cmd, nil
}

func (a *App) finishAutoSync(
	runID uint64,
	trigger string,
	status string,
	runErr error,
	mayHaveUpdatedConfig bool,
) {
	a.mu.Lock()
	if a.autoSyncRunID != runID {
		a.mu.Unlock()
		return
	}
	cancel := a.autoSyncCancel
	a.autoSyncCancel = nil
	a.autoSyncCmd = nil
	a.autoSyncStarting = false
	a.autoSyncStopRequested = false
	a.mu.Unlock()
	if cancel != nil {
		cancel()
	}
	if mayHaveUpdatedConfig {
		// Auto-sync checkpoints tracked playlist IDs in config.json.
		a.invalidateConfigCache()
	}

	message := ""
	if runErr != nil {
		message = sanitizeHelperDiagnostic(runErr.Error())
	}
	if status == "failed" {
		if message == "" {
			message = "Auto-sync failed."
		}
		a.emitAutoSyncEvent("auto_sync_error", map[string]interface{}{
			"status":  status,
			"trigger": trigger,
			"message": message,
		})
	}
	payload := map[string]interface{}{
		"status":  status,
		"trigger": trigger,
	}
	if message != "" {
		payload["message"] = message
	}
	a.emitAutoSyncEvent("auto_sync_complete", payload)
}

func autoSyncContextStatus(ctx context.Context) string {
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		return "timed_out"
	}
	return "cancelled"
}

func (a *App) emitAutoSyncEvent(name string, payload interface{}) {
	a.mu.Lock()
	hook := a.autoSyncEventHook
	ctx := a.ctx
	a.mu.Unlock()
	if hook != nil {
		hook(name, payload)
		return
	}
	if ctx != nil {
		wailsRuntime.EventsEmit(ctx, name, payload)
	}
}

// CancelAutoSync terminates the dedicated auto-sync process tree. Downloads,
// indexing, and iPod operations are owned by separate process slots.
func (a *App) CancelAutoSync() {
	a.mu.Lock()
	cmd := a.autoSyncCmd
	cancel := a.autoSyncCancel
	active := a.autoSyncStarting || cmd != nil
	if active {
		a.autoSyncStopRequested = true
	}
	a.mu.Unlock()
	if !active {
		return
	}
	if cmd != nil {
		if err := killCommandTree(cmd); err != nil {
			a.logWarningf("Failed to stop auto-sync: %v", err)
		}
	}
	if cancel != nil {
		cancel()
	}
}

func (a *App) autoSyncWasStopped(runID uint64) bool {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.autoSyncRunID == runID && a.autoSyncStopRequested
}

func (a *App) autoSyncFailureStatus(runID uint64, ctx context.Context) string {
	if a.autoSyncWasStopped(runID) || ctx.Err() != nil {
		return autoSyncContextStatus(ctx)
	}
	return "failed"
}

func (a *App) autoSyncState() string {
	a.mu.Lock()
	defer a.mu.Unlock()
	switch {
	case a.autoSyncStarting:
		return "starting"
	case a.autoSyncCmd != nil:
		return "running"
	default:
		return "idle"
	}
}
