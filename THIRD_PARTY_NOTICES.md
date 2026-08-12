# Third-Party Notices

## iOpenPod

Vela uses headless device, database, artwork, checksum, backup, and sync
components from [iOpenPod](https://github.com/TheRealSavi/iOpenPod).

Copyright (c) John Gibbons and iOpenPod contributors.

MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Rockbox and mks5lboot

The experimental iPod Classic capacity-unlock workflow is designed around
Rockbox's iPod 6G firmware and the upstream Rockbox Utility/mks5lboot
bootloader process. Vela does not bundle or execute an opaque community
firmware binary, does not replace USB drivers, and does not automate a
bootloader or NOR installation.

The guided workflow pins the official Rockbox Utility 1.5.1 Windows archive
at SHA-256
`3226b5ede00bd7d7a0458af4f5428b8080c7983650e14087b6b4050d6a23c46d`
and its GPL corresponding-source archive at SHA-256
`82e34ed756b4777d117b13c400040622057d5b5ef38138d9fcb373fe8527e073`.
The source archive contains the iPod 6G bootloader/DFU implementation under
`utils/mks5lboot`. Both files come directly from `download.rockbox.org` and
are transferred only after explicit user action.

The reproducible helper recipe pins Rockbox source commit
`2df1172e985c45e9bf7fe3283bbb42dfaa36c735`, applies the pinned Olsro patch,
then applies Vela's byte-exact NOR-readback patch. Rockbox and the resulting
modified helper are GPL-2.0-or-later. The full license is at
`packaging/ipod-unlock/licenses/GPL-2.0-or-later.txt`; exact source, patches,
build script, and lock metadata are under `packaging/ipod-unlock/`.

If a helper binary is distributed, its exact corresponding-source archive,
build manifest, and license must be offered from the same location. The binary
must not be distributed by itself. Rockbox project:
https://www.rockbox.org/

## Olsro iPod SysCfg research

The narrow 2.0.2 compatibility transform and Rockbox debug-menu patch were
audited against Olsro's `reddit-ipod-guides` revision
`1f3d33805259c1c2b58a5076bb3580e86bacdaf1`.

Copyright (c) 2024 Olsro.

The repository is MIT licensed. Its complete notice is copied at
`packaging/ipod-unlock/licenses/Olsro-MIT.txt`. Vela ports only the audited
transformation and validates its byte-level diff; it does not bundle or run
the published SysCfg editor binaries.

## Apple iPod firmware

Apple's iPod Classic 2.0.2 IPSW is proprietary and is not redistributed by
Vela. It can be downloaded from Apple's server only after an explicit user
action and is accepted only at the exact pinned size, SHA-1, and SHA-256.
Neither the IPSW nor iTunes may be included in Vela packages. Apple and iPod
are trademarks of Apple Inc.; the experimental workflow is not Apple factory
support.

## pytsk3 and The Sleuth Kit

Windows desktop builds use pytsk3 20260715 solely for bounded, read-only
identification of attached HFS+ iPods that Windows cannot mount. Vela opens
only removable drive handles, inspects partition/filesystem metadata, and
requires an `iPod_Control` or `iTunes_Control` root before reporting a device.
This path does not extract files and does not expose a write operation.

pytsk3 is distributed under Apache-2.0. Its bundled Sleuth Kit and talloc
components retain their upstream IBM Public License, Common Public License
1.0, and LGPL notices. The exact wheel license files must remain in Windows
packages and release SBOMs.

Upstream projects:

- https://github.com/py4n6/pytsk
- https://github.com/sleuthkit/sleuthkit

## FFmpeg and FFprobe

Desktop packages require owner-supplied, architecture-native FFmpeg and
FFprobe binaries. FFmpeg licensing depends on the exact build configuration:
it may be LGPL or GPL and may include components with additional obligations.
The release SBOM must record the exact version, build configuration, upstream
source, checksum, license text, and corresponding-source offer/location for
the binaries actually packaged. No binary is approved merely by being present
in `VELA_TOOLS_DIR`.

Upstream project: https://ffmpeg.org/

## Chromaprint / fpcalc

Desktop packages require an owner-supplied, architecture-native `fpcalc` from
Chromaprint. The release SBOM must record its exact version, upstream source,
checksum, applicable license, license text, and corresponding-source
obligations for the packaged binary.

Upstream project: https://acoustid.org/chromaprint

## Release SBOM and provenance scope

Tag automation generates an architecture-specific SPDX JSON inventory from
each packaged application, then augments it with the final install-artifact
hash and validated owner evidence for each media-tool input. It also generates
a GitHub/Sigstore provenance bundle for each install artifact. `SHA256SUMS`
covers the install files, SBOMs, and provenance bundles. These files are
technical release records, not legal approval and not proof that a third-party
binary is redistributable.

The owner must review each generated inventory and add or verify the exact
media-tool versions, checksums, FFmpeg configuration, applicable licenses,
copyright notices, upstream/source URLs, and corresponding-source offers. No
workflow metadata may contain a WVD file, browser cookie, OAuth or service
token, signing certificate/private key, notary credential, or dumped runner
environment.

## Distribution status

These notices are an inventory aid, not legal approval. Distribution remains
blocked pending owner/legal review of GPL-family dependencies, Widevine/DRM
authorization and service terms, and the repository/product distribution
license. Vela must never package WVD device files, cookies, credentials,
tokens, or signing secrets.

Production distribution also remains externally blocked on owner-controlled
Apple Developer signing/notarization access, protected release environments,
approved native FFmpeg/FFprobe/fpcalc inputs and source offers, native
arm64/Intel and macOS 12 runner coverage, and supervised iPod hardware-matrix
results. A successful build, signature, notarization, SBOM, or provenance
attestation does not satisfy those legal or hardware gates.
