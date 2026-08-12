package main

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"sync"
)

// backendPathMemo serializes cold backend preparation and caches immutable
// success/not-present results. Transient filesystem failures remain retryable.
type backendPathMemo struct {
	mu    sync.Mutex
	ready bool
	path  string
	err   error
}

func (m *backendPathMemo) resolve(prepare func() (string, error)) (string, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.ready {
		return m.path, m.err
	}

	path, err := prepare()
	if err == nil || errors.Is(err, fs.ErrNotExist) {
		m.path = path
		m.err = err
		m.ready = true
	}
	return path, err
}

func ensureExtractedBackend(
	assets fs.FS,
	assetPath string,
	appDataDir string,
	executableName string,
	mode fs.FileMode,
) (string, error) {
	sum, size, err := hashFSAsset(assets, assetPath)
	if err != nil {
		return "", err
	}
	if size == 0 {
		return "", fs.ErrNotExist
	}

	versionDir := filepath.Join(appDataDir, "runtime", "backend", hex.EncodeToString(sum[:8]))
	backendPath := filepath.Join(versionDir, executableName)
	if matches, matchErr := fileMatchesHash(backendPath, sum); matchErr == nil && matches {
		if err := os.Chmod(backendPath, mode); err != nil {
			return "", err
		}
		return backendPath, nil
	}

	if err := os.MkdirAll(versionDir, 0755); err != nil {
		return "", err
	}
	temp, err := os.CreateTemp(versionDir, executableName+"-*.tmp")
	if err != nil {
		return "", err
	}
	tempPath := temp.Name()
	defer os.Remove(tempPath)

	closeWithError := func(cause error) (string, error) {
		_ = temp.Close()
		return "", cause
	}
	if err := temp.Chmod(mode); err != nil {
		return closeWithError(err)
	}

	source, err := assets.Open(assetPath)
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			err = fs.ErrNotExist
		}
		return closeWithError(err)
	}
	writtenHash := sha256.New()
	written, copyErr := io.Copy(io.MultiWriter(temp, writtenHash), source)
	closeErr := source.Close()
	if copyErr != nil {
		return closeWithError(copyErr)
	}
	if closeErr != nil {
		return closeWithError(closeErr)
	}
	if written != size || !hashBytesEqual(writtenHash.Sum(nil), sum[:]) {
		return closeWithError(fmt.Errorf("bundled backend changed while extracting"))
	}
	if err := temp.Sync(); err != nil {
		return closeWithError(err)
	}
	if err := temp.Close(); err != nil {
		return "", err
	}

	// Verify bytes after the flush, before publishing executable code.
	if matches, verifyErr := fileMatchesHash(tempPath, sum); verifyErr != nil || !matches {
		if verifyErr != nil {
			return "", fmt.Errorf("verify extracted backend: %w", verifyErr)
		}
		return "", fmt.Errorf("verify extracted backend: checksum mismatch")
	}

	if err := publishExtractedBackend(tempPath, backendPath, sum); err != nil {
		return "", err
	}
	if err := os.Chmod(backendPath, mode); err != nil {
		return "", err
	}
	return backendPath, nil
}

func hashFSAsset(assets fs.FS, assetPath string) ([sha256.Size]byte, int64, error) {
	var zero [sha256.Size]byte
	file, err := assets.Open(assetPath)
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return zero, 0, fs.ErrNotExist
		}
		return zero, 0, err
	}
	defer file.Close()

	hasher := sha256.New()
	size, err := io.Copy(hasher, file)
	if err != nil {
		return zero, 0, err
	}
	var sum [sha256.Size]byte
	copy(sum[:], hasher.Sum(nil))
	return sum, size, nil
}

func fileMatchesHash(path string, expected [sha256.Size]byte) (bool, error) {
	file, err := os.Open(path)
	if err != nil {
		return false, err
	}
	defer file.Close()

	hasher := sha256.New()
	if _, err := io.Copy(hasher, file); err != nil {
		return false, err
	}
	return hashBytesEqual(hasher.Sum(nil), expected[:]), nil
}

func hashBytesEqual(left, right []byte) bool {
	if len(left) != len(right) {
		return false
	}
	var different byte
	for i := range left {
		different |= left[i] ^ right[i]
	}
	return different == 0
}

func publishExtractedBackend(
	tempPath string,
	backendPath string,
	expected [sha256.Size]byte,
) error {
	if err := os.Rename(tempPath, backendPath); err == nil {
		return nil
	}

	// Another Vela process may have won the same content-addressed publish.
	if matches, _ := fileMatchesHash(backendPath, expected); matches {
		return nil
	}

	// Windows cannot atomically replace an existing corrupt destination.
	if err := os.Remove(backendPath); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err := os.Rename(tempPath, backendPath); err != nil {
		if matches, _ := fileMatchesHash(backendPath, expected); matches {
			return nil
		}
		return err
	}
	return nil
}
