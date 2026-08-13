# Desktop build and macOS release procedure

Vela's desktop artifacts are native per architecture. The macOS arm64 and
Intel builds are separate products; this repository does not relabel two
independent binaries as universal2. This document defines the production
contract; it does not record that a release, notarization, macOS 12 smoke run,
or hardware test has passed.

## Approved media-tool inputs

Every desktop build requires an owner-approved, architecture-matched directory
containing `ffmpeg`, `ffprobe`, and `fpcalc` (`.exe` on Windows). Vela never
downloads these tools during a build. Set:

```text
VELA_TOOLS_DIR=/absolute/path/to/native/tools
VELA_TOOLS_CHECKSUMS=/absolute/path/to/SHA256SUMS
VELA_RELEASE_OWNER_METADATA=/absolute/path/to/owner-metadata.json
```

The checksum manifest uses the normal `sha256sum` format. Keep its filenames
identical to the files in `VELA_TOOLS_DIR`. Record upstream URL, version,
architecture, checksum, license, configuration flags, and source-offer/source
location in the release SBOM. `packaging/tools/checksums.example.sha256` is a
template, not an approved manifest. The owner metadata must follow
`packaging/release/owner-metadata.example.json`, replace every placeholder, and
name files relative to `VELA_TOOLS_DIR`. The release utility validates its
URLs, SPDX license expressions, source offer, build configuration, and exact
file hashes, and requires evidence for `ffmpeg`, `ffprobe`, and `fpcalc`; the
placeholder example intentionally fails validation.

The Windows release workflow likewise requires a native self-hosted runner
labelled `Windows,amd64`, with approved inputs at
`C:\vela-tools\amd64\bin` and its manifest at
`C:\vela-tools\amd64\SHA256SUMS`. Its owner evidence is
`C:\vela-tools\amd64\owner-metadata.json`. Native macOS owner evidence is
`/opt/vela-tools/<arm64|amd64>/owner-metadata.json`.

## Owner prerequisites

Before creating a `v*` tag, the repository owner must provide:

- Native self-hosted build runners labelled `self-hosted,macOS,arm64` and
  `self-hosted,macOS,amd64`. The label is a routing promise, so runner setup
  must also verify the actual `uname -m`.
- Native macOS 12 smoke runners carrying those labels plus `macOS-12` and
  `vela-smoke`. Each smoke runner needs an interactive, logged-in desktop in a
  dedicated disposable account and the owner-reviewed production UI harness
  described below.
- A native Windows runner labelled `self-hosted,Windows,amd64`.
- The approved native media tools and checksum manifests at the paths above.
  Build runners must not fetch an unreviewed FFmpeg, FFprobe, or fpcalc.
  Every build and signing runner also needs the matching validated
  `owner-metadata.json`; metadata and checksums must describe the same files.
- A repository variable `VELA_BUNDLE_ID` containing the owner-approved reverse
  DNS identifier. `com.example.vela` is rejected. A repository variable
  `VELA_ICON_ICNS` must point to the owner-approved `.icns` file provisioned on
  both native build runners. The repository variable
  `VELA_MACOS12_SMOKE_HARNESS` must name the executable production UI harness
  on both macOS 12 smoke runners. Set the optional repository variable
  `VELA_SMOKE_REQUIRE_IPOD=1` only when every smoke runner has the required
  mounted fixture device.
- A protected `macos-release` environment restricted to version tags, with
  required reviewers and the environment secrets `VELA_CODESIGN_IDENTITY` and
  `VELA_NOTARY_PROFILE`. The matching Developer ID certificate/private key and
  notarytool keychain profile must already exist on both release runners.
- A protected `windows-release` environment and owner-reviewed Windows build
  tools.
- GitHub artifact-attestation support for the repository plan, with Actions
  permitted to request an OIDC token and write attestations.

Do not place certificate exports, private keys, App Store Connect credentials,
notary credentials, media-service credentials, cookies, tokens, WVD files, or
device files in the repository or ordinary workflow artifacts. The workflows
scope notarization secret values to the signing step; SBOM, provenance, smoke,
and publishing steps receive no signing secret as an input or environment
variable.

## Native macOS commands

Run these commands on each matching Mac (Python 3.11, Go 1.23, Node 18, Wails
2.12, Xcode command-line tools):

