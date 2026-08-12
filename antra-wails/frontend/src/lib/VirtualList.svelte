<script context="module" lang="ts">
  const restoredScrollPositions: Record<string, number> = Object.create(null);
  const restoredScrollKeys: string[] = [];
  const MAX_RESTORED_LISTS = 64;

  function rememberScrollPosition(key: string, value: number) {
    if (!key) return;
    const currentIndex = restoredScrollKeys.indexOf(key);
    if (currentIndex >= 0) restoredScrollKeys.splice(currentIndex, 1);
    restoredScrollKeys.push(key);
    restoredScrollPositions[key] = value;
    if (restoredScrollKeys.length > MAX_RESTORED_LISTS) {
      const oldestKey = restoredScrollKeys.shift();
      if (oldestKey) delete restoredScrollPositions[oldestKey];
    }
  }

  function restoredScrollPosition(key: string): number {
    return restoredScrollPositions[key] || 0;
  }
</script>

<script lang="ts">
  import { createEventDispatcher, tick } from 'svelte';

  const MAX_OVERSCAN_ROWS = 20;
  const FOCUSABLE_SELECTOR = [
    'a[href]',
    'area[href]',
    'button:not([disabled])',
    'input:not([disabled]):not([type="hidden"])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    'summary',
    'iframe',
    'audio[controls]',
    'video[controls]',
    '[contenteditable]:not([contenteditable="false"])',
    '[tabindex]:not([tabindex="-1"])',
  ].join(',');
  const fallbackObjectKeys = new WeakMap<object, string>();
  let fallbackObjectKeySequence = 0;

  function defaultItemKey(item: any, index: number): string {
    const explicitKey = item?.id ?? item?.key;
    if (explicitKey !== undefined && explicitKey !== null) return String(explicitKey);
    if (item !== null && (typeof item === 'object' || typeof item === 'function')) {
      const objectItem = item as object;
      let key = fallbackObjectKeys.get(objectItem);
      if (!key) {
        fallbackObjectKeySequence += 1;
        key = `virtual-object:${fallbackObjectKeySequence}`;
        fallbackObjectKeys.set(objectItem, key);
      }
      return key;
    }
    return String(item ?? index);
  }

  export let items: any[] = [];
  export let itemKey: (item: any, index: number) => string = defaultItemKey;
  export let rowHeight = 58;
  export let overscan = 4;
  export let maxHeight = '65vh';
  export let viewportClass = '';
  export let restoreKey = '';
  export let ariaLabel = 'Items';

  const dispatch = createEventDispatcher<{
    scrollstate: { atBottom: boolean; scrollTop: number };
  }>();

  let container: HTMLDivElement | null = null;
  let scrollTop = 0;
  let viewportHeight = 480;
  let activeRestoreKey = restoreKey;
  let lastAtBottom = true;
  let destroyed = false;
  let restoringScroll = false;
  let pendingRestoreTop: number | null = null;
  let restoreFrame = 0;

  $: safeRowHeight = Math.max(1, rowHeight);
  $: safeOverscan = Math.max(0, Math.min(MAX_OVERSCAN_ROWS, Math.floor(overscan)));
  $: totalHeight = items.length * safeRowHeight;
  $: startIndex = Math.max(0, Math.floor(scrollTop / safeRowHeight) - safeOverscan);
  $: endIndex = Math.min(
    items.length,
    Math.ceil((scrollTop + viewportHeight) / safeRowHeight) + safeOverscan,
  );
  $: visibleItems = items.slice(startIndex, endIndex).map((item, localIndex) => {
    const index = startIndex + localIndex;
    return { item, index, key: itemKey(item, index) };
  });
  $: translateY = startIndex * safeRowHeight;

  function rememberAttachedScrollPosition(node: HTMLDivElement, key: string) {
    if (destroyed || container !== node || !node.isConnected) return;
    rememberScrollPosition(key, node.scrollTop);
  }

  function syncScrollState(rememberPosition = true) {
    if (!container) return;
    scrollTop = container.scrollTop;
    if (rememberPosition) rememberAttachedScrollPosition(container, activeRestoreKey);
    const atBottom = totalHeight - scrollTop - container.clientHeight <= safeRowHeight;
    if (atBottom !== lastAtBottom) {
      lastAtBottom = atBottom;
      dispatch('scrollstate', { atBottom, scrollTop });
    }
  }

  function handleScroll() {
    if (!container) return;
    let rememberPosition = !restoringScroll;
    if (pendingRestoreTop !== null && !restoringScroll) {
      const { reachableTarget } = restorationTarget(container, pendingRestoreTop, totalHeight);
      if (Math.abs(container.scrollTop - reachableTarget) <= 1) {
        rememberPosition = false;
      } else {
        cancelPendingRestore();
        rememberPosition = true;
      }
    }
    syncScrollState(rememberPosition);
  }

  function cancelPendingRestore() {
    if (restoreFrame) window.cancelAnimationFrame(restoreFrame);
    restoreFrame = 0;
    restoringScroll = false;
    pendingRestoreTop = null;
  }

  export function scrollToIndex(index: number, behavior: ScrollBehavior = 'auto') {
    if (!container || !items.length) return;
    cancelPendingRestore();
    const clampedIndex = Math.max(0, Math.min(items.length - 1, Math.floor(index)));
    const centeredTop = clampedIndex * safeRowHeight
      - Math.max(0, (container.clientHeight - safeRowHeight) / 2);
    container.scrollTo({ top: Math.max(0, centeredTop), behavior });
    if (behavior === 'auto') syncScrollState();
  }

  export function scrollToKey(key: string, behavior: ScrollBehavior = 'auto') {
    const index = items.findIndex((item, itemIndex) => itemKey(item, itemIndex) === key);
    if (index >= 0) scrollToIndex(index, behavior);
  }

  export function scrollToEnd(behavior: ScrollBehavior = 'auto') {
    if (!container) return;
    cancelPendingRestore();
    container.scrollTo({ top: Math.max(0, totalHeight - container.clientHeight), behavior });
    if (behavior === 'auto') syncScrollState();
  }

  function revealIndex(index: number) {
    if (!container || !items.length) return;
    cancelPendingRestore();
    const clampedIndex = Math.max(0, Math.min(items.length - 1, Math.floor(index)));
    const rowTop = clampedIndex * safeRowHeight;
    const rowBottom = rowTop + safeRowHeight;
    const viewportTop = container.scrollTop;
    const viewportBottom = viewportTop + container.clientHeight;
    let nextScrollTop = viewportTop;
    if (rowTop < viewportTop) nextScrollTop = rowTop;
    else if (rowBottom > viewportBottom) nextScrollTop = rowBottom - container.clientHeight;
    const maximumScrollTop = Math.max(0, totalHeight - container.clientHeight);
    container.scrollTop = Math.max(0, Math.min(maximumScrollTop, nextScrollTop));
    syncScrollState();
  }

  function isFocusable(element: HTMLElement): boolean {
    return element.tabIndex >= 0
      && !element.matches(':disabled')
      && !element.closest('[hidden], [inert], [aria-hidden="true"]')
      && element.getClientRects().length > 0
      && window.getComputedStyle(element).visibility !== 'hidden';
  }

  function focusableElements(scope: ParentNode): HTMLElement[] {
    return Array.from(scope.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR))
      .filter(isFocusable);
  }

  function focusedControl(target: Element, row: HTMLElement): HTMLElement | null {
    const candidate = target.closest(FOCUSABLE_SELECTOR) as HTMLElement | null;
    return candidate && row.contains(candidate) && isFocusable(candidate) ? candidate : null;
  }

  function renderedRow(index: number): HTMLElement | null {
    return container?.querySelector<HTMLElement>(`[data-virtual-index="${index}"]`) ?? null;
  }

  function renderedFocusableInDirection(index: number, direction: 1 | -1): HTMLElement | null {
    if (!container) return null;
    const rows = Array.from(
      container.querySelectorAll<HTMLElement>('.virtual-row[data-virtual-index]'),
    );
    if (direction < 0) rows.reverse();
    for (const row of rows) {
      const rowIndex = Number(row.dataset.virtualIndex);
      if (!Number.isFinite(rowIndex) || (direction > 0 ? rowIndex <= index : rowIndex >= index)) {
        continue;
      }
      const controls = focusableElements(row);
      if (controls.length) return direction > 0 ? controls[0] : controls[controls.length - 1];
    }
    return null;
  }

  async function focusIndex(index: number, preferredControlIndex: number) {
    revealIndex(index);
    await tick();
    const row = renderedRow(index);
    if (!row) return;
    const controls = focusableElements(row);
    const focusTarget = controls.length
      ? controls[Math.min(preferredControlIndex, controls.length - 1)]
      : row;
    focusTarget.focus({ preventScroll: true });
  }

  async function handleTabNavigation(
    event: KeyboardEvent,
    target: Element,
    row: HTMLElement,
    currentIndex: number,
  ) {
    const direction: 1 | -1 = event.shiftKey ? -1 : 1;
    const controls = focusableElements(row);
    const control = focusedControl(target, row);
    if (control) {
      const controlIndex = controls.indexOf(control);
      const hasControlInRow = direction > 0
        ? controlIndex < controls.length - 1
        : controlIndex > 0;
      if (hasControlInRow) return;
    }

    const renderedTarget = renderedFocusableInDirection(currentIndex, direction);
    if (renderedTarget) {
      if (!control) {
        event.preventDefault();
        renderedTarget.focus({ preventScroll: true });
      }
      return;
    }

    const boundaryIndex = direction > 0 ? endIndex : startIndex - 1;
    const hasUnrenderedItems = direction > 0
      ? boundaryIndex < items.length
      : boundaryIndex >= 0;
    if (!hasUnrenderedItems) return;

    event.preventDefault();
    revealIndex(boundaryIndex);
    await tick();
    const nextTarget = renderedFocusableInDirection(currentIndex, direction);
    if (nextTarget) {
      nextTarget.focus({ preventScroll: true });
      return;
    }
    renderedRow(boundaryIndex)?.focus({ preventScroll: true });
  }

  async function handleKeydown(event: KeyboardEvent) {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
    if (!['Tab', 'ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    const target = event.target;
    if (!(target instanceof Element)) return;
    const row = target.closest('[data-virtual-index]') as HTMLElement | null;
    if (!row || !container || !container.contains(row)) return;
    const currentIndex = Number(row.dataset.virtualIndex);
    if (!Number.isFinite(currentIndex)) return;

    if (event.key === 'Tab') {
      await handleTabNavigation(event, target, row, currentIndex);
      return;
    }
    if (target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])')) {
      return;
    }

    event.preventDefault();
    const nextIndex = event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? items.length - 1
        : Math.max(0, Math.min(items.length - 1, currentIndex + (event.key === 'ArrowDown' ? 1 : -1)));
    if (nextIndex === currentIndex) return;

    const controls = focusableElements(row);
    const control = focusedControl(target, row);
    const preferredControlIndex = control ? Math.max(0, controls.indexOf(control)) : 0;
    await focusIndex(nextIndex, preferredControlIndex);
  }

  interface VirtualViewportParameters {
    restoreKey: string;
    totalHeight: number;
    viewportHeight: number;
  }

  function restorationTarget(node: HTMLDivElement, requestedScrollTop: number, declaredTotalHeight: number) {
    const declaredMaximum = Math.max(0, declaredTotalHeight - node.clientHeight);
    const desiredTarget = Math.max(0, Math.min(requestedScrollTop, declaredMaximum));
    const reachableMaximum = Math.max(0, node.scrollHeight - node.clientHeight);
    return {
      desiredTarget,
      reachableMaximum,
      reachableTarget: Math.min(desiredTarget, reachableMaximum),
    };
  }

  function virtualViewport(node: HTMLDivElement, initial: VirtualViewportParameters) {
    container = node;
    destroyed = false;
    activeRestoreKey = initial.restoreKey;

    const scheduleRestore = () => {
      if (restoreFrame || pendingRestoreTop === null) return;
      restoreFrame = window.requestAnimationFrame(() => {
        restoreFrame = 0;
        if (destroyed || container !== node) {
          restoringScroll = false;
          return;
        }
        const requestedScrollTop = pendingRestoreTop;
        if (requestedScrollTop === null) {
          restoringScroll = false;
          return;
        }
        const { desiredTarget, reachableMaximum, reachableTarget } = restorationTarget(
          node,
          requestedScrollTop,
          initial.totalHeight,
        );
        restoringScroll = true;
        node.scrollTop = reachableTarget;
        const fullyRestored = reachableMaximum + 1 >= desiredTarget
          && Math.abs(node.scrollTop - desiredTarget) <= 1;
        if (fullyRestored) {
          pendingRestoreTop = null;
          restoringScroll = false;
        } else {
          pendingRestoreTop = requestedScrollTop;
        }
        syncScrollState(fullyRestored);
        if (!fullyRestored) scheduleRestore();
      });
    };

    const restore = (requestedScrollTop: number) => {
      if (restoreFrame) window.cancelAnimationFrame(restoreFrame);
      restoreFrame = 0;
      pendingRestoreTop = requestedScrollTop;
      restoringScroll = true;
      scheduleRestore();
    };

    const clamp = (contentHeight: number, visibleHeight: number) => {
      const maximumScrollTop = Math.max(0, contentHeight - visibleHeight);
      if (node.scrollTop <= maximumScrollTop) return;
      node.scrollTop = maximumScrollTop;
      handleScroll();
    };

    restore(restoredScrollPosition(activeRestoreKey));
    const observer = new ResizeObserver(entries => {
      if (destroyed || container !== node) return;
      const height = entries[0]?.contentRect.height;
      if (height && height !== viewportHeight) viewportHeight = height;
    });
    observer.observe(node);
    node.addEventListener('scroll', handleScroll);
    node.addEventListener('keydown', handleKeydown);

    return {
      update(next: VirtualViewportParameters) {
        if (next.restoreKey !== activeRestoreKey) {
          if (pendingRestoreTop === null && !restoringScroll) {
            rememberAttachedScrollPosition(node, activeRestoreKey);
          }
          activeRestoreKey = next.restoreKey;
          initial = next;
          restore(restoredScrollPosition(activeRestoreKey));
          return;
        }
        initial = next;
        if (pendingRestoreTop !== null) {
          restore(pendingRestoreTop);
          return;
        }
        clamp(next.totalHeight, next.viewportHeight);
      },
      destroy() {
        if (pendingRestoreTop === null && !restoringScroll) {
          rememberAttachedScrollPosition(node, activeRestoreKey);
        }
        destroyed = true;
        observer.disconnect();
        node.removeEventListener('scroll', handleScroll);
        node.removeEventListener('keydown', handleKeydown);
        if (restoreFrame) window.cancelAnimationFrame(restoreFrame);
        restoreFrame = 0;
        restoringScroll = false;
        pendingRestoreTop = null;
        if (container === node) container = null;
      },
    };
  }
