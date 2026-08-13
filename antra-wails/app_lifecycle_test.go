package main

import (
	"context"
	"os"
	"os/exec"
	"testing"
	"time"
)

func TestActiveDownloadFixtureProcess(t *testing.T) {
	if os.Getenv("VELA_ACTIVE_DOWNLOAD_FIXTURE") != "1" {
		return
	}
	for {
		time.Sleep(time.Hour)
	}
}

func TestCancelDownloadStopsAndDetachesActiveProcess(t *testing.T) {
	cmd := exec.Command(os.Args[0], "-test.run=TestActiveDownloadFixtureProcess")
	cmd.Env = append(os.Environ(), "VELA_ACTIVE_DOWNLOAD_FIXTURE=1")
	hideProcess(cmd)
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() {
		done <- cmd.Wait()
	}()

	app := NewApp()
	downloadCtx, cancel := context.WithCancel(context.Background())
	defer cancel()
	app.attachActiveDownload(cancel, cmd)

	app.CancelDownload()

	select {
	case <-done:
	case <-time.After(5 * time.Second):
		_ = killCommandTree(cmd)
		t.Fatal("cancelled download process did not exit")
	}
	select {
	case <-downloadCtx.Done():
	default:
		t.Fatal("download context was not cancelled")
	}
	app.mu.Lock()
	defer app.mu.Unlock()
	if app.activeCmd != nil || app.cancelDownload != nil {
		t.Fatal("cancelled download remained attached")
	}
	if !app.isStopping || app.downloadStopReason != "cancelled" {
		t.Fatalf(
			"cancel state = stopping:%v reason:%q",
			app.isStopping,
			app.downloadStopReason,
		)
	}
}
