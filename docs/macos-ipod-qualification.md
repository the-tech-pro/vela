# macOS mounted-iPod qualification record

Status: **NOT RUN — RELEASE BLOCKED**

This is the mandatory evidence record for mounted-volume iPod support on native
Apple Silicon and Intel macOS 12+ builds. Unit tests, fixture volumes, Windows
results, and successful compilation do not satisfy this gate. Do not change the
status until every required supervised scenario has passed on physical devices
using the exact signed/notarized release candidates.

The experimental Classic 6G/6.5G capacity-unlock, DFU/WTF observation, raw HFS+
inspection, NOR workflow, and iTunes handoff are Windows-only and are excluded
from this macOS record.

## Owner-supplied test inventory

Record before testing:

- release tag and SHA-256 of each tested DMG;
- app version, signature assessment, and notarization/stapling result;
- Mac model, CPU architecture, exact macOS version, and filesystem;
- iPod model/generation, firmware, filesystem, stable redacted identity, used
  capacity, and free capacity;
- cable/adapter/dock path and whether the iPod was mounted by macOS;
- host backup location and independently verified pre-test recovery copy;
- tester and reviewer names, UTC timestamps, and evidence location.

Never publish serial numbers, FireWire GUIDs, user paths, credentials, personal
media, or complete device databases. Use owner-created non-copyrighted fixture
media and dedicated test devices.

## Required architecture runs

Complete this entire sequence once with the notarized arm64 DMG on Apple
Silicon and once with the notarized amd64 DMG on Intel. Record `PASS`, `FAIL`,
or `BLOCKED` plus the evidence reference for every numbered item.

1. **Clean install and discovery**
   - Verify the DMG checksum, stapling, and Gatekeeper assessment.
   - Install to a clean disposable account and launch without bypasses.
   - Attach an already initialized supported iPod and confirm exactly one
     mounted device with the expected redacted identity, model, capacity, and
     read-only discovery state.
2. **Browse without mutation**
   - Browse tracks, albums, artists, playlists, podcasts, and smart playlists.
   - Confirm pagination and totals, then prove the device database generation
     and media tree are unchanged.
3. **Backup and verification**
   - Create a full host-side backup through Vela.
   - Deep-verify its catalog and content-addressed blobs, quit/relaunch Vela,
     and verify the offline inventory remains available.
4. **Reviewed sync**
   - Stage only owner-created fixture media, review the plan/details, confirm
     identity and capacity, execute sync, and verify database checksums and
     playback on the physical iPod.
   - Repeat with cancellation before commit and prove no partial database
     publication or orphan process remains.
5. **Same-device restore**
   - Run restore preflight for the exact source identity, review the plan, and
     complete a restore from the verified snapshot.
   - Prove the mandatory additional safety snapshot exists and is verified,
     then verify files, database, playlists, and on-device playback.
6. **Interrupted-operation recovery**
   - Interrupt a supervised operation only at a documented cancellable phase.
   - Relaunch, inspect durable recovery state, reconnect the same device, take
     the offered safe action, and prove the journal reaches a terminal state.
   - Separately confirm that close/cancel is blocked during protected commit
     and durability-flush phases; never cut power during those phases.
7. **Eject and reconnect**
   - Eject through Vela, verify volumes disappear cleanly, disconnect only
     after macOS confirms eject, reconnect, and repeat discovery/browse.
   - Confirm no VelaBackend process, cancellation sentinel, or mounted scratch
     image remains after application shutdown.

## Required negative and compatibility cases

Record each case on both architectures where the fixture is applicable:

- no iPod attached;
- ordinary non-iPod removable volume;
- supported iPod with insufficient free space;
- changed database generation between plan and commit;
- disconnect before commit;
- reconnect with a different stable identity;
- corrupted or incomplete backup blob;
- unsupported/uninitialized device;
- read-only or permission-denied mount;
- application sleep/wake while only read operations are active.

Every case must fail closed with a bounded, actionable message. No test may
silently substitute a different device, skip a backup, ignore identity or
generation drift, or report success after an incomplete durability flush.

## Result records

### Apple Silicon / arm64

- Status: NOT RUN
- DMG/tag/hash:
- Mac/macOS:
- iPod inventory:
- Scenario results and evidence:
- Tester:
- Independent reviewer:
- Open defects:

### Intel / amd64

- Status: NOT RUN
- DMG/tag/hash:
- Mac/macOS:
- iPod inventory:
- Scenario results and evidence:
- Tester:
- Independent reviewer:
- Open defects:

## Release decision

Release remains blocked unless both architecture records are complete, every
required scenario and negative case is `PASS`, evidence is independently
reviewed, and no open defect can risk data loss, device mismatch, incomplete
recovery, unsafe eject, or an orphaned mutation process.
