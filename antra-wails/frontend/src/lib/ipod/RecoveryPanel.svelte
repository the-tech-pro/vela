<script lang="ts">
  import { createEventDispatcher, onMount } from 'svelte';
  import {
    AlertTriangle, CheckCircle2, HardDrive, RefreshCw, ShieldAlert,
  } from 'lucide-svelte';
  import { GetIPodRecoveryState } from '../../../wailsjs/go/main/App.js';
  import type { IPodEventPayload, IPodRecoveryState } from '../ipodTypes';
  import { parseRecoveryState } from '../ipodTypes';

  export let ipodEvent: IPodEventPayload | null = null;
  export let demoMode = false;

  const dispatch = createEventDispatcher<{
    announce: { message: string };
  }>();
  let state: IPodRecoveryState | null = null;
  let loading = false;
  let error = '';
  let handledEvent: IPodEventPayload | null = null;

  $: operation = state?.operation || null;
  $: recovery = state?.recovery || operation?.recovery || {};
  $: reconnect = state?.reconnect || operation?.reconnect || {};
  $: safetySnapshotId = recovery.safety_snapshot_id || operation?.safety_snapshot_id || '';
  $: recoveryCode = recovery.code || '';
  $: if (ipodEvent && ipodEvent !== handledEvent) {
    handledEvent = ipodEvent;
    if (
      ipodEvent.type === 'ipod_operation_ended'
      || ipodEvent.type === 'ipod_restore'
      || ipodEvent.type === 'ipod_migration'
    ) {
      void loadRecoveryState(false);
    }
  }

  function recoveryHeading(): string {
    if (recoveryCode === 'restore_durability_pending') return 'Restore durability is pending';
    if (recoveryCode === 'restore_incomplete') return 'Restore is incomplete';
    if (state?.requires_recovery) return 'Recovery is required';
    if (state?.incomplete) return 'An operation is incomplete';
    return 'No recovery action required';
  }

  function recoveryExplanation(): string {
    if (recoveryCode === 'restore_durability_pending') {
      return 'The restored content was verified, but the operating system did not confirm every final device flush. Keep the iPod connected and use safe eject before unplugging it.';
    }
    if (recoveryCode === 'restore_incomplete') {
      return 'The regular-file restore did not finish cleanly. Reconnect the exact target below so Vela can inspect the persisted recovery record and safety snapshot.';
    }
    if (state?.requires_recovery) {
      return recovery.message
        || 'A restore or migration crossed a write boundary without a verified clean finish. Follow the persisted reconnect details exactly.';
    }
    if (state?.incomplete) {
      return 'Vela still has a running operation journal entry. Keep the named iPod connected until the operation reaches a terminal state.';
    }
    return operation
      ? `The latest ${operation.kind.replace(/_/g, ' ')} operation finished with status “${operation.status}”.`
      : 'Vela has no interrupted restore or migration in its operation journal.';
  }

  async function loadRecoveryState(announce = true) {
    if (loading) return;
    loading = true;
    error = '';
    try {
      if (demoMode) {
        state = {
          protocol_version: 1,
          journal_version: 1,
          operation: null,
          incomplete: false,
          requires_recovery: false,
          reconnect: {},
          recovery: {},
        };
      } else {
        state = parseRecoveryState(await GetIPodRecoveryState());
      }
      if (announce) dispatch('announce', { message: recoveryHeading() });
    } catch (caught) {
      error = caught instanceof Error ? caught.message : String(caught);
      dispatch('announce', { message: `Recovery state could not be loaded: ${error}` });
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    void loadRecoveryState(false);
  });
</script>

