package main

import (
	"runtime"
	"testing"
)

func TestIPodRecoveryUSBInspectionShape(t *testing.T) {
	result := NewApp().InspectIPodRecoveryUSB()
	if !result.ReadOnly {
		t.Fatal("USB recovery inspection must remain read-only")
	}
	if result.Platform != runtime.GOOS {
		t.Fatalf("unexpected platform: got %q want %q", result.Platform, runtime.GOOS)
	}
	if result.Devices == nil {
		t.Fatal("devices must serialize as an array, not null")
	}
	if runtime.GOOS == "windows" && !result.Supported {
		t.Fatal("Windows should expose read-only recovery USB inspection")
	}
	if runtime.GOOS != "windows" && result.Supported {
		t.Fatal("non-Windows recovery USB inspection must be unsupported")
	}
	for _, device := range result.Devices {
		if device.VendorID != "05AC" {
			t.Fatalf("reported a non-Apple recovery device: %#v", device)
		}
		if device.Mode != "dfu" && device.Mode != "wtf" {
			t.Fatalf("unexpected recovery mode: %#v", device)
		}
		if device.ProductID == "" {
			t.Fatalf("missing recovery product ID: %#v", device)
		}
	}
}
