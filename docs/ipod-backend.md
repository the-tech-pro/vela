# Vela 2.2 iPod backend contract

Vela integrates iOpenPod 1.67.1 only through `antra.core.ipod_service`.
Neither the Python service nor the packaged backend imports `iopenpod.gui` or
PyQt.

## Safety model

- Watch, scan, and browse are read-only. They do not call iOpenPod's
  `scan_for_ipods()` enrichment path because that path can update SysInfo and
  authority files. Vela runs discovery plus scanner identification phases 1–3
  and deliberately skips VPD persistence and `enrich()`.
- On Windows, scan/watch also inspect removable raw volume metadata with
  pytsk3 when the OS cannot mount a Mac-formatted iPod. Discovery is bounded
  to partition metadata and root names, requires HFS+ plus `iPod_Control` or
  `iTunes_Control`, and exposes `filesystem_accessible: false`,
  `raw_read_only: true`, and `access_state: mac_formatted_read_only`.
  No file extraction or writable raw-device API is exposed.
- Missing serial/FireWire identity produces `browse_only: true` and
  `needs_preparation: true`. There is no automatic preparation operation.
- Sync is limited to supported iPod Classic, Mini, and Nano identities. Touch,
  Shuffle, uncertain filesystems, read-only mounts, incomplete volume identity,
  and insufficient storage fail closed.
- A sync plan is stored under app data as a short-lived opaque ID. It is bound
  to the exact device ID, canonical mount path, volume identity, content-backed
  database generation, exact source file state, staging ID, and storage
  estimate.
- Execution requires explicit confirmation. Vela recomputes and compares the
  reviewed plan, creates and verifies a content-addressable `BackupManager`
  snapshot outside the iPod, revalidates again, then delegates the reviewed
  plan to the typed `SyncEngine`.
- One backup/sync mutation is allowed at a time. Active downloads block device
  mutation; Apple indexing is checkpointed/stopped and resumed afterwards.
- Provider output is downloaded and validated locally. The `stage` operation
  records completed local files and opens a review path; providers never write
  directly to `iPod_Control`.

## NDJSON protocol

All events contain `protocol_version: 1`. Invoke the backend with
`--ipod-operation <operation> --ipod-request <json-file>`. The request file is
a JSON object with `protocol_version: 1`; it may include a host-side
`cancel_path`. Progress events are bounded:

```json
{"type":"ipod_progress","protocol_version":1,"stage":"backup:hashing","current":4,"total":10,"message":"..."}
```

Operations:

- `scan`: read-only device summaries, including an explicit inaccessible HFS+
  state for qualifying Windows raw devices.
- `watch`: long-running connect/change/disconnect events.
- `browse`: `mount_path`, resource (`tracks`, `albums`, `artists`, `playlists`,
  `podcasts`, `smart_playlists`), one-based `page`, and `page_size` up to 250.
- `stage`: `mount_path` and completed local `completed_files`. Wails supplies the
  configured `library_root`; callers cannot override it. Missing, skipped, or
  out-of-root files are rejected and provider temporary files are never staged.
- `plan`: `mount_path`, exact local `source_files`, and optional `staging_id`.
- `plan-details`: reviewed `plan_id`, group, one-based `page`, and `page_size`
  up to 100. It reads the immutable reviewed snapshot and never recomputes or
  mutates the device.
- `backup`: reviewed `plan_id`.
- `execute`: reviewed `plan_id` and `confirmed: true`; always creates a fresh
  mandatory backup even if `backup` was called separately.
- `cancel`: writes the current operation's cancellation sentinel.
- `eject`: exact verified `mount_path`.
- `backup-devices`: offline backup archives and aggregate repository size.
- `backup-snapshots`: bounded snapshot inventory for an `archive_id`.
- `backup-details`: manifest metadata and identity/compatibility information for
  an exact `archive_id` plus `snapshot_id`.
- `backup-verify`: streams every catalog entry and unique blob through full
  SHA-256 verification.
- `backup-manual`: explicit fresh snapshot of the selected mounted device.
- `backup-note`: bounded user note for an exact snapshot.
- `backup-export`: verified regular-file export to a user-selected host folder.
- `backup-delete`: explicit confirmed snapshot deletion; iOpenPod garbage
  collects content-addressed blobs that no retained snapshot references.
- `restore-preflight`: validates the snapshot/catalog/blobs, exact original
  device identity, filesystem naming rules, database generation, and capacity;
  returns an expiring reviewed plan.
- `restore`: confirmed journaled same-device restore. It creates and deep
  verifies a new host-side safety snapshot before entering commit.
- `migration-preflight`: builds a reviewed addition-only plan for a compatible,
  separately initialized replacement device.
- `migration`: confirmed migration through iOpenPod's target-specific sync and
  database/checksum builders.
- `recovery-state`: durable interrupted-operation state and safe next actions.
- `capacity-unlock-eligibility`: read-only Windows/model/firmware/FAT32/identity/
  USB/write-read/storage-health gate.
