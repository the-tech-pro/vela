<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import {
    AlertTriangle, CheckCircle2, LoaderCircle, LockKeyhole, Square, XCircle,
  } from 'lucide-svelte';
  import { CancelIPodOperationByID } from '../../../wailsjs/go/main/App.js';
  import type { IPodOperationEnvelope } from '../ipodTypes';
  import { isIPodOperationActive } from '../ipodTypes';

  export let operation: IPodOperationEnvelope;

  const dispatch = createEventDispatcher<{
    notify: { tone: 'error' | 'warning' | 'success'; message: string };
    announce: { message: string };
  }>();
  let cancelling = false;

  $: active = isIPodOperationActive(operation);
  $: protectedPhase = operation.phase === 'committing' || operation.phase === 'finalizing';
  $: credibleProgress = active
    && operation.total > 0
    && operation.current >= 0
    && operation.current <= operation.total;
  $: failed = operation.status === 'failed' || operation.phase === 'failed';
  $: cancelled = operation.status === 'cancelled' || operation.phase === 'cancelled';
  $: completed = !active && !failed && !cancelled;
  $: cancelAvailable = active
    && operation.can_cancel
    && !protectedPhase
    && operation.kind !== 'backup_verify'
    && operation.kind !== 'capacity_unlock';
  $: title = active
    ? `${operationLabel(operation.kind)} in progress`
    : failed
      ? `${operationLabel(operation.kind)} failed`
      : cancelled
        ? `${operationLabel(operation.kind)} cancelled`
        : `${operationLabel(operation.kind)} complete`;
  $: phaseLabel = operation.phase
    ? operation.phase.replace(/[_:-]+/g, ' ')
    : 'working';

  function operationLabel(kind: string): string {
    const labels: Record<string, string> = {
      backup: 'iPod backup',
      manual_backup: 'Manual backup',
      backup_verify: 'Backup verification',
      backup_note: 'Backup note update',
      backup_export: 'Backup export',
      backup_delete: 'Backup deletion',
      restore: 'Same-device restore',
      migration: 'Replacement migration',
      sync: 'iPod sync',
      capacity_unlock: 'Capacity unlock step',
    };
    return labels[kind] || 'iPod operation';
  }

  async function cancelOperation() {
    if (!cancelAvailable || cancelling) return;
    cancelling = true;
    try {
      await CancelIPodOperationByID(operation.operation_id);
      const message = 'Cancellation requested. Vela will stop at the next safe boundary.';
      dispatch('announce', { message });
      dispatch('notify', { tone: 'warning', message });
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : String(caught);
      dispatch('notify', { tone: 'error', message });
    } finally {
      cancelling = false;
    }
  }
</script>

<section
  class:failed
  class:completed
  class:cancelled
  class:protected={protectedPhase}
  class="operation-status"
  aria-labelledby="ipod-operation-status-title"
>
  <div class="status-icon" aria-hidden="true">
    {#if active && protectedPhase}
      <LockKeyhole size={19} />
    {:else if active}
      <span class="rotating"><LoaderCircle size={20} /></span>
    {:else if failed}
      <XCircle size={20} />
    {:else if cancelled}
      <Square size={18} />
    {:else}
      <CheckCircle2 size={20} />
    {/if}
  </div>

  <div class="status-copy">
    <strong id="ipod-operation-status-title">{title}</strong>
    <span>{operation.message || (active ? `Current phase: ${phaseLabel}.` : `Finished in the ${phaseLabel} phase.`)}</span>
    {#if active}
      {#if credibleProgress}
        <progress max={operation.total} value={operation.current}>
          {Math.round(operation.current / operation.total * 100)}%
        </progress>
        <small>{phaseLabel} · {operation.current.toLocaleString()}/{operation.total.toLocaleString()}</small>
      {:else}
        <small class="indeterminate">{phaseLabel} · Progress is indeterminate</small>
      {/if}
      {#if protectedPhase}
        <small class="commit-warning"><AlertTriangle size={14} /> Commit has started. Keep Vela open and the iPod connected; cancellation is no longer safe.</small>
      {/if}
    {/if}
    {#if operation.recovery?.required}
      <small class="recovery-warning"><AlertTriangle size={14} /> Recovery guidance is available below. Do not dismiss or disconnect the target.</small>
    {/if}
  </div>

  {#if cancelAvailable}
    <button type="button" disabled={cancelling} on:click={cancelOperation}>
      <Square size={14} />
      {cancelling ? 'Requesting…' : 'Cancel safely'}
    </button>
  {/if}
</section>

<style>
  .operation-status {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    align-items: start;
    gap: 12px;
    padding: 14px;
    border: 1px solid var(--line);
    border-radius: 14px;
    background: var(--surface);
  }
  .operation-status.protected {
    border-color: var(--warning-color);
  }
  .operation-status.failed {
    border-color: var(--error-color);
  }
  .operation-status.completed {
    border-color: var(--success-color);
  }
  .status-icon {
    width: 36px;
    height: 36px;
    display: grid;
    place-items: center;
    border-radius: 10px;
    background: var(--accent-soft);
    color: var(--accent);
  }
  .failed .status-icon {
    color: var(--error-color);
  }
  .completed .status-icon {
    color: var(--success-color);
  }
  .protected .status-icon {
    color: var(--warning-color);
  }
  .status-copy {
    min-width: 0;
    display: grid;
    gap: 5px;
  }
  .status-copy > span,
  small {
    color: var(--muted);
    font-size: 11px;
    line-height: 1.45;
  }
  progress {
    width: 100%;
    height: 6px;
    margin-top: 3px;
    accent-color: var(--accent);
  }
  .indeterminate::before {
    content: '';
    width: 7px;
    height: 7px;
    display: inline-block;
    margin-right: 7px;
    border-radius: 50%;
    background: var(--accent);
  }
  .commit-warning,
  .recovery-warning {
    display: flex;
    align-items: flex-start;
    gap: 6px;
    color: var(--warning-color);
  }
  button {
    min-height: 36px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 7px;
    padding: 0 12px;
    border: 1px solid var(--line);
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
  .rotating {
    display: inline-grid;
    animation: rotate .8s linear infinite;
  }
  @keyframes rotate {
    to { transform: rotate(360deg); }
  }
  @media (prefers-reduced-motion: reduce) {
    .rotating { animation: none; }
  }
  @media (max-width: 720px) {
    .operation-status {
      grid-template-columns: auto minmax(0, 1fr);
    }
    button {
      grid-column: 1 / -1;
      width: 100%;
    }
  }
</style>
