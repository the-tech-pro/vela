<script lang="ts">
  export let label: string;
  export let disabled = false;
  export let pressed: boolean | undefined = undefined;
  export let type: 'button' | 'submit' | 'reset' = 'button';
</script>

<button class="icon-button" {type} {disabled} aria-label={label} title={label} aria-pressed={pressed} on:click>
  <slot />
</button>

<style>
  .icon-button{width:40px;height:40px;display:grid;place-items:center;padding:0;border:1px solid transparent;border-radius:10px;background:var(--surface-2);color:var(--muted);cursor:pointer}.icon-button:hover:not(:disabled){background:var(--surface-hover);color:var(--text)}.icon-button:disabled{opacity:var(--disabled-opacity);cursor:default}.icon-button:focus-visible{outline:2px solid var(--focus-ring);outline-offset:2px}
</style>
