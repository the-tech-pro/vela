<script lang="ts">
  import { createEventDispatcher, onDestroy, onMount } from 'svelte';
  import {
    AlertTriangle, Archive, ChevronLeft, ChevronRight, FolderOpen,
    HardDrive, Plus, RefreshCw, Save, ShieldCheck, Trash2,
  } from 'lucide-svelte';
  import {
    CreateManualIPodBackup, DeleteIPodBackup, ExportIPodBackup,
    GetIPodBackupSnapshot, ListIPodBackupDevices, ListIPodBackupSnapshots,
    PickDirectory, UpdateIPodBackupNote, VerifyIPodBackup,
  } from '../../../wailsjs/go/main/App.js';
  import { main } from '../../../wailsjs/go/models';
  import ConfirmationDialog from './ConfirmationDialog.svelte';
  import type {
    IPodBackupDeviceArchive, IPodBackupDevicesResponse, IPodBackupSnapshot,
    IPodBackupSnapshotDetails, IPodBackupVerification, IPodDevice, IPodEventPayload,
  } from '../ipodTypes';
  import {
    parseBackupDevices, parseBackupSnapshotDetails, parseBackupSnapshots,
    parseBackupVerification,
  } from '../ipodTypes';

  export let device: IPodDevice;
  export let ipodEvent: IPodEventPayload | null = null;
  export let operationBusy = false;
  export let demoMode = false;

  const dispatch = createEventDispatcher<{
    select: {
      archive: IPodBackupDeviceArchive | null;
      snapshot: IPodBackupSnapshot | null;
      details: IPodBackupSnapshotDetails | null;
    };
    announce: { message: string };
  }>();
  const pageSize = 10;
  let archives: IPodBackupDeviceArchive[] = [];
  let selectedArchiveId = '';
  let snapshots: IPodBackupSnapshot[] = [];
  let selectedSnapshotId = '';
  let details: IPodBackupSnapshotDetails | null = null;
  let archiveTotal = 0;
  let aggregateRepositorySize = 0;
  let snapshotTotal = 0;
  let snapshotPage = 1;
  let repositorySize = 0;
  let loadingArchives = false;
  let loadingSnapshots = false;
  let loadingDetails = false;
  let error = '';
  let note = '';
  let noteStarting = false;
  let manualStarting = false;
  let verifying = false;
  let exportStarting = false;
  let deleteStarting = false;
  let verification: IPodBackupVerification | null = null;
  let showDeleteConfirmation = false;
  let handledEvent: IPodEventPayload | null = null;
  let archiveRequestId = 0;
  let snapshotRequestId = 0;
  let detailRequestId = 0;
  let destroyed = false;

  $: selectedArchive = archives.find(archive => archive.archive_id === selectedArchiveId) || null;
  $: selectedSnapshot = snapshots.find(snapshot => snapshot.snapshot_id === selectedSnapshotId)
    || details?.snapshot
    || null;
  $: snapshotPages = Math.max(1, Math.ceil(snapshotTotal / pageSize));
  $: if (ipodEvent && ipodEvent !== handledEvent) {
    handledEvent = ipodEvent;
    if (
      ipodEvent.type === 'ipod_operation_ended'
      && ['manual_backup', 'backup_note', 'backup_export', 'backup_delete'].includes(ipodEvent.kind || '')
    ) {
      void loadArchives(true);
    }
  }

  function archiveBelongsToConnectedDevice(archive: IPodBackupDeviceArchive): boolean {
    const stableId = archive.device_meta.stable_device_id;
    return stableId === device.device_id
      || archive.archive_id === device.serial
      || archive.archive_id === device.firewire_guid;
  }

  function formatBytes(bytes: number): string {
    if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1000)));
    return `${(bytes / 1000 ** index).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
  }

  function formatTimestamp(timestamp: string): string {
    const date = new Date(timestamp);
    return Number.isNaN(date.getTime()) ? timestamp || 'Unknown date' : date.toLocaleString();
  }

  function selectedPayload(nextDetails = details) {
    dispatch('select', {
      archive: selectedArchive,
      snapshot: selectedSnapshot,
      details: nextDetails,
    });
  }

  function demoArchives(): IPodBackupDeviceArchive[] {
    return [
      {
        archive_id: device.serial || 'fixture-archive',
        device_name: device.name,
        snapshot_count: 2,
        identity_is_stable: true,
        repository_size_bytes: 19_100_000_000,
        device_meta: {
          stable_device_id: device.device_id,
          model_family: device.model_family,
          generation: device.generation,
        },
      },
      {
        archive_id: 'offline-classic',
        device_name: 'Travel iPod',
        snapshot_count: 4,
        identity_is_stable: true,
        repository_size_bytes: 7_800_000_000,
        device_meta: { model_family: 'iPod Classic', generation: '5th Generation' },
      },
    ];
  }

  function demoBackupDevices(): IPodBackupDevicesResponse {
    const devices = demoArchives();
    return {
      protocol_version: 1,
      total: devices.length,
      truncated: false,
      repository_size_bytes: devices.reduce(
        (total, archive) => total + archive.repository_size_bytes,
        0,
      ),
      devices,
    };
  }

  function demoSnapshots(archive: IPodBackupDeviceArchive): IPodBackupSnapshot[] {
    return [
      {
        snapshot_id: '20260812T120000_000001Z',
        timestamp: '2026-08-12T12:00:00Z',
        archive_id: archive.archive_id,
        device_name: archive.device_name,
        file_count: 2841,
        total_size_bytes: 18_400_000_000,
        reason: 'manual',
        note: 'Known-good library before travel',
        files_added: 42,
        files_removed: 0,
        files_changed: 7,
        device_meta: archive.device_meta,
        is_valid: true,
      },
      {
        snapshot_id: '20260810T093000_000001Z',
        timestamp: '2026-08-10T09:30:00Z',
        archive_id: archive.archive_id,
        device_name: archive.device_name,
        file_count: 2792,
        total_size_bytes: 18_000_000_000,
        reason: 'pre-sync',
        note: '',
        files_added: 18,
        files_removed: 2,
        files_changed: 3,
        device_meta: archive.device_meta,
        is_valid: true,
      },
    ];
  }

  function demoDetails(snapshot: IPodBackupSnapshot): IPodBackupSnapshotDetails {
    return {
      protocol_version: 1,
      archive_id: snapshot.archive_id,
      snapshot: {
        ...snapshot,
        identity_is_stable: true,
        source_verification: 'full_sha256',
        manifest_version: 1,
        snapshot_fingerprint: 'fixture-manifest-fingerprint',
      },
      scope: {
        kind: 'full_regular_file_tree',
        functional_backup: true,
        raw_disk_image: false,
        included_file_count: snapshot.file_count,
        included_bytes: snapshot.total_size_bytes,
        content_verification: 'full_sha256',
        restores_included_tree_exactly: true,
      },
      exclusions: [
        { category: 'filesystem_structure', description: 'Empty directories, partition state, and firmware are not captured.' },
        { category: 'host_metadata', description: 'Permissions, ACLs, extended attributes, and resource forks are not captured.' },
      ],
      repository_size_bytes: archives.find(
        archive => archive.archive_id === snapshot.archive_id,
      )?.repository_size_bytes || 0,
    };
  }

  async function loadArchives(preserveSelection = false) {
    const requestId = ++archiveRequestId;
    loadingArchives = true;
    error = '';
    try {
      const response = demoMode
        ? demoBackupDevices()
        : parseBackupDevices(await ListIPodBackupDevices());
      if (destroyed || requestId !== archiveRequestId) return;
      archives = response.devices;
      archiveTotal = response.total;
      aggregateRepositorySize = response.repository_size_bytes;
      const preserved = preserveSelection
        ? archives.find(archive => archive.archive_id === selectedArchiveId)
        : null;
      const next = preserved
        || archives.find(archiveBelongsToConnectedDevice)
        || archives[0]
        || null;
      if (!next) {
        selectedArchiveId = '';
        snapshots = [];
        selectedSnapshotId = '';
        details = null;
        repositorySize = 0;
        selectedPayload(null);
        return;
      }
      await chooseArchive(next, preserveSelection ? snapshotPage : 1);
    } catch (caught) {
      if (destroyed || requestId !== archiveRequestId) return;
      error = caught instanceof Error ? caught.message : String(caught);
      dispatch('announce', { message: `Backup inventory could not be loaded: ${error}` });
    } finally {
      if (!destroyed && requestId === archiveRequestId) loadingArchives = false;
    }
  }

  async function chooseArchive(archive: IPodBackupDeviceArchive, page = 1) {
    snapshotRequestId += 1;
    detailRequestId += 1;
    selectedArchiveId = archive.archive_id;
    selectedSnapshotId = '';
    details = null;
    verification = null;
    note = '';
    repositorySize = archive.repository_size_bytes;
    selectedPayload(null);
    await loadSnapshots(page);
  }

  async function loadSnapshots(page: number) {
    const archiveId = selectedArchiveId;
    if (!archiveId) return;
    const requestId = ++snapshotRequestId;
    loadingSnapshots = true;
    error = '';
    try {
      const archive = archives.find(item => item.archive_id === archiveId);
      if (!archive) return;
      if (demoMode) {
        const items = demoSnapshots(archive);
        snapshots = items;
        snapshotTotal = items.length;
        snapshotPage = 1;
        repositorySize = archive.repository_size_bytes;
      } else {
        const response = parseBackupSnapshots(await ListIPodBackupSnapshots(
          new main.IPodBackupSnapshotsRequest({
            archive_id: archiveId,
            page,
            page_size: pageSize,
          }),
        ));
        if (destroyed || requestId !== snapshotRequestId || selectedArchiveId !== archiveId) return;
        snapshots = response.items;
        snapshotTotal = response.total;
        snapshotPage = response.page;
        repositorySize = response.repository_size_bytes;
      }
      const next = snapshots.find(item => item.snapshot_id === selectedSnapshotId)
        || snapshots[0]
        || null;
      if (next) await chooseSnapshot(next);
      else {
        selectedSnapshotId = '';
        details = null;
        selectedPayload(null);
      }
    } catch (caught) {
      if (destroyed || requestId !== snapshotRequestId) return;
      error = caught instanceof Error ? caught.message : String(caught);
    } finally {
      if (!destroyed && requestId === snapshotRequestId) loadingSnapshots = false;
    }
  }

  async function chooseSnapshot(snapshot: IPodBackupSnapshot) {
    selectedSnapshotId = snapshot.snapshot_id;
    note = snapshot.note;
    details = null;
    verification = null;
    selectedPayload(null);
    const archiveId = selectedArchiveId;
    const requestId = ++detailRequestId;
    loadingDetails = true;
    error = '';
    try {
      const response = demoMode
        ? demoDetails(snapshot)
        : parseBackupSnapshotDetails(await GetIPodBackupSnapshot(
            new main.IPodBackupSnapshotRequest({
              archive_id: archiveId,
              snapshot_id: snapshot.snapshot_id,
            }),
          ));
      if (
        destroyed
        || requestId !== detailRequestId
        || selectedArchiveId !== archiveId
        || selectedSnapshotId !== snapshot.snapshot_id
      ) return;
      details = response;
      repositorySize = response.repository_size_bytes;
      note = response.snapshot.note;
      selectedPayload(response);
    } catch (caught) {
      if (destroyed || requestId !== detailRequestId) return;
      error = caught instanceof Error ? caught.message : String(caught);
    } finally {
      if (!destroyed && requestId === detailRequestId) loadingDetails = false;
    }
  }

  async function createManualBackup() {
    if (manualStarting || operationBusy) return;
    manualStarting = true;
    try {
      if (!demoMode) {
        await CreateManualIPodBackup(new main.IPodManualBackupRequest({
          mount_path: device.path,
        }));
      }
      dispatch('announce', { message: `Manual backup started for ${device.name}.` });
    } catch (caught) {
      error = caught instanceof Error ? caught.message : String(caught);
      dispatch('announce', { message: `Manual backup could not start: ${error}` });
    } finally {
      manualStarting = false;
    }
  }

  async function verifySnapshot() {
    if (!selectedArchive || !selectedSnapshot || verifying || operationBusy) return;
    verifying = true;
    verification = null;
    error = '';
    try {
      verification = demoMode
        ? {
            protocol_version: 1,
            operation_id: 'fixture-verification',
            archive_id: selectedArchive.archive_id,
            snapshot_id: selectedSnapshot.snapshot_id,
            file_count: selectedSnapshot.file_count,
            unique_blobs_verified: selectedSnapshot.file_count,
            verified_bytes: selectedSnapshot.total_size_bytes,
            verification: 'full_sha256',
            ok: true,
          }
        : parseBackupVerification(await VerifyIPodBackup(
            new main.IPodBackupVerifyRequest({
              archive_id: selectedArchive.archive_id,
              snapshot_id: selectedSnapshot.snapshot_id,
            }),
          ));
      dispatch('announce', {
        message: verification.ok
          ? 'Deep SHA-256 verification completed successfully.'
          : 'Deep verification did not complete successfully.',
      });
    } catch (caught) {
      error = caught instanceof Error ? caught.message : String(caught);
      dispatch('announce', { message: `Backup verification failed: ${error}` });
    } finally {
      verifying = false;
    }
  }

  async function saveNote() {
    if (!selectedArchive || !selectedSnapshot || noteStarting || operationBusy) return;
    noteStarting = true;
    error = '';
    const normalized = note.trim();
    try {
      if (!demoMode) {
        await UpdateIPodBackupNote(new main.IPodBackupNoteRequest({
          archive_id: selectedArchive.archive_id,
          snapshot_id: selectedSnapshot.snapshot_id,
          note: normalized,
        }));
      }
      snapshots = snapshots.map(snapshot => snapshot.snapshot_id === selectedSnapshot.snapshot_id
        ? { ...snapshot, note: normalized }
        : snapshot);
      if (details) details = { ...details, snapshot: { ...details.snapshot, note: normalized } };
      note = normalized;
      selectedPayload(details);
      dispatch('announce', { message: 'Backup note update started.' });
    } catch (caught) {
      error = caught instanceof Error ? caught.message : String(caught);
      dispatch('announce', { message: `Backup note could not be updated: ${error}` });
    } finally {
      noteStarting = false;
    }
  }

  async function exportSnapshot() {
    if (!selectedArchive || !selectedSnapshot || exportStarting || operationBusy) return;
    exportStarting = true;
    error = '';
    try {
      const destination = demoMode ? 'C:\\Users\\Vela\\Backups' : await PickDirectory();
      if (!destination) return;
      if (!demoMode) {
        await ExportIPodBackup(new main.IPodBackupExportRequest({
          archive_id: selectedArchive.archive_id,
          snapshot_id: selectedSnapshot.snapshot_id,
          destination_dir: destination,
        }));
      }
      dispatch('announce', { message: `Backup export started to ${destination}.` });
    } catch (caught) {
      error = caught instanceof Error ? caught.message : String(caught);
      dispatch('announce', { message: `Backup export could not start: ${error}` });
    } finally {
      exportStarting = false;
    }
  }

  async function deleteSnapshot() {
    if (!selectedArchive || !selectedSnapshot || deleteStarting || operationBusy) return;
    deleteStarting = true;
    error = '';
    try {
      if (!demoMode) {
        await DeleteIPodBackup(new main.IPodBackupDeleteRequest({
          archive_id: selectedArchive.archive_id,
          snapshot_id: selectedSnapshot.snapshot_id,
          confirmed: true,
        }));
      }
      showDeleteConfirmation = false;
      dispatch('announce', { message: 'Confirmed backup deletion started.' });
      if (demoMode) {
        snapshots = snapshots.filter(item => item.snapshot_id !== selectedSnapshot.snapshot_id);
        snapshotTotal = snapshots.length;
        const next = snapshots[0] || null;
        if (next) await chooseSnapshot(next);
        else selectedPayload(null);
      }
    } catch (caught) {
      error = caught instanceof Error ? caught.message : String(caught);
      dispatch('announce', { message: `Backup deletion could not start: ${error}` });
    } finally {
      deleteStarting = false;
    }
  }

  onMount(() => {
    void loadArchives();
  });

  onDestroy(() => {
    destroyed = true;
    archiveRequestId += 1;
    snapshotRequestId += 1;
    detailRequestId += 1;
  });
</script>

<section class="backup-inventory" aria-busy={loadingArchives || loadingSnapshots || loadingDetails}>
  <header class="inventory-heading">
    <div>
      <p>Offline archive inventory</p>
      <h3>Backups</h3>
      <span>Archives remain available here when their iPod is disconnected.</span>
    </div>
    <button class="primary" type="button" title={!device.write_ready ? device.write_block_reason : undefined} disabled={manualStarting || operationBusy || !device.write_ready || device.filesystem_read_only} on:click={createManualBackup}>
      <Plus size={16} />
      {manualStarting ? 'Starting…' : 'Back up now'}
    </button>
  </header>

  <div class="scope-banner">
    <ShieldCheck size={19} />
    <div>
      <strong>Full regular-file snapshot</strong>
      <span>Includes the complete regular-file tree with content verification. It is a functional backup, not a raw disk image.</span>
      <span><b>Excluded:</b> partitions and raw sectors, NOR flash, SysCFG capacity data, firmware, bootloaders, and host filesystem metadata.</span>
    </div>
  </div>

  {#if error}
    <div class="inline-error"><AlertTriangle size={17} /><span>{error}</span></div>
  {/if}

  {#if loadingArchives && !archives.length}
    <div class="empty-state"><RefreshCw size={22} /><strong>Loading backup archives</strong></div>
  {:else if !archives.length}
    <div class="empty-state">
      <Archive size={24} />
      <strong>No backup archives yet</strong>
      <span>Create a manual backup of this connected iPod to establish its offline archive.</span>
    </div>
  {:else}
    <div class="inventory-layout">
      <section class="archive-column" aria-labelledby="backup-archives-title">
        <div class="column-heading">
          <div>
            <h4 id="backup-archives-title">Device archives</h4>
            <span>{archiveTotal} archive{archiveTotal === 1 ? '' : 's'} · {formatBytes(aggregateRepositorySize)} total repository</span>
          </div>
          <button class="icon-button" type="button" aria-label="Refresh backup archives" title="Refresh backup archives" disabled={loadingArchives} on:click={() => loadArchives(true)}>
            <RefreshCw size={16} />
          </button>
        </div>
        <div class="archive-list">
          {#each archives as archive (archive.archive_id)}
            <button
              class:selected={selectedArchiveId === archive.archive_id}
              type="button"
              aria-pressed={selectedArchiveId === archive.archive_id}
              on:click={() => chooseArchive(archive)}
            >
              <span class="archive-icon"><HardDrive size={17} /></span>
              <span class="archive-copy">
                <strong>{archive.device_name}</strong>
                <small>{archive.device_meta.model_family || 'iPod archive'}{archive.device_meta.generation ? ` · ${archive.device_meta.generation}` : ''}</small>
                <small>{archive.snapshot_count} snapshot{archive.snapshot_count === 1 ? '' : 's'} · {archiveBelongsToConnectedDevice(archive) ? 'Connected' : 'Offline'}</small>
              </span>
              {#if !archive.identity_is_stable}<AlertTriangle size={15} aria-label="Identity is not stable" />{/if}
            </button>
          {/each}
        </div>
      </section>

      <section class="snapshot-column" aria-labelledby="backup-snapshots-title">
        <div class="column-heading">
          <div>
            <h4 id="backup-snapshots-title">Snapshots</h4>
            <span>{snapshotTotal} total · {formatBytes(repositorySize)} repository</span>
          </div>
        </div>
        {#if loadingSnapshots && !snapshots.length}
          <div class="empty-state compact-state"><RefreshCw size={20} /><span>Loading snapshots…</span></div>
        {:else if !snapshots.length}
          <div class="empty-state compact-state"><Archive size={20} /><span>This archive has no snapshots.</span></div>
        {:else}
          <div class="snapshot-list">
            {#each snapshots as snapshot (snapshot.snapshot_id)}
              <button
                class:selected={selectedSnapshotId === snapshot.snapshot_id}
                type="button"
                aria-pressed={selectedSnapshotId === snapshot.snapshot_id}
                on:click={() => chooseSnapshot(snapshot)}
              >
                <span class="snapshot-date">
                  <strong>{formatTimestamp(snapshot.timestamp)}</strong>
                  <small>{snapshot.reason.replace(/_/g, ' ')}</small>
                </span>
                <span>{snapshot.file_count.toLocaleString()} files · {formatBytes(snapshot.total_size_bytes)}</span>
                <small>{snapshot.note || 'No note'}</small>
              </button>
            {/each}
          </div>
          {#if snapshotTotal > pageSize}
            <nav class="pager" aria-label="Backup snapshot pages">
              <button type="button" disabled={snapshotPage <= 1 || loadingSnapshots} on:click={() => loadSnapshots(snapshotPage - 1)}>
                <ChevronLeft size={15} /> Previous
              </button>
              <span>Page {snapshotPage} of {snapshotPages}</span>
              <button type="button" disabled={snapshotPage >= snapshotPages || loadingSnapshots} on:click={() => loadSnapshots(snapshotPage + 1)}>
                Next <ChevronRight size={15} />
              </button>
            </nav>
          {/if}
        {/if}
      </section>
    </div>

    {#if selectedSnapshot}
      <section class="snapshot-detail" aria-labelledby="snapshot-detail-title">
        <header>
          <div>
            <p>Selected snapshot</p>
            <h4 id="snapshot-detail-title">{formatTimestamp(selectedSnapshot.timestamp)}</h4>
          </div>
          <span>{selectedSnapshot.snapshot_id}</span>
        </header>

        {#if loadingDetails}
          <div class="empty-state compact-state"><RefreshCw size={20} /><span>Loading verified manifest details…</span></div>
        {:else if details}
          <dl class="snapshot-facts">
            <div><dt>Scope</dt><dd>Full regular-file tree</dd></div>
            <div><dt>Included</dt><dd>{details.scope.included_file_count.toLocaleString()} files · {formatBytes(details.scope.included_bytes)}</dd></div>
            <div><dt>Verification</dt><dd>{details.scope.content_verification.replace(/_/g, ' ') || 'Manifest validation'}</dd></div>
            <div><dt>Stable hardware identity</dt><dd>{details.snapshot.identity_is_stable ? 'Yes' : 'No — destructive restore is blocked'}</dd></div>
          </dl>
          <details class="exclusions">
            <summary>Scope and exclusions</summary>
            <ul>
              <li>Partitions, raw sectors, NOR flash, SysCFG capacity data, firmware, and bootloaders are not included.</li>
              {#each details.exclusions as exclusion (exclusion.category)}
                <li><strong>{exclusion.category.replace(/_/g, ' ')}:</strong> {exclusion.description}</li>
              {/each}
            </ul>
          </details>

          <label class="note-field" for="backup-note">
            Snapshot note
            <textarea id="backup-note" maxlength="4000" rows="3" bind:value={note} disabled={noteStarting || operationBusy}></textarea>
            <span>{note.length.toLocaleString()}/4,000 characters</span>
          </label>

          {#if verification}
            <div class:verified={verification.ok} class="verification-result">
              <ShieldCheck size={18} />
              <span>{verification.ok ? 'Deep verification passed' : 'Verification incomplete'} · {verification.unique_blobs_verified.toLocaleString()} blobs · {formatBytes(verification.verified_bytes)}</span>
            </div>
          {/if}

          <div class="snapshot-actions">
            <button type="button" disabled={noteStarting || operationBusy || note.trim() === selectedSnapshot.note} on:click={saveNote}>
              <Save size={15} /> {noteStarting ? 'Starting…' : 'Save note'}
            </button>
            <button type="button" disabled={verifying || operationBusy} on:click={verifySnapshot}>
              <ShieldCheck size={15} /> {verifying ? 'Verifying…' : 'Deep verify'}
            </button>
            <button type="button" disabled={exportStarting || operationBusy} on:click={exportSnapshot}>
              <FolderOpen size={15} /> {exportStarting ? 'Starting…' : 'Export…'}
            </button>
            <button class="danger-button" type="button" disabled={operationBusy} on:click={() => showDeleteConfirmation = true}>
              <Trash2 size={15} /> Delete
            </button>
          </div>
        {/if}
      </section>
    {/if}
  {/if}
</section>

{#if showDeleteConfirmation && selectedSnapshot}
  <ConfirmationDialog
    title="Delete this backup snapshot?"
    description={`This removes snapshot ${selectedSnapshot.snapshot_id} from Vela’s backup repository. Other snapshots may share deduplicated data and remain intact, but this snapshot cannot be restored after deletion.`}
    confirmLabel="Delete snapshot"
    cancelLabel="Keep snapshot"
    requiredPhrase="DELETE BACKUP"
    acknowledgement="I understand that this snapshot will no longer be available for restore or migration."
    destructive={true}
    busy={deleteStarting}
    allowEscape={!deleteStarting}
    on:confirm={deleteSnapshot}
    on:cancel={() => showDeleteConfirmation = false}
  />
{/if}

<style>
  .backup-inventory {
    display: grid;
    gap: 14px;
  }
  .inventory-heading,
  .snapshot-detail > header,
  .column-heading {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
  }
  .inventory-heading p,
  .snapshot-detail header p {
    margin: 0;
    color: var(--accent);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .07em;
    text-transform: uppercase;
  }
  h3,
  h4 {
    margin: 3px 0 0;
  }
  h3 { font-size: 20px; }
  h4 { font-size: 15px; }
  .inventory-heading > div > span,
  .column-heading span,
  .snapshot-detail > header > span {
    color: var(--muted);
    font-size: 11px;
  }
  .snapshot-detail > header > span {
    max-width: 48%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: var(--font-mono, ui-monospace);
  }
  button {
    min-height: 36px;
    border: 0;
    border-radius: 9px;
    font-weight: 650;
    cursor: pointer;
  }
  button:disabled {
    opacity: var(--disabled-opacity);
    cursor: default;
  }
  button:focus-visible,
  textarea:focus-visible,
  summary:focus-visible {
    outline: 2px solid var(--focus-ring);
    outline-offset: 2px;
  }
  .primary {
    min-height: 40px;
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 0 14px;
    background: var(--accent);
    color: var(--bg);
  }
  .scope-banner,
  .inline-error {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    align-items: start;
    gap: 10px;
    padding: 12px 14px;
    border: 1px solid var(--line);
    border-radius: 12px;
    background: var(--surface-2);
    color: var(--accent);
  }
  .scope-banner > div {
    display: grid;
    gap: 4px;
  }
  .scope-banner span {
    color: var(--muted);
    font-size: 12px;
    line-height: 1.45;
  }
  .scope-banner b {
    color: var(--text);
  }
  .inline-error {
    color: var(--error-color);
  }
  .inline-error span {
    color: var(--text);
    font-size: 12px;
  }
  .inventory-layout {
    display: grid;
    grid-template-columns: minmax(210px, .72fr) minmax(300px, 1.28fr);
    gap: 12px;
  }
  .archive-column,
  .snapshot-column,
  .snapshot-detail {
    min-width: 0;
    padding: 14px;
    border: 1px solid var(--line);
    border-radius: 14px;
    background: var(--surface);
  }
  .icon-button {
    width: 36px;
    display: grid;
    place-items: center;
    padding: 0;
    background: var(--surface-2);
    color: var(--muted);
  }
  .archive-list,
  .snapshot-list {
    display: grid;
    gap: 6px;
    margin-top: 11px;
  }
  .archive-list > button,
  .snapshot-list > button {
    width: 100%;
    min-width: 0;
    border: 1px solid var(--line);
    background: var(--bg);
    color: var(--text);
    text-align: left;
  }
  .archive-list > button {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    align-items: center;
    gap: 9px;
    padding: 9px;
  }
  .archive-list > button.selected,
  .snapshot-list > button.selected {
    border-color: var(--accent);
    background: var(--accent-soft);
  }
  .archive-icon {
    width: 32px;
    height: 32px;
    display: grid;
    place-items: center;
    border-radius: 9px;
    background: var(--surface-2);
    color: var(--accent);
  }
  .archive-copy,
  .snapshot-date {
    min-width: 0;
    display: grid;
    gap: 2px;
  }
  .archive-copy strong,
  .archive-copy small,
  .snapshot-list strong,
  .snapshot-list span,
  .snapshot-list small {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .archive-copy small,
  .snapshot-list span,
  .snapshot-list small {
    color: var(--muted);
    font-size: 11px;
  }
  .snapshot-list > button {
    display: grid;
    grid-template-columns: minmax(150px, 1fr) auto;
    gap: 5px 12px;
    padding: 10px;
  }
  .snapshot-list > button > small {
    grid-column: 1 / -1;
  }
  .pager {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    margin-top: 11px;
  }
  .pager button,
  .snapshot-actions button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 0 10px;
    background: var(--surface-2);
    color: var(--text);
  }
  .pager span {
    color: var(--muted);
    font-size: 11px;
  }
  .empty-state {
    min-height: 150px;
    display: grid;
    place-items: center;
    align-content: center;
    gap: 7px;
    color: var(--muted);
    text-align: center;
  }
  .empty-state span {
    max-width: 420px;
    font-size: 12px;
  }
  .compact-state { min-height: 110px; }
  .snapshot-detail {
    display: grid;
    gap: 13px;
  }
  .snapshot-facts {
    margin: 0;
  }
  .snapshot-facts > div {
    display: flex;
    justify-content: space-between;
    gap: 14px;
    padding: 8px 0;
    border-top: 1px solid var(--line);
    font-size: 12px;
  }
  dt { color: var(--muted); }
  dd {
    margin: 0;
    text-align: right;
  }
  .exclusions {
    border: 1px solid var(--line);
    border-radius: 10px;
    background: var(--bg);
  }
  .exclusions summary {
    min-height: 36px;
    display: flex;
    align-items: center;
    padding: 0 11px;
    font-weight: 650;
    cursor: pointer;
  }
  .exclusions ul {
    margin: 0;
    padding: 0 28px 13px;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.5;
  }
  .note-field {
    display: grid;
    gap: 6px;
    font-size: 12px;
    font-weight: 650;
  }
  .note-field textarea {
    width: 100%;
    min-height: 84px;
    resize: vertical;
    border: 1px solid var(--line);
    border-radius: 10px;
    background: var(--surface-2);
    color: var(--text);
    padding: 10px;
    line-height: 1.45;
  }
  .note-field > span {
    color: var(--muted);
    font-size: 11px;
    font-weight: 400;
    text-align: right;
  }
  .verification-result {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px;
    border-radius: 10px;
    background: var(--surface-2);
    color: var(--warning-color);
    font-size: 12px;
  }
  .verification-result.verified {
    color: var(--success-color);
  }
  .snapshot-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 7px;
  }
  .snapshot-actions .danger-button {
    color: var(--error-color);
  }
  @media (max-width: 900px) {
    .inventory-layout { grid-template-columns: 1fr; }
  }
  @media (max-width: 720px) {
    .inventory-heading { flex-direction: column; }
    .inventory-heading .primary { width: 100%; justify-content: center; }
    .snapshot-list > button { grid-template-columns: 1fr; }
    .snapshot-list > button > small { grid-column: auto; }
    .snapshot-actions { display: grid; grid-template-columns: 1fr 1fr; }
    .snapshot-detail > header { display: grid; }
    .snapshot-detail > header > span { max-width: 100%; }
  }
</style>
