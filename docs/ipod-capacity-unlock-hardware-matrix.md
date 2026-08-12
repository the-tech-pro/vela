# Experimental iPod capacity-unlock hardware gate

Status: **NOT PASSED — feature remains Experimental**

This gate cannot be satisfied by unit tests, emulators, virtual machines, or
synthetic SysCfg data. Every run must be supervised on owned physical hardware
with recovery materials available. No unchecked run may be described as
supported, factory-tested, or guaranteed recoverable.

## Required device profiles

Complete both Windows 10 and Windows 11 runs for each profile:

- `HW-6G-80-S`: MB029, silver 80 GB, original firmware 1.1.2.
- `HW-6G-80-B`: MB147, black 80 GB, original firmware 1.1.2.
- `HW-6G-160-S`: MB145, silver original-thick 160 GB, firmware 1.1.2.
- `HW-6G-160-B`: MB150, black original-thick 160 GB, firmware 1.1.2.
- `HW-65G-120-S`: MB562, silver 120 GB, original firmware 2.0.1.
- `HW-65G-120-B`: MB565, black 120 GB, original firmware 2.0.1.

At minimum, distribute representative iFlash storage across the runs:

- single-card adapter at 256 GB;
- dual/quad adapter at 512 GB;
- at least one 1 TB configuration where the adapter/device combination claims
  support;
- original hard drive control run for each generation.

Record the adapter model/revision, card manufacturer/model/capacity, reported
sector size, cable, USB controller, host build number, iTunes version, helper
archive SHA-256, corresponding-source SHA-256, Rockbox Utility/source
SHA-256 values, and Vela version for every run.

## Required successful run

For every device/OS row, archive redacted evidence that:

1. Eligibility accepts only the exact mounted device and rejects a mismatched
   model, firmware, filesystem, USB identity, or unstable identity source.
2. Vela creates and deep-verifies a fresh filesystem snapshot.
3. The original SysCfg parses and two copies in independent host locations
   reread to the same SHA-256.
4. The candidate changes only the approved tags and the staged iPod copy is
   byte-identical.
5. Official Rockbox Utility 1.5.1 and its `utils/mks5lboot`-containing GPL
   source archive match the locked size and SHA-256 before the manual
   bootloader step.
6. The pinned, source-built Rockbox helper writes NOR and its direct readback
   reports a byte-exact match.
7. A separately exported post-flash SysCfg dump matches the candidate exactly.
8. Windows read-only USB inspection observes the expected DFU/WTF identity
   without changing a driver.
9. The user-controlled iTunes restore selects Apple's exact 2.0.2 IPSW
   (`a12f25067a821850979efe8222de6e2bb98eba985ba21f61abe386355c6655b4`).
10. Postflight proves firmware 2.0.2, target model identity, the original
   FireWire identity, FAT32, writable storage, and healthy capacity.
11. A normal Vela sync writes a valid library, playback works, all installed
    capacity is visible, and safe eject/remount succeeds.

## Required failure and recovery runs

Perform each scenario on every generation and on both Windows versions where
the scenario is OS-sensitive:

- Cancel at each pre-NOR state; verify no later action runs and the original
  iPod remains bootable.
- Disconnect during backup, artifact download, SysCfg selection, candidate
  staging, DFU wait, iTunes wait, and postflight; restart Vela and resume the
  exact persisted session.
- Attempt cancellation at the manual NOR window and every post-NOR state;
  verify both UI and backend reject it.
- Supply corrupt/truncated SysCfg, wrong model/region preset, changed unknown
  tag, incorrect staged candidate, and incorrect NOR readback; verify each
  fails closed before advancement.
- Supply wrong-size/wrong-hash Apple, Olsro, Rockbox Utility, mks5lboot source,
  and helper-source artifacts; verify temporary files are removed and prior
  valid files remain unchanged.
- Attach zero, one, and multiple recovery-mode Apple devices; verify Vela
  advances only for one supported identity and never guesses.
- Exercise failed DFU timing, WTF mode, iTunes restore rejection, ordinary
  downgrade recovery, safe eject failure, and app restart after NOR commit.
- Verify the original SysCfg copies remain available after every failure.
- Confirm the documented external-hardware recovery path with the supervising
  hardware specialist. Do not intentionally corrupt NOR solely to create a
  test result.

## Evidence and sign-off

Each run record must contain:

- unique run ID and UTC timestamps;
- two-person supervision names/signatures;
- redacted eligibility and operation journal;
- all relevant SHA-256 values;
- screenshots or video of physical handoff points;
- Windows Device Manager/USB evidence;
- iTunes result and postflight report;
- observed capacity, filesystem check, sync/playback/eject results;
- failure details and recovery outcome;
- pass/fail disposition with linked defect IDs.

The release owner may remove the Experimental label only when every required
profile has a passing Windows 10 and Windows 11 run, all required failure
scenarios pass, no unresolved safety-severity defect remains, and legal review
approves distribution of the exact GPL helper plus corresponding source.

Hard NOR corruption can still require an external programmer or board-level
repair even after this matrix passes. Vela must never promise a guaranteed
unbrick path.
