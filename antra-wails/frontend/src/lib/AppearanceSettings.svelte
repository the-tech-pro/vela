<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import { Check, RotateCcw } from 'lucide-svelte';
  import type { AppearancePreference, UIPreferences } from './uiPreferences';

  export let appearance: AppearancePreference;
  export let preferences: UIPreferences;

  const dispatch = createEventDispatcher<{
    appearance: AppearancePreference;
    preferences: UIPreferences;
    reset: void;
  }>();
  const appearanceOptions: AppearancePreference[] = ['system', 'light', 'dark'];

  function update<K extends keyof UIPreferences>(key: K, value: UIPreferences[K]) {
    dispatch('preferences', { ...preferences, [key]: value });
  }

  function reset() {
    dispatch('reset');
  }

  function updateDensity(event: Event) {
    update('density', (event.currentTarget as HTMLSelectElement).value as UIPreferences['density']);
  }

  function updateMotion(event: Event) {
    update('motion', (event.currentTarget as HTMLSelectElement).value as UIPreferences['motion']);
  }
</script>

<section class="appearance-page" id="settings-appearance">
  <div class="settings-heading">
    <div><p class="eyebrow">Appearance</p><h2>Make Vela feel right</h2></div>
    <button class="reset-button" type="button" on:click={reset}><RotateCcw size={15}/> Reset</button>
  </div>

  <div class="preview" aria-label="Live appearance preview">
    <aside style:width={`${Math.round(preferences.sidebar_width * .24)}px`}>
      <span></span><span class="selected"></span><span></span>
    </aside>
    <div class="preview-content">
      <span class="preview-title"></span>
      <div class="preview-cards">
        {#each [0, 1, 2] as card (card)}
          <article style:width={`${Math.round(preferences.artwork_size * .34)}px`}>
            <span class="preview-art"></span><small></small>
          </article>
        {/each}
      </div>
    </div>
  </div>

  <div class="setting-row">
    <div><strong>Color mode</strong><span>System follows your operating system as it changes.</span></div>
    <div class="segmented" role="group" aria-label="Color mode">
      {#each appearanceOptions as option (option)}
        <button type="button" class:active={appearance === option} on:click={() => dispatch('appearance', option)}>
          {#if appearance === option}<Check size={13}/>{/if}{option[0].toUpperCase() + option.slice(1)}
        </button>
      {/each}
    </div>
  </div>

  <label class="range-row">
    <span><strong>UI scale</strong><small>Scale controls and text from 85% to 125%.</small></span>
    <output>{Math.round(preferences.scale * 100)}%</output>
    <input type="range" min="0.85" max="1.25" step="0.05" value={preferences.scale} on:input={(event) => update('scale', Number(event.currentTarget.value))} />
  </label>

  <div class="setting-row">
    <div><strong>Density</strong><span>Adjust spacing without hiding information.</span></div>
    <select value={preferences.density} on:change={updateDensity}>
      <option value="compact">Compact</option>
      <option value="comfortable">Comfortable</option>
      <option value="spacious">Spacious</option>
    </select>
  </div>

  <label class="range-row">
    <span><strong>Sidebar width</strong><small>Keep navigation between 210 and 300 pixels.</small></span>
    <output>{preferences.sidebar_width}px</output>
    <input type="range" min="210" max="300" step="5" value={preferences.sidebar_width} on:input={(event) => update('sidebar_width', Number(event.currentTarget.value))} />
  </label>

  <label class="range-row">
    <span><strong>Artwork and card size</strong><small>Choose how many releases fit on screen.</small></span>
    <output>{preferences.artwork_size}px</output>
    <input type="range" min="130" max="210" step="5" value={preferences.artwork_size} on:input={(event) => update('artwork_size', Number(event.currentTarget.value))} />
  </label>

  <div class="setting-row">
    <div><strong>Motion</strong><span>Your operating system’s reduced-motion setting always takes priority.</span></div>
    <select value={preferences.motion} on:change={updateMotion}>
      <option value="system">System</option>
      <option value="reduced">Reduced</option>
      <option value="full">Full</option>
    </select>
  </div>
</section>

<style>
  .appearance-page { padding:23px;background:var(--surface);border:1px solid var(--line);border-radius:18px; }
  .settings-heading { display:flex;justify-content:space-between;align-items:center;margin-bottom:12px; }
  .settings-heading h2 { margin:4px 0 0;font-size:20px; }
  .eyebrow { margin:0;color:var(--accent);font-size:11px;font-weight:720;text-transform:uppercase;letter-spacing:.09em; }
  .reset-button { min-height:36px;display:flex;align-items:center;gap:6px;padding:0 11px;border:0;border-radius:9px;background:var(--surface-2);color:var(--muted);cursor:pointer; }
  .reset-button:hover { background:var(--surface-hover);color:var(--text); }
  .preview { height:150px;display:flex;overflow:hidden;margin:16px 0 8px;border:1px solid var(--line);border-radius:14px;background:var(--bg); }
  .preview aside { min-width:50px;display:grid;align-content:start;gap:8px;padding:18px 9px;background:var(--sidebar);border-right:1px solid var(--line);transition:width .16s ease; }
  .preview aside span { height:8px;border-radius:99px;background:var(--faint);opacity:.55; }
  .preview aside .selected { background:var(--accent);opacity:1; }
  .preview-content { flex:1;min-width:0;padding:20px; }
  .preview-title { width:38%;height:12px;display:block;margin-bottom:18px;border-radius:99px;background:var(--text);opacity:.8; }
  .preview-cards { display:flex;gap:12px;overflow:hidden; }
  .preview-cards article { flex:0 0 auto;display:grid;gap:6px;transition:width .16s ease; }
  .preview-art { width:100%;aspect-ratio:1;display:block;border-radius:8px;background:linear-gradient(145deg,var(--accent),color-mix(in srgb,var(--accent) 35%,var(--surface-2))); }
  .preview-cards small { width:75%;height:6px;border-radius:99px;background:var(--muted);opacity:.55; }
  .setting-row,.range-row { min-height:64px;display:flex;align-items:center;justify-content:space-between;gap:24px;padding:12px 0;border-top:1px solid var(--line); }
  .setting-row>div,.range-row>span { display:grid;gap:4px; }
  .setting-row span,.range-row small { color:var(--muted);font-size:11px;font-weight:400; }
  .setting-row select { min-width:170px; }
  .segmented { display:flex;gap:3px;padding:3px;border-radius:9px;background:var(--surface-2); }
  .segmented button { min-width:76px;min-height:34px;display:flex;align-items:center;justify-content:center;gap:4px;padding:0 9px;border:0;border-radius:7px;background:transparent;color:var(--muted);cursor:pointer; }
  .segmented button.active { background:var(--surface);color:var(--text);box-shadow:0 1px 4px rgba(0,0,0,.12); }
  .range-row { display:grid;grid-template-columns:minmax(0,1fr) 56px minmax(160px,240px);cursor:pointer; }
  .range-row output { color:var(--muted);font:11px ui-monospace,SFMono-Regular,Consolas,monospace;text-align:right; }
  .range-row input { width:100%;padding:0;border:0;background:transparent;box-shadow:none;accent-color:var(--accent); }
  button:focus-visible,input:focus-visible,select:focus-visible { outline:2px solid var(--accent);outline-offset:2px; }
  @media(max-width:720px) {
    .range-row { grid-template-columns:1fr auto; }
    .range-row input { grid-column:1/-1; }
    .setting-row { align-items:flex-start;flex-direction:column;gap:10px; }
    .setting-row select,.segmented { width:100%; }
    .segmented button { flex:1;min-width:0; }
  }
</style>