```bash
python3 -m pip install -r requirements-desktop.txt
go install github.com/wailsapp/wails/v2/cmd/wails@v2.12.0
npm ci --prefix antra-wails/frontend

export PATH="$(go env GOPATH)/bin:$PATH"
export VELA_TOOLS_DIR="/opt/vela-tools/arm64/bin"       # use amd64 on Intel
export VELA_TOOLS_CHECKSUMS="/opt/vela-tools/arm64/SHA256SUMS"
export VELA_RELEASE_OWNER_METADATA="/opt/vela-tools/arm64/owner-metadata.json"
export VELA_BUNDLE_ID="OWNER_APPROVED_REVERSE_DNS_ID"
export VELA_ICON_ICNS="/opt/vela-release/Vela.icns"
export MACOSX_DEPLOYMENT_TARGET=12.0
python3 build_desktop.py --build-mode release --target-arch arm64  # amd64 on Intel
```

The result is `antra-wails/build/bin/Vela.app`. Its Python backend is a nested
PyInstaller bundle at `Contents/Helpers/VelaBackend.app`; its executable code,
frameworks, and resources remain in their macOS-mandated bundle locations and
are not extracted into a writable temporary directory at launch.

An unsigned manual run of `.github/workflows/macos.yml` uses fresh
GitHub-hosted native runners (`macos-14` for arm64 and `macos-15-intel` for
amd64). It installs Homebrew FFmpeg/FFprobe and downloads the pinned official
Chromaprint fpcalc binary only for development, records their exact hashes and
build metadata, builds and validates both native apps, ad-hoc signs them, runs
the repository smoke baseline, and uploads:

```text
Vela-development-macOS-arm64/Vela-macOS-arm64.dmg
Vela-development-macOS-amd64/Vela-macOS-amd64.dmg
```

These development DMGs prove that native packaging and launch paths work on
clean Macs. They are not Developer ID signed, notarized, stapled, or approved
for distribution. Protected release builds still use the owner-controlled
native runners and inputs listed above.

For an approved release identity and preconfigured `notarytool` keychain
profile:

```bash
export VELA_CODESIGN_IDENTITY="Developer ID Application: OWNER (TEAMID)"
export VELA_NOTARY_PROFILE="VELA_NOTARY_KEYCHAIN_PROFILE"
export VELA_NOTARIZE=1
bash packaging/macos/sign-and-package.sh
```

The script signs nested Mach-O code inside-out, signs the app and DMG, submits
with `notarytool`, and staples both. It intentionally does not use App Sandbox
and does not use `codesign --deep` as a substitute for explicit nested signing.
Set `VELA_ICON_ICNS` before the build to copy an approved `.icns` resource.

## Tag release flow and artifact contract

`.github/workflows/macos.yml` is reusable. A normal unsigned invocation builds
and archives `Vela.app` separately on native arm64 and Intel runners. The tag
release workflow calls it with protected packaging enabled:

1. Reject any signing request whose ref is not exactly
   `v<antra.__version__>`.
2. Build and test the unsigned app on each native architecture without loading
   the signing environment.
3. Enter `macos-release`, restore the architecture-matched app, sign nested
   code, notarize and staple the app and DMG, and create the normalized DMG.
4. Generate an SPDX JSON package scan from the signed app, then augment it
   fail-closed with the final DMG hash and validated owner evidence for the
   exact media-tool inputs. Generate a GitHub/Sigstore provenance bundle for
   the DMG. These actions run after signing and receive no signing/notarization
   secret.
5. Run the matching native macOS 12 smoke contract.

The final release job waits for the Windows job and the complete reusable
macOS workflow, including both architecture legs and both macOS 12 smoke legs.
It then downloads only artifacts named `Vela-release-*`, rejects a missing or
unexpected file, creates `SHA256SUMS`, creates a draft GitHub release with the
complete asset set, and publishes it. Publishing fails closed if that tag
already has a release, preventing an in-place sequence of partially replaced
public assets. No public release asset is uploaded by an earlier job.

Public release files are exactly:

```text
Vela-Windows-amd64.exe
Vela-Windows-amd64.spdx.json
Vela-Windows-amd64.provenance.json
Vela-macOS-arm64.dmg
Vela-macOS-arm64.spdx.json
Vela-macOS-arm64.provenance.json
Vela-macOS-amd64.dmg
Vela-macOS-amd64.spdx.json
Vela-macOS-amd64.provenance.json
SHA256SUMS
```