<section class:attention={!!state?.requires_recovery || !!state?.incomplete} class="recovery-panel" aria-busy={loading}>
  <header>
    <div>
      <p>Persisted safety state</p>
      <h3>Recovery</h3>
    </div>
    <button type="button" disabled={loading} on:click={() => loadRecoveryState()}>
      <RefreshCw size={15} />
      {loading ? 'Checking…' : 'Refresh'}
    </button>
  </header>

  {#if error}
    <div class="message error">
      <ShieldAlert size={20} />
      <div><strong>Recovery state unavailable</strong><span>{error}</span></div>
    </div>
  {:else if loading && !state}
    <div class="message">
      <RefreshCw size={20} />
      <div><strong>Checking the operation journal</strong><span>No device write is performed.</span></div>
    </div>
  {:else if state}
    <div class="message" class:warning={state.requires_recovery || state.incomplete}>
      {#if state.requires_recovery || state.incomplete}
        <AlertTriangle size={20} />
      {:else}
        <CheckCircle2 size={20} />
      {/if}
      <div>
        <strong>{recoveryHeading()}</strong>
        <span>{recoveryExplanation()}</span>
      </div>
    </div>

    {#if state.requires_recovery || state.incomplete}
      <dl>
        <div>
          <dt>Operation</dt>
          <dd>{operation?.kind?.replace(/_/g, ' ') || 'Restore or migration'}</dd>
        </div>
        <div>
          <dt>Last durable phase</dt>
          <dd>{operation?.phase?.replace(/_/g, ' ') || 'Unknown'}</dd>
        </div>
        <div>
          <dt>Reconnect this device</dt>
          <dd class="technical">{reconnect.required_device_id || operation?.target_id || 'Use the exact target from the interrupted operation'}</dd>
        </div>
        <div>
          <dt>Expected mount path</dt>
          <dd class="technical">{reconnect.mount_path || 'Reconnect and wait for the same mounted volume'}</dd>
        </div>
        <div>
          <dt>Safety snapshot</dt>
          <dd class="technical">{safetySnapshotId || 'Not recorded before the interruption'}</dd>
        </div>
      </dl>

      <div class="safety-copy">
        <HardDrive size={18} />
        <div>
          <strong>Do not substitute another iPod</strong>
          <span>{recovery.next_action || 'Reconnect the exact target, keep it mounted, and refresh this panel. Vela does not provide an unsafe dismissal for incomplete writes.'}</span>
        </div>
      </div>
    {/if}
  {/if}
</section>

<style>
  .recovery-panel {
    display: grid;
    gap: 13px;
    padding: 17px;
    border: 1px solid var(--line);
    border-radius: 14px;
    background: var(--surface);
  }
  .recovery-panel.attention {
    border-color: var(--warning-color);
  }
  header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
  }
  header p {
    margin: 0;
    color: var(--accent);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .07em;
    text-transform: uppercase;
  }
  h3 {
    margin: 3px 0 0;
    font-size: 18px;
  }
  button {
    min-height: 36px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 7px;
    padding: 0 11px;
    border: 0;
    border-radius: 9px;
    background: var(--surface-2);
    color: var(--text);
    font-weight: 650;
    cursor: pointer;
  }
  button:disabled {
    opacity: var(--disabled-opacity);
    cursor: default;
  }
  button:focus-visible {
    outline: 2px solid var(--focus-ring);
    outline-offset: 2px;
  }
  .message,
  .safety-copy {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    align-items: start;
    gap: 10px;
    padding: 12px;
    border-radius: 11px;
    background: var(--surface-2);
    color: var(--accent);
  }
  .message.warning,
  .safety-copy {
    color: var(--warning-color);
  }
  .message.error {
    color: var(--error-color);
  }
  .message div,
  .safety-copy div {
    display: grid;
    gap: 4px;
  }
  .message span,
  .safety-copy span {
    color: var(--muted);
    font-size: 12px;
    line-height: 1.5;
  }
  dl {
    margin: 0;
  }
  dl > div {
    display: grid;
    grid-template-columns: minmax(130px, .55fr) minmax(0, 1fr);
    gap: 14px;
    padding: 9px 0;
    border-top: 1px solid var(--line);
  }
  dt {
    color: var(--muted);
    font-size: 12px;
  }
  dd {
    min-width: 0;
    margin: 0;
    overflow-wrap: anywhere;
    text-align: right;
    font-size: 12px;
  }
  .technical {
    font-family: var(--font-mono, ui-monospace);
  }
  @media (max-width: 720px) {
    dl > div {
      grid-template-columns: 1fr;
      gap: 4px;
    }
    dd { text-align: left; }
  }
</style>
