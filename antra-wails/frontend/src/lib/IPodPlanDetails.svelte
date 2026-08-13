<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import { AlertTriangle, ChevronLeft, ChevronRight, LoaderCircle } from 'lucide-svelte';
  import { GetIPodSyncPlanDetails } from '../../wailsjs/go/main/App.js';
  import { main } from '../../wailsjs/go/models';
  import type {
    IPodPlan, IPodPlanDetailItem, IPodPlanDetailPage, IPodPlanGroup,
  } from './ipodTypes';

  export let plan: IPodPlan;
  export let demoMode = false;

  const dispatch = createEventDispatcher<{ stale: { message: string } }>();
  const pageSize = 25;
  const groupLabels: Record<IPodPlanGroup, string> = {
    additions: 'Additions',
    removals: 'Removals',
    metadata_updates: 'Metadata',
    artwork_updates: 'Artwork',
    conversions: 'Conversions',
    playlist_effects: 'Playlists',
    warnings: 'Warnings',
    unsupported: 'Unsupported',
  };

  let activeGroup: IPodPlanGroup = 'additions';
  let detailPage: IPodPlanDetailPage | null = null;
  let loading = false;
  let error = '';
  let loadedPlanId = '';
  let requestId = 0;

  $: visibleGroups = plan.groups?.length
    ? plan.groups
    : (Object.keys(groupLabels) as IPodPlanGroup[]).map(group => ({
        group,
        total: plan.group_previews?.[group]?.length || 0,
        page_size_max: 100,
      }));
  $: activeDescriptor = visibleGroups.find(descriptor => descriptor.group === activeGroup);
  $: totalPages = Math.max(1, Math.ceil((detailPage?.total || activeDescriptor?.total || 0) / pageSize));
  $: if (plan.plan_id && plan.plan_id !== loadedPlanId) initializePlan(plan);

  function parseResult(raw: string): IPodPlanDetailPage {
    const parsed = JSON.parse(raw || '{}');
    if (parsed?.error) throw new Error(parsed.error);
    return parsed as IPodPlanDetailPage;
  }

  function initializePlan(nextPlan: IPodPlan) {
    loadedPlanId = nextPlan.plan_id;
    activeGroup = nextPlan.groups?.find(descriptor => descriptor.total > 0)?.group || 'additions';
    detailPage = null;
    error = '';
    void loadGroup(activeGroup, 1);
  }

  async function loadGroup(group: IPodPlanGroup, page = 1) {
    activeGroup = group;
    loading = true;
    error = '';
    const currentRequest = ++requestId;
    try {
      if (demoMode) {
        const items = plan.group_previews?.[group] || [];
        detailPage = {
          protocol_version: 1,
          plan_id: plan.plan_id,
          group,
          page: 1,
          page_size: pageSize,
          total: plan.groups?.find(descriptor => descriptor.group === group)?.total || items.length,
          items,
          expires_at: plan.expires_at,
        };
        return;
      }
      const response = parseResult(await GetIPodSyncPlanDetails(new main.IPodPlanDetailsRequest({
        plan_id: plan.plan_id,
        group,
        page,
        page_size: pageSize,
      })));
      if (currentRequest !== requestId || response.plan_id !== plan.plan_id) return;
      detailPage = response;
    } catch (caught) {
      if (currentRequest !== requestId) return;
      const message = caught instanceof Error ? caught.message : String(caught);
      error = message;
      if (/stale|expired|changed/i.test(message)) dispatch('stale', { message });
    } finally {
      if (currentRequest === requestId) loading = false;
    }
  }

  function itemTitle(item: IPodPlanDetailItem): string {
    return item.title || item.message || item.description || groupLabels[item.group];
  }

  function itemMeta(item: IPodPlanDetailItem): string {
    return [
      item.artist,
      item.album,
      item.code,
      item.track_count != null ? `${item.track_count} tracks` : '',
      item.skipped_count ? `${item.skipped_count} skipped` : '',
      item.estimated_bytes ? formatBytes(item.estimated_bytes) : '',
      item.removed_bytes ? `${formatBytes(item.removed_bytes)} removed` : '',
    ].filter(Boolean).join(' · ');
  }

  function itemDetails(item: IPodPlanDetailItem): string {
    if (Array.isArray(item.metadata_fields)) return `Fields: ${item.metadata_fields.join(', ')}`;
    if (item.metadata_fields && typeof item.metadata_fields === 'object') return `Fields: ${Object.keys(item.metadata_fields).join(', ')}`;
    if (typeof item.conversion === 'string') return item.conversion;
    if (item.conversion && typeof item.conversion === 'object') return Object.values(item.conversion).filter(value => typeof value === 'string').join(' → ');
    const path = item.source_path || item.ipod_location || '';
    return path ? path.split(/[\\/]/).pop() || '' : '';
  }

  function formatBytes(bytes: number): string {
    if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    const unit = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1000)));
    return `${(bytes / 1000 ** unit).toFixed(unit > 1 ? 1 : 0)} ${units[unit]}`;
  }
