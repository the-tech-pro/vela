//go:build darwin

package main

import (
	"path/filepath"
	"testing"
)

func TestDarwinBundledBackendPath(t *testing.T) {
	executable := filepath.Join(
		string(filepath.Separator),
		"Applications",
		"Vela.app",
		"Contents",
		"MacOS",
		"Vela",
	)
	want := filepath.Join(
		string(filepath.Separator),
		"Applications",
		"Vela.app",
		"Contents",
		"Helpers",
		"VelaBackend",
		"VelaBackend",
	)

	if got := darwinBundledBackendPath(executable); got != want {
		t.Fatalf("darwinBundledBackendPath() = %q, want %q", got, want)
	}
}
