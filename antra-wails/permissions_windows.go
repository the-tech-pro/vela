//go:build windows

package main

// Windows protects this file through the user's application-data ACL. Unix
// mode bits do not map reliably to NTFS ACLs.
func ensurePrivateConfigPermissions(path string) error {
	return nil
}
