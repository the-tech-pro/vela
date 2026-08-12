package main

import (
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestEmbeddedArtworkCacheKeyUsesCompletePath(t *testing.T) {
	first := embeddedArtworkCacheKey(`C:\Music\Vela\Artist\Album One\01.flac`)
	second := embeddedArtworkCacheKey(`C:\Music\Vela\Artist\Album Two\01.flac`)
	if first == second {
		t.Fatal("different releases must never share an embedded-artwork cache key")
	}
	if len(first) != 64 || len(second) != 64 {
		t.Fatalf("expected SHA-256 cache keys, got %d and %d characters", len(first), len(second))
	}
}

func TestDownloadWorkerCeilingScalesWithCPU(t *testing.T) {
	tests := []struct {
		cpus int
		want int
	}{
		{1, 8},
		{4, 8},
		{5, 12},
		{8, 12},
		{9, 16},
		{32, 16},
	}
	for _, test := range tests {
		if got := downloadWorkerCeilingForCPU(test.cpus); got != test.want {
			t.Fatalf("cpus=%d: got ceiling %d, want %d", test.cpus, got, test.want)
		}
	}
}

func TestRefreshCachedArtworkURLsRenewsMediaSession(t *testing.T) {
	app := &App{
		mediaBaseURL: "http://127.0.0.1:4567",
		mediaToken:   "new-token",
	}
	old := url.Values{}
	old.Set("path", `C:\Music\Vela\Artist\Album\cover.jpg`)
	old.Set("token", "old-token")
	payload := libraryPayload{
		Albums: []libraryReleaseSummary{{
			RelativePath: "Artist/Album",
			ArtworkURL:   "http://127.0.0.1:1234/media/art?" + old.Encode(),
		}},
	}

	app.refreshCachedArtworkURLs(&payload, nil)

	renewed := payload.Albums[0].ArtworkURL
	if !strings.HasPrefix(renewed, "http://127.0.0.1:4567/media/art?") {
		t.Fatalf("expected current media server URL, got %q", renewed)
	}
	if !strings.Contains(renewed, "token=new-token") {
		t.Fatalf("expected renewed media token, got %q", renewed)
	}
}

func TestMediaTokensUseIndependentCryptoRandomValues(t *testing.T) {
	first, err := newMediaToken()
	if err != nil {
		t.Fatal(err)
	}
	second, err := newMediaToken()
	if err != nil {
		t.Fatal(err)
	}
	if len(first) != 48 || len(second) != 48 || first == second {
		t.Fatalf("expected distinct 192-bit hex tokens, got %q and %q", first, second)
	}
}

func TestResolveLibraryPathRejectsSymlinkEscape(t *testing.T) {
	root := t.TempDir()
	outside := t.TempDir()
	secret := filepath.Join(outside, "secret.flac")
	if err := os.WriteFile(secret, []byte("not audio"), 0600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "escape")
	if err := os.Symlink(outside, link); err != nil {
		t.Skipf("symlink creation unavailable: %v", err)
	}
	if _, err := resolveLibraryPath(root, filepath.Join(link, "secret.flac")); err == nil {
		t.Fatal("symlink traversal outside the library root must be rejected")
	}
}

func TestResolveLibraryPathAllowsContainedFile(t *testing.T) {
	root := t.TempDir()
	track := filepath.Join(root, "Artist", "Album", "01.flac")
	if err := os.MkdirAll(filepath.Dir(track), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(track, []byte("audio"), 0600); err != nil {
		t.Fatal(err)
	}
	got, err := resolveLibraryPath(root, track)
	if err != nil {
		t.Fatal(err)
	}
	if filepath.Clean(got) != filepath.Clean(track) {
		t.Fatalf("resolved %q, want %q", got, track)
	}
}