- `capacity-unlock-start`: confirmed creation of a device-bound persisted
  experimental session after all risk acknowledgements.
- `capacity-unlock-advance`: revision-checked state-machine actions for backup,
  artifact verification, manual Rockbox/NOR/DFU/iTunes attestations, recovery,
  resume, and pre-NOR cancellation.

## Wails API

- `StartIPodWatcher()` / `StopIPodWatcher()`
- `ScanIPodDevices()`
- `BrowseIPodLibrary(IPodBrowseRequest)`
- `StageDownloadsForIPod(IPodStageRequest)`
- `CreateIPodSyncPlan(IPodPlanRequest)`
- `GetIPodSyncPlanDetails(IPodPlanDetailsRequest)`
- `CreateIPodBackup(planID)`
- `ExecuteIPodSync(IPodExecuteRequest)`
- `ListIPodBackupDevices()`
- `ListIPodBackupSnapshots(IPodBackupSnapshotsRequest)`
- `GetIPodBackupSnapshot(IPodBackupSnapshotRequest)`
- `VerifyIPodBackup(IPodBackupVerifyRequest)`
- `CreateManualIPodBackup(IPodManualBackupRequest)`
- `UpdateIPodBackupNote(IPodBackupNoteRequest)`
- `ExportIPodBackup(IPodBackupExportRequest)`
- `DeleteIPodBackup(IPodBackupDeleteRequest)`
- `PreflightIPodRestore(IPodRestorePreflightRequest)`
- `StartIPodRestore(IPodRestoreRequest)`
- `PreflightIPodMigration(IPodMigrationPreflightRequest)`
- `StartIPodMigration(IPodMigrationRequest)`
- `GetIPodRecoveryState()`
- `GetIPodCapacityUnlockEligibility(IPodCapacityUnlockEligibilityRequest)`
- `StartIPodCapacityUnlock(IPodCapacityUnlockStartRequest)`
- `AdvanceIPodCapacityUnlock(IPodCapacityUnlockAdvanceRequest)`
- `InspectIPodRecoveryUSB()`
- `PickIPodRecoveryFile()`
- `CancelIPodOperation()`
- `CancelIPodOperationByID(operationID)`
- `EjectIPod(mountPath)`
- `GetThirdPartyNotices()`

Long-running watcher, backup, restore, migration, and capacity-unlock messages
are emitted on `ipod-event`. Mutation events carry an operation envelope with
the host `operation_id`, kind, device/snapshot identifiers when applicable,
phase, `can_cancel`, bounded progress, and structured recovery data. Once a
restore/database/NOR commit boundary is observed, cancellation is rejected in
both Go and Python. Wails also blocks application close while a protected iPod
commit or durability flush is active.

## Recovery meanings

**Full file restore** is the complete regular-file snapshot supported by
iOpenPod. It does not restore a partition table, NOR, SysCfg, firmware, or a
factory image. It is allowed only for the exact stable source identity
(serial plus FireWire GUID), after an additional verified host-side safety
snapshot and a final mount/volume/database revalidation.

**Compatible-device migration** restores content rather than raw device state.
The target must be an initialized compatible Classic, Mini, or Nano profile.
Vela excludes source identity material, stages verified media and bounded
metadata, and asks iOpenPod to create an addition-only plan that regenerates
the target's database, checksums, artwork, and playlists. Unproven
compatibility fails closed.

Both workflows are journaled atomically. Interrupted restore states preserve
the operation ID, safety snapshot, expected reconnect identity, commit
boundary, and recovery instructions. `RestoreIncompleteError` and
`RestoreDurabilityPendingError` are surfaced as durable recovery states rather
than being reported as ordinary success.

## Experimental Classic 6G/6.5G capacity unlock

The capacity-unlock workflow is Windows-only and remains experimental. The
Advanced section remains visible so unsupported devices receive an explicit
reason; starting the workflow is enabled only for a native, non-virtualized
Windows FAT32 WinPod with stable serial and FireWire provenance, Apple
normal-mode USB identity, healthy/writable storage evidence, no active device
mutation, and one of the explicit original model/firmware pairs:

- Classic 6G 80 GB: MB029 or MB147 on firmware 1.1.2.
- Classic 6G original 160 GB: MB145 or MB150 on firmware 1.1.2.
- Classic 6.5G 120 GB: MB562 or MB565 on firmware 2.0.1.

Opening the wizard performs no download or mutation. A confirmed session first
creates and deeply verifies a normal Vela backup. Each artifact then requires
an explicit Download or local-file validation action. Apple's unmodified
`iPod_35.2.0.2.ipsw` is download-only and must match exactly:

```text
size    61033067
SHA-1   5a7ab72fd7e299118bb0f25adfea1ad4808e1f0a
SHA-256 a12f25067a821850979efe8222de6e2bb98eba985ba21f61abe386355c6655b4
```

