<script lang="ts">
  export let variant: 'primary' | 'secondary' | 'ghost' | 'danger' = 'secondary';
  export let type: 'button' | 'submit' | 'reset' = 'button';
  export let disabled = false;
  export let busy = false;
  export let label = '';
</script>

<button class={`ui-button ${variant}`} {type} disabled={disabled || busy} aria-busy={busy} aria-label={label || undefined} on:click>
  <slot />
</button>

<style>
  .ui-button{min-height:36px;display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:0 14px;border:1px solid transparent;border-radius:10px;font:inherit;font-size:12px;font-weight:650;cursor:pointer}.primary{background:var(--accent);color:var(--accent-contrast,#fff)}.secondary{border-color:var(--line);background:var(--surface-2);color:var(--text)}.ghost{background:transparent;color:var(--muted)}.danger{background:var(--error-color);color:var(--accent-contrast,#fff)}.ui-button:disabled{opacity:var(--disabled-opacity);cursor:default}.ui-button:focus-visible{outline:2px solid var(--focus-ring);outline-offset:2px}
</style>
