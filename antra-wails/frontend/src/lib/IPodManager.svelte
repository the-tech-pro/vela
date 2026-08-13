<script lang="ts">
  import { createEventDispatcher, onDestroy } from 'svelte';
  import {
    AlertTriangle, Album, Archive, Check, ChevronLeft, ChevronRight, Database,
    Disc3, FolderOpen, HardDrive, ListMusic, LoaderCircle, LockKeyhole, Music2,
    RefreshCw, Search, ShieldCheck, Smartphone, Unplug, Users, Wrench, X,
  } from 'lucide-svelte';
  import {
    BrowseIPodLibrary, CreateIPodSyncPlan, EjectIPod,
    ExecuteIPodSync, GetDownloadedRelease, StageDownloadsForIPod,
  } from '../../wailsjs/go/main/App.js';
  import { main } from '../../wailsjs/go/models';
  import VirtualList from './VirtualList.svelte';
  import BackupsRecovery from './ipod/BackupsRecovery.svelte';
  import CapacityUnlockWizard from './ipod/CapacityUnlockWizard.svelte';
  import OperationStatus from './ipod/OperationStatus.svelte';
  import type {
    IPodBrowseItem, IPodDevice, IPodDownloadedTrack, IPodEventPayload,
    IPodOperationEnvelope, IPodPlan, LocalReleaseSummary,
  } from './ipodTypes';
  import {
    getIPodEventData, isIPodOperationActive, mergeIPodOperation,
    parseDownloadedTracks, parseIPodBrowseResponse, parseIPodPlan,
    parseIPodResponse, recordBoolean, recordString,
  } from './ipodTypes';

  export let device: IPodDevice;
  export let localReleases: LocalReleaseSummary[] = [];
  export let ipodEvent: IPodEventPayload | null = null;
  export let downloadBusy = false;
  export let demoMode = false;
  export let connected = true;

  const dispatch = createEventDispatcher<{
    eject: { device_id: string; path: string };
    notify: { tone: 'error' | 'warning' | 'success'; message: string };
  }>();

  type DeviceTab = 'overview' | 'tracks' | 'albums' | 'artists' | 'playlists' | 'sync' | 'backups' | 'advanced';
  type BrowseResource = 'tracks' | 'albums' | 'artists' | 'playlists';
  interface SelectedFile { path: string; title: string; release: string; }
  const browseResources: BrowseResource[] = ['tracks', 'albums', 'artists', 'playlists'];

  let activeTab: DeviceTab = 'overview';
  let advancedLoaded = false;
  let capacityUnlockActive = false;
  let filesystemUnavailable = false;
  let browseResource: BrowseResource = 'tracks';
  let browseItems: IPodBrowseItem[] = [];
  let browseTotal = 0;
  let browsePage = 1;
  const browsePageSize = 50;
  let browseLoading = false;
  let browseError = '';
  let browseSearch = '';
  let localSearch = '';
  let localLoadingPath = '';
  let localError = '';
  let selectedFiles: SelectedFile[] = [];
  let plan: IPodPlan | null = null;
  let planning = false;
  let planError = '';
  let planStale = false;
  let exactConfirmation = false;
  let mutationRunning = false;
  let operationEnvelope: IPodOperationEnvelope | null = null;
  let announcement = '';
  let ejectArmed = false;
  let ejecting = false;
  let handledEvent: IPodEventPayload | null = null;
  let browseRequestId = 0;
  let destroyed = false;
  let ipodPlanDetailsPromise: Promise<typeof import('./IPodPlanDetails.svelte')> | null = null;

  $: filteredBrowseItems = browseItems.filter(item => {
    const query = browseSearch.trim().toLowerCase();
    return !query || browseLabel(item).toLowerCase().includes(query) || browseMeta(item).toLowerCase().includes(query);
  });
  $: filteredLocalReleases = localReleases.filter(release => {
    const query = localSearch.trim().toLowerCase();
    return !query || `${release.title} ${release.artist || ''}`.toLowerCase().includes(query);
  });
  $: browsePages = Math.max(1, Math.ceil(browseTotal / browsePageSize));
  $: operationActive = isIPodOperationActive(operationEnvelope);
  $: filesystemUnavailable = device.filesystem_accessible === false;
  $: deviceWriteBlocked = !device.write_ready || device.filesystem_read_only || device.browse_only;
  $: writeBlocked = !connected || filesystemUnavailable || deviceWriteBlocked || capacityUnlockActive || mutationRunning || operationActive || planning || downloadBusy || ejecting;
  $: writeGuidance = device.write_block_reason
    || (device.filesystem_read_only
      ? 'This mounted filesystem is read-only. Remount it with write access, then scan again.'
      : 'This iPod is not verified for writes. Reconnect it directly and scan again.');
  $: if (filesystemUnavailable && activeTab !== 'overview') activeTab = 'overview';
  $: if (activeTab === 'advanced') advancedLoaded = true;
  $: if (ipodEvent && ipodEvent !== handledEvent) {
    handledEvent = ipodEvent;
    handleIPodEvent(ipodEvent);
  }

  function loadIPodPlanDetails() {
    if (!ipodPlanDetailsPromise) ipodPlanDetailsPromise = import('./IPodPlanDetails.svelte');
    return ipodPlanDetailsPromise;
  }

  function formatBytes(bytes: number): string {
    if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const unit = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1000)));
    return `${(bytes / 1000 ** unit).toFixed(unit > 1 ? 1 : 0)} ${units[unit]}`;
  }

  function formatDetectedStorage(sizeGb: number): string {
    return Number.isFinite(sizeGb) && sizeGb > 0
      ? `${sizeGb.toFixed(1)} GB total`
      : 'Total capacity unavailable';
  }

  function browseLabel(item: IPodBrowseItem): string {
    return String(item.title || item.name || item.album || item.artist || item.playlist_name || 'Untitled');
  }

  function browseMeta(item: IPodBrowseItem): string {
    return [item.artist, item.album, item.genre, item.track_count != null ? `${item.track_count} songs` : '', item.year]
      .filter(Boolean).join(' · ');
  }

  function browseItemKey(item: IPodBrowseItem, index: number): string {
    return String(
      item.persistent_id
      || item.track_id
      || item.album_id
      || item.artist_id
      || item.playlist_id
      || `${browseResource}:${browseLabel(item)}:${browseMeta(item)}:${index}`,
    );
  }

  function localReleaseKey(release: LocalReleaseSummary): string {
    return release.relative_path;
  }

  function resourceIcon(resource: BrowseResource) {
    return resource === 'albums' ? Album : resource === 'artists' ? Users : resource === 'playlists' ? ListMusic : Music2;
  }

  async function openBrowse(resource: BrowseResource, page = 1) {
    if (filesystemUnavailable) {
      activeTab = 'overview';
      return;
    }
    const requestId = ++browseRequestId;
    activeTab = resource;
    browseResource = resource;
    browsePage = page;
    browseLoading = true;
    browseError = '';
    try {
      if (demoMode) {
        const demoItems = resource === 'tracks'
          ? [{ title: 'Open Skies', artist: 'Nova Lane', album: 'Afterglow' }, { title: 'Blue Hours', artist: 'The Still', album: 'Blue Hours' }]
          : resource === 'albums'
            ? [{ album: 'Afterglow', artist: 'Nova Lane', track_count: 10 }]
            : resource === 'artists'
              ? [{ artist: 'Nova Lane', track_count: 10 }]
              : [{ title: 'Late Night Drive', track_count: 42 }];
        if (requestId !== browseRequestId || destroyed) return;
        browseItems = demoItems;
        browseTotal = demoItems.length;
        return;
      }
      const result = parseIPodBrowseResponse(await BrowseIPodLibrary(new main.IPodBrowseRequest({
        mount_path: device.path,
        resource,
        page,
        page_size: browsePageSize,
      })));
      if (requestId !== browseRequestId || destroyed) return;
      browseItems = result.items;
      browseTotal = result.total;
    } catch (caught) {
      if (requestId !== browseRequestId || destroyed) return;
      browseError = caught instanceof Error ? caught.message : String(caught);
    } finally {
      if (requestId === browseRequestId && !destroyed) browseLoading = false;
    }
  }

  async function toggleLocalRelease(release: LocalReleaseSummary) {
    const existing = selectedFiles.filter(item => item.release === release.relative_path);
    if (existing.length) {
      selectedFiles = selectedFiles.filter(item => item.release !== release.relative_path);
      return;
    }
    localLoadingPath = release.relative_path;
    localError = '';
    try {
      const tracks: IPodDownloadedTrack[] = demoMode
        ? [{ title: 'Open Skies', file_path: 'C:\\Music\\Vela\\Open Skies.flac' }]
        : parseDownloadedTracks(await GetDownloadedRelease(release.relative_path));
      if (destroyed) return;
      const files = tracks
        .map(track => ({
          path: track.file_path,
          title: track.title || track.file_name || 'Local song',
          release: release.relative_path,
        }));
      if (!files.length) throw new Error('This release has no indexed local audio files.');
      selectedFiles = [...selectedFiles, ...files];
    } catch (caught) {
      if (destroyed) return;
      localError = caught instanceof Error ? caught.message : String(caught);
    } finally {
      if (!destroyed) localLoadingPath = '';
    }
  }

  async function createPlan(stagingId = '') {
    if (!selectedFiles.length || writeBlocked) return;
    planning = true;
    planError = '';
    planStale = false;
    exactConfirmation = false;
    try {
      plan = demoMode
        ? {
            protocol_version: 1, plan_id: 'fixture-plan', additions: selectedFiles.length,
            removals: 0, updates: 0, conversions: 0, playlist_changes: 1,
            warnings: 1, unsupported: 0, required_bytes: selectedFiles.length * 28_000_000,
            source_count: selectedFiles.length, review_fingerprint: 'fixture',
            storage: {
              bytes_to_add: selectedFiles.length * 28_000_000, bytes_to_remove: 0,
              bytes_to_update: 0, net_change_bytes: selectedFiles.length * 28_000_000,
              required_free_bytes: selectedFiles.length * 28_000_000,
              free_before_bytes: 31_400_000_000,
              free_after_bytes: 31_400_000_000 - selectedFiles.length * 28_000_000,
            },
            groups: [
              { group: 'additions', total: selectedFiles.length, page_size_max: 100 },
              { group: 'removals', total: 0, page_size_max: 100 },
              { group: 'metadata_updates', total: 0, page_size_max: 100 },
              { group: 'artwork_updates', total: 0, page_size_max: 100 },
              { group: 'conversions', total: 0, page_size_max: 100 },
              { group: 'playlist_effects', total: 1, page_size_max: 100 },
              { group: 'warnings', total: 1, page_size_max: 100 },
              { group: 'unsupported', total: 0, page_size_max: 100 },
            ],
            group_previews: {
              additions: selectedFiles.map((file, index) => ({
                item_id: `fixture-add-${index}`, group: 'additions', action: 'add',
                title: file.title, artist: 'Demo artist', source_path: file.path,
                estimated_bytes: 28_000_000,
              })),
              playlist_effects: [{
                item_id: 'fixture-playlist', group: 'playlist_effects', action: 'add',
                title: 'Vela fixture playlist', track_count: selectedFiles.length,
              }],
              warnings: [{
                item_id: 'fixture-warning', group: 'warnings', action: 'warning',
                code: 'demo_fixture', message: 'Fixture review only; no device write will occur.',
              }],
            },
            expires_at: Date.now() / 1000 + 900,
          }
        : parseIPodPlan(await CreateIPodSyncPlan(new main.IPodPlanRequest({
            mount_path: device.path,
            source_files: selectedFiles.map(item => item.path),
            staging_id: stagingId || undefined,
          })));
      activeTab = 'sync';
    } catch (caught) {
      planError = caught instanceof Error ? caught.message : String(caught);
    } finally {
      planning = false;
    }
  }

  export async function reviewCompletedFiles(files: string[]) {
    if (filesystemUnavailable || deviceWriteBlocked) {
      activeTab = filesystemUnavailable ? 'overview' : 'sync';
      announcement = writeGuidance;
      return;
    }
    const mountRoot = device.path.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
    const eligibleFiles = [...new Set(files.filter(path => {
      if (typeof path !== 'string' || !path.trim()) return false;
      const normalized = path.replace(/\\/g, '/').toLowerCase();
      return normalized !== mountRoot
        && !normalized.startsWith(`${mountRoot}/`)
        && !/\.(?:part|tmp|download)$/i.test(normalized);
    }))];
    if (!eligibleFiles.length) {
      planError = 'No validated completed local files were eligible for staging. No sync plan was created.';
      activeTab = 'sync';
      return;
    }
    planning = true;
    planError = '';
    try {
      const staged = parseIPodResponse(await StageDownloadsForIPod(new main.IPodStageRequest({
        mount_path: device.path,
        completed_files: eligibleFiles,
      })));
      const completedFiles = Array.isArray(staged.completed_files)
        ? staged.completed_files.filter((path): path is string => typeof path === 'string' && !!path)
        : [];
      const stagingId = recordString(staged, 'staging_id');
      if (!completedFiles.length || !stagingId) {
        throw new Error('Staging returned no eligible completed files. No sync plan was created.');
      }
      selectedFiles = completedFiles.map(path => ({ path, title: path.split(/[\\/]/).pop() || path, release: `stage:${stagingId}` }));
      planning = false;
      await createPlan(stagingId);
    } catch (caught) {
      planError = caught instanceof Error ? caught.message : String(caught);
      planning = false;
      activeTab = 'sync';
    }
  }

  export function showPlan(nextPlan: IPodPlan) {
    if (filesystemUnavailable || deviceWriteBlocked) {
      activeTab = filesystemUnavailable ? 'overview' : 'sync';
      announcement = writeGuidance;
      return;
    }
    plan = nextPlan;
    planError = '';
    planStale = false;
    exactConfirmation = false;
    activeTab = 'sync';
  }

  function handlePlanStale(event: CustomEvent<{ message: string }>) {
    planStale = true;
    exactConfirmation = false;
    planError = event.detail.message;
  }

  async function executePlan() {
    if (!plan || !exactConfirmation || writeBlocked) return;
    mutationRunning = true;
    announcement = 'Revalidating the reviewed plan before backup.';
    try {
      if (demoMode) {
        announcement = 'Fixture sync completed without writing a device.';
        mutationRunning = false;
        return;
      }
      await ExecuteIPodSync(new main.IPodExecuteRequest({ plan_id: plan.plan_id, confirmed: true }));
    } catch (caught) {
      mutationRunning = false;
      const message = caught instanceof Error ? caught.message : String(caught);
      announcement = message;
      dispatch('notify', { tone: 'error', message });
    }
  }

  function handleIPodEvent(event: IPodEventPayload) {
    operationEnvelope = mergeIPodOperation(operationEnvelope, event);
    if (event.message) announcement = event.message;
    const data = getIPodEventData(event);
    if (event?.type === 'ipod_progress') {
      const syncProgress = event.kind === 'sync'
        || event.operation === 'execute'
        || ['backup', 'execute', 'revalidate'].some(prefix => String(event.stage || '').startsWith(prefix));
      mutationRunning = mutationRunning || syncProgress;
    } else if (event.type === 'ipod_execute') {
      mutationRunning = false;
      announcement = recordBoolean(data, 'ok')
        ? 'Sync completed after a verified backup.'
        : 'Sync finished with partial results.';
      exactConfirmation = false;
      plan = null;
    } else if (event.type === 'ipod_operation_ended') {
      if (event.kind === 'sync' || event.operation === 'execute') mutationRunning = false;
      if (event.status === 'failed' && (event.kind === 'sync' || event.operation === 'execute')) {
        const message = event.message || 'The iPod operation failed.';
        announcement = message;
        if (/stale|changed|expired/i.test(message)) planStale = true;
      } else if (event.status === 'completed') {
        announcement = `${(event.kind || 'iPod operation').replace(/_/g, ' ')} completed.`;
      }
    } else if (event.type === 'ipod_watch_error') {
      dispatch('notify', { tone: 'warning', message: event.message || 'Automatic iPod detection stopped.' });
    }
  }

  function handleAnnouncement(event: CustomEvent<{ message: string }>) {
    announcement = event.detail.message;
  }

  function handleOperationNotification(event: CustomEvent<{
    tone: 'error' | 'warning' | 'success';
    message: string;
  }>) {
    announcement = event.detail.message;
    dispatch('notify', event.detail);
  }

  function handleCapacitySessionState(event: CustomEvent<{ active: boolean }>) {
    capacityUnlockActive = event.detail.active;
    if (capacityUnlockActive) activeTab = 'advanced';
  }

  function handleTabKeydown(event: KeyboardEvent) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    if (!(event.currentTarget instanceof HTMLElement)) return;
    const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>(
      '[role="tab"]:not(:disabled)',
    ));
    if (!tabs.length) return;
    const current = event.target instanceof HTMLButtonElement
      ? tabs.indexOf(event.target)
      : tabs.findIndex(tab => tab.getAttribute('aria-selected') === 'true');
    let next = Math.max(0, current);
    if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = tabs.length - 1;
    else if (event.key === 'ArrowRight') next = (next + 1) % tabs.length;
    else next = (next - 1 + tabs.length) % tabs.length;
    event.preventDefault();
    tabs[next].focus();
    tabs[next].click();
  }

  async function ejectDevice() {
    if (!ejectArmed || writeBlocked) return;
    ejecting = true;
    try {
      if (!demoMode) {
        const result = parseIPodResponse(await EjectIPod(device.path));
        if (!recordBoolean(result, 'ok')) {
          throw new Error(recordString(result, 'message', 'The iPod could not be ejected.'));
        }
      }
      dispatch('eject', { device_id: device.device_id, path: device.path });
    } catch (caught) {
      dispatch('notify', { tone: 'error', message: caught instanceof Error ? caught.message : String(caught) });
    } finally {
      ejecting = false;
      ejectArmed = false;
    }
  }

  onDestroy(() => {
    destroyed = true;
    browseRequestId += 1;
  });