The bootloader step uses official Rockbox Utility 1.5.1. Vela requires both
the Windows package and its source archive, which contains
`utils/mks5lboot`, to match the pins in `artifact-lock.json`:

```text
RockboxUtility-v1.5.1.zip
size    13661541
SHA-256 3226b5ede00bd7d7a0458af4f5428b8080c7983650e14087b6b4050d6a23c46d

RockboxUtility-v1.5.1-src.tar.bz2
size    1495776
SHA-256 82e34ed756b4777d117b13c400040622057d5b5ef38138d9fcb373fe8527e073
```

The Olsro source reference and Rockbox patch are pinned to guide revision
`1f3d33805259c1c2b58a5076bb3580e86bacdaf1`; the Rockbox base is commit
`2df1172e985c45e9bf7fe3283bbb42dfaa36c735`. Vela ports only the narrow
2.0.2 compatibility transformation. The parser rejects malformed headers,
duplicate/missing tags, wrong source profiles, and any byte change outside the
approved tags. Two byte-identical original SysCfg copies in separate host
locations are mandatory. The staged candidate and fresh post-flash NOR dump
must each match the device-bound candidate byte for byte.

The rebuilt SysCfg helper is not accepted through a typed hash attestation.
The user must select the helper ZIP, corresponding-source tarball, and
`BUILD-MANIFEST.txt` together. Vela hashes both archives, cross-checks the
manifest's commit/patch/license lock, inspects both archive structures, and
requires the embedded Olsro and Vela patches to match their locked SHA-256
values before recording the manual Rockbox installation.

Rockbox installation, click-wheel actions, the physical NOR flash, DFU entry,
and the destructive iTunes restore are manual. Vela observes supported
recovery USB identities read-only and records explicit attestations; it does
not automate iTunes, replace USB drivers, uninstall software, or install
certificates/proxies. Cancellation is hidden before the unobservable manual
NOR commit window and remains forbidden after commit.

See `packaging/ipod-unlock/README.md` for corresponding-source/build
requirements and `docs/ipod-capacity-unlock-hardware-matrix.md` for the
supervised hardware release gate. Hard NOR corruption may still require
external hardware recovery. This is not factory support and is not a
guaranteed unbrick path.

## Reviewed plan response

`CreateIPodSyncPlan` returns an opaque `plan_id`, `expires_at`,
`review_fingerprint`, compatibility counts (`additions`, `removals`, `updates`,
`conversions`, `playlist_changes`, `warnings`, `unsupported`), a `storage`
object, group descriptors, and up to five item previews per non-empty group.
The full item list is never sent in one Wails payload.

The fixed groups are `additions`, `removals`, `metadata_updates`,
`artwork_updates`, `conversions`, `playlist_effects`, `warnings`, and
`unsupported`. Each item has a stable `item_id`, `group`, and `action`, plus
bounded display fields applicable to that action. Track rows can include
`title`, `artist`, `album`, `description`, `source_path`, `ipod_location`,
`db_track_id`, `estimated_bytes`, `removed_bytes`, `metadata_fields`, and
`conversion`. Playlist rows include `title`, `track_count`, `skipped_count`,
and optional `playlist_id`/`source_path`. Warning rows include `code` and
`message`.

`GetIPodSyncPlanDetails` accepts:

```json
{"plan_id":"opaque","group":"additions","page":1,"page_size":50}
```

It returns `plan_id`, `group`, `page`, bounded `page_size`, `total`, `items`,
and `expires_at`. Execution recomputes and compares both the summary and every
reviewed detail item before backup, so paging does not weaken stale-plan,
device-identity, database-generation, or source-file checks.

`storage` contains `bytes_to_add`, `bytes_to_remove`, `bytes_to_update`,
`net_change_bytes`, `required_free_bytes`, `free_before_bytes`, and
`free_after_bytes`.

## Download-to-iPod handoff

Completed download events expose only canonical files that still exist under
the configured Vela library root:

- `event` with `name: "track_completed"` includes
  `payload.final_file_path` (string or `null`).
- `library_update` includes `completed_files: string[]`.
- `playlist_summary` includes `completed_files: string[]`; persisted
  `HistoryItem` accepts the same field and validates it again in Go.

Only `COMPLETED` results are included. Exact-output `SKIPPED` results, missing
files, directories, out-of-root paths, and provider temporary paths are
omitted. Multi-track jobs return all distinct successful paths. A local
hard-link/copy materialized for the current album or playlist is treated as a
completed final path when it is inside the library.

For an iPod-targeted job, the Svelte worker should collect
`playlist_summary.completed_files`, call:

```json
StageDownloadsForIPod({
  "mount_path":"verified mounted device path",
  "completed_files":["validated final library path"]
})
```

then call `CreateIPodSyncPlan` with the returned `staging_id` and exact
`completed_files`. The backend binds that staging record to the selected
device, mount, file fingerprint, and configured library root. This flow never
accepts an iPod destination path and never writes `iPod_Control` directly.
