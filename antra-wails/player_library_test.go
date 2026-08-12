package main

import "testing"

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
