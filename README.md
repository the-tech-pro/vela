# Vela

Vela is a desktop application for browsing an Apple Music library and building an organised local music collection. It combines an Apple Music-inspired library interface, persistent local indexing, a resumable concurrent download queue, and local playback and iPod-management foundations.

## Version 2.2.1

- Apple Music is the only connected-library service.
- Apple Music credentials stay on the device and are sent only to Apple's authenticated endpoints.
- The Apple Music library and downloaded collection are stored in persistent local indexes for immediate repeat navigation.
- Full-library indexing reports progress by total track workload. `100%` is shown only after every release has indexed successfully.
- Downloads are grouped into resumable jobs with configurable concurrency, pause, cancel, progress, and history controls.
- Output supports FLAC, ALAC, AAC, and MP3, using the retained non-P2P downloader sources.
- All product features are available in the paid build; there is no supporter key or feature-gated tier.

See [FEATURES.md](FEATURES.md) for the detailed feature inventory, [PRD.md](PRD.md) for product requirements, [design-system.md](design-system.md) for the interface specification, and [the desktop release contract](docs/desktop-builds.md).

## Test the UI

Run:

```text
test-ui.cmd
```

This starts the Svelte demo at `http://127.0.0.1:5173/?demo=1`. The demo does not connect to Apple Music or download audio.

## Build on Windows

Install the build dependencies:

```text
install-build-dependencies.cmd
```

Then build the desktop application:

```text
build-app.cmd
```

The packaged application is written to `antra-wails/build/bin/Vela.exe`.

## Release artifacts and macOS

Production tag automation expects these install files:

- Windows x64: `Vela-Windows-amd64.exe`
- macOS 12+ Apple Silicon: `Vela-macOS-arm64.dmg`
- macOS 12+ Intel: `Vela-macOS-amd64.dmg`

Each release also carries architecture-specific SPDX JSON and provenance files
plus `SHA256SUMS`. This is a release contract, not a claim that a build,
notarization, macOS 12 smoke run, login flow, or physical-device test has
passed. See [the desktop release procedure](docs/desktop-builds.md) for runner,
signing, notarization, SBOM, Gatekeeper, and remaining owner/legal prerequisites.

On macOS, verify the matching DMG checksum, drag `Vela.app` to Applications,
and allow the normal Gatekeeper check; do not disable Gatekeeper or remove
quarantine attributes. Vela keeps its legacy internal data at
`~/Library/Application Support/Antra`, which is not deleted with the app.

Browser-assisted Apple/Amazon login requires Chrome, Edge, Brave, or Chromium.
Safari cannot be used for the automated capture path; manual Apple
authorization/token entry is only a limited fallback. Scheduled Apple Music
sync runs only while Vela is open and awake, not as an OS background service.

macOS iPod operations require a volume mounted by macOS. The experimental
Classic 6G/6.5G capacity-unlock and DFU/WTF workflow is Windows-only and remains
blocked on supervised hardware validation.

## Development checks

```text
python -m unittest discover tests
cd antra-wails/frontend
npm run check
npm run build
```

## Privacy and responsible use

Vela does not include P2P or Soulseek functionality. Provider credentials required by the retained downloader remain local application configuration and are distinct from Apple Music library authentication.

Use Vela only with music and services you are authorised to access. Users are responsible for complying with copyright law and applicable service terms. This repository hosts no copyrighted audio.

## Licence

No distribution licence is currently included. Copyright remains with the rights holder, and this repository does not grant permission to redistribute, resell, or commercially use the software. Commercial customer terms must be supplied before release distribution.