</script>

<section class="plan-details" aria-label="Reviewed sync plan details">
  <nav aria-label="Plan effect groups">
    {#each visibleGroups as descriptor (descriptor.group)}
      <button class:active={activeGroup === descriptor.group} class:attention={descriptor.group === 'warnings' || descriptor.group === 'unsupported'} on:click={() => loadGroup(descriptor.group, 1)}>
        <span>{groupLabels[descriptor.group]}</span><strong>{descriptor.total}</strong>
      </button>
    {/each}
  </nav>

  {#if loading}
    <div class="detail-state"><LoaderCircle size={20}/><span>Loading bounded plan details…</span></div>
  {:else if error}
    <div class="detail-state error" role="status"><AlertTriangle size={20}/><span>{error}</span><button on:click={() => loadGroup(activeGroup, detailPage?.page || 1)}>Try again</button></div>
  {:else if !detailPage?.items.length}
    <div class="detail-state"><span>No {groupLabels[activeGroup].toLowerCase()} in this reviewed plan.</span></div>
  {:else}
    <div class="detail-list">
      {#each detailPage.items as item (item.item_id)}
        <article class:attention={item.group === 'warnings' || item.group === 'unsupported'}>
          <span class="action">{item.action}</span>
          <div><strong>{itemTitle(item)}</strong>{#if itemMeta(item)}<small>{itemMeta(item)}</small>{/if}{#if itemDetails(item)}<small>{itemDetails(item)}</small>{/if}</div>
        </article>
      {/each}
    </div>
  {/if}

  {#if totalPages > 1}
    <footer><button aria-label="Previous detail page" disabled={loading || (detailPage?.page || 1) <= 1} on:click={() => loadGroup(activeGroup, (detailPage?.page || 1) - 1)}><ChevronLeft size={15}/> Previous</button><span>Page {detailPage?.page || 1} of {totalPages}</span><button aria-label="Next detail page" disabled={loading || (detailPage?.page || 1) >= totalPages} on:click={() => loadGroup(activeGroup, (detailPage?.page || 1) + 1)}>Next <ChevronRight size={15}/></button></footer>
  {/if}
</section>

<style>
  .plan-details{display:grid;gap:9px;margin:12px 0}
  nav{display:flex;gap:4px;overflow-x:auto;padding-bottom:2px}
  nav button{min-width:82px;display:flex;align-items:center;justify-content:space-between;gap:8px;padding:7px 8px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--muted);font-size:9px;cursor:pointer;white-space:nowrap}
  nav button.active{border-color:var(--accent);background:var(--accent-soft);color:var(--accent)}
  nav button.attention strong{color:var(--warning-color,#d88300)}
  nav strong{font-size:10px}
  .detail-list{max-height:280px;overflow:auto;display:grid;gap:5px}
  article{display:grid;grid-template-columns:72px minmax(0,1fr);gap:9px;padding:9px;border:1px solid var(--line);border-radius:9px;background:var(--bg)}
  article.attention{border-color:var(--warning-border)}
  article>div{min-width:0;display:grid;gap:2px}
  article strong,article small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  article small{color:var(--muted);font-size:9px}
  .action{align-self:start;overflow:hidden;padding:4px 6px;border-radius:99px;background:var(--surface-2);color:var(--muted);font-size:8px;text-align:center;text-overflow:ellipsis;text-transform:uppercase;white-space:nowrap}
  .detail-state{min-height:110px;display:flex;align-items:center;justify-content:center;gap:7px;color:var(--muted);font-size:10px;text-align:center}
  .detail-state.error{color:var(--error-color,#ff453a)}
  .detail-state button,footer button{min-height:30px;display:flex;align-items:center;gap:4px;padding:0 9px;border:0;border-radius:7px;background:var(--surface-2);color:var(--text);cursor:pointer}
  footer{display:flex;align-items:center;justify-content:center;gap:10px;color:var(--muted);font-size:9px}
  button:disabled{opacity:.4;cursor:default}
  button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
</style>
