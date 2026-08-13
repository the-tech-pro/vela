<script lang="ts">
  import { createEventDispatcher, onDestroy, tick } from 'svelte';
  import { Check, ChevronDown } from 'lucide-svelte';

  interface SelectOption {
    value: string;
    label: string;
    disabled?: boolean;
    description?: string;
  }

  export let value = '';
  export let options: SelectOption[] = [];
  export let label = '';
  export let help = '';
  export let error = '';
  export let disabled = false;
  export let placeholder = 'Choose an option';
  export let id = `select-${Math.random().toString(36).slice(2)}`;
  export let ariaLabel = '';

  const dispatch = createEventDispatcher<{ change: string }>();
  const listboxId = `${id}-listbox`;
  const helpId = `${id}-help`;
  const errorId = `${id}-error`;
  let trigger: HTMLButtonElement | null = null;
  let menu: HTMLDivElement | null = null;
  let open = false;
  let activeIndex = -1;
  let menuTop = 0;
  let menuLeft = 0;
  let menuWidth = 180;
  let menuMaxHeight = 280;
  let typeahead = '';
  let typeaheadTimer: ReturnType<typeof setTimeout> | null = null;

  function triggerNode(node: HTMLButtonElement) {
    trigger = node;
    return { destroy: () => { if (trigger === node) trigger = null; } };
  }

  function menuNode(node: HTMLDivElement) {
    menu = node;
    return { destroy: () => { if (menu === node) menu = null; } };
  }

  $: selectedIndex = options.findIndex(option => option.value === value);
  $: selected = selectedIndex >= 0 ? options[selectedIndex] : null;
  $: activeId = activeIndex >= 0 ? `${id}-option-${activeIndex}` : undefined;
  $: describedBy = [error ? errorId : '', help ? helpId : ''].filter(Boolean).join(' ') || undefined;

  function enabledIndex(start: number, direction: 1 | -1): number {
    if (!options.length) return -1;
    let index = start;
    for (let count = 0; count < options.length; count += 1) {
      index = (index + direction + options.length) % options.length;
      if (!options[index].disabled) return index;
    }
    return -1;
  }

  function updatePosition() {
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const roomBelow = window.innerHeight - rect.bottom - 12;
    const roomAbove = rect.top - 12;
    menuMaxHeight = Math.max(120, Math.min(300, Math.max(roomBelow, roomAbove)));
    menuTop = roomBelow >= Math.min(240, menuMaxHeight)
      ? rect.bottom + 6
      : Math.max(8, rect.top - menuMaxHeight - 6);
    menuWidth = Math.max(180, rect.width);
    menuLeft = Math.min(rect.left, Math.max(8, window.innerWidth - menuWidth - 8));
  }

  async function openMenu() {
    if (disabled || open) return;
    open = true;
    activeIndex = selectedIndex >= 0 && !options[selectedIndex]?.disabled
      ? selectedIndex
      : enabledIndex(-1, 1);
    await tick();
    updatePosition();
    scrollActive();
  }

  function closeMenu(restoreFocus = true) {
    if (!open) return;
    open = false;
    if (restoreFocus) tick().then(() => trigger?.focus());
  }

  function choose(index: number) {
    const option = options[index];
    if (!option || option.disabled) return;
    value = option.value;
    dispatch('change', value);
    closeMenu();
  }

  function scrollActive() {
    tick().then(() => {
      const activeOption = menu?.querySelector<HTMLElement>(`#${activeId}`);
      if (activeOption && typeof activeOption.scrollIntoView === 'function') {
        activeOption.scrollIntoView({ block: 'nearest' });
      }
    });
  }

  function move(direction: 1 | -1) {
    activeIndex = enabledIndex(activeIndex, direction);
    scrollActive();
  }

  function handleTypeahead(key: string) {
    typeahead += key.toLocaleLowerCase();
    if (typeaheadTimer) clearTimeout(typeaheadTimer);
    typeaheadTimer = setTimeout(() => typeahead = '', 650);
    const match = options.findIndex(option =>
      !option.disabled && option.label.toLocaleLowerCase().startsWith(typeahead)
    );
    if (match >= 0) {
      activeIndex = match;
      scrollActive();
    }
  }

  function handleKeydown(event: KeyboardEvent) {
    if (disabled) return;
    if (!open && ['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
      event.preventDefault();
      openMenu();
      return;
    }
    if (!open) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      closeMenu();
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      move(event.key === 'ArrowDown' ? 1 : -1);
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      activeIndex = enabledIndex(event.key === 'Home' ? -1 : 0, event.key === 'Home' ? 1 : -1);
      scrollActive();
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      choose(activeIndex);
    } else if (event.key.length === 1 && /\S/.test(event.key)) {
      handleTypeahead(event.key);
    } else if (event.key === 'Tab') {
      closeMenu(false);
    }
  }

  function handleDocumentPointer(event: PointerEvent) {
    const target = event.target as Node | null;
    if (open && target && !trigger?.contains(target) && !menu?.contains(target)) closeMenu(false);
  }

  function handleViewportChange() {
    if (open) updatePosition();
  }

  if (typeof document !== 'undefined') {
    document.addEventListener('pointerdown', handleDocumentPointer, true);
    window.addEventListener('resize', handleViewportChange);
    window.addEventListener('scroll', handleViewportChange, true);
  }

  onDestroy(() => {
    if (typeaheadTimer) clearTimeout(typeaheadTimer);
    if (typeof document !== 'undefined') {
      document.removeEventListener('pointerdown', handleDocumentPointer, true);
      window.removeEventListener('resize', handleViewportChange);
      window.removeEventListener('scroll', handleViewportChange, true);
    }
  });
