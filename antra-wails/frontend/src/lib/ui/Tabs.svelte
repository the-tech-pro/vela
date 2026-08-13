<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  interface TabOption { value: string; label: string; disabled?: boolean; }
  export let value = '';
  export let tabs: TabOption[] = [];
  export let label = 'Sections';
  const dispatch = createEventDispatcher<{ change: string }>();

  function select(next: string) {
    value = next;
    dispatch('change', next);
  }

  function keydown(event: KeyboardEvent) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const enabled = tabs.filter(tab => !tab.disabled);
    if (!enabled.length) return;
    let index = enabled.findIndex(tab => tab.value === value);
    if (event.key === 'Home') index = 0;
    else if (event.key === 'End') index = enabled.length - 1;
    else index = (index + (event.key === 'ArrowRight' ? 1 : -1) + enabled.length) % enabled.length;
    event.preventDefault();
    select(enabled[index].value);
    const container = event.currentTarget as HTMLElement;
    requestAnimationFrame(() => container.querySelector<HTMLButtonElement>('[tabindex="0"]')?.focus());
  }
</script>

<div class="ui-tabs" role="tablist" aria-label={label} tabindex="-1" on:keydown={keydown}>
  {#each tabs as tab (tab.value)}
    <button type="button" role="tab" aria-selected={tab.value === value} tabindex={tab.value === value ? 0 : -1} disabled={tab.disabled} on:click={() => select(tab.value)}>{tab.label}</button>
  {/each}
</div>

<style>
  .ui-tabs{display:flex;gap:4px;overflow:auto;padding:4px;border-radius:11px;background:var(--surface-2)}.ui-tabs button{min-height:36px;padding:0 11px;border:0;border-radius:8px;background:transparent;color:var(--muted);font:inherit;white-space:nowrap;cursor:pointer}.ui-tabs button[aria-selected="true"]{background:var(--surface);color:var(--accent);box-shadow:var(--shadow-sm)}.ui-tabs button:disabled{opacity:var(--disabled-opacity)}.ui-tabs button:focus-visible{outline:2px solid var(--focus-ring);outline-offset:1px}
</style>
