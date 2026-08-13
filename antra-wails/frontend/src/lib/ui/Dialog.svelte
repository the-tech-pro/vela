<script lang="ts">
  import { createEventDispatcher, onMount, tick } from 'svelte';
  import { X } from 'lucide-svelte';
  export let open = false;
  export let title: string;
  export let description = '';
  export let dismissible = true;
  export let labelledby = `dialog-${Math.random().toString(36).slice(2)}`;
  const dispatch = createEventDispatcher<{ close: void }>();
  let dialog: HTMLDivElement | null = null;
  let invoker: HTMLElement | null = null;

  function dialogNode(node: HTMLDivElement) {
    dialog = node;
    return { destroy: () => { if (dialog === node) dialog = null; } };
  }

  $: if (open) {
    invoker = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    tick().then(() => {
      const first = dialog?.querySelector<HTMLElement>('button,[href],input,textarea,[tabindex]:not([tabindex="-1"])');
      if (first) first.focus();
      else dialog?.focus();
    });
  }

  function close() {
    if (!dismissible) return;
    dispatch('close');
    tick().then(() => invoker?.focus());
  }

  function keydown(event: KeyboardEvent) {
    if (event.key === 'Escape' && dismissible) {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== 'Tab' || !dialog) return;
    const focusable = Array.from(dialog.querySelectorAll<HTMLElement>('button:not(:disabled),[href],input:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])'));
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  onMount(() => () => invoker?.focus());
</script>

{#if open}
  <div class="dialog-backdrop" role="presentation" on:click|self={close}>
    <div use:dialogNode class="dialog" role="dialog" aria-modal="true" aria-labelledby={labelledby} aria-describedby={description ? `${labelledby}-description` : undefined} tabindex="-1" on:keydown={keydown}>
      <header><div><h2 id={labelledby}>{title}</h2>{#if description}<p id={`${labelledby}-description`}>{description}</p>{/if}</div>{#if dismissible}<button type="button" aria-label="Close" title="Close" on:click={close}><X size={18}/></button>{/if}</header>
      <div class="dialog-body"><slot /></div>
      {#if $$slots.footer}<footer><slot name="footer" /></footer>{/if}
    </div>
  </div>
{/if}

<style>
  .dialog-backdrop{position:fixed;z-index:900;inset:0;display:grid;place-items:center;padding:24px;background:var(--overlay-scrim)}.dialog{width:min(620px,92vw);max-height:88vh;display:flex;flex-direction:column;overflow:hidden;border:1px solid var(--line);border-radius:16px;background:var(--surface);box-shadow:var(--shadow-dialog,var(--shadow));color:var(--text)}header{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:18px 20px;border-bottom:1px solid var(--line)}h2{margin:0;font-size:20px}p{margin:4px 0 0;color:var(--muted);font-size:12px}header button{width:40px;height:40px;display:grid;place-items:center;border:0;border-radius:10px;background:var(--surface-2);color:var(--muted)}.dialog-body{min-height:0;overflow:auto;padding:20px}footer{display:flex;justify-content:flex-end;gap:8px;padding:14px 20px;border-top:1px solid var(--line)}button:focus-visible,.dialog:focus-visible{outline:2px solid var(--focus-ring);outline-offset:2px}
</style>
