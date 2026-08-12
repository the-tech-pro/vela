<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import { FileUp } from 'lucide-svelte';

  export let label: string;
  export let path = '';
  export let disabled = false;

  const dispatch = createEventDispatcher<{ choose: void }>();
</script>

<div class="file-choice">
  <div>
    <strong>{label}</strong>
    {#if path}
      <code title={path}>{path}</code>
    {:else}
      <span>No file selected</span>
    {/if}
  </div>
  <button type="button" {disabled} on:click={() => dispatch('choose')}>
    <FileUp size={15} aria-hidden="true" />
    Choose file
  </button>
</div>

<style>
  .file-choice {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    min-width: 0;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm, 8px);
    padding: 11px 12px;
    background: var(--surface-soft);
  }

  .file-choice > div {
    display: grid;
    min-width: 0;
    gap: 3px;
  }

  strong {
    color: var(--text);
    font-size: 11px;
  }

  span,
  code {
    overflow: hidden;
    color: var(--muted);
    font-size: 10px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  code {
    font-family: var(--font-mono, monospace);
  }

  button {
    display: inline-flex;
    flex: 0 0 auto;
    align-items: center;
    gap: 6px;
    min-height: 31px;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm, 8px);
    padding: 6px 9px;
    background: var(--surface);
    color: var(--text);
    font: inherit;
    font-size: 10px;
    font-weight: 700;
    cursor: pointer;
  }

  button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  button:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }

  @media (max-width: 640px) {
    .file-choice {
      align-items: stretch;
      flex-direction: column;
    }

    button {
      justify-content: center;
    }
  }
</style>
