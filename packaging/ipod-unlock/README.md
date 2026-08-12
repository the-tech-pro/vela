# iPod Classic capacity-unlock helper

This directory is the corresponding-source and release recipe for the
experimental Classic 6G/6.5G SysCfg helper. Vela does **not** bundle or trust
the opaque community helper binary from the reference guide.

## Locked inputs

`artifact-lock.json` is the source of truth:

- Official Rockbox Utility 1.5.1 for Windows
  (`RockboxUtility-v1.5.1.zip`, 13,661,541 bytes,
  SHA-256 `3226b5ede00bd7d7a0458af4f5428b8080c7983650e14087b6b4050d6a23c46d`).
  Its separately pinned GPL source archive is 1,495,776 bytes with SHA-256
  `82e34ed756b4777d117b13c400040622057d5b5ef38138d9fcb373fe8527e073`
  and contains the bootloader/DFU implementation under `utils/mks5lboot`.
- Rockbox source commit
  `2df1172e985c45e9bf7fe3283bbb42dfaa36c735`, GPL-2.0-or-later.
- Olsro guide revision
  `1f3d33805259c1c2b58a5076bb3580e86bacdaf1`, including the original
  5,827-byte Rockbox patch.
- `vela-rockbox-syscfg-readback.patch`, which rejects an invalid SysCfg
  size/count and requires direct byte-exact NOR comparison after every write.
- Olsro's MIT SysCfg editor source is an audit reference only. Vela ports the
  narrow transformation in Python and never executes the community editor.
- Apple's 2.0.2 IPSW is download-only. It is not source, is not redistributable,
  and must never be placed in a Vela package or repository.

The Vela patch applies **after** the pinned Olsro patch. The original Olsro
code considered any structurally readable SysCfg a successful write. The Vela
overlay compares the complete header and every entry byte directly against
NOR. Vela still requires a separately exported post-flash SysCfg dump and
compares that file byte for byte with the device-bound candidate before
advancing.

Rockbox Utility is used only for the explicit user-controlled bootloader step.
Vela downloads and validates the official package and corresponding source only
after user action; it never invokes the executable, changes USB drivers, or
installs the bootloader itself.

## Build

Use a disposable Linux or WSL environment with Git, curl, GNU make/tar,
Perl, and normal C build prerequisites. The script can build Rockbox's pinned
ARM cross-toolchain when `arm-elf-eabi-gcc` is unavailable. Building the
toolchain can take a long time and requires the upstream archives referenced
by the pinned Rockbox `tools/rockboxdev.sh`.

```bash
JOBS=4 bash packaging/ipod-unlock/build-rockbox-helper.sh \
  /tmp/vela-rockbox-work \
  /tmp/vela-rockbox-output
```

For a prebuilt local Rockbox toolchain:

```bash
ROCKBOX_TOOLCHAIN_PREFIX=/opt/rockbox-arm \
JOBS=4 \
bash packaging/ipod-unlock/build-rockbox-helper.sh \
  /tmp/vela-rockbox-work \
  /tmp/vela-rockbox-output
```

The script refuses existing work/output directories, checks both patch
SHA-256 values before application, checks the exact Git commit, builds the
`ipod6g` normal firmware target, and produces:

- `vela-ipod6g-syscfg-helper.zip`
- `vela-ipod6g-helper-corresponding-source.tar.gz`
- `BUILD-MANIFEST.txt`

Vela requires the user to select all three outputs together. It hashes both
archives, matches them to the manifest, checks every locked build field, and
inspects the helper and corresponding-source archive structures. The embedded
Olsro and Vela patch bytes must also match their locked SHA-256 values before
Vela records the manual installation attestation. Build outputs are
intentionally not committed here.

## Distribution gate

If the helper binary is offered to users, distribute the exact corresponding
source archive, build manifest, and GPL text from the same location and for
the same duration as the binary. Do not publish the binary alone. Preserve
Rockbox and contributor notices and label the build as modified by Vela.

Before any helper release:

1. Verify every lock entry with
   `python packaging/ipod-unlock/verify_release_inputs.py`.
2. Rebuild from an empty directory and archive the complete console log.
3. Verify the helper and corresponding-source hashes on a second machine.
4. Complete the supervised matrix in
   `docs/ipod-capacity-unlock-hardware-matrix.md`.
5. Obtain owner/legal approval for GPL distribution.

Until all hardware rows pass, Vela must continue returning
`experimental: true` and must not claim factory support or a guaranteed
unbrick path.