The SBOM is an automated binary/app inventory, not a legal conclusion. It must
be reviewed and supplemented with the owner-controlled media-tool metadata,
licenses, build options, source offers, and copied notices listed below.
Provenance bundles describe the GitHub build subject and digest; they must not
contain runner environment dumps or release secrets.

## Validation

Before release, run on both arm64 and Intel:

```bash
python3 packaging/ipod-unlock/verify_release_inputs.py
python3 packaging/release/release_metadata.py validate-owner \
  --metadata "$VELA_RELEASE_OWNER_METADATA" \
  --media-root "$VELA_TOOLS_DIR"
python3 -m pytest -q
(cd antra-wails && go test ./...)
(cd antra-wails/frontend && npm ci && npm run check && npm run build)
codesign --verify --deep --strict --verbose=2 antra-wails/build/bin/Vela.app
spctl --assess --type execute --verbose=2 antra-wails/build/bin/Vela.app
xcrun stapler validate dist/macos/*/*/Vela-*-macOS-*.dmg
```

GitHub's architecture matrix requires native self-hosted runners labelled
`macOS,arm64` and `macOS,amd64`, with approved tools pre-provisioned under
`/opt/vela-tools`. Signing/notarization is protected by the `macos-release`
environment and uses only the secret names `VELA_CODESIGN_IDENTITY` and
`VELA_NOTARY_PROFILE`.

The deployment target is not runtime proof. Newer hosted runners cannot
replace macOS 12 validation, and a queued job is not evidence of a pass.

## macOS 12 smoke harness contract

Each `macOS-12,vela-smoke` job first checks the actual OS major version and CPU,
validates the stapled DMG with Gatekeeper, mounts it read-only, copies
`Vela.app` to a fresh temporary install directory, and assesses the copied app.
It then runs `packaging/macos/smoke-test.sh`. That repository-owned baseline
checks native app/helper slices, one-shot and long-lived correlated backend IPC,
bundled media-tool resolution and decoding/probing of a generated audio fixture,
read-only mounted-iPod discovery, frontend DOM readiness, clean quit, and orphan
cleanup. A lane can set `VELA_SMOKE_REQUIRE_IPOD=1` when a
mounted fixture device is mandatory.

The job then requires the executable named by
`VELA_MACOS12_SMOKE_HARNESS` and invokes:

```text
/usr/local/bin/vela-macos12-smoke --app <absolute Vela.app> --arch <arm64|amd64>
```

That owner-maintained harness implements the interface above. It returns zero
only after it has, in the disposable GUI account:

- launched the supplied app without bypassing Gatekeeper;
- observed the Wails window and backend IPC become ready;
- exercised Chromium discovery/CDP capture against a local login fixture;
- completed one non-copyrighted local fixture download/transcode, cancelled a
  second operation, and verified no child process survived;
- played non-copyrighted fixture media through the application player;
- saved a harmless preference, relaunched, and verified persistence;
- completed an owner-controlled sleep/wake cycle with a scheduled wake and
  verified that the UI/backend recovered; and
- performed read-only discovery of a mounted fixture iPod volume when that
  runner is designated for device coverage;
- quit cleanly with no surviving Vela or VelaBackend process.

The harness must never use a production account, real service credential,
signing credential, personal media library, or destructive iPod operation. It
must redact app/backend output, keep fixture paths outside a real user profile,
and return nonzero for a crash, timeout, IPC failure, failed cancellation, or
unclean shutdown. Physical-device coverage and interactive browser login still
require separately recorded supervised tests; the harness must not claim them
when no fixture/device is attached.

## Clean installation and Gatekeeper

Choose the DMG matching the Mac: `arm64` for Apple Silicon and `amd64` for
Intel. Download that DMG and `SHA256SUMS` from the same release, then verify the
single matching line, for example:

```bash
grep ' Vela-macOS-arm64.dmg$' SHA256SUMS | shasum -a 256 -c -
```

Open the DMG and drag `Vela.app` to `/Applications`. Launch it from Finder so
macOS performs its normal Developer ID, notarization, and Gatekeeper checks.
Do not instruct users to remove quarantine attributes or disable Gatekeeper.
If macOS cannot verify the app, stop and treat the artifact as failed rather
than using a bypass. Release owners can additionally run:

