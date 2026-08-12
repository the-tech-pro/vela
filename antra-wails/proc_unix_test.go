//go:build !windows

package main

import (
	"os/exec"
	"testing"
	"time"
)

func TestHideProcessCreatesProcessGroup(t *testing.T) {
	cmd := exec.Command("sh", "-c", "sleep 30")
	hideProcess(cmd)
	if cmd.SysProcAttr == nil || !cmd.SysProcAttr.Setpgid {
		t.Fatal("managed Unix commands must start in a separate process group")
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	if err := killCommandTree(cmd); err != nil {
		t.Fatal(err)
	}
	done := make(chan struct{})
	go func() {
		_ = cmd.Wait()
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(processTerminationGrace + 2*time.Second):
		t.Fatal("process group did not terminate")
	}
}
