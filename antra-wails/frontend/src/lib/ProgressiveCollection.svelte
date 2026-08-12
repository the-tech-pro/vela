<script lang="ts">
  import { afterUpdate, onMount } from 'svelte';

  export let items: any[] = [];
  export let itemKey: (item: any, index: number) => string = (item, index) =>
    String(item?.id ?? item?.key ?? item ?? index);
  export let initialCount = 36;
  export let chunkSize = 36;
  export let itemLabel = 'items';
  export let ariaLabel = 'Items';

  let visibleCount = Math.min(items.length, Math.max(1, initialCount));
  let previousItems = items;
  let sentinel: HTMLButtonElement | null = null;
  let observer: IntersectionObserver | null = null;
  let observedSentinel: HTMLButtonElement | null = null;
  let revealFrame = 0;
  let destroyed = false;

  $: if (items !== previousItems) {
    previousItems = items;
    visibleCount = Math.min(items.length, Math.max(1, initialCount));
  }
  $: visibleItems = items.slice(0, visibleCount);

  function revealNext() {
    if (destroyed || revealFrame || visibleCount >= items.length) return;
    revealFrame = window.requestAnimationFrame(() => {
      revealFrame = 0;
      if (destroyed) return;
      visibleCount = Math.min(items.length, visibleCount + Math.max(1, chunkSize));
    });
  }

  function sentinelIsNearViewport(): boolean {
    if (!sentinel) return false;
    const bounds = sentinel.getBoundingClientRect();
    return bounds.top <= window.innerHeight + 240 && bounds.bottom >= -240;
  }

  function observeSentinel(node: HTMLButtonElement) {
    sentinel = node;
    if (observer) {
      observedSentinel = node;
      observer.observe(node);
    }
    return {
      destroy() {
        observer?.unobserve(node);
        if (observedSentinel === node) observedSentinel = null;
        if (sentinel === node) sentinel = null;
      },
    };
  }

  afterUpdate(() => {
    if (observer && sentinel && sentinel !== observedSentinel) {
      if (observedSentinel) observer.unobserve(observedSentinel);
      observedSentinel = sentinel;
      observer.observe(sentinel);
    }
    if (visibleCount < items.length && sentinelIsNearViewport()) revealNext();
  });

  onMount(() => {
    destroyed = false;
    observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) revealNext();
    }, { rootMargin: '240px' });
    if (sentinel) {
      observedSentinel = sentinel;
      observer.observe(sentinel);
    }

    return () => {
      destroyed = true;
      observer?.disconnect();
      observer = null;
      observedSentinel = null;
      if (revealFrame) window.cancelAnimationFrame(revealFrame);
      revealFrame = 0;
    };
  });
</script>

<div class="progressive-collection">
  <div class="progressive-items" role="list" aria-label={ariaLabel}>
    {#each visibleItems as item, index (itemKey(item, index))}
      <div class="progressive-item" role="listitem" aria-posinset={index + 1} aria-setsize={items.length}>
        <slot {item} {index} posinset={index + 1} setsize={items.length} />
      </div>
    {/each}
  </div>
  {#if visibleCount < items.length}
    <button
      use:observeSentinel
      class="load-more"
      type="button"
      on:click={revealNext}
      aria-label={`Show more ${itemLabel}`}
    >
      <strong>Show more</strong>
      <span>{visibleCount.toLocaleString()} of {items.length.toLocaleString()}</span>
    </button>
  {/if}
</div>

<style>
  .progressive-collection,
  .progressive-items {
    display: contents;
  }

  .progressive-item {
    display: contents;
  }

  .load-more {
    min-height: 92px;
    display: grid;
    place-items: center;
    align-content: center;
    gap: 4px;
    padding: 12px;
    border: 1px solid var(--line);
    border-radius: 13px;
    background: var(--surface);
    color: var(--accent);
    cursor: pointer;
  }

  .load-more span {
    color: var(--muted);
    font-size: 10px;
  }

  .load-more:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
</style>