</script>

<div class:has-error={!!error} class="select-field">
  {#if label}<label for={id}>{label}</label>{/if}
  <button
    use:triggerNode
    type="button"
    {id}
    class="select-trigger"
    class:placeholder={!selected}
    {disabled}
    aria-label={ariaLabel || label || placeholder}
    role="combobox"
    aria-haspopup="listbox"
    aria-expanded={open}
    aria-controls={listboxId}
    aria-activedescendant={open ? activeId : undefined}
    aria-invalid={error ? 'true' : undefined}
    aria-describedby={describedBy}
    on:click={() => open ? closeMenu() : openMenu()}
    on:keydown={handleKeydown}
  >
    <span>{selected?.label || placeholder}</span><ChevronDown size={16} aria-hidden="true"/>
  </button>
  {#if help}<small id={helpId}>{help}</small>{/if}
  {#if error}<small id={errorId} class="error" role="alert">{error}</small>{/if}
</div>

{#if open}
  <div
    use:menuNode
    id={listboxId}
    class="select-popover"
    role="listbox"
    tabindex="-1"
    aria-label={ariaLabel || label || placeholder}
    aria-activedescendant={activeId}
    style:top={`${menuTop}px`}
    style:left={`${menuLeft}px`}
    style:width={`${menuWidth}px`}
    style:max-height={`${menuMaxHeight}px`}
    on:keydown={handleKeydown}
  >
    {#each options as option, index (`${option.value}:${index}`)}
      <button
        id={`${id}-option-${index}`}
        type="button"
        role="option"
        aria-selected={option.value === value}
        disabled={option.disabled}
        class:active={index === activeIndex}
        on:mouseenter={() => !option.disabled && (activeIndex = index)}
        on:click={() => choose(index)}
      >
        <span><strong>{option.label}</strong>{#if option.description}<small>{option.description}</small>{/if}</span>
        {#if option.value === value}<Check size={15} aria-hidden="true"/>{/if}
      </button>
    {/each}
  </div>
{/if}

<style>
  .select-field{display:grid;gap:6px;min-width:170px}.select-field>label{color:var(--text);font-size:12px;font-weight:600}.select-field>small{color:var(--muted);font-size:11px}.select-field>small.error{color:var(--error-color)}
  .select-trigger{width:100%;min-height:38px;display:flex;align-items:center;justify-content:space-between;gap:10px;padding:0 11px;border:1px solid var(--line);border-radius:10px;background:var(--surface-2);color:var(--text);font:inherit;text-align:left;cursor:pointer}.select-trigger span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.select-trigger.placeholder{color:var(--muted)}.select-trigger:disabled{opacity:var(--disabled-opacity);cursor:default}.select-trigger:focus-visible{outline:2px solid var(--focus-ring);outline-offset:2px;border-color:var(--accent)}.has-error .select-trigger{border-color:var(--error-color)}
  .select-popover{position:fixed;z-index:1000;overflow:auto;padding:5px;border:1px solid var(--line-strong,var(--line));border-radius:11px;background:var(--popover-bg,var(--surface));box-shadow:var(--shadow-popover,var(--shadow));color:var(--text)}
  .select-popover button{width:100%;min-height:36px;display:flex;align-items:center;justify-content:space-between;gap:10px;padding:7px 9px;border:0;border-radius:8px;background:transparent;color:inherit;font:inherit;text-align:left;cursor:pointer}.select-popover button.active{background:var(--surface-hover)}.select-popover button[aria-selected="true"]{color:var(--accent)}.select-popover button:disabled{opacity:var(--disabled-opacity);cursor:default}.select-popover button>span{min-width:0;display:grid;gap:1px}.select-popover strong,.select-popover small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.select-popover strong{font-size:12px;font-weight:600}.select-popover small{color:var(--muted);font-size:11px}
  @media(max-width:720px){.select-field{width:100%}}
</style>
