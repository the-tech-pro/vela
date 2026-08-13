//go:build !windows

package main

import (
	"errors"
	"os"
	"os/exec"
	"syscall"
	"time"
)

const processTerminationGrace = 3 * time.Second

// Put every managed command in its own process group so provider/transcoder
// grandchildren cannot outlive Vela.
func hideProcess(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if cmd.Cancel != nil {
		cmd.Cancel = func() error {
			return killCommandTree(cmd)
		}
		cmd.WaitDelay = processTerminationGrace + time.Second
	}
}

func killCommandTree(cmd *exec.Cmd) error {
	if cmd == nil || cmd.Process == nil {
		return nil
	}
	pgid, err := syscall.Getpgid(cmd.Process.Pid)
	if err != nil {
		if errors.Is(err, syscall.ESRCH) {
			return nil
		}
		return err
	}
	if err := syscall.Kill(-pgid, syscall.SIGTERM); err != nil && !errors.Is(err, syscall.ESRCH) {
		return err
	}

	deadline := time.Now().Add(processTerminationGrace)
	for time.Now().Before(deadline) {
		if err := syscall.Kill(-pgid, 0); errors.Is(err, syscall.ESRCH) {
			return nil
		}
		time.Sleep(50 * time.Millisecond)
	}
	// macOS can report EPERM for the final group signal while terminated
	// children are waiting to be reaped. The caller's Cmd.Wait remains the
	// authoritative completion check after the successful SIGTERM above.
	if err := syscall.Kill(-pgid, syscall.SIGKILL); err != nil &&
		!errors.Is(err, syscall.ESRCH) &&
		!errors.Is(err, syscall.EPERM) &&
		!errors.Is(err, os.ErrProcessDone) {
		return err
	}
	return nil
}
