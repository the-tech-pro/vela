<script lang="ts">
  import { normalizeArtworkSize, sizedArtworkUrl } from './artwork';

  export let src: string | null | undefined = '';
  export let alt = '';
  export let loading: 'eager' | 'lazy' = 'lazy';
  export let decoding: 'sync' | 'async' | 'auto' = 'async';
  export let fetchPriority: 'high' | 'low' | 'auto' = 'auto';
  export let displaySize = 0;

  let currentSrc = '';
  let failed = false;

  $: requestedSize = normalizeArtworkSize(displaySize);
  $: resolvedSrc = sizedArtworkUrl(src, requestedSize);
  $: if (resolvedSrc !== currentSrc) {
    currentSrc = resolvedSrc;
    failed = false;
  }

  function handleError(event: Event) {
    const failedSrc = (event.currentTarget as HTMLImageElement).getAttribute('src')
      || (event.currentTarget as HTMLImageElement).currentSrc;
    if (failedSrc === resolvedSrc) failed = true;
  }

  function applyFetchPriority(node: HTMLImageElement, priority: typeof fetchPriority) {
    node.setAttribute('fetchpriority', priority);
    return {
      update(nextPriority: typeof fetchPriority) {
        node.setAttribute('fetchpriority', nextPriority);
      }
    };
  }
</script>

<span class="artwork-image">
  {#if resolvedSrc && !failed}
    <img
      src={resolvedSrc}
      {alt}
      {loading}
      {decoding}
      width={requestedSize || undefined}
      height={requestedSize || undefined}
      use:applyFetchPriority={fetchPriority}
      on:error={handleError}
    />
  {:else}
    <span
      class="artwork-fallback"
      role={alt ? 'img' : undefined}
      aria-label={alt || undefined}
      aria-hidden={alt ? undefined : 'true'}
    >
      <slot>
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <rect x="4" y="4" width="16" height="16" rx="2" />
          <path d="m7 16 3.5-3.5 2.5 2 2-2 2 3.5" />
          <circle cx="9" cy="9" r="1.25" />
        </svg>
      </slot>
    </span>
  {/if}
</span>

<style>
  .artwork-image {
    width: 100%;
    height: 100%;
    min-width: 0;
    aspect-ratio: 1;
    display: grid;
    overflow: hidden;
    border-radius: inherit;
    background: var(--surface-2, var(--surface-color));
    color: var(--faint, var(--text-faint));
  }

  img,
  .artwork-fallback {
    width: 100%;
    height: 100%;
    min-width: 0;
    grid-area: 1 / 1;
    border-radius: inherit;
  }

  img {
    display: block;
    object-fit: cover;
  }

  .artwork-fallback {
    display: grid;
    place-items: center;
  }

  .artwork-fallback :global(svg) {
    width: 38%;
    height: 38%;
  }
</style>
