//go:build darwin

package main

import (
	"errors"
	"io/fs"
	"os"
	"path/filepath"
)

var bundledBackendMemo backendPathMemo

func darwinBundledBackendPath(executable string) string {
	return filepath.Clean(filepath.Join(
		filepath.Dir(executable), "..", "Helpers", "VelaBackend", "VelaBackend",
	))
}

// ensureBundledBackend resolves the signed PyInstaller onedir helper in-place.
// macOS hardened runtime validation does not permit extracting executable code
// from the signed application bundle into a writable directory.
func ensureBundledBackend() (string, error) {
	return bundledBackendMemo.resolve(func() (string, error) {
		executable, err := os.Executable()
		if err != nil {
			return "", err
		}
		backend := darwinBundledBackendPath(executable)
		info, err := os.Stat(backend)
		if err != nil {
			if errors.Is(err, os.ErrNotExist) {
				return "", fs.ErrNotExist
			}
			return "", err
		}
		if info.IsDir() || info.Mode()&0111 == 0 {
			return "", fs.ErrNotExist
		}
		return backend, nil
	})
}
