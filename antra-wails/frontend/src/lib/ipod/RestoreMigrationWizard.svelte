<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import {
    AlertTriangle, ArrowRight, CheckCircle2, Database, HardDrive, ShieldCheck,
  } from 'lucide-svelte';
  import {
    PreflightIPodMigration, PreflightIPodRestore, StartIPodMigration,
    StartIPodRestore,
  } from '../../../wailsjs/go/main/App.js';
  import { main } from '../../../wailsjs/go/models';
  import ConfirmationDialog from './ConfirmationDialog.svelte';
  import type {
    IPodBackupDeviceArchive, IPodBackupSnapshot, IPodBackupSnapshotDetails,
    IPodDevice, IPodMigrationPreflight, IPodPlanGroup, IPodRestorePreflight,
  } from '../ipodTypes';
  import { parseMigrationPreflight, parseRestorePreflight } from '../ipodTypes';

  export let device: IPodDevice;
  export let archive: IPodBackupDeviceArchive | null = null;
  export let snapshot: IPodBackupSnapshot | null = null;
  export let details: IPodBackupSnapshotDetails | null = null;
  export let operationBusy = false;
  export let demoMode = false;

  type WizardMode = 'restore' | 'migration';
  const dispatch = createEventDispatcher<{
    announce: { message: string };
  }>();
  let mode: WizardMode = 'restore';
  let restoreReview: IPodRestorePreflight | null = null;
  let migrationReview: IPodMigrationPreflight | null = null;
  let preflighting = false;
  let starting = false;
  let error = '';
  let showConfirmation = false;
  let handledSelection = '';

  $: selectionIdentity = `${archive?.archive_id || ''}|${snapshot?.snapshot_id || ''}`;
  $: if (selectionIdentity !== handledSelection) {
    handledSelection = selectionIdentity;
    clearReview();
  }
  $: reviewReady = mode === 'restore'
    ? !!restoreReview
    : !!migrationReview && !migrationReview.blocked && !!migrationReview.migration_plan_id;

  function clearReview() {
    restoreReview = null;
    migrationReview = null;
    error = '';
    showConfirmation = false;
  }

  function changeMode(next: WizardMode) {
    if (mode === next) return;
    mode = next;
    clearReview();
  }

  function formatBytes(bytes: number): string {
    if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1000)));
    return `${(bytes / 1000 ** index).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
  }

  function formatUnknown(value: unknown): string {
    if (typeof value === 'string') return value;
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
    if (value === null || value === undefined) return '';
    try {
      return JSON.stringify(value);
    } catch {
      return 'Unavailable';
    }
  }

  function groupLabel(group: IPodPlanGroup): string {
    const labels: Record<IPodPlanGroup, string> = {
      additions: 'Additions',
      removals: 'Removals',
      metadata_updates: 'Metadata updates',
      artwork_updates: 'Artwork updates',
      conversions: 'Conversions',
      playlist_effects: 'Playlist effects',
      warnings: 'Warnings',
      unsupported: 'Unsupported',
    };
    return labels[group];
  }

  function demoRestoreReview(): IPodRestorePreflight {
    if (!archive || !snapshot) throw new Error('Choose a backup snapshot first.');
    return {
      protocol_version: 1,
      restore_plan_id: 'restore_fixture',
      source_archive_id: archive.archive_id,
      source_snapshot_id: snapshot.snapshot_id,
      snapshot: details?.snapshot || {
        ...snapshot,
        identity_is_stable: true,
        source_verification: 'full_sha256',
      },
      scope: details?.scope || {
        kind: 'full_regular_file_tree',
        functional_backup: true,
        raw_disk_image: false,
        included_file_count: snapshot.file_count,
        included_bytes: snapshot.total_size_bytes,
        content_verification: 'full_sha256',
        restores_included_tree_exactly: true,
      },
      exclusions: details?.exclusions || [],
      target: {
        device_id: device.device_id,
        archive_id: archive.archive_id,
        name: device.name,
        model_family: device.model_family,
        database_generation: { exists: true, size: 12_400_000 },
      },
      verification: {
        ok: true,
        method: 'full_sha256',
        file_count: snapshot.file_count,
        unique_blobs_verified: Math.max(0, Math.floor(snapshot.file_count * 0.96)),
        verified_bytes: snapshot.total_size_bytes,
        filesystem_names_valid: true,
      },
      storage: {
        final_allocated_bytes: snapshot.total_size_bytes,
        volume_total_bytes: Math.max(
          snapshot.total_size_bytes,
          Math.round(device.disk_size_gb * 1_000_000_000),
        ),
        volume_free_bytes: Math.max(0, Math.round(device.free_space_gb * 1_000_000_000)),
        final_state_fits: true,
        atomic_temp_capacity_rechecked_on_execute: true,
      },
      expires_at: Date.now() / 1000 + 900,
      confirmation_required: true,
      raw_replacement_restore_allowed: false,
    };
  }

  function demoMigrationReview(): IPodMigrationPreflight {
    if (!archive || !snapshot) throw new Error('Choose a backup snapshot first.');
    return {
      protocol_version: 1,
      blocked: false,
      compatible: true,
      code: '',
      message: '',
      raw_restore_allowed: false,
      safe_migration_available: true,
      same_device: false,
      issues: [],
      requirements: [
        'The source snapshot remains immutable and is never raw-restored.',
        'A verified target safety backup is mandatory before writing.',
      ],
      source: {
        archive_id: archive.archive_id,
        snapshot_id: snapshot.snapshot_id,
        device_id: 'fixture-source-device',
        model_family: archive.device_meta.model_family,
        generation: archive.device_meta.generation,
      },
      target: {
        archive_id: device.serial || 'fixture-target',
        device_id: device.device_id,
        model_family: device.model_family,
        generation: device.generation,
        database_generation: { exists: true, size: 12_400_000 },
      },
      migration_plan_id: 'migration_fixture',
      confirmation_required: true,
      target_safety_backup_required: true,
      staging_bundle: {
        schema_version: 1,
        path: 'Vela app data migration staging',
        fingerprint: 'fixture-bundle-fingerprint',
        media_file_count: snapshot.file_count,
        playlist_count: 12,
        total_media_bytes: snapshot.total_size_bytes,
      },
      metadata: {
        preserved: ['track descriptive metadata', 'ratings', 'play counts', 'standard playlist membership and order'],
        not_preserved: ['source hardware identity and volume files', 'artwork and photos', 'smart-playlist rules and system playlists', 'skip counts and last-played times'],
        unresolved_source_tracks: 0,
        skipped_playlists: 0,
        unresolved_playlist_items: 0,
      },
      additions: snapshot.file_count,
      removals: 0,
      updates: 0,
      conversions: 0,
      playlist_changes: 12,
      warnings: 0,
      unsupported: 0,
      required_bytes: snapshot.total_size_bytes,
      source_count: snapshot.file_count,
      storage: {
        bytes_to_add: snapshot.total_size_bytes,
        bytes_to_remove: 0,
        bytes_to_update: 0,
        net_change_bytes: snapshot.total_size_bytes,
        required_free_bytes: snapshot.total_size_bytes,
        free_before_bytes: device.free_space_gb * 1_000_000_000,
        free_after_bytes: Math.max(0, device.free_space_gb * 1_000_000_000 - snapshot.total_size_bytes),
      },
      groups: [
        { group: 'additions', total: snapshot.file_count, page_size_max: 100 },
        { group: 'playlist_effects', total: 12, page_size_max: 100 },
        { group: 'removals', total: 0, page_size_max: 100 },
        { group: 'unsupported', total: 0, page_size_max: 100 },
      ],
      group_previews: {},
      review_fingerprint: 'fixture-review-fingerprint',
      expires_at: Date.now() / 1000 + 900,
    };
  }

  async function runPreflight() {
    if (!archive || !snapshot || preflighting || operationBusy) return;
    preflighting = true;
    error = '';
    restoreReview = null;
    migrationReview = null;
    try {
      if (mode === 'restore') {
        restoreReview = demoMode
          ? demoRestoreReview()
          : parseRestorePreflight(await PreflightIPodRestore(
              new main.IPodRestorePreflightRequest({
                archive_id: archive.archive_id,
                snapshot_id: snapshot.snapshot_id,
                mount_path: device.path,
              }),
            ));
        dispatch('announce', { message: 'Same-device restore preflight is ready for review.' });
      } else {
        migrationReview = demoMode
          ? demoMigrationReview()
          : parseMigrationPreflight(await PreflightIPodMigration(
              new main.IPodMigrationPreflightRequest({
                archive_id: archive.archive_id,
                snapshot_id: snapshot.snapshot_id,
                mount_path: device.path,
              }),
            ));
        dispatch('announce', {
          message: migrationReview.blocked
            ? `Replacement migration is blocked: ${migrationReview.message}`
            : 'Replacement migration preflight is ready for review.',
        });
      }
    } catch (caught) {
      error = caught instanceof Error ? caught.message : String(caught);
      dispatch('announce', { message: `${mode === 'restore' ? 'Restore' : 'Migration'} preflight failed: ${error}` });
    } finally {
      preflighting = false;
    }
  }

  async function startReviewedOperation() {
    if (!reviewReady || starting || operationBusy) return;
    starting = true;
    error = '';
    try {
      if (!demoMode) {
        if (mode === 'restore' && restoreReview) {
          await StartIPodRestore(new main.IPodRestoreRequest({
            restore_plan_id: restoreReview.restore_plan_id,
            confirmed: true,
          }));
        } else if (mode === 'migration' && migrationReview?.migration_plan_id) {
          await StartIPodMigration(new main.IPodMigrationRequest({
            migration_plan_id: migrationReview.migration_plan_id,
            confirmed: true,
          }));
        }
      }
      showConfirmation = false;
      dispatch('announce', {
        message: mode === 'restore'
          ? 'Confirmed same-device restore started.'
          : 'Confirmed replacement migration started.',
      });
    } catch (caught) {
      error = caught instanceof Error ? caught.message : String(caught);
      showConfirmation = false;
      dispatch('announce', { message: `${mode === 'restore' ? 'Restore' : 'Migration'} could not start: ${error}` });
    } finally {
      starting = false;
    }
  }
</script>

<section class="wizard" aria-busy={preflighting || starting}>
  <header>
    <p>Reviewed device write</p>
    <h3>Restore or migrate</h3>
    <span>Preflight binds a short-lived plan to this exact mounted target. Nothing is written during review.</span>
  </header>

  <fieldset>
    <legend>Choose the operation</legend>
    <label class:selected={mode === 'restore'}>
      <input type="radio" name="ipod-recovery-mode" value="restore" checked={mode === 'restore'} on:change={() => changeMode('restore')} />
      <Database size={19} />
      <span>
        <strong>Same-device restore</strong>
        <small>Restore the full included regular-file tree only to the original verified iPod.</small>
      </span>
    </label>
    <label class:selected={mode === 'migration'}>
      <input type="radio" name="ipod-recovery-mode" value="migration" checked={mode === 'migration'} on:change={() => changeMode('migration')} />
      <HardDrive size={19} />
      <span>
        <strong>Compatible replacement migration</strong>
        <small>Stage verified media and metadata through a reviewed addition-only sync plan.</small>
      </span>
    </label>
  </fieldset>

  {#if mode === 'migration'}
    <div class="boundary-notice">
      <AlertTriangle size={18} />
      <span>Vela never raw-restores a source snapshot to a replacement iPod. Compatibility must pass, the target keeps its own identity and device files, and a target safety backup is created before writes.</span>
    </div>
  {/if}

  {#if !archive || !snapshot}
    <div class="empty-review">
      <ShieldCheck size={23} />
      <strong>Choose a backup snapshot</strong>
      <span>Select an archive and snapshot above before running preflight.</span>
    </div>
  {:else}
    <div class="selection-summary">
      <div><span>Source archive</span><strong>{archive.device_name}</strong><small>{archive.archive_id}</small></div>
      <span class="direction-icon" aria-hidden="true"><ArrowRight size={18} /></span>
      <div><span>Mounted target</span><strong>{device.name}</strong><small>{device.model_family}{device.generation ? ` · ${device.generation}` : ''}</small></div>
    </div>

    {#if error}
      <div class="inline-error"><AlertTriangle size={17} /><span>{error}</span></div>
    {/if}

    <button class="preflight-button" type="button" title={!device.write_ready ? device.write_block_reason : undefined} disabled={preflighting || operationBusy || !device.write_ready || device.filesystem_read_only} on:click={runPreflight}>
      <ShieldCheck size={16} />
      {preflighting
        ? (mode === 'restore' ? 'Checking original device…' : 'Checking compatibility and staging…')
        : (mode === 'restore' ? 'Preflight same-device restore' : 'Preflight replacement migration')}
    </button>

    {#if restoreReview}
      <section class="review-card" aria-labelledby="restore-review-title">
        <header>
          <div><p>Preflight passed</p><h4 id="restore-review-title">Same-device restore review</h4></div>
          <CheckCircle2 size={20} />
        </header>
        <dl>
          <div><dt>Source snapshot</dt><dd>{restoreReview.source_snapshot_id}</dd></div>
          <div><dt>Target</dt><dd>{restoreReview.target.name} · {restoreReview.target.model_family}</dd></div>
          <div><dt>Hardware identity</dt><dd>{restoreReview.target.device_id}</dd></div>
          <div><dt>Scope</dt><dd>Full regular-file tree · {restoreReview.scope.included_file_count.toLocaleString()} files</dd></div>
          <div><dt>Included storage</dt><dd>{formatBytes(restoreReview.scope.included_bytes)}</dd></div>
          <div><dt>Snapshot verification</dt><dd>Full SHA-256 passed · {restoreReview.verification.file_count.toLocaleString()} files</dd></div>
          <div><dt>Verified content</dt><dd>{restoreReview.verification.unique_blobs_verified.toLocaleString()} unique blobs · {formatBytes(restoreReview.verification.verified_bytes)}</dd></div>
          <div><dt>Filesystem names</dt><dd>{restoreReview.verification.filesystem_names_valid ? 'Valid for this target' : 'Invalid'}</dd></div>
          <div><dt>Final allocated size</dt><dd>{formatBytes(restoreReview.storage.final_allocated_bytes)} of {formatBytes(restoreReview.storage.volume_total_bytes)} · {restoreReview.storage.final_state_fits ? 'Fits target capacity' : 'Does not fit'}</dd></div>
          <div><dt>Atomic temporary space</dt><dd>{restoreReview.storage.atomic_temp_capacity_rechecked_on_execute ? 'Rechecked immediately before commit' : 'Not confirmed'}</dd></div>
          <div><dt>Raw replacement restore</dt><dd>Not allowed</dd></div>
          <div><dt>Plan expires</dt><dd>{new Date(restoreReview.expires_at * 1000).toLocaleTimeString()}</dd></div>
        </dl>
        <div class="review-warning">
          <AlertTriangle size={17} />
          <span>This review did not write to the target. A verified pre-restore safety snapshot is created first, and exact atomic temporary-space capacity is rechecked immediately before commit. Once commit begins, Cancel and Escape are unavailable; keep this exact iPod connected.</span>
        </div>
        <button class="start-button" type="button" disabled={operationBusy} on:click={() => showConfirmation = true}>
          Review destructive confirmation
        </button>
      </section>
    {:else if migrationReview}
      <section class:blocked={migrationReview.blocked} class="review-card" aria-labelledby="migration-review-title">
        <header>
          <div>
            <p>{migrationReview.blocked ? 'Compatibility blocked' : 'Preflight passed'}</p>
            <h4 id="migration-review-title">{migrationReview.blocked ? 'Replacement migration unavailable' : 'Replacement migration review'}</h4>
          </div>
          {#if migrationReview.blocked}<AlertTriangle size={20} />{:else}<CheckCircle2 size={20} />{/if}
        </header>

        {#if migrationReview.blocked}
          <div class="blocked-reason">
            <strong>{migrationReview.message || 'The source and target profiles are not compatible.'}</strong>
            <span>Code: {migrationReview.code || 'migration_blocked'}</span>
          </div>
          {#if migrationReview.issues.length}
            <ul class="issue-list">
              {#each migrationReview.issues as issue, index (`${issue.field}-${index}`)}
                <li>
                  <strong>{issue.field.replace(/_/g, ' ')}</strong>
                  <span>{issue.message || `${formatUnknown(issue.source)} does not match ${formatUnknown(issue.target)}.`}</span>
                </li>
              {/each}
            </ul>
          {/if}
          {#if migrationReview.requirements.length}
            <details>
              <summary>Migration safety requirements</summary>
              <ul>{#each migrationReview.requirements as requirement (requirement)}<li>{requirement}</li>{/each}</ul>
            </details>
          {/if}
        {:else}
          <dl>
            <div><dt>Source</dt><dd>{migrationReview.source.model_family || archive.device_name} · {migrationReview.source.generation || 'Generation unavailable'}</dd></div>
            <div><dt>Replacement target</dt><dd>{migrationReview.target.model_family || device.model_family} · {migrationReview.target.generation || device.generation}</dd></div>
            <div><dt>Migration method</dt><dd>Verified staging bundle → normal addition-only sync</dd></div>
            <div><dt>Raw replacement restore</dt><dd>No — explicitly blocked</dd></div>
            <div><dt>Media staged</dt><dd>{migrationReview.staging_bundle?.media_file_count.toLocaleString() || '0'} files · {formatBytes(migrationReview.staging_bundle?.total_media_bytes || 0)}</dd></div>
            <div><dt>Storage after migration</dt><dd>{formatBytes(migrationReview.storage?.free_after_bytes || 0)} free</dd></div>
            <div><dt>Target safety backup</dt><dd>{migrationReview.target_safety_backup_required ? 'Required before writing' : 'Not reported'}</dd></div>
          </dl>

          <div class="plan-counts">
            <div><strong>{migrationReview.additions || 0}</strong><span>Additions</span></div>
            <div><strong>{migrationReview.removals || 0}</strong><span>Removals</span></div>
            <div><strong>{migrationReview.conversions || 0}</strong><span>Conversions</span></div>
            <div><strong>{migrationReview.playlist_changes || 0}</strong><span>Playlist effects</span></div>
            <div><strong>{migrationReview.warnings || 0}</strong><span>Warnings</span></div>
            <div><strong>{migrationReview.unsupported || 0}</strong><span>Unsupported</span></div>
          </div>

          {#if migrationReview.groups?.length}
            <div class="group-review">
              <h5>Reviewed groups</h5>
              {#each migrationReview.groups as group (group.group)}
                <div><span>{groupLabel(group.group)}</span><strong>{group.total.toLocaleString()}</strong></div>
              {/each}
            </div>
          {/if}

          {#if migrationReview.metadata}
            <div class="metadata-review">
              <section>
                <h5>Metadata carried forward</h5>
                <ul>{#each migrationReview.metadata.preserved as item (item)}<li>{item}</li>{/each}</ul>
              </section>
              <section>
                <h5>Not carried forward</h5>
                <ul>{#each migrationReview.metadata.not_preserved as item (item)}<li>{item}</li>{/each}</ul>
              </section>
            </div>
            {#if migrationReview.metadata.unresolved_source_tracks || migrationReview.metadata.skipped_playlists || migrationReview.metadata.unresolved_playlist_items}
              <div class="review-warning">
                <AlertTriangle size={17} />
                <span>{migrationReview.metadata.unresolved_source_tracks} unresolved source tracks · {migrationReview.metadata.skipped_playlists} skipped playlists · {migrationReview.metadata.unresolved_playlist_items} unresolved playlist items</span>
              </div>
            {/if}
          {/if}

          <button class="start-button" type="button" disabled={operationBusy} on:click={() => showConfirmation = true}>
            Review replacement confirmation
          </button>
        {/if}
      </section>
    {/if}
  {/if}
</section>

{#if showConfirmation && mode === 'restore' && restoreReview}
  <ConfirmationDialog
    title={`Restore ${device.name} from this snapshot?`}
    description={`Vela will create a safety snapshot, then replace the included regular-file tree on this exact iPod with snapshot ${restoreReview.source_snapshot_id}. This is not a partition, NOR, SysCFG, or firmware restore.`}
    confirmLabel="Start same-device restore"
    cancelLabel="Return to review"
    requiredPhrase={`RESTORE ${device.name}`}
    acknowledgement="I verified the source snapshot, target identity, scope, and exclusions, and I will keep this iPod connected after commit starts."
    destructive={true}
    busy={starting}
    allowEscape={!starting}
    on:confirm={startReviewedOperation}
    on:cancel={() => showConfirmation = false}
  />
{/if}

{#if showConfirmation && mode === 'migration' && migrationReview?.migration_plan_id}
  <ConfirmationDialog
    title={`Migrate verified media to ${device.name}?`}
    description="This writes the compatible replacement target through the reviewed addition-only sync plan after creating a verified target safety backup. The source snapshot is never raw-restored and the target keeps its own hardware identity."
    confirmLabel="Start replacement migration"
    cancelLabel="Return to review"
    requiredPhrase={`MIGRATE TO ${device.name}`}
    acknowledgement={`I verified that ${device.name} is the intended replacement target and understand that cancellation ends when commit starts.`}
    destructive={true}
    busy={starting}
    allowEscape={!starting}
    on:confirm={startReviewedOperation}
    on:cancel={() => showConfirmation = false}
  />
{/if}

<style>
  .wizard {
    display: grid;
    gap: 14px;
    padding: 17px;
    border: 1px solid var(--line);
    border-radius: 14px;
    background: var(--surface);
  }
  .wizard > header p,
  .review-card header p {
    margin: 0;
    color: var(--accent);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .07em;
    text-transform: uppercase;
  }
  h3,
  h4,
  h5 {
    margin: 3px 0 0;
  }
  h3 { font-size: 20px; }
  h4 { font-size: 17px; }
  h5 { font-size: 12px; }
  .wizard > header > span {
    display: block;
    margin-top: 5px;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.45;
  }
  fieldset {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    margin: 0;
    padding: 0;
    border: 0;
  }
  legend {
    grid-column: 1 / -1;
    margin-bottom: 2px;
    color: var(--muted);
    font-size: 12px;
    font-weight: 650;
  }
  fieldset label {
    position: relative;
    min-height: 84px;
    display: grid;
    grid-template-columns: auto auto minmax(0, 1fr);
    align-items: start;
    gap: 10px;
    padding: 13px;
    border: 1px solid var(--line);
    border-radius: 11px;
    background: var(--bg);
    color: var(--muted);
    cursor: pointer;
  }
  fieldset label.selected {
    border-color: var(--accent);
    background: var(--accent-soft);
    color: var(--accent);
  }
  fieldset input {
    width: 18px;
    height: 18px;
    margin: 1px 0 0;
    accent-color: var(--accent);
  }
  fieldset input:focus-visible {
    outline: 2px solid var(--focus-ring);
    outline-offset: 2px;
  }
  fieldset label > span {
    display: grid;
    gap: 4px;
  }
  fieldset small {
    color: var(--muted);
    font-size: 11px;
    font-weight: 400;
    line-height: 1.4;
  }
  .boundary-notice,
  .inline-error,
  .review-warning {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    align-items: start;
    gap: 9px;
    padding: 11px 12px;
    border-radius: 10px;
    background: var(--surface-2);
    color: var(--warning-color);
  }
  .boundary-notice span,
  .inline-error span,
  .review-warning span {
    color: var(--muted);
    font-size: 12px;
    line-height: 1.5;
  }
  .inline-error {
    color: var(--error-color);
  }
  .selection-summary {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
    align-items: center;
    gap: 10px;
    padding: 12px;
    border: 1px solid var(--line);
    border-radius: 11px;
    background: var(--bg);
  }
  .selection-summary > div {
    min-width: 0;
    display: grid;
    gap: 2px;
  }
  .selection-summary > div:last-child {
    text-align: right;
  }
  .selection-summary span,
  .selection-summary small {
    overflow: hidden;
    color: var(--muted);
    font-size: 11px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .selection-summary small {
    font-family: var(--font-mono, ui-monospace);
  }
  .direction-icon {
    display: grid;
    place-items: center;
    color: var(--muted);
  }
  button {
    min-height: 38px;
    border: 0;
    border-radius: 10px;
    font-weight: 650;
    cursor: pointer;
  }
  button:disabled {
    opacity: var(--disabled-opacity);
    cursor: default;
  }
  button:focus-visible,
  summary:focus-visible {
    outline: 2px solid var(--focus-ring);
    outline-offset: 2px;
  }
  .preflight-button,
  .start-button {
    width: 100%;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 7px;
    padding: 0 14px;
    background: var(--accent);
    color: var(--bg);
  }
  .start-button {
    margin-top: 2px;
  }
  .empty-review {
    min-height: 150px;
    display: grid;
    place-items: center;
    align-content: center;
    gap: 7px;
    color: var(--muted);
    text-align: center;
  }
  .empty-review span {
    font-size: 12px;
  }
  .review-card {
    display: grid;
    gap: 12px;
    padding: 14px;
    border: 1px solid var(--success-color);
    border-radius: 12px;
    background: var(--bg);
  }
  .review-card.blocked {
    border-color: var(--error-color);
  }
  .review-card > header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    color: var(--success-color);
  }
  .review-card.blocked > header {
    color: var(--error-color);
  }
  .review-card dl {
    margin: 0;
  }
  .review-card dl > div {
    display: grid;
    grid-template-columns: minmax(130px, .55fr) minmax(0, 1fr);
    gap: 12px;
    padding: 8px 0;
    border-top: 1px solid var(--line);
    font-size: 12px;
  }
  dt { color: var(--muted); }
  dd {
    min-width: 0;
    margin: 0;
    overflow-wrap: anywhere;
    text-align: right;
  }
  .plan-counts {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 6px;
  }
  .plan-counts > div {
    display: grid;
    gap: 2px;
    padding: 10px;
    border-radius: 9px;
    background: var(--surface-2);
  }
  .plan-counts strong { font-size: 18px; }
  .plan-counts span {
    color: var(--muted);
    font-size: 11px;
  }
  .group-review {
    display: grid;
    gap: 5px;
  }
  .group-review > div {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    padding: 7px 9px;
    border-radius: 8px;
    background: var(--surface-2);
    font-size: 12px;
  }
  .metadata-review {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  .metadata-review section {
    padding: 11px;
    border: 1px solid var(--line);
    border-radius: 10px;
  }
  .metadata-review ul,
  details ul,
  .issue-list {
    margin: 7px 0 0;
    padding-left: 18px;
    color: var(--muted);
    font-size: 11px;
    line-height: 1.5;
  }
  .blocked-reason {
    display: grid;
    gap: 4px;
    padding: 11px;
    border-radius: 10px;
    background: var(--surface-2);
  }
  .blocked-reason span {
    color: var(--muted);
    font-family: var(--font-mono, ui-monospace);
    font-size: 11px;
  }
  .issue-list {
    display: grid;
    gap: 7px;
    list-style: none;
    padding: 0;
  }
  .issue-list li {
    display: grid;
    gap: 3px;
    padding: 9px;
    border-left: 3px solid var(--error-color);
    background: var(--surface-2);
  }
  details {
    border: 1px solid var(--line);
    border-radius: 10px;
  }
  details summary {
    min-height: 36px;
    display: flex;
    align-items: center;
    padding: 0 10px;
    font-size: 12px;
    font-weight: 650;
    cursor: pointer;
  }
  details ul { padding: 0 26px 12px; }
  @media (max-width: 720px) {
    fieldset,
    .metadata-review {
      grid-template-columns: 1fr;
    }
    .selection-summary {
      grid-template-columns: 1fr;
    }
    .direction-icon { transform: rotate(90deg); }
    .selection-summary > div:last-child { text-align: left; }
    .review-card dl > div {
      grid-template-columns: 1fr;
      gap: 3px;
    }
    dd { text-align: left; }
    .plan-counts { grid-template-columns: repeat(2, 1fr); }
  }
</style>
