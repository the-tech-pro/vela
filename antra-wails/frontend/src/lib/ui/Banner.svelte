<script lang="ts">
  export let tone: 'neutral' | 'success' | 'warning' | 'error' = 'neutral';
  export let live: 'off' | 'polite' | 'assertive' = 'off';
</script>

<div class={`ui-banner ${tone}`} role={tone === 'error' ? 'alert' : 'status'} aria-live={live}>
  <slot />
</div>

<style>
  .ui-banner{display:flex;align-items:flex-start;gap:10px;padding:12px 14px;border:1px solid var(--line);border-radius:12px;background:var(--surface);color:var(--muted)}.success{border-color:var(--success-border);background:var(--success-soft)}.warning{border-color:var(--warning-border);background:var(--warning-soft)}.error{border-color:var(--error-border);background:var(--error-soft)}
</style>
