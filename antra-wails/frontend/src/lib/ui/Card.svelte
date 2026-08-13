<script lang="ts">
  export let elevated = false;
  export let labelledby = '';
</script>

<section class:elevated class="ui-card" aria-labelledby={labelledby || undefined}>
  <slot />
</section>

<style>
  .ui-card{min-width:0;padding:16px;border:1px solid var(--line);border-radius:14px;background:var(--surface)}.ui-card.elevated{box-shadow:var(--shadow-card,var(--shadow))}
</style>
