package main

import (
	"bytes"
	"io/fs"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"testing/fstest"
)

type countingFS struct {
	base      fs.FS
	openCount atomic.Int64
	readBytes atomic.Int64
}

func (c *countingFS) Open(name string) (fs.File, error) {
	file, err := c.base.Open(name)
	if err != nil {
		return nil, err
	}
	c.openCount.Add(1)
	return &countingFile{File: file, readBytes: &c.readBytes}, nil
}

type countingFile struct {
	fs.File
	readBytes *atomic.Int64
}

func (f *countingFile) Read(buffer []byte) (int, error) {
	n, err := f.File.Read(buffer)
	f.readBytes.Add(int64(n))
	return n, err
}

func TestBackendPathMemoConcurrentWarmLookupDoesNotRehash(t *testing.T) {
	const assetPath = "runtime/backend/VelaBackend"
	payload := bytes.Repeat([]byte("small-fixture-"), 128)
	assets := &countingFS{base: fstest.MapFS{
		assetPath: &fstest.MapFile{Data: payload, Mode: 0755},
	}}
	root := t.TempDir()
	var memo backendPathMemo
	prepare := func() (string, error) {
		return ensureExtractedBackend(assets, assetPath, root, "VelaBackend", 0755)
	}

	const callers = 32
	paths := make(chan string, callers)
	errors := make(chan error, callers)
	var wait sync.WaitGroup
	for range callers {
		wait.Add(1)
		go func() {
			defer wait.Done()
			path, err := memo.resolve(prepare)
			paths <- path
			errors <- err
		}()
	}
	wait.Wait()
	close(paths)
	close(errors)

	for err := range errors {
		if err != nil {
			t.Fatal(err)
		}
	}
	var extractedPath string
	for path := range paths {
		if extractedPath == "" {
			extractedPath = path
		} else if path != extractedPath {
			t.Fatalf("concurrent callers received different paths: %q and %q", extractedPath, path)
		}
	}
	if got, err := os.ReadFile(extractedPath); err != nil || !bytes.Equal(got, payload) {
		t.Fatalf("extracted fixture mismatch: bytes=%d err=%v", len(got), err)
	}

	opens := assets.openCount.Load()
	readBytes := assets.readBytes.Load()
	for range 100 {
		path, err := memo.resolve(prepare)
		if err != nil || path != extractedPath {
			t.Fatalf("warm lookup = %q, %v", path, err)
		}
	}
	if assets.openCount.Load() != opens || assets.readBytes.Load() != readBytes {
		t.Fatalf(
			"warm lookup reread backend: opens %d->%d bytes %d->%d",
			opens,
			assets.openCount.Load(),
			readBytes,
			assets.readBytes.Load(),
		)
	}
}

func TestEnsureExtractedBackendReplacesCorruptPublishedFile(t *testing.T) {
	const assetPath = "runtime/backend/VelaBackend"
	payload := []byte("verified backend fixture")
	assets := fstest.MapFS{assetPath: &fstest.MapFile{Data: payload, Mode: 0755}}
	root := t.TempDir()

	path, err := ensureExtractedBackend(assets, assetPath, root, "VelaBackend", 0755)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("corrupt"), 0755); err != nil {
		t.Fatal(err)
	}
	pathAgain, err := ensureExtractedBackend(assets, assetPath, root, "VelaBackend", 0755)
	if err != nil {
		t.Fatal(err)
	}
	if pathAgain != path {
		t.Fatalf("content-addressed path changed: %q != %q", pathAgain, path)
	}
	got, err := os.ReadFile(pathAgain)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, payload) {
		t.Fatalf("corrupt backend was not replaced: %q", got)
	}
	matches, err := fileMatchesHash(pathAgain, mustAssetHash(t, assets, assetPath))
	if err != nil || !matches {
		t.Fatalf("published backend failed integrity check: matches=%v err=%v", matches, err)
	}
}

func mustAssetHash(t *testing.T, assets fs.FS, assetPath string) [32]byte {
	t.Helper()
	sum, _, err := hashFSAsset(assets, assetPath)
	if err != nil {
		t.Fatal(err)
	}
	return sum
}

func BenchmarkBackendPathMemoWarm(b *testing.B) {
	const assetPath = "runtime/backend/VelaBackend"
	assets := &countingFS{base: fstest.MapFS{
		assetPath: &fstest.MapFile{Data: bytes.Repeat([]byte("fixture"), 1024), Mode: 0755},
	}}
	root := b.TempDir()
	var memo backendPathMemo
	prepare := func() (string, error) {
		return ensureExtractedBackend(assets, assetPath, root, filepath.Base(assetPath), 0755)
	}
	if _, err := memo.resolve(prepare); err != nil {
		b.Fatal(err)
	}
	opens := assets.openCount.Load()
	readBytes := assets.readBytes.Load()

	b.ResetTimer()
	for range b.N {
		if _, err := memo.resolve(prepare); err != nil {
			b.Fatal(err)
		}
	}
	b.StopTimer()
	if assets.openCount.Load() != opens || assets.readBytes.Load() != readBytes {
		b.Fatal("warm benchmark reread fixture")
	}
}
