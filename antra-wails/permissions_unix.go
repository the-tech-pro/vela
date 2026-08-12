//go:build !windows

package main

import "os"

func ensurePrivateConfigPermissions(path string) error {
	return os.Chmod(path, 0600)
}
