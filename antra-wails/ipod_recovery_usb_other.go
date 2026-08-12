//go:build !windows

package main

import "runtime"

func inspectIPodRecoveryUSB() IPodRecoveryUSBInspection {
	return IPodRecoveryUSBInspection{
		Supported: false,
		Available: false,
		ReadOnly:  true,
		Platform:  runtime.GOOS,
		Devices:   make([]IPodRecoveryUSBDevice, 0),
		Message:   "iPod DFU/WTF USB inspection is currently supported only on Windows.",
	}
}
