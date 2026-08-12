package main

import (
	"bufio"
	"context"
	"os"
	"os/exec"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestIPodWatcherFixtureProcess(t *testing.T) {
	if os.Getenv("VELA_IPOD_WATCHER_FIXTURE") != "1" {
		return
	}
	time.Sleep(30 * time.Second)
	os.Exit(0)
}

func TestStartIPodWatcherIsIdempotentDuringConcurrentCalls(t *testing.T) {
	t.Setenv("LOCALAPPDATA", t.TempDir())
	app := NewApp()
	app.ctx = context.Background()
	var preparations atomic.Int32
	cleaned := make(chan struct{}, 1)
	app.ipodCommandFactory = func(
		ctx context.Context,
		operation string,
		_ interface{},
	) (*exec.Cmd, *bufio.Scanner, func(), error) {
		preparations.Add(1)
		if operation != "watch" {
			t.Fatalf("unexpected operation %q", operation)
		}
		cmd := exec.CommandContext(
			ctx,
			os.Args[0],
			"-test.run=^TestIPodWatcherFixtureProcess$",
		)
		cmd.Env = append(os.Environ(), "VELA_IPOD_WATCHER_FIXTURE=1")
		hideProcess(cmd)
		stdout, err := cmd.StdoutPipe()
		if err != nil {
			return nil, nil, func() {}, err
		}
		return cmd, bufio.NewScanner(stdout), func() {
			select {
			case cleaned <- struct{}{}:
			default:
			}
		}, nil
	}

	const callers = 24
	start := make(chan struct{})
	errors := make(chan error, callers)
	var wait sync.WaitGroup
	for range callers {
		wait.Add(1)
		go func() {
			defer wait.Done()
			<-start
			errors <- app.StartIPodWatcher()
		}()
	}
	close(start)
	wait.Wait()
	close(errors)
	for err := range errors {
		if err != nil {
			t.Fatal(err)
		}
	}

	if preparations.Load() != 1 {
		t.Fatalf("watcher process prepared %d times, want 1", preparations.Load())
	}
	if err := app.StartIPodWatcher(); err != nil {
		t.Fatal(err)
	}
	if preparations.Load() != 1 {
		t.Fatalf("second start prepared another process: %d", preparations.Load())
	}

	app.StopIPodWatcher()
	select {
	case <-cleaned:
	case <-time.After(5 * time.Second):
		t.Fatal("watcher process was not reaped after stop")
	}
	app.mu.Lock()
	running := app.ipodWatcherCmd != nil || app.ipodWatcherStarting
	app.mu.Unlock()
	if running {
		t.Fatal("watcher state remained active after stop")
	}
}
