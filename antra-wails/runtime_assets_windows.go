//go:build windows

package main

import (
	"embed"
)

//go:embed all:runtime
var runtimeAssets embed.FS

var bundledBackendMemo backendPathMemo

func ensureBundledBackend() (string, error) {
	const backendAsset = "runtime/backend/VelaBackend.exe"
	return bundledBackendMemo.resolve(func() (string, error) {
		return ensureExtractedBackend(
			runtimeAssets,
			backendAsset,
			getAppDataDir(),
			"VelaBackend.exe",
			0755,
		)
	})
}
