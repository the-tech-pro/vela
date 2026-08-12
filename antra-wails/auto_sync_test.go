package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"sync/atomic"
	"testing"
	"time"
)

func TestAutoSyncFixtureProcess(t *testing.T) {
	if os.Getenv("VELA_AUTO_SYNC_FIXTURE") != "1" {
		return
	}
	fmt.Println(`{"type":"auto_sync_playlist_start","name":"Fixture"}`)
	if os.Getenv("VELA_AUTO_SYNC_SLOW") == "1" {
		time.Sleep(2 * time.Second)
	} else {
		time.Sleep(250 * time.Millisecond)
	}
	if os.Getenv("VELA_AUTO_SYNC_FAIL") == "1" {
		fmt.Println(`{"type":"error","message":"fixture failed"}`)
		os.Exit(4)
	}
	fmt.Println(`{"type":"auto_sync_summary","synced":1,"new_tracks":0}`)
	os.Exit(0)
}

type capturedAutoSyncEvent struct {
	name    string
	payload interface{}
}

func autoSyncFixtureCommand(
	slow bool,
	fail bool,
) func(context.Context) (*exec.Cmd, error) {
	return func(ctx context.Context) (*exec.Cmd, error) {
		cmd := exec.CommandContext(
			ctx,
			os.Args[0],
			"-test.run=^TestAutoSyncFixtureProcess$",
		)
		env := append(os.Environ(), "VELA_AUTO_SYNC_FIXTURE=1")
		if slow {
			env = append(env, "VELA_AUTO_SYNC_SLOW=1")
		}
		if fail {
			env = append(env, "VELA_AUTO_SYNC_FAIL=1")
		}
		cmd.Env = env
		hideProcess(cmd)
		return cmd, nil
	}
}

func TestRunAutoSyncReturnsImmediatelyGuardsDuplicatesAndCompletes(t *testing.T) {
	app := NewApp()
	app.ctx = context.Background()
	app.autoSyncCommandFactory = autoSyncFixtureCommand(false, false)
	events := make(chan capturedAutoSyncEvent, 32)
	var sawStreamEvent atomic.Bool
	app.autoSyncEventHook = func(name string, payload interface{}) {
		if name == "auto_sync_event" {
			sawStreamEvent.Store(true)
		}
		events <- capturedAutoSyncEvent{name: name, payload: payload}
	}

	started := time.Now()
	if result := app.RunAutoSync(); result != "Auto-sync started." {
		t.Fatalf("start result = %q", result)
	}
	if elapsed := time.Since(started); elapsed > 100*time.Millisecond {
		t.Fatalf("RunAutoSync blocked for %v", elapsed)
	}
	if duplicate := app.RunAutoSync(); duplicate != "error:"+errAutoSyncRunning.Error() {
		t.Fatalf("duplicate result = %q", duplicate)
	}

	complete := waitForAutoSyncEvent(t, events, "auto_sync_complete")
	if !sawStreamEvent.Load() {
		t.Fatal("auto-sync stdout was not streamed")
	}
	payload, ok := complete.payload.(map[string]interface{})
	if !ok || payload["status"] != "completed" || payload["trigger"] != "manual" {
		t.Fatalf("unexpected completion payload: %#v", complete.payload)
	}
	if state := app.autoSyncState(); state != "idle" {
		t.Fatalf("state after completion = %q", state)
	}
}

func TestRunAutoSyncExposesAsynchronousStartupError(t *testing.T) {
	app := NewApp()
	app.ctx = context.Background()
	app.autoSyncCommandFactory = func(context.Context) (*exec.Cmd, error) {
		return nil, errors.New("fixture startup failure")
	}
	events := make(chan capturedAutoSyncEvent, 8)
	app.autoSyncEventHook = func(name string, payload interface{}) {
		events <- capturedAutoSyncEvent{name: name, payload: payload}
	}

	if result := app.RunAutoSync(); result != "Auto-sync started." {
		t.Fatalf("start result = %q", result)
	}
	errorEvent := waitForAutoSyncEvent(t, events, "auto_sync_error")
	errorPayload := errorEvent.payload.(map[string]interface{})
	if errorPayload["status"] != "failed" {
		t.Fatalf("unexpected error payload: %#v", errorPayload)
	}
	complete := waitForAutoSyncEvent(t, events, "auto_sync_complete")
	if complete.payload.(map[string]interface{})["status"] != "failed" {
		t.Fatalf("unexpected completion payload: %#v", complete.payload)
	}
}

func TestCancelAutoSyncStopsDedicatedProcess(t *testing.T) {
	app := NewApp()
	app.ctx = context.Background()
	app.autoSyncCommandFactory = autoSyncFixtureCommand(true, false)
	events := make(chan capturedAutoSyncEvent, 16)
	app.autoSyncEventHook = func(name string, payload interface{}) {
		events <- capturedAutoSyncEvent{name: name, payload: payload}
	}

	if result := app.RunAutoSync(); result != "Auto-sync started." {
		t.Fatalf("start result = %q", result)
	}
	deadline := time.Now().Add(5 * time.Second)
	for app.autoSyncState() != "running" {
		if time.Now().After(deadline) {
			t.Fatal("auto-sync process did not start")
		}
		time.Sleep(10 * time.Millisecond)
	}
	app.CancelAutoSync()
	complete := waitForAutoSyncEvent(t, events, "auto_sync_complete")
	if complete.payload.(map[string]interface{})["status"] != "cancelled" {
		t.Fatalf("unexpected cancellation payload: %#v", complete.payload)
	}
}

func waitForAutoSyncEvent(
	t *testing.T,
	events <-chan capturedAutoSyncEvent,
	wanted string,
) capturedAutoSyncEvent {
	t.Helper()
	timeout := time.NewTimer(8 * time.Second)
	defer timeout.Stop()
	for {
		select {
		case event := <-events:
			if event.name == wanted {
				return event
			}
		case <-timeout.C:
			t.Fatalf("timed out waiting for %s", wanted)
		}
	}
}