</script>

<div
  class={`virtual-list ${viewportClass}`}
  use:virtualViewport={{ restoreKey, totalHeight, viewportHeight }}
  role="list"
  aria-label={ariaLabel}
  style:--virtual-content-height={`${totalHeight}px`}
  style:--virtual-max-height={maxHeight}
>
  <div class="virtual-spacer">
    <div class="virtual-window" style:--virtual-offset={`${translateY}px`}>
      {#each visibleItems as visibleItem (visibleItem.key)}
        <div
          class="virtual-row"
          role="listitem"
          aria-posinset={visibleItem.index + 1}
          aria-setsize={items.length}
          data-virtual-index={visibleItem.index}
          tabindex="-1"
          style:--virtual-row-height={`${safeRowHeight}px`}
        >
          <slot
            item={visibleItem.item}
            index={visibleItem.index}
            posinset={visibleItem.index + 1}
            setsize={items.length}
          />
        </div>
      {/each}
    </div>
  </div>
</div>

<style>
  .virtual-list {
    position: relative;
    width: 100%;
    height: min(var(--virtual-content-height), var(--virtual-max-height));
    min-height: min(var(--virtual-content-height), 120px);
    max-height: var(--virtual-max-height);
    overflow: auto;
    overscroll-behavior: contain;
    contain: layout paint style;
  }

  .virtual-spacer {
    position: relative;
    width: 100%;
    height: var(--virtual-content-height);
  }

  .virtual-window {
    position: absolute;
    inset: 0 0 auto;
    transform: translateY(var(--virtual-offset));
    will-change: transform;
  }

  .virtual-row {
    width: 100%;
    height: var(--virtual-row-height);
    overflow: visible;
  }

  .virtual-row:focus-visible {
    outline: 2px solid var(--focus-ring, var(--accent-color));
    outline-offset: -2px;
  }
</style>
