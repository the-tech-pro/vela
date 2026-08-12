<script context="module" lang="ts">
  let dialogSequence = 0;
</script>

<script lang="ts">
  import { createEventDispatcher, onDestroy, onMount, tick } from 'svelte';
  import { AlertTriangle, LoaderCircle } from 'lucide-svelte';

  export let title: string;
  export let description: string;
  export let confirmLabel = 'Confirm';
  export let cancelLabel = 'Cancel';
  export let destructive = false;
  export let requiredPhrase = '';
  export let acknowledgement = '';
  export let busy = false;
  export let allowEscape = true;

  const dispatch = createEventDispatcher<{
    confirm: void;
    cancel: void;
  }>();
  const dialogId = `ipod-confirmation-${++dialogSequence}`;
  let dialogElement: HTMLElement | null = null;
  let phraseInput: HTMLInputElement | null = null;
  let acknowledgementInput: HTMLInputElement | null = null;
  let cancelButton: HTMLButtonElement | null = null;
  let phrase = '';
  let acknowledged = false;
  let previouslyFocused: HTMLElement | null = null;

  $: phraseMatches = !requiredPhrase || phrase === requiredPhrase;
  $: acknowledgementMatches = !acknowledgement || acknowledged;
  $: canConfirm = !busy && phraseMatches && acknowledgementMatches;

  function captureDialog(node: HTMLElement) {
    dialogElement = node;
    return { destroy: () => { if (dialogElement === node) dialogElement = null; } };
  }

  function capturePhraseInput(node: HTMLInputElement) {
    phraseInput = node;
    return { destroy: () => { if (phraseInput === node) phraseInput = null; } };
  }

  function captureAcknowledgementInput(node: HTMLInputElement) {
    acknowledgementInput = node;
    return { destroy: () => { if (acknowledgementInput === node) acknowledgementInput = null; } };
  }

  function captureCancelButton(node: HTMLButtonElement) {
    cancelButton = node;
    return { destroy: () => { if (cancelButton === node) cancelButton = null; } };
  }

  function focusableElements(): HTMLElement[] {
    if (!dialogElement) return [];
    return Array.from(dialogElement.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )).filter(element => !element.hasAttribute('hidden'));
  }

  function requestCancel() {
    if (busy) return;
    dispatch('cancel');
  }

  function handleKeydown(event: KeyboardEvent) {
    if (event.key === 'Escape') {
      if (!allowEscape || busy) return;
      event.preventDefault();
      requestCancel();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = focusableElements();
    if (!focusable.length) {
      event.preventDefault();
      dialogElement?.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  onMount(async () => {
    previouslyFocused = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    await tick();
    if (phraseInput) phraseInput.focus();
    else if (acknowledgementInput) acknowledgementInput.focus();
    else if (cancelButton) cancelButton.focus();
    else dialogElement?.focus();
  });

  onDestroy(() => {
    previouslyFocused?.focus();
  });
</script>

<svelte:window on:keydown={handleKeydown} />

<div class="dialog-backdrop" role="presentation">
  <div
    class:destructive
    class="confirmation-dialog"
    role="dialog"
    aria-modal="true"
    aria-labelledby={`${dialogId}-title`}
    aria-describedby={`${dialogId}-description`}
    tabindex="-1"
    use:captureDialog
  >
    <header>
      <span class="dialog-icon" aria-hidden="true"><AlertTriangle size={20} /></span>
      <div>
        <p>Confirmation required</p>
        <h2 id={`${dialogId}-title`}>{title}</h2>
      </div>
    </header>

    <div class="dialog-body">
      <p id={`${dialogId}-description`}>{description}</p>

      {#if requiredPhrase}
        <label for={`${dialogId}-phrase`}>
          Type <strong>{requiredPhrase}</strong> to continue
          <input
            id={`${dialogId}-phrase`}
            type="text"
            autocomplete="off"
            spellcheck="false"
            disabled={busy}
            use:capturePhraseInput
            bind:value={phrase}
          />
        </label>
      {/if}

      {#if acknowledgement}
        <label class="acknowledgement" for={`${dialogId}-acknowledgement`}>
          <input
            id={`${dialogId}-acknowledgement`}
            type="checkbox"
            disabled={busy}
            use:captureAcknowledgementInput
            bind:checked={acknowledged}
          />
          <span>{acknowledgement}</span>
        </label>
      {/if}
    </div>

    <footer>
      <button
        class="secondary"
        type="button"
        disabled={busy}
        use:captureCancelButton
        on:click={requestCancel}
      >
        {cancelLabel}
      </button>
      <button
        class:danger={destructive}
        class="primary"
        type="button"
        disabled={!canConfirm}
        on:click={() => dispatch('confirm')}
      >
        {#if busy}<span class="spinner" aria-hidden="true"><LoaderCircle size={16} /></span>{/if}
        {busy ? 'Starting…' : confirmLabel}
      </button>
    </footer>
  </div>
</div>

<style>
  .dialog-backdrop {
    position: fixed;
    inset: 0;
    z-index: 90;
    display: grid;
    place-items: center;
    padding: 24px;
    background: var(--overlay-scrim);
    backdrop-filter: blur(14px);
  }
  .confirmation-dialog {
    width: min(500px, 92vw);
    max-height: 88vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    border: 1px solid var(--line);
    border-radius: 18px;
    background: var(--surface);
    color: var(--text);
    box-shadow: var(--shadow);
  }
  header {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    padding: 20px 22px 16px;
    border-bottom: 1px solid var(--line);
  }
  header div {
    min-width: 0;
  }
  header p {
    margin: 0 0 3px;
    color: var(--muted);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .07em;
    text-transform: uppercase;
  }
  h2 {
    margin: 0;
    font-size: 20px;
    line-height: 1.3;
  }
  .dialog-icon {
    width: 38px;
    height: 38px;
    flex: 0 0 auto;
    display: grid;
    place-items: center;
    border-radius: 10px;
    background: var(--accent-soft);
    color: var(--accent);
  }
  .destructive .dialog-icon {
    color: var(--error-color);
  }
  .dialog-body {
    overflow-y: auto;
    display: grid;
    gap: 16px;
    padding: 20px 22px;
  }
  .dialog-body > p {
    margin: 0;
    color: var(--muted);
    line-height: 1.55;
  }
  label {
    display: grid;
    gap: 7px;
    font-size: 13px;
    font-weight: 600;
  }
  label strong {
    font-family: var(--font-mono, ui-monospace);
  }
  input[type='text'] {
    width: 100%;
    min-height: 40px;
    border: 1px solid var(--line);
    border-radius: 10px;
    background: var(--surface-2);
    color: var(--text);
    padding: 8px 11px;
    font-family: var(--font-mono, ui-monospace);
  }
  .acknowledgement {
    grid-template-columns: auto minmax(0, 1fr);
    align-items: flex-start;
    gap: 10px;
    color: var(--muted);
    font-weight: 500;
    line-height: 1.5;
  }
  .acknowledgement input {
    width: 18px;
    height: 18px;
    margin: 1px 0 0;
    accent-color: var(--accent);
  }
  footer {
    display: flex;
    justify-content: flex-end;
    gap: 9px;
    padding: 15px 22px 20px;
    border-top: 1px solid var(--line);
  }
  button {
    min-height: 40px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 7px;
    padding: 0 16px;
    border: 0;
    border-radius: 10px;
    font-weight: 650;
    cursor: pointer;
  }
  button:disabled {
    opacity: var(--disabled-opacity);
    cursor: default;
  }
  .secondary {
    background: var(--surface-2);
    color: var(--text);
  }
  .primary {
    background: var(--accent);
    color: var(--bg);
  }
  .primary.danger {
    background: var(--error-color);
  }
  button:focus-visible,
  input:focus-visible,
  .confirmation-dialog:focus-visible {
    outline: 2px solid var(--focus-ring);
    outline-offset: 2px;
  }
  .spinner {
    display: inline-grid;
    animation: rotate .8s linear infinite;
  }
  @keyframes rotate {
    to { transform: rotate(360deg); }
  }
  @media (prefers-reduced-motion: reduce) {
    .spinner { animation: none; }
  }
  @media (max-width: 720px) {
    .dialog-backdrop { padding: 16px; }
    footer { flex-direction: column-reverse; }
    footer button { width: 100%; }
  }
</style>
