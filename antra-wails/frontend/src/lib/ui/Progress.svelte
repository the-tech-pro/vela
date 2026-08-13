<script lang="ts">
  export let value: number | null = null;
  export let max = 100;
  export let label = 'Progress';
  $: bounded = value == null ? null : Math.max(0, Math.min(max, value));
</script>

<div class:indeterminate={bounded == null} class="ui-progress" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={max} aria-valuenow={bounded == null ? undefined : bounded}>
  <span style:--progress={bounded == null ? '35%' : `${max > 0 ? (bounded / max) * 100 : 0}%`}></span>
</div>

<style>
  .ui-progress{height:7px;overflow:hidden;border-radius:999px;background:var(--surface-2)}.ui-progress span{width:var(--progress);height:100%;display:block;border-radius:inherit;background:var(--accent);transition:width .16s ease}.indeterminate span{animation:indeterminate 1.25s ease-in-out infinite}@keyframes indeterminate{from{transform:translateX(-100%)}to{transform:translateX(290%)}}@media(prefers-reduced-motion:reduce){.ui-progress span{transition:none}.indeterminate span{animation:none}}
</style>