```bash
xcrun stapler validate /Applications/Vela.app
spctl --assess --type execute --verbose=2 /Applications/Vela.app
```

Vela retains its legacy internal data location at
`~/Library/Application Support/Antra`. It contains configuration, local
indexes, history, operation journals, staging state, and iPod backups. Removing
`Vela.app` does not remove this data; do not delete it during an upgrade or
recovery without a separate verified backup.

## macOS operational limits

- Browser-assisted Apple Music and Amazon login requires an installed
  Chromium-family browser (Chrome, Edge, Brave, or Chromium) because capture
  uses the Chrome DevTools Protocol. A packaged build cannot download
  Playwright Chromium on demand. Safari is not a compatible capture browser;
  Apple authorization and Music User Token fields can be entered manually,
  but that is a limited fallback rather than automated Safari capture, and it
  does not create a general Safari fallback for every provider.
- Scheduled Apple Music sync is an in-process scheduler. It runs only while
  Vela is open and its process is awake. Vela does not install a launch agent
  or background service, and a missed time while the app is quit or the Mac is
  asleep is not promised to run later. Use **Sync Now** after reopening.
- macOS iPod browse, backup, restore, migration, sync, and eject paths require
  an iPod volume mounted by macOS. Discovery and browsing are read-only;
  mutations retain their review, backup, identity, and confirmation gates.
- Raw HFS+ metadata discovery on an unmounted device is a Windows compatibility
  path, not a macOS requirement. The experimental Classic 6G/6.5G capacity
  unlock, including DFU/WTF observation and manual iTunes handoff, is
  Windows-only. It is not available from the macOS build.

## Release/SBOM checklist and unresolved blockers

- Inventory Python wheels, Go modules, npm packages, iOpenPod modules, FFmpeg,
  FFprobe, Chromaprint/fpcalc, Windows pytsk3/Sleuth Kit components, optional
  Rockbox helper inputs/outputs, and copied notices with versions and hashes.
- Windows packages must retain pytsk3 20260715's wheel metadata and its
  Apache-2.0, IBM Public License, Common Public License 1.0, and talloc LGPL
  license files. Its raw HFS+ path remains metadata-only and read-only.
- Capture FFmpeg build configuration and determine whether it is LGPL or GPL;
  codecs/options can change the obligations. Provide corresponding source and
  notices before distribution.
- Verify Chromaprint/fpcalc's applicable license and source obligations for the
  exact binary.
- Retain iOpenPod's MIT notice and verify the packaged headless module list.
- Run `python packaging/ipod-unlock/verify_release_inputs.py`. If the optional
  Rockbox SysCfg helper is distributed, publish its exact GPL corresponding
  source archive, build manifest, and full license beside the binary. Never
  package the Apple IPSW, iTunes, or Olsro's opaque binaries.
- Keep the official Rockbox Utility 1.5.1 Windows and source-package pins in
  `packaging/ipod-unlock/artifact-lock.json` synchronized with runtime
  metadata. The source archive must retain `utils/mks5lboot`; if Vela ever
  mirrors the Windows package instead of linking to Rockbox, offer that exact
  GPL source from the same location.
- Keep capacity unlock marked Experimental until every supervised row and
  failure scenario in `docs/ipod-capacity-unlock-hardware-matrix.md` passes.
- Do not bundle WVD files, browser cookies, OAuth tokens, credentials, signing
  material, or notarization profiles.
- Review every generated SPDX file and provenance subject/digest before
  publishing. Automated generation does not supply missing third-party legal
  metadata and does not prove the runner labels, physical hardware, login
  flows, or macOS 12 harness passed in any prior release.
- Owner/legal approval remains required for GPL-family dependencies, the
  repository's missing/unclear distribution license, and all commercial terms.
- Widevine/DRM and service access require explicit user authorization and legal
  review. Do not market Vela as bypassing DRM, subscriptions, regions, or terms.
- External production blockers also include Apple Developer account/certificate
  ownership, notarization access, protected-environment approval, native
  arm64/Intel and macOS 12 runner availability, the owner-maintained smoke
  harness, exact media-tool provenance/source offers, supervised iPod hardware
  results recorded in `docs/macos-ipod-qualification.md`, and an approved
  icon/bundle identifier.

These are release blockers. Successful compilation, signing, or notarization
does not make the product legally or operationally release-ready.
