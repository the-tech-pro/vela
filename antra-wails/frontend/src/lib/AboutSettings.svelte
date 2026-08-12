<script lang="ts">
  import { onMount } from 'svelte';
  import { FileText, LoaderCircle } from 'lucide-svelte';
  import { GetThirdPartyNotices } from '../../wailsjs/go/main/App.js';

  export let demoMode = false;

  let notices = '';
  let loading = true;
  let error = '';

  onMount(() => {
    let active = true;
    const loadNotices = async () => {
      try {
        const result = demoMode
          ? 'Third-Party Notices\n\niOpenPod\nCopyright (c) John Gibbons and iOpenPod contributors.\n\nMIT License'
          : await GetThirdPartyNotices();
        if (active) notices = result;
      } catch (caught) {
        if (active) error = caught instanceof Error ? caught.message : String(caught);
      } finally {
        if (active) loading = false;
      }
    };
    void loadNotices();
    return () => {
      active = false;
    };
  });
</script>

<section class="about-page" id="settings-about">
  <header>
    <span><FileText size={22}/></span>
    <div><p>About</p><h2>Third-party notices</h2></div>
  </header>
  <p class="summary">Vela embeds iOpenPod’s headless device and sync components. Its graphical application is not included.</p>
  {#if loading}
    <div class="state"><LoaderCircle size={20}/><span>Loading notices…</span></div>
  {:else if error}
    <div class="state error" role="status">{error}</div>
  {:else}
    <pre>{notices}</pre>
  {/if}
</section>

<style>
  .about-page{padding:23px;background:var(--surface);border:1px solid var(--line);border-radius:18px}
  header{display:flex;align-items:center;gap:12px}
  header>span{width:44px;height:44px;display:grid;place-items:center;border-radius:12px;background:var(--accent-soft);color:var(--accent)}
  header p{margin:0;color:var(--accent);font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em}
  h2{margin:3px 0 0;font-size:20px}
  .summary{color:var(--muted);font-size:12px;line-height:1.5}
  pre{max-height:480px;overflow:auto;padding:16px;border:1px solid var(--line);border-radius:12px;background:var(--bg);color:var(--muted);font:11px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap}
  .state{min-height:180px;display:flex;align-items:center;justify-content:center;gap:8px;color:var(--muted)}
  .state.error{color:var(--error-color,#ff453a)}
</style>