</script>

<section class="device-manager">
  <p class="visually-hidden" aria-live="polite" aria-atomic="true">{announcement}</p>
  <header class="device-hero">
    <span class="device-glyph"><Smartphone size={30}/></span>
    <div><p>{device.model_family}{device.generation ? ` · ${device.generation}` : ''}</p><h2>{device.name}</h2><span>{device.capacity || formatDetectedStorage(device.disk_size_gb)} · {filesystemUnavailable ? `${device.filesystem_type || 'HFS+'} ${device.raw_read_only ? 'raw read-only access' : 'read-only access'}` : (device.filesystem_type || 'Filesystem unknown')}</span></div>
    <div class="hero-actions">
      {#if !ejectArmed}
        <button class="secondary" disabled={writeBlocked} on:click={() => ejectArmed = true}><Unplug size={16}/> Safe eject</button>
      {:else}
        <button class="danger" disabled={writeBlocked} on:click={ejectDevice}><Unplug size={16}/> {ejecting ? 'Ejecting…' : 'Confirm eject'}</button>
        <button class="icon" aria-label="Cancel eject" on:click={() => ejectArmed = false}><X size={16}/></button>
      {/if}
    </div>
  </header>

  {#if !connected}
    <div class="safety-banner warning"><Unplug size={18}/><div><strong>Waiting for the same iPod</strong><span>{filesystemUnavailable ? 'The raw read-only device is no longer attached. Only Overview remains available until it reconnects.' : 'The mounted volume is unavailable. Only the persisted Advanced recovery workflow remains usable until this exact device reconnects.'}</span></div></div>
  {:else if filesystemUnavailable}
    <div class="safety-banner warning filesystem-warning" role="status"><AlertTriangle size={20}/><div><strong>Mac-formatted iPod detected</strong><span>{device.access_message || 'Windows has not mounted this HFS+ filesystem, so the device remains read-only.'}</span><small>Vela made no writes. Filesystem-dependent browsing, backup, sync, and eject actions remain disabled.</small></div></div>
  {:else if capacityUnlockActive}
    <div class="safety-banner warning"><LockKeyhole size={18}/><div><strong>Capacity-unlock session active</strong><span>Normal sync, backup, browse, and eject controls stay locked until the persisted workflow completes or is safely cancelled.</span></div></div>
  {:else if deviceWriteBlocked}
    <div class="safety-banner warning" role="status"><AlertTriangle size={18}/><div><strong>{device.browse_only ? 'Browse only' : 'Writes unavailable'}</strong><span>{writeGuidance}</span>{#if device.write_block_code}<small>Code: {device.write_block_code.replace(/_/g, ' ')}</small>{/if}</div></div>
  {:else}
    <div class="safety-banner safe"><ShieldCheck size={18}/><div><strong>Verified for reviewed sync</strong><span>Every write still requires plan review, confirmation, and a fresh mandatory backup.</span></div></div>
  {/if}
  {#if downloadBusy && !filesystemUnavailable}<div class="safety-banner warning"><AlertTriangle size={18}/><span>Finish the active download before backing up, syncing, or ejecting this iPod.</span></div>{/if}

  <div class="device-tabs" role="tablist" aria-label={`${device.name} sections`} tabindex="-1" on:keydown={handleTabKeydown}>
    <button
      id="device-tab-overview"
      role="tab"
      aria-selected={activeTab === 'overview'}
      aria-controls="device-panel-overview"
      tabindex={activeTab === 'overview' ? 0 : -1}
      class:active={activeTab === 'overview'}
      disabled={!filesystemUnavailable && (capacityUnlockActive || mutationRunning || operationActive)}
      on:click={() => activeTab = 'overview'}
    ><HardDrive size={16}/> Overview</button>
    {#each browseResources as resource (resource)}
      <button
        id={`device-tab-${resource}`}
        role="tab"
        aria-selected={activeTab === resource}
        aria-controls={`device-panel-${resource}`}
        tabindex={activeTab === resource ? 0 : -1}
        class:active={activeTab === resource}
        disabled={!connected || filesystemUnavailable || capacityUnlockActive || mutationRunning || operationActive}
        on:click={() => openBrowse(resource)}
      ><svelte:component this={resourceIcon(resource)} size={16}/>{resource}</button>
    {/each}
    <button
      id="device-tab-sync"
      role="tab"
      aria-selected={activeTab === 'sync'}
      aria-controls="device-panel-sync"
      tabindex={activeTab === 'sync' ? 0 : -1}
      class:active={activeTab === 'sync'}
      disabled={!connected || filesystemUnavailable || capacityUnlockActive || mutationRunning || operationActive}
      on:click={() => activeTab = 'sync'}
    ><RefreshCw size={16}/> Sync</button>
    <button
      id="device-tab-backups"
      role="tab"
      aria-selected={activeTab === 'backups'}
      aria-controls="device-panel-backups"
      tabindex={activeTab === 'backups' ? 0 : -1}
      class:active={activeTab === 'backups'}
      disabled={!connected || filesystemUnavailable || capacityUnlockActive || mutationRunning || operationActive}
      on:click={() => activeTab = 'backups'}
    ><Archive size={16}/> Backups &amp; recovery</button>
    <button
      id="device-tab-advanced"
      role="tab"
      aria-selected={activeTab === 'advanced'}
      aria-controls="device-panel-advanced"
      tabindex={activeTab === 'advanced' ? 0 : -1}
      class:active={activeTab === 'advanced'}
      disabled={filesystemUnavailable}
      on:click={() => activeTab = 'advanced'}
    ><Wrench size={16}/> Advanced</button>
  </div>

  <div
    class="device-panel"
    id={`device-panel-${activeTab}`}
    role="tabpanel"
    aria-labelledby={`device-tab-${activeTab}`}
    tabindex="0"
  >
  {#if advancedLoaded && !filesystemUnavailable}
    <div class="advanced-pane" hidden={activeTab !== 'advanced'}>
      <CapacityUnlockWizard
        {device}
        {connected}
        active={activeTab === 'advanced'}
        {ipodEvent}
        operationBusy={operationActive || mutationRunning || planning || downloadBusy || ejecting}
        {demoMode}
        on:announce={handleOperationNotification}
        on:sessionstate={handleCapacitySessionState}
      />
    </div>
  {/if}
  {#if activeTab === 'overview'}
    {#if filesystemUnavailable}
      <div class="overview-grid">
        <article class="capacity-card"><div><strong>Total detected storage</strong><span>{formatDetectedStorage(device.disk_size_gb)}</span></div></article>
        <article><HardDrive size={19}/><dl><div><dt>Filesystem access</dt><dd>{device.filesystem_type || 'HFS+'} {device.raw_read_only ? 'raw read-only' : 'read-only'}</dd></div><div><dt>Windows mount</dt><dd>Unavailable</dd></div><div><dt>Raw device</dt><dd class="raw-path">{device.raw_device_path || 'Unavailable'}</dd></div></dl></article>
        <article><Database size={19}/><dl><div><dt>Database details</dt><dd>Unavailable</dd></div><div><dt>Checksum details</dt><dd>Unavailable</dd></div><div><dt>Media capabilities</dt><dd>Unavailable</dd></div></dl></article>
        <article><FolderOpen size={19}/><dl><div><dt>Device identity</dt><dd>Unavailable</dd></div><div><dt>Serial</dt><dd>Unavailable</dd></div><div><dt>Firmware</dt><dd>Unavailable</dd></div></dl></article>
        <article class="next-step-card"><ShieldCheck size={19}/><div><strong>Back up before reformatting</strong><span>Use macOS or a trusted read-only HFS+ tool to back up this iPod.</span><span>Verify the backup before reformatting the device for Windows.</span></div></article>
      </div>
    {:else}
      <div class="overview-grid">
        <article class="capacity-card"><div><strong>Storage</strong><span>{device.free_space_gb.toFixed(1)} GB free of {device.disk_size_gb.toFixed(1)} GB</span></div><progress max={device.disk_size_gb || 1} value={Math.max(0, device.disk_size_gb - device.free_space_gb)}></progress></article>
        <article><Database size={19}/><dl><div><dt>Database</dt><dd>{device.uses_sqlite_db ? 'SQLiteDB' : 'iTunesDB'}</dd></div><div><dt>Checksum</dt><dd>{device.checksum_type}</dd></div><div><dt>Firmware</dt><dd>{device.firmware || 'Unknown'}</dd></div><div><dt>Model</dt><dd>{device.model_number || 'Unknown'}</dd></div></dl></article>
        <article><Disc3 size={19}/><div><strong>Capabilities</strong><span>{Array.isArray(device.audio_codecs) ? device.audio_codecs.join(', ') : Object.keys(device.audio_codecs || {}).join(', ') || 'Audio'}</span><span>{device.podcasts_supported ? 'Podcasts' : 'No podcast database'} · {device.voice_memos_supported ? 'Voice memos' : 'No voice memos'}</span><span>{device.supports_sparse_artwork ? 'Sparse artwork supported' : 'Standard artwork database'}</span></div></article>
        <article><FolderOpen size={19}/><dl><div><dt>Verified identity</dt><dd>{device.device_id.slice(0, 12)}…</dd></div><div><dt>Volume</dt><dd>{device.volume_identity_key ? 'Verified' : 'Incomplete'}</dd></div><div><dt>Serial</dt><dd>{device.serial || 'Unavailable'}</dd></div></dl></article>
      </div>
    {/if}
  {:else if activeTab === 'backups'}
    <BackupsRecovery
      {device}
      {ipodEvent}
      operationBusy={operationActive || mutationRunning || planning || downloadBusy || ejecting}
      {demoMode}
      on:announce={handleAnnouncement}
    />
  {:else if activeTab !== 'sync' && activeTab !== 'advanced'}
    <div class="browser-toolbar"><label><Search size={15}/><input aria-label={`Search ${browseResource}`} bind:value={browseSearch} placeholder={`Search ${browseResource}`} /></label><button class="icon" aria-label="Refresh device library" disabled={browseLoading} on:click={() => openBrowse(browseResource, browsePage)}><RefreshCw size={16}/></button></div>
    {#if browseLoading}<div class="state"><LoaderCircle size={22}/><span>Reading {browseResource} without modifying the iPod…</span></div>
    {:else if browseError}<div class="state error"><strong>Could not read {browseResource}</strong><span>{browseError}</span><button class="secondary" on:click={() => openBrowse(browseResource, browsePage)}>Try again</button></div>
    {:else if !filteredBrowseItems.length}<div class="state"><strong>No {browseResource} found</strong><span>{browseSearch ? 'Try a different search.' : 'This iPod library section is empty.'}</span></div>
    {:else}
      <VirtualList
        items={filteredBrowseItems}
        itemKey={browseItemKey}
        rowHeight={58}
        maxHeight="420px"
        viewportClass="browse-list"
        restoreKey={`ipod:${device.device_id}:${browseResource}:${browsePage}:${browseSearch}`}
        ariaLabel={`${browseResource} on ${device.name}`}
        let:item
        let:index
      >
        <article>
          <span>{(browsePage - 1) * browsePageSize + index + 1}</span>
          <div><strong>{browseLabel(item)}</strong><small>{browseMeta(item) || browseResource.slice(0, -1)}</small></div>
        </article>
      </VirtualList>
    {/if}
    {#if browseTotal > browsePageSize}<footer class="pager"><button disabled={browsePage <= 1 || browseLoading} on:click={() => openBrowse(browseResource, browsePage - 1)}><ChevronLeft size={16}/> Previous</button><span>Page {browsePage} of {browsePages} · {browseTotal} items</span><button disabled={browsePage >= browsePages || browseLoading} on:click={() => openBrowse(browseResource, browsePage + 1)}>Next <ChevronRight size={16}/></button></footer>{/if}
  {:else if activeTab === 'sync'}
    <div class="sync-layout">
      <section class="local-picker">
        <header><div><p>Local Vela library</p><h3>Choose completed music</h3></div><span>{selectedFiles.length} songs selected</span></header>
        <label class="local-search"><Search size={15}/><input aria-label="Search local releases" bind:value={localSearch} placeholder="Search downloaded albums and playlists" /></label>
        {#if localError}<p class="inline-error">{localError}</p>{/if}
        <VirtualList
          items={filteredLocalReleases}
          itemKey={localReleaseKey}
          rowHeight={58}
          maxHeight="340px"
          viewportClass="release-picker"
          restoreKey={`ipod:${device.device_id}:local:${localSearch}`}
          ariaLabel="Downloaded releases available to sync"
          let:item
        >
          <button
            class:selected={selectedFiles.some(selected => selected.release === item.relative_path)}
            disabled={!!localLoadingPath || mutationRunning}
            on:click={() => toggleLocalRelease(item)}
          >
            <span>{#if localLoadingPath === item.relative_path}<LoaderCircle size={16}/>{:else if selectedFiles.some(selected => selected.release === item.relative_path)}<Check size={16}/>{:else}<Album size={16}/>{/if}</span>
            <div><strong>{item.title}</strong><small>{item.artist || item.kind} · {item.track_count} songs</small></div>
          </button>
        </VirtualList>
        <button class="primary plan-button" disabled={!selectedFiles.length || writeBlocked} on:click={() => createPlan()}>{planning ? 'Creating review…' : `Review sync plan (${selectedFiles.length})`}</button>
      </section>

      <section class="plan-review">
        <header><div><p>Reviewed plan</p><h3>{plan ? 'Review every effect' : 'No plan created'}</h3></div>{#if plan}<small>Expires {new Date(plan.expires_at * 1000).toLocaleTimeString()}</small>{/if}</header>
        {#if deviceWriteBlocked}<div class="safety-banner warning" role="status"><AlertTriangle size={17}/><span>{writeGuidance}</span></div>{/if}
        {#if planError}<div class="inline-error">{planError}</div>{/if}
        {#if planStale}<div class="safety-banner warning"><AlertTriangle size={17}/><span>This plan is stale. Recreate and review it before syncing.</span></div>{/if}
        {#if plan}
          <div class="plan-groups">
            <article><strong>{plan.additions}</strong><span>Additions</span></article>
            <article><strong>{plan.removals}</strong><span>Removals</span></article>
            <article><strong>{plan.updates}</strong><span>Metadata/artwork updates</span></article>
            <article><strong>{plan.conversions}</strong><span>Conversions</span></article>
            <article><strong>{plan.playlist_changes}</strong><span>Playlist effects</span></article>
            <article class:warning={plan.unsupported > 0}><strong>{plan.unsupported}</strong><span>Unsupported items</span></article>
          </div>
          {#await loadIPodPlanDetails() then planDetailsModule}
            <svelte:component this={planDetailsModule.default} {plan} {demoMode} on:stale={handlePlanStale} />
          {:catch}
            <div class="inline-error">The detailed plan view could not be loaded.</div>
          {/await}
          <dl class="storage-review"><div><dt>Add / update</dt><dd>{formatBytes((plan.storage?.bytes_to_add || 0) + (plan.storage?.bytes_to_update || 0))}</dd></div><div><dt>Removed</dt><dd>{formatBytes(plan.storage?.bytes_to_remove || 0)}</dd></div><div><dt>Net storage change</dt><dd>{formatBytes(Math.abs(plan.storage?.net_change_bytes || 0))}{(plan.storage?.net_change_bytes || 0) < 0 ? ' freed' : ' required'}</dd></div><div><dt>Free after sync</dt><dd>{formatBytes(plan.storage?.free_after_bytes || 0)}</dd></div><div><dt>Reviewed local files</dt><dd>{plan.source_count}</dd></div></dl>
          <label class="exact-confirm"><input type="checkbox" bind:checked={exactConfirmation} disabled={mutationRunning} /><span>I reviewed this exact plan and authorize a fresh verified backup followed by sync.</span></label>
          <div class="sync-actions"><button class="primary" disabled={!exactConfirmation || writeBlocked || planStale} on:click={executePlan}><ShieldCheck size={16}/> Back up & sync</button></div>
        {:else if !planError}<div class="state"><ShieldCheck size={24}/><span>Select local releases and create a plan. Nothing is written during review.</span></div>{/if}
      </section>
    </div>
  {/if}
  </div>

  {#if operationEnvelope}
    <div class="persistent-operation">
      <OperationStatus
        operation={operationEnvelope}
        on:announce={handleAnnouncement}
        on:notify={handleOperationNotification}
      />
    </div>
  {/if}
</section>

<style>
  .device-manager{display:grid;gap:16px}
  .device-hero{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:16px}
  .device-glyph{width:62px;height:78px;display:grid;place-items:center;border-radius:10px;background:linear-gradient(145deg,var(--surface),var(--surface-2));border:1px solid var(--line);color:var(--accent);box-shadow:var(--shadow)}
  .device-hero p,.local-picker header p,.plan-review header p{margin:0;color:var(--accent);font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em}
  .device-hero h2{margin:3px 0;font-size:28px}.device-hero>div>span{color:var(--muted);font-size:11px}.hero-actions{display:flex;align-items:center;gap:6px}
  button{border:0;cursor:pointer}.primary,.secondary,.danger{min-height:38px;display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:0 14px;border-radius:10px;font-size:12px;font-weight:650}.primary{background:var(--accent);color:var(--bg)}.secondary{background:var(--surface-2);color:var(--text)}.danger{background:var(--error-color);color:var(--bg)}.icon{width:36px;height:36px;display:grid;place-items:center;padding:0;border-radius:9px;background:var(--surface-2);color:var(--muted)}button:disabled{opacity:var(--disabled-opacity);cursor:default}
  .safety-banner{display:flex;align-items:flex-start;gap:10px;padding:12px 14px;border:1px solid var(--line);border-radius:12px;background:var(--surface);color:var(--muted)}.safety-banner.safe{border-color:var(--success-border);color:var(--success-color)}.safety-banner.warning{border-color:var(--warning-border);color:var(--warning-color)}.safety-banner>div{display:grid;gap:3px}.safety-banner span,.safety-banner small{color:var(--muted);font-size:11px}
  .filesystem-warning{padding:16px;border-width:2px;background:var(--warning-soft)}.filesystem-warning strong{font-size:14px;color:var(--warning-color)}
  .device-tabs{display:flex;gap:4px;overflow-x:auto;padding:4px;border-radius:11px;background:var(--surface-2)}.device-tabs button{min-height:36px;display:flex;align-items:center;gap:6px;padding:0 11px;border-radius:8px;background:transparent;color:var(--muted);text-transform:capitalize;white-space:nowrap}.device-tabs button.active{background:var(--surface);color:var(--accent);box-shadow:var(--shadow)}
  .overview-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.overview-grid article{min-height:130px;display:flex;align-items:flex-start;gap:12px;padding:17px;border:1px solid var(--line);border-radius:14px;background:var(--surface)}.overview-grid article>div{display:grid;gap:6px}.overview-grid span{color:var(--muted);font-size:11px}.capacity-card{grid-column:1/-1;display:grid!important}.capacity-card>div{display:flex!important;justify-content:space-between}.capacity-card progress{width:100%;height:8px;accent-color:var(--accent)}.next-step-card{grid-column:1/-1;min-height:0!important}.raw-path{font-family:var(--font-mono);overflow-wrap:anywhere}dl{width:100%;margin:0}dl>div{display:flex;justify-content:space-between;gap:14px;padding:6px 0;border-top:1px solid var(--line);font-size:11px}dt{color:var(--muted)}dd{margin:0;text-align:right;overflow:hidden;text-overflow:ellipsis}
  .browser-toolbar{display:flex;gap:8px}.browser-toolbar label,.local-search{height:38px;flex:1;display:flex;align-items:center;gap:7px;padding:0 10px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}.browser-toolbar input,.local-search input{width:100%;padding:0;border:0;background:transparent;box-shadow:none}
  .state{min-height:230px;display:grid;place-items:center;align-content:center;gap:8px;color:var(--muted);text-align:center}.state.error{color:var(--error-color)}.state span{font-size:11px}:global(.browse-list){border-radius:10px}:global(.browse-list) article{height:54px;display:grid;grid-template-columns:34px minmax(0,1fr);align-items:center;gap:10px;padding:7px 11px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}:global(.browse-list) article>span{color:var(--faint);font-size:10px;text-align:center}:global(.browse-list) article>div{min-width:0;display:grid;gap:3px}:global(.browse-list) strong,:global(.browse-list) small{overflow:hidden;white-space:nowrap;text-overflow:ellipsis}:global(.browse-list) small{color:var(--muted);font-size:10px}.pager{display:flex;align-items:center;justify-content:center;gap:12px}.pager button{min-height:36px;display:flex;align-items:center;gap:5px;padding:0 10px;border-radius:8px;background:var(--surface-2);color:var(--text)}.pager span{color:var(--muted);font-size:10px}
  .sync-layout{display:grid;grid-template-columns:minmax(280px,.78fr) minmax(380px,1.22fr);gap:14px}.local-picker,.plan-review{min-width:0;padding:17px;border:1px solid var(--line);border-radius:14px;background:var(--surface)}.local-picker header,.plan-review>header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:12px}.local-picker h3,.plan-review h3{margin:3px 0 0}.local-picker header>span,.plan-review header small{color:var(--muted);font-size:10px}:global(.release-picker){margin:10px 0}:global(.release-picker) button{width:100%;height:53px;display:grid;grid-template-columns:30px minmax(0,1fr);align-items:center;gap:8px;padding:8px;border:1px solid var(--line);border-radius:10px;background:var(--bg);text-align:left}:global(.release-picker) button.selected{border-color:var(--accent);background:var(--accent-soft)}:global(.release-picker) button>span{width:28px;height:28px;display:grid;place-items:center;border-radius:8px;background:var(--surface-2);color:var(--accent)}:global(.release-picker) button>div{min-width:0;display:grid;gap:2px}:global(.release-picker) strong,:global(.release-picker) small{overflow:hidden;white-space:nowrap;text-overflow:ellipsis}:global(.release-picker) small{color:var(--muted);font-size:10px}.plan-button{width:100%}.inline-error{padding:10px;border-radius:9px;background:var(--error-soft);color:var(--error-color);font-size:11px}
  .plan-groups{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}.plan-groups article{display:grid;gap:2px;padding:11px;border-radius:10px;background:var(--bg)}.plan-groups article.warning{color:var(--warning-color)}.plan-groups strong{font-size:19px}.plan-groups span{color:var(--muted);font-size:9px}.storage-review{margin:12px 0}.exact-confirm{display:flex;align-items:flex-start;gap:9px;margin:12px 0;color:var(--muted);font-size:11px;line-height:1.45}.exact-confirm input{margin-top:2px;accent-color:var(--accent)}.sync-actions{display:flex;gap:8px}
  button:focus-visible,input:focus-visible,.device-panel:focus-visible{outline:2px solid var(--focus-ring);outline-offset:2px}
  .device-panel{min-width:0}
  .persistent-operation{position:sticky;z-index:6;bottom:0;padding-top:4px}
  .visually-hidden{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important}
  @media(max-width:900px){.sync-layout{grid-template-columns:1fr}.overview-grid{grid-template-columns:1fr}.capacity-card{grid-column:auto}}
  @media(max-width:720px){.device-hero{grid-template-columns:auto 1fr}.hero-actions{grid-column:1/-1}.plan-groups{grid-template-columns:repeat(2,1fr)}}
</style>
