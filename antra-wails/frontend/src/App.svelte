<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { GetConfig, SaveConfig, PickDirectory, GetSuggestedDownloadLocation, StartDownload, CancelDownload, PauseDownload, ResumeDownload, SetDownloadWorkerCount, GetDownloadWorkerCapacity, GetHistory, AddHistory, ClearHistory, ValidateTidalAuth, StartTidalOAuthLogin, StartAppleBrowserLogin, StartAmazonBrowserLogin, ConfirmAmazonLogin, CaptureSpDC } from '../wailsjs/go/main/App.js';
  import { GetArtistDiscography, SearchArtists, CheckSourceHealth, GetDownloadedMusicLibrary, RefreshDownloadedMusicLibrary, GetDownloadedRelease, GetAppleMusicLibrary, RefreshAppleMusicLibrary, GetAppleMusicPlaylistDetail, GetAppleMusicArtistDetail, StartAppleMusicIndex, ResetAppleMusicIndex, ScanIPodDevices, RunAutoSync } from '../wailsjs/go/main/App.js';
  import { EventsOn, ClipboardGetText } from '../wailsjs/runtime/runtime.js';
  import type { main } from '../wailsjs/go/models';
  import ArtworkImage from './lib/ArtworkImage.svelte';
  import PlayerBar from './lib/PlayerBar.svelte';
  import ProgressiveCollection from './lib/ProgressiveCollection.svelte';
  import VirtualList from './lib/VirtualList.svelte';
  import type { PlayerTrack } from './lib/playerTypes';
  import type { IPodDevice, IPodEventPayload, IPodPlan } from './lib/ipodTypes';
  import {
    getIPodEventDevice, parseIPodDevicesResponse, parseIPodEventPayload,
  } from './lib/ipodTypes';
  import { BoundedDeque } from './lib/boundedBuffer';
  import {
    TrackActivityModel,
    type ActiveTrackState,
    type TrackActivityCounts,
  } from './lib/trackActivity';
  import {
    DEFAULT_UI_PREFERENCES,
    applyUIPreferences,
    normalizeUIPreferences,
    type AppearancePreference,
    type StartupDestination,
    type UIPreferences,
  } from './lib/uiPreferences';
  import { Library, Download, HardDriveDownload, Users, Compass, Settings, Plus, Search, Smartphone,
    WifiOff, SlidersHorizontal, ArrowUpDown, MoreHorizontal, Check, Circle,
    LoaderCircle, Clock3, ChevronDown, RefreshCw, X, FolderOpen, ArrowLeft,
    Star, Album, ListMusic, UserRound, Pause, Play, FileText, ChevronUp,
    Trash2, SkipForward, Palette } from 'lucide-svelte';

  let config: main.Config = {
    config_schema_version: 2,
    download_path: '',
    download_path_is_library_root: true,
    sources_enabled: [],
    first_run_complete: false,
    apple_enabled: true,
    apple_authorization_token: '',
    apple_music_user_token: '',
    apple_storefront: 'gb',
    apple_wvd_path: '',
    amazon_enabled: false,
    amazon_direct_creds_json: '',
    amazon_wvd_path: '',
    amazon_region: 'us',
    qobuz_enabled: false,
    qobuz_email: '',
    qobuz_password: '',
    qobuz_app_id: '285473059',
    qobuz_app_secret: '',
    qobuz_user_auth_token: '',
    deezer_arl_token: '',
    deezer_bf_secret: 'g4el58wc0zvf9na1',
    output_format: 'lossless',
    max_retries: 3,
    max_concurrent_jobs: 2,
    library_mode: 'smart_dedup',
    prefer_explicit: true,
    folder_structure: 'standard',
    album_folder_structure: 'standard',
    playlist_folder_structure: 'standard',
    single_track_structure: 'album_numbered',
    filename_format: 'default',
    spotify_sp_dc: '',
    tidal_enabled: false,
    tidal_auth_mode: 'session_json',
    tidal_session_json: '',
    tidal_access_token: '',
    tidal_refresh_token: '',
    tidal_session_id: '',
    tidal_token_type: 'Bearer',
    tidal_country_code: '',
    antra_api_key: '',
    theme: '',
    strict_matching: false,
    download_source: 'auto',
    download_sources: ['auto'],
    save_cover_art_sidecar: true,
    single_track_filename_template: '{artist} - {title}',
    album_track_filename_template: '{track} - {title}',
    folder_structure_template: '{album_artist}/{year} - {album}',
    multi_disc_handling: '',
    track_number_padding: 2,
    illegal_character_replacement: '_',
    whitespace_handling: 'keep',
    fetch_lyrics: true,
    filename_conflict_behavior: 'skip',
    auto_sync_enabled: false,
    auto_sync_hour: 6,
    auto_sync_minute: 0,
    auto_sync_days: 127,
    tracked_playlists: [],
  } as main.Config;
  let tidalValidationStatus: { ok: boolean; message: string; display_name?: string; country_code?: string } | null = null;
  let tidalValidationLoading = false;

  // ── TIDAL OAuth login state ─────────────────────────────────────────────────
  interface TidalOAuthState {
    phase: 'idle' | 'starting' | 'waiting_browser' | 'success' | 'error';
    url?: string;
    code?: string;
    message?: string;
    displayName?: string;
    countryCode?: string;
    sessionJson?: string;
  }
  let tidalOAuth: TidalOAuthState = { phase: 'idle' };

  interface BrowserLoginState {
    phase: 'idle' | 'starting' | 'waiting_for_user' | 'capturing' | 'success' | 'error';
    message?: string;
    detail?: string;
  }
  let appleLogin: BrowserLoginState = { phase: 'idle' };
  let amazonLogin: BrowserLoginState = { phase: 'idle' };
  let spDcCapture: BrowserLoginState = { phase: 'idle' };

  // ── Theme system ────────────────────────────────────────────────────────────
  type Appearance = AppearancePreference;
  const uiDemoMode = new URLSearchParams(window.location.search).get('demo') === '1';
  function isLocalPerformanceEnabled(): boolean {
    if (new URLSearchParams(window.location.search).get('perf') === '1') return true;
    try {
      return localStorage.getItem('vela.performance') === '1';
    } catch {
      return false;
    }
  }
  const frontendPerformanceEnabled = isLocalPerformanceEnabled();
  type TimerHandle = ReturnType<typeof setTimeout>;
  type IdleCapableWindow = Window & {
    requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number;
    cancelIdleCallback?: (handle: number) => void;
  };
  const scheduledTimeouts: TimerHandle[] = [];
  const scheduledIdleCallbacks: number[] = [];
  const scheduledAnimationFrames: number[] = [];
  let componentDestroyed = false;
  let frontendPerformanceSequence = 0;
  let appearanceSettingsPromise: Promise<typeof import('./lib/AppearanceSettings.svelte')> | null = null;
  let aboutSettingsPromise: Promise<typeof import('./lib/AboutSettings.svelte')> | null = null;
  let ipodManagerPromise: Promise<typeof import('./lib/IPodManager.svelte')> | null = null;

  function loadAppearanceSettings() {
    if (!appearanceSettingsPromise) appearanceSettingsPromise = import('./lib/AppearanceSettings.svelte');
    return appearanceSettingsPromise;
  }

  function loadAboutSettings() {
    if (!aboutSettingsPromise) aboutSettingsPromise = import('./lib/AboutSettings.svelte');
    return aboutSettingsPromise;
  }

  function loadIPodManager() {
    if (!ipodManagerPromise) ipodManagerPromise = import('./lib/IPodManager.svelte');
    return ipodManagerPromise;
  }

  function markFrontendPerformance(name: string) {
    if (!frontendPerformanceEnabled) return;
    const markName = `vela:${name}`;
    performance.clearMarks(markName);
    performance.mark(markName);
  }

  function measureFrontendPerformance<T>(name: string, operation: () => T): T {
    if (!frontendPerformanceEnabled) return operation();
    const measureName = `vela:${name}`;
    const entryName = `${measureName}:${++frontendPerformanceSequence}`;
    const startMark = `${entryName}:start`;
    const endMark = `${entryName}:end`;
    performance.mark(startMark);
    try {
      return operation();
    } finally {
      performance.mark(endMark);
      performance.measure(measureName, startMark, endMark);
      performance.clearMarks(startMark);
      performance.clearMarks(endMark);
    }
  }

  function scheduleTimeout(callback: () => void, delay = 0): TimerHandle | null {
    if (componentDestroyed) return null;
    let timer: TimerHandle;
    timer = setTimeout(() => {
      const index = scheduledTimeouts.indexOf(timer);
      if (index >= 0) scheduledTimeouts.splice(index, 1);
      if (!componentDestroyed) callback();
    }, delay);
    scheduledTimeouts.push(timer);
    return timer;
  }

  function cancelScheduledTimeout(timer: TimerHandle | null) {
    if (timer === null) return;
    clearTimeout(timer);
    const index = scheduledTimeouts.indexOf(timer);
    if (index >= 0) scheduledTimeouts.splice(index, 1);
  }

  function scheduleAnimationFrame(callback: () => void): number | null {
    if (componentDestroyed) return null;
    let handle = 0;
    handle = window.requestAnimationFrame(() => {
      const index = scheduledAnimationFrames.indexOf(handle);
      if (index >= 0) scheduledAnimationFrames.splice(index, 1);
      if (!componentDestroyed) callback();
    });
    scheduledAnimationFrames.push(handle);
    return handle;
  }

  function cancelScheduledAnimationFrame(handle: number | null) {
    if (handle === null) return;
    window.cancelAnimationFrame(handle);
    const index = scheduledAnimationFrames.indexOf(handle);
    if (index >= 0) scheduledAnimationFrames.splice(index, 1);
  }

  function scheduleIdleWork(callback: () => void, timeout = 1500) {
    if (componentDestroyed) return;
    const idleWindow = window as IdleCapableWindow;
    const requestIdleCallback = idleWindow.requestIdleCallback?.bind(idleWindow);
    if (!requestIdleCallback) {
      scheduleTimeout(callback);
      return;
    }
    let handle = 0;
    handle = requestIdleCallback(() => {
      const index = scheduledIdleCallbacks.indexOf(handle);
      if (index >= 0) scheduledIdleCallbacks.splice(index, 1);
      if (!componentDestroyed) callback();
    }, { timeout });
    scheduledIdleCallbacks.push(handle);
  }

  function clearScheduledWork() {
    scheduledTimeouts.forEach(timer => clearTimeout(timer));
    scheduledTimeouts.length = 0;
    scheduledAnimationFrames.forEach(handle => window.cancelAnimationFrame(handle));
    scheduledAnimationFrames.length = 0;
    const idleWindow = window as IdleCapableWindow;
    const cancelIdleCallback = idleWindow.cancelIdleCallback?.bind(idleWindow);
    if (cancelIdleCallback) {
      scheduledIdleCallbacks.forEach(handle => cancelIdleCallback(handle));
    }
    scheduledIdleCallbacks.length = 0;
  }

  let appearance: Appearance = 'system';
  let uiPreferences: UIPreferences = { ...DEFAULT_UI_PREFERENCES };
  let systemDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  let systemReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let motionReduced = false;

  function syncUIPreferencesToConfig() {
    config.ui = { ...uiPreferences } as main.UIConfig;
  }

  function applyCurrentUIPreferences() {
    motionReduced = applyUIPreferences(appearance, uiPreferences, systemDark, systemReducedMotion);
  }

  function applyAppearance(value: Appearance, persist = true) {
    appearance = value;
    applyCurrentUIPreferences();
    config.theme = value;
    if (persist && !uiDemoMode) void autoSaveSettings();
  }

  function updateUIPreferences(next: UIPreferences, persist = true) {
    uiPreferences = normalizeUIPreferences(next);
    syncUIPreferencesToConfig();
    applyCurrentUIPreferences();
    historyBuffer.setCapacity(uiPreferences.completed_history_retention);
    historyItems = historyBuffer.toArray();
    window.dispatchEvent(new CustomEvent('vela:notification-preferences', {
      detail: {
        completion: uiPreferences.completion_notifications,
        device: uiPreferences.device_notifications,
      },
    }));
    if (persist && !uiDemoMode) void autoSaveSettings();
  }

  function resetAppearancePreferences() {
    updateUIPreferences({
      ...uiPreferences,
      scale: DEFAULT_UI_PREFERENCES.scale,
      density: DEFAULT_UI_PREFERENCES.density,
      sidebar_width: DEFAULT_UI_PREFERENCES.sidebar_width,
      artwork_size: DEFAULT_UI_PREFERENCES.artwork_size,
      motion: DEFAULT_UI_PREFERENCES.motion,
    });
    applyAppearance('system');
  }

  function handleSettingsChange() {
    updateUIPreferences(uiPreferences, false);
    void autoSaveSettings();
  }

  // ── Filename template system ────────────────────────────────────────────────
  const TEMPLATE_DEMO = {
    title: 'Come Together',
    artist: 'The Beatles',
    album_artist: 'The Beatles',
    album: 'Abbey Road',
    year: '1969',
    track: '07',
    disc: '1',
    genre: 'Rock',
    composer: 'Lennon-McCartney',
    isrc: 'GBAYE6800032',
    codec: 'flac',
    bitrate: '1411',
    quality: 'LOSSLESS',
  };

  function renderPreview(template: string): string {
    if (!template) return '';
    return template.replace(/\{(\w+)\}/gi, (_m, key) => {
      const k = key.toLowerCase() as keyof typeof TEMPLATE_DEMO;
      return TEMPLATE_DEMO[k] ?? `{${key}}`;
    });
  }

  let focusedTemplateEl: HTMLInputElement | null = null;

  function captureFocusedTemplate(node: HTMLInputElement) {
    focusedTemplateEl = node;
    return {
      destroy() {
        focusedTemplateEl = null;
      },
    };
  }

  function insertToken(token: string) {
    const el = focusedTemplateEl;
    if (!el) return;
    const start = el.selectionStart ?? el.value.length;
    const end   = el.selectionEnd   ?? el.value.length;
    const before = el.value.slice(0, start);
    const after  = el.value.slice(end);
    el.value = before + token + after;
    // Trigger Svelte reactivity via an input event
    el.dispatchEvent(new Event('input', { bubbles: true }));
    const newPos = start + token.length;
    el.setSelectionRange(newPos, newPos);
    el.focus();
  }

  function restoreFolderDefaults() {
    config.single_track_filename_template  = '{artist} - {title}';
    config.album_track_filename_template   = '{track} - {title}';
    config.folder_structure_template       = '{album_artist}/{year} - {album}';
    config.multi_disc_handling             = '';
    config.track_number_padding            = 2;
    config.illegal_character_replacement   = '_';
    config.whitespace_handling             = 'keep';
  }

  let isLoading = true;
  let setupMode = false;
  let showHistory = false;
  let showSettings = false;
  let showFolderSettings = false;
  let folderSettingsSaving = false;
  type SettingsPage = 'general' | 'appearance' | 'apple' | 'downloads' | 'audio' | 'discovery' | 'naming' | 'providers' | 'about';
  let settingsPage: SettingsPage = 'general';
  let settingsSaveState: 'idle' | 'saving' | 'saved' | 'error' = 'idle';
  let settingsError = '';
  let showDownloadedMusic = false;
  let settingsButtonEl: HTMLButtonElement | null = null;
  let historyItems: any[] = [];
  const historyBuffer = new BoundedDeque<any>(DEFAULT_UI_PREFERENCES.completed_history_retention);
  let historySequence = 0;
  let inputUrl = '';
  let inputUrlEl: HTMLTextAreaElement | null = null;

  function captureInputUrl(node: HTMLTextAreaElement) {
    inputUrlEl = node;
    return {
      destroy() {
        if (inputUrlEl === node) inputUrlEl = null;
      },
    };
  }

  let showCustomDownload = false;
  let customDestination = '';
  let customIPodDestinationId = '';
  let pendingDownloadIPodDestinationId = '';
  let isDownloading = false;
  let jobPreparationStatus = 'Reading release information…';
  interface DownloadJob {
    id: string;
    url: string;
    title: string;
    artwork?: string;
    status: 'waiting' | 'paused' | 'downloading' | 'downloaded' | 'failed' | 'cancelled';
    total: number;
    completed: number;
    ipodDestinationId?: string;
    ipodDestinationPath?: string;
    completedFiles?: string[];
    stagingError?: string;
  }
  let downloadJobs: DownloadJob[] = [];
  $: queuedJobs = downloadJobs.filter(job => job.status === 'waiting' || job.status === 'paused');
  type AppPage = 'library' | 'downloads' | 'downloaded' | 'devices' | 'settings';
  let currentPage: AppPage = 'library';
  let queuePaused = false;
  let priorityJobId = '';
  let showDownloadLogs = false;
  const queueStorageKey = 'vela-download-queue-v2';
  let toastMessage = '';
  let toastTone: 'error' | 'warning' | 'success' = 'error';
  let toastTimer: TimerHandle | null = null;
  let confirmDialog: { title: string; message: string; confirmLabel: string; danger: boolean } | null = null;
  let confirmResolver: ((confirmed: boolean) => void) | null = null;
  const lastPageStorageKey = 'vela-last-destination-v1';

  function normalizeHistoryItems(items: any[]): any[] {
    const occurrences: Record<string, number> = Object.create(null);
    return items.map(item => {
      if (item?.__uiKey) return item;
      const base = String(
        item?.job_id
        || item?.id
        || [item?.date, item?.url, item?.title, item?.total].filter(value => value != null).join('|')
        || `history-${historySequence++}`,
      );
      const occurrence = occurrences[base] || 0;
      occurrences[base] = occurrence + 1;
      return { ...item, __uiKey: `${base}#${occurrence}` };
    });
  }

  function replaceHistoryItems(items: any[]) {
    historyBuffer.setCapacity(uiPreferences.completed_history_retention);
    historyBuffer.replace(normalizeHistoryItems(items));
    historyItems = historyBuffer.toArray();
  }

  function prependHistoryItem(item: any) {
    historyBuffer.setCapacity(uiPreferences.completed_history_retention);
    const normalized = {
      ...item,
      __uiKey: item?.__uiKey || `${item?.job_id || item?.date || Date.now()}#new-${historySequence++}`,
    };
    historyBuffer.pushFront(normalized);
    historyItems = historyBuffer.toArray();
  }

  function historyItemKey(item: any): string {
    return String(item?.__uiKey || item?.job_id || item?.id || item?.date || item?.url);
  }

  function downloadJobKey(job: DownloadJob): string {
    return job.id;
  }

  function currentDestination(): StartupDestination {
    if (currentPage === 'downloads' || currentPage === 'downloaded') return currentPage;
    if (currentPage === 'library') return libraryView === 'recent' ? 'recently-added' : libraryView;
    return 'recently-added';
  }

  function rememberCurrentDestination() {
    if (!uiDemoMode && uiPreferences.remember_last_page) {
      localStorage.setItem(lastPageStorageKey, currentDestination());
    }
  }

  function applyStartupDestination() {
    const remembered = uiPreferences.remember_last_page ? localStorage.getItem(lastPageStorageKey) : '';
    const destination = remembered || uiPreferences.startup_destination;
    if (destination === 'downloads' || destination === 'downloaded') {
      currentPage = destination;
      return;
    }
    currentPage = 'library';
    libraryView = destination === 'albums'
      || destination === 'playlists'
      || destination === 'favourites'
      || destination === 'artists'
      ? destination
      : 'recent';
  }

  function showToast(message: string, tone: 'error' | 'warning' | 'success' = 'error') {
    toastMessage = message;
    toastTone = tone;
    if (toastTimer) cancelScheduledTimeout(toastTimer);
    toastTimer = scheduleTimeout(() => { toastMessage = ''; toastTimer = null; }, 7000);
  }

  function requestConfirmation(title: string, message: string, confirmLabel: string, danger = true): Promise<boolean> {
    confirmDialog = { title, message, confirmLabel, danger };
    return new Promise(resolve => { confirmResolver = resolve; });
  }

  function resolveConfirmation(confirmed: boolean) {
    const resolve = confirmResolver;
    confirmDialog = null;
    confirmResolver = null;
    resolve?.(confirmed);
  }

  function persistDownloadQueue() {
    if (!uiDemoMode) localStorage.setItem(queueStorageKey, JSON.stringify({ jobs: downloadJobs, paused: queuePaused }));
  }

  function restoreDownloadQueue() {
    try {
      const saved = JSON.parse(localStorage.getItem(queueStorageKey) || '{}');
      downloadJobs = (saved.jobs || []).map((job: DownloadJob) => job.status === 'downloading' ? { ...job, status: 'paused' as const } : job);
      queuePaused = !!saved.paused;
    } catch {
      downloadJobs = [];
      queuePaused = false;
    }
  }

  async function selectPage(page: AppPage) {
    if (page !== 'library') {
      appleDetailRequestId++;
      showAppleLibraryDetail = false;
      appleLibraryDetailLoading = false;
      showDetailMenu = false;
    }
    if (page !== 'downloaded') {
      downloadedDetailRequestId++;
      downloadedSelectedRelease = null;
      downloadedSelectedPath = '';
      downloadedSelectedReleaseLoading = false;
    }
    currentPage = page;
    rememberCurrentDestination();
    if (uiDemoMode) return;
    if (page === 'library' && config.apple_music_user_token && config.apple_authorization_token && !appleLibraryLoading) {
      if (!appleLibrary) void loadAppleMusicLibrary();
    } else if (page === 'downloaded') {
      void refreshDownloadedMusicLibrary();
    } else if (page === 'downloads') {
      await openHistory();
    } else if (page === 'devices') {
      await loadIPodDevices();
    }
  }

  interface LibraryReleaseSummary {
    kind: string;
    relative_path: string;
    title: string;
    artist?: string;
    year?: string;
    track_count: number;
    artwork_url?: string;
  }

  interface LibraryReleaseTrack {
    title: string;
    artist?: string;
    album?: string;
    file_name: string;
    file_path: string;
    disc_number?: number;
    track_number?: number;
    duration_seconds?: number;
    codec?: string;
    audio_url: string;
  }

  interface LibraryReleaseDetail extends LibraryReleaseSummary {
    tracks: LibraryReleaseTrack[];
  }

  let downloadedLibrary: { albums: LibraryReleaseSummary[]; playlists: LibraryReleaseSummary[]; error?: string } = {
    albums: [],
    playlists: []
  };
  let downloadedLibraryLoading = false;
  let downloadedLibraryError = '';
  let downloadedSelectedRelease: LibraryReleaseDetail | null = null;
  let downloadedSelectedReleaseLoading = false;
  let downloadedSelectedPath = '';
  let downloadedView: 'albums' | 'playlists' = 'albums';
  let downloadedDetailCache: Record<string, LibraryReleaseDetail> = Object.create(null);
  let downloadedDetailRequestId = 0;
  let downloadedLibraryRequestId = 0;
  let playerBarEl: PlayerBar;
  let currentPlayerTrack: PlayerTrack | null = null;

  // Apple Music library state
  interface AppleLibraryPlaylistItem {
    id: string;
    name: string;
    url: string;
    image_url: string | null;
    track_count: number;
    is_algorithmic: boolean;
  }
  interface AppleLibraryAlbumItem {
    id: string;
    name: string;
    url: string;
    image_url: string | null;
    track_count: number;
    artist_name: string;
    release_date?: string;
  }
  interface AppleLibraryData {
    saved_songs_count: number;
    albums: AppleLibraryAlbumItem[];
    playlists: AppleLibraryPlaylistItem[];
    from_cache?: boolean;
    indexed_at?: number;
    index_complete?: boolean;
    details?: Record<string, AppleLibraryDetail>;
    artists?: { name: string; image_url?: string; track_count: number }[];
    artist_details?: Record<string, AppleLibraryDetail>;
  }
  let appleLibrary: AppleLibraryData | null = null;
  let appleLibraryLoading = false;
  let appleLibraryError = '';
  let appleLibraryRequestId = 0;
  let appleIndexing = false;
  let appleIndexPercent = 0;
  let appleIndexLabel = '';
  let appleIndexStarted = false;
  let downloadedIndexing = false;
  let downloadedIndexPercent = 0;
  let downloadedIndexLabel = '';
  let libraryFilter = '';
  let librarySort: 'recent' | 'title' | 'artist' = 'recent';
  let librarySortDirection: 'ascending' | 'descending' = 'ascending';
  type LibraryView = 'recent' | 'albums' | 'playlists' | 'favourites' | 'artists';
  let libraryView: LibraryView = 'recent';
  let libraryKindFilter: 'all' | 'albums' | 'playlists' = 'all';
  let showSortMenu = false;
  let showFilterMenu = false;
  let showLibraryNavMenu = false;
  let showDetailMenu = false;
  let detailTrackMenuIndex: number | null = null;
  let libraryNavMenuX = 0;
  let libraryNavMenuY = 0;
  let selectedLibraryItems = new Set<string>();
  let libraryContextItem: AppleLibraryAlbumItem | AppleLibraryPlaylistItem | null = null;
  let libraryContextX = 0;
  let libraryContextY = 0;
  interface AppleLibraryTrackItem {
    title: string;
    artist: string;
    album: string;
    duration_ms?: number;
    artwork_url?: string;
    position: number;
  }

  interface IPodManagerHandle {
    reviewCompletedFiles(files: string[]): Promise<void>;
    showPlan(plan: IPodPlan): void;
  }
  let ipodDevices: IPodDevice[] = [];
  let ipodDevicesLoading = false;
  let ipodDevicesError = '';
  let ipodScanInitialized = false;
  let selectedIPodId = '';
  let selectedIPodSnapshot: IPodDevice | null = null;
  let latestIPodEvent: IPodEventPayload | null = null;
  let ipodManagerEl: IPodManagerHandle | null = null;
  $: connectedSelectedIPod = ipodDevices.find(device =>
    device.device_id === selectedIPodSnapshot?.device_id
      || (
        Boolean(selectedIPodSnapshot?.firewire_guid)
        && device.firewire_guid.toLowerCase() === selectedIPodSnapshot?.firewire_guid.toLowerCase()
      )
  ) || null;
  $: selectedIPodDevice = connectedSelectedIPod
    || (selectedIPodSnapshot?.device_id === selectedIPodId ? selectedIPodSnapshot : null);
  $: selectedIPodConnected = Boolean(connectedSelectedIPod);
  $: writableIPodDevices = ipodDevices.filter(device =>
    device.filesystem_accessible !== false && !device.browse_only
  );

  function requestDesktopNotification(kind: 'completion' | 'device', title: string, body: string) {
    const enabled = kind === 'completion'
      ? uiPreferences.completion_notifications
      : uiPreferences.device_notifications;
    if (!enabled) return;
    window.dispatchEvent(new CustomEvent('vela:desktop-notification-request', {
      detail: { kind, title, body },
    }));
  }

  function requestIPodConnectedNotification(device: IPodDevice) {
    if (device.filesystem_accessible === false) {
      requestDesktopNotification(
        'device',
        `${device.name || 'iPod'} detected`,
        'A Mac-formatted iPod was detected and remains read-only in Vela.',
      );
      return;
    }
    requestDesktopNotification('device', `${device.name || 'iPod'} connected`, 'The device is ready in Vela.');
  }

  async function loadIPodDevices() {
    if (uiDemoMode) {
      if (!ipodDevices.length) {
        ipodDevices = [{
          device_id: 'fixture-classic', path: 'E:\\', name: 'Max’s iPod', model_family: 'iPod Classic',
          generation: '6th Generation', model_number: 'MB145', capacity: '80GB', serial: 'FIXTURE',
          firewire_guid: '0011223344556677', firmware: '1.1.2', filesystem_type: 'FAT32',
          volume_identity_key: 'fixture-volume', disk_size_gb: 80, free_space_gb: 31.4,
          uses_sqlite_db: false, checksum_type: 2, audio_codecs: ['AAC', 'MP3', 'ALAC'],
          podcasts_supported: true, voice_memos_supported: true, supports_sparse_artwork: false,
          filesystem_accessible: true, raw_read_only: false, access_state: 'mounted',
          access_message: '', raw_device_path: '',
          browse_only: false, needs_preparation: false, write_block_reason: '',
        }];
        selectedIPodId = ipodDevices[0].device_id;
        selectedIPodSnapshot = ipodDevices[0];
      }
      return;
    }
    ipodDevicesLoading = true;
    ipodDevicesError = '';
    try {
      const raw = await ScanIPodDevices();
      const nextDevices = parseIPodDevicesResponse(raw);
      if (ipodScanInitialized) {
        const previousIds = new Set(ipodDevices.map(device => `${device.device_id}|${device.path}`));
        const nextIds = new Set(nextDevices.map(device => `${device.device_id}|${device.path}`));
        nextDevices.filter(device => !previousIds.has(`${device.device_id}|${device.path}`)).forEach(device => {
          requestIPodConnectedNotification(device);
        });
        ipodDevices.filter(device => !nextIds.has(`${device.device_id}|${device.path}`)).forEach(device => {
          requestDesktopNotification('device', `${device.name || 'iPod'} disconnected`, 'The device is no longer available.');
        });
      }
      ipodDevices = nextDevices;
      reconcileSelectedIPod(nextDevices);
      ipodScanInitialized = true;
    } catch (caught) {
      ipodDevicesError = caught instanceof Error ? caught.message : String(caught);
    } finally {
      ipodDevicesLoading = false;
    }
  }

  async function openIPodDevice(device: IPodDevice) {
    selectedIPodId = device.device_id;
    selectedIPodSnapshot = device;
    await selectPage('devices');
  }

  function reconcileSelectedIPod(devices: IPodDevice[]) {
    const snapshotFireWire = selectedIPodSnapshot?.firewire_guid.toLowerCase() || '';
    const selected = devices.find(device =>
      device.device_id === selectedIPodId
        || (Boolean(snapshotFireWire) && device.firewire_guid.toLowerCase() === snapshotFireWire)
    );
    if (!selected) return;
    selectedIPodId = selected.device_id;
    selectedIPodSnapshot = selected;
  }

  function removeEjectedIPod(event: CustomEvent<{ device_id: string; path: string }>) {
    const { device_id, path } = event.detail;
    ipodDevices = ipodDevices.filter(device => !(device.device_id === device_id && device.path === path));
    if (selectedIPodId === device_id) {
      selectedIPodId = '';
      selectedIPodSnapshot = null;
    }
    requestDesktopNotification('device', 'iPod safely ejected', 'It is now safe to disconnect the device.');
    showToast('The selected iPod was safely ejected.', 'success');
  }

  function handleIPodEvent(payload: IPodEventPayload) {
    latestIPodEvent = payload;
    const incoming = getIPodEventDevice(payload);
    if ((payload.type === 'ipod_connected' || payload.type === 'ipod_changed') && incoming) {
      const index = ipodDevices.findIndex(device => device.device_id === incoming.device_id && device.path === incoming.path);
      if (index >= 0) {
        ipodDevices[index] = incoming;
        ipodDevices = [...ipodDevices];
      } else {
        ipodDevices = [...ipodDevices, incoming];
        requestIPodConnectedNotification(incoming);
      }
      reconcileSelectedIPod(ipodDevices);
      ipodDevicesError = '';
    } else if (payload.type === 'ipod_disconnected' && incoming) {
      ipodDevices = ipodDevices.filter(device => !(device.device_id === incoming.device_id && device.path === incoming.path));
      requestDesktopNotification('device', `${incoming.name || 'iPod'} disconnected`, 'Reconnect it before continuing any review.');
    } else if (payload.type === 'ipod_watch_error') {
      ipodDevicesError = payload.message || 'Automatic iPod detection stopped.';
      showToast(ipodDevicesError, 'warning');
    }
  }

  async function openCompletedDownloadReview(job: DownloadJob) {
    const target = ipodDevices.find(device =>
      device.device_id === job.ipodDestinationId && device.path === job.ipodDestinationPath
    );
    if (!target) {
      showToast('The selected iPod disconnected before its completed download could be staged.', 'warning');
      return;
    }
    selectedIPodId = target.device_id;
    selectedIPodSnapshot = target;
    currentPage = 'devices';
    await loadIPodManager();
    await tick();
    if (!ipodManagerEl) {
      showToast('Open the selected iPod to review the completed download.', 'warning');
      return;
    }
    await ipodManagerEl.reviewCompletedFiles(job.completedFiles || []);
    showToast(
      job.completedFiles?.length
        ? 'The completed local files are ready for iPod plan review.'
        : 'No validated completed local files were eligible for iPod staging.',
      job.completedFiles?.length ? 'success' : 'warning',
    );
  }

  function collectValidatedCompletionFiles(files: unknown) {
    const activeJobIndex = downloadJobs.findIndex(job => job.status === 'downloading');
    if (activeJobIndex < 0) return;
    const activeJob = downloadJobs[activeJobIndex];
    if (!activeJob.ipodDestinationId || !activeJob.ipodDestinationPath) return;
    const completed = [...(activeJob.completedFiles || [])];
    eligibleCompletionFiles(files, activeJob.ipodDestinationPath).forEach(file => {
      if (!completed.includes(file)) completed.push(file);
    });
    downloadJobs[activeJobIndex] = { ...activeJob, completedFiles: completed };
    downloadJobs = [...downloadJobs];
    persistDownloadQueue();
  }

  function eligibleCompletionFiles(files: unknown, ipodMountPath: string): string[] {
    if (!Array.isArray(files)) return [];
    const mountRoot = ipodMountPath.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
    const eligible: string[] = [];
    files.forEach(file => {
      if (typeof file !== 'string' || !file.trim()) return;
      const normalized = file.replace(/\\/g, '/').toLowerCase();
      if (mountRoot && (normalized === mountRoot || normalized.startsWith(`${mountRoot}/`))) return;
      if (/\.(?:part|tmp|download)$/i.test(normalized)) return;
      if (!eligible.includes(file)) eligible.push(file);
    });
    return eligible;
  }

  interface AppleLibraryDetail {
    name: string;
    url?: string;
    image_url?: string;
    content_type?: string;
    track_count: number;
    tracks: AppleLibraryTrackItem[];
  }
  let showAppleLibraryDetail = false;
  let appleLibraryDetailLoading = false;
  let appleLibraryDetailError = '';
  let appleLibraryDetail: AppleLibraryDetail | null = null;
  let libraryDetailFilter = '';
  let libraryDetailSort: 'position' | 'title' | 'artist' | 'album' = 'position';
  let libraryDetailDescending = false;
  let appleDetailCache: Record<string, AppleLibraryDetail> = Object.create(null);
  let appleDetailRequestId = 0;

  function closeAppleLibraryDetail() {
    if (libraryView === 'favourites') return;
    appleDetailRequestId++;
    showAppleLibraryDetail = false;
    appleLibraryDetailLoading = false;
    appleLibraryDetailError = '';
    showDetailMenu = false;
    detailTrackMenuIndex = null;
  }

  function openLibraryView(view: LibraryView) {
    appleDetailRequestId++;
    libraryView = view;
    showAppleLibraryDetail = false;
    appleLibraryDetailLoading = false;
    appleLibraryDetail = null;
    showDetailMenu = false;
    void selectPage('library');
  }

  async function openAppleLibraryDetail(url: string, fallbackName: string, fallbackImage = '') {
    const requestId = ++appleDetailRequestId;
    showAppleLibraryDetail = true;
    appleLibraryDetailError = '';
    const cached = appleDetailCache[url];
    appleLibraryDetail = cached || { name: fallbackName, url, image_url: fallbackImage, track_count: 0, tracks: [] };
    appleLibraryDetailLoading = !cached;
    if (cached) return;
    if (uiDemoMode) {
      appleLibraryDetail = { name: fallbackName, url, image_url: fallbackImage, content_type: url.includes('/album/') ? 'album' : 'playlist', track_count: 3, tracks: [
        { title: 'Open Skies', artist: 'Nova Lane', album: fallbackName, duration_ms: 218000, artwork_url: fallbackImage, position: 1 },
        { title: 'Afterglow', artist: 'Nova Lane', album: fallbackName, duration_ms: 247000, artwork_url: fallbackImage, position: 2 },
        { title: 'Slow Motion', artist: 'Nova Lane', album: fallbackName, duration_ms: 203000, artwork_url: fallbackImage, position: 3 },
      ] };
      appleLibraryDetailLoading = false;
      appleDetailCache[url] = appleLibraryDetail;
      return;
    }
    try {
      const raw = await GetAppleMusicPlaylistDetail(url);
      const data = typeof raw === 'string' ? JSON.parse(raw) : raw;
      if (requestId !== appleDetailRequestId || currentPage !== 'library' || !showAppleLibraryDetail) return;
      if (data?.error) appleLibraryDetailError = data.error;
      else {
        appleLibraryDetail = { ...data, url: data.url || url } as AppleLibraryDetail;
        appleDetailCache[url] = appleLibraryDetail;
      }
    } catch (e: any) {
      if (requestId !== appleDetailRequestId) return;
      appleLibraryDetailError = e?.message || String(e);
    } finally {
      if (requestId === appleDetailRequestId) appleLibraryDetailLoading = false;
    }
  }

  async function openAppleArtistDetail(name: string, artwork = '') {
    const requestId = ++appleDetailRequestId;
    showAppleLibraryDetail = true;
    appleLibraryDetailError = '';
    const key = `artist:${name.toLocaleLowerCase()}`;
    const cached = appleDetailCache[key];
    appleLibraryDetail = cached || { name, image_url: artwork, content_type: 'artist', track_count: 0, tracks: [] };
    appleLibraryDetailLoading = !cached;
    if (cached) return;
    try {
      const detail = JSON.parse(await GetAppleMusicArtistDetail(name) || '{}');
      if (requestId !== appleDetailRequestId || currentPage !== 'library' || !showAppleLibraryDetail) return;
      if (detail.error) throw new Error(detail.error);
      appleLibraryDetail = { ...detail, image_url: artwork || detail.image_url || '' };
      appleDetailCache[key] = appleLibraryDetail;
    } catch (e: any) {
      if (requestId !== appleDetailRequestId) return;
      appleLibraryDetailError = e?.message || String(e);
    } finally {
      if (requestId === appleDetailRequestId) appleLibraryDetailLoading = false;
    }
  }

  function downloadOpenApplePlaylist() {
    if (!appleLibraryDetail?.url) return;
    const url = appleLibraryDetail.url;
    showAppleLibraryDetail = false;
    downloadPlaylistUrl(url);
  }

  function favouriteSongsPlaylist(): AppleLibraryPlaylistItem | null {
    return (appleLibrary?.playlists || []).find(item => /^(favourite|favorite) songs$/i.test(item.name.trim())) || null;
  }

  function openFavourites() {
    libraryView = 'favourites';
    currentPage = 'library';
    showAppleLibraryDetail = false;
    appleLibraryDetail = null;
    const playlist = favouriteSongsPlaylist();
    if (playlist) openAppleLibraryDetail(playlist.url, playlist.name, playlist.image_url || '');
  }

  function startAppleIndexOnce() {
    if (uiDemoMode || appleIndexStarted || !config.apple_music_user_token || !config.apple_authorization_token) return;
    appleIndexStarted = true;
    appleIndexing = true;
    appleIndexPercent = 0;
    appleIndexLabel = 'Reading local library index';
    StartAppleMusicIndex().catch(() => {
      appleIndexing = false;
      appleIndexStarted = false;
    });
  }

  function ingestAppleLibrarySnapshot(data: AppleLibraryData) {
    for (const [url, detail] of Object.entries(data.details || {})) {
      appleDetailCache[url] = detail;
    }
    for (const [key, detail] of Object.entries(data.artist_details || {})) {
      appleDetailCache[`artist:${key}`] = detail;
    }
  }

  async function loadAppleMusicLibrary(forceRefresh = false, startIndexer = true) {
    if (!config.apple_music_user_token || !config.apple_authorization_token) return;
    const requestId = ++appleLibraryRequestId;
    appleLibraryLoading = !appleLibrary;
    appleLibraryError = '';
    try {
      const raw = forceRefresh ? await RefreshAppleMusicLibrary() : await GetAppleMusicLibrary();
      const data = measureFrontendPerformance('apple-payload-parse', () =>
        typeof raw === 'string' ? JSON.parse(raw) : raw
      );
      if (requestId !== appleLibraryRequestId || componentDestroyed) return;
      if (data.error) {
        appleLibraryError = data.error;
      } else {
        measureFrontendPerformance('apple-payload-apply', () => {
          appleLibrary = data as AppleLibraryData;
          ingestAppleLibrarySnapshot(appleLibrary);
        });
        if (startIndexer) scheduleTimeout(startAppleIndexOnce, 500);
        if (libraryView === 'favourites' && !showAppleLibraryDetail) scheduleTimeout(openFavourites);
      }
    } catch (e: any) {
      if (requestId !== appleLibraryRequestId || componentDestroyed) return;
      appleLibraryError = e?.message || String(e);
    } finally {
      if (requestId === appleLibraryRequestId && !componentDestroyed) appleLibraryLoading = false;
    }
  }

  function toggleLibrarySelection(url: string) {
    selectedLibraryItems = selectedLibraryItems.has(url)
      ? new Set([...selectedLibraryItems].filter(item => item !== url))
      : new Set([...selectedLibraryItems, url]);
  }

  function computeLibraryAlbums(source: AppleLibraryAlbumItem[], queryText: string, sortBy: typeof librarySort, direction: typeof librarySortDirection): AppleLibraryAlbumItem[] {
    const query = queryText.trim().toLocaleLowerCase();
    const albums = [...source].filter(album =>
      !query || `${album.name} ${album.artist_name}`.toLocaleLowerCase().includes(query)
    );
    if (sortBy === 'title') albums.sort((a, b) => a.name.localeCompare(b.name));
    if (sortBy === 'artist') albums.sort((a, b) => a.artist_name.localeCompare(b.artist_name));
    if (direction === 'descending') albums.reverse();
    return albums;
  }

  function computeLibraryPlaylists(source: AppleLibraryPlaylistItem[], queryText: string, sortBy: typeof librarySort, direction: typeof librarySortDirection): AppleLibraryPlaylistItem[] {
    const query = queryText.trim().toLocaleLowerCase();
    const playlists = [...source].filter(item => !query || item.name.toLocaleLowerCase().includes(query));
    if (sortBy === 'title' || sortBy === 'artist') playlists.sort((a, b) => a.name.localeCompare(b.name));
    if (direction === 'descending') playlists.reverse();
    return playlists;
  }

  $: visibleLibraryAlbums = computeLibraryAlbums(appleLibrary?.albums || [], libraryFilter, librarySort, librarySortDirection);
  $: visibleLibraryPlaylists = computeLibraryPlaylists(appleLibrary?.playlists || [], libraryFilter, librarySort, librarySortDirection);
  $: visibleLibraryDetailTracks = (() => {
    const tracks = [...(appleLibraryDetail?.tracks || [])]
      .filter(track => !libraryDetailFilter.trim() || `${track.title} ${track.artist} ${track.album}`.toLocaleLowerCase().includes(libraryDetailFilter.trim().toLocaleLowerCase()))
      .sort((a, b) => {
      if (libraryDetailSort === 'title') return a.title.localeCompare(b.title);
      if (libraryDetailSort === 'artist') return a.artist.localeCompare(b.artist);
      if (libraryDetailSort === 'album') return a.album.localeCompare(b.album);
      return (a.position || 0) - (b.position || 0);
      });
    if (libraryDetailDescending) tracks.reverse();
    return tracks;
  })();

  function computeLibraryArtists(
    library: AppleLibraryData | null,
    queryText: string,
    direction: typeof librarySortDirection,
  ): { name: string; albums: AppleLibraryAlbumItem[]; image?: string | null; trackCount: number }[] {
    const query = queryText.trim().toLocaleLowerCase();
    const artists = library?.artists?.length
      ? library.artists.map(artist => ({ name: artist.name, albums: [], image: artist.image_url || null, trackCount: artist.track_count }))
      : (() => {
          const groups: Record<string, AppleLibraryAlbumItem[]> = Object.create(null);
          for (const album of library?.albums || []) {
            const name = album.artist_name || 'Unknown Artist';
            const albums = groups[name];
            if (albums) albums.push(album);
            else groups[name] = [album];
          }
          return Object.entries(groups).map(([name, albums]) => ({
            name,
            albums,
            image: albums.find(album => album.image_url)?.image_url,
            trackCount: albums.reduce((sum, album) => sum + (album.track_count || 0), 0),
          }));
        })();
    const filtered = artists
      .filter(artist => !query || artist.name.toLocaleLowerCase().includes(query))
      .sort((a, b) => a.name.localeCompare(b.name));
    if (direction === 'descending') filtered.reverse();
    return filtered;
  }
  $: visibleLibraryArtists = computeLibraryArtists(appleLibrary, libraryFilter, librarySortDirection);

  function appleAlbumKey(album: AppleLibraryAlbumItem): string {
    return album.id || album.url;
  }

  function applePlaylistKey(playlist: AppleLibraryPlaylistItem): string {
    return playlist.id || playlist.url;
  }

  function appleArtistKey(artist: { name: string }): string {
    return artist.name;
  }

  function appleTrackKey(track: AppleLibraryTrackItem, index: number): string {
    return `${track.position || index + 1}|${track.title}|${track.artist}|${track.album}`;
  }

  function openLibraryItemMenu(event: MouseEvent, item: AppleLibraryAlbumItem | AppleLibraryPlaylistItem) {
    event.preventDefault();
    libraryContextItem = item;
    libraryContextX = Math.min(event.clientX, window.innerWidth - 220);
    libraryContextY = Math.min(event.clientY, window.innerHeight - 150);
  }

  function openLibraryNavMenu(event: MouseEvent) {
    event.preventDefault();
    showLibraryNavMenu = true;
    libraryNavMenuX = Math.min(event.clientX, window.innerWidth - 210);
    libraryNavMenuY = Math.min(event.clientY, window.innerHeight - 100);
  }

  function dismissOpenMenus(event: PointerEvent) {
    const target = event.target as HTMLElement | null;
    if (target?.closest('.context-menu, .tool-menu, .job-options')) return;
    showSortMenu = false;
    showFilterMenu = false;
    showDetailMenu = false;
    libraryContextItem = null;
    showLibraryNavMenu = false;
  }

  function dismissMenusOnPointerDown(node: HTMLElement) {
    node.addEventListener('pointerdown', dismissOpenMenus);
    return {
      destroy() {
        node.removeEventListener('pointerdown', dismissOpenMenus);
      },
    };
  }

  function focusDialog(node: HTMLElement) {
    const previousFocus = document.activeElement;
    let frame = window.requestAnimationFrame(() => {
      frame = 0;
      node.focus();
    });
    return {
      destroy() {
        if (frame) window.cancelAnimationFrame(frame);
        frame = 0;
        if (previousFocus instanceof HTMLElement && previousFocus.isConnected) previousFocus.focus();
      },
    };
  }

  async function resetAppleIndex() {
    if (!await requestConfirmation('Reset library index?', 'Vela will rebuild all cached Apple Music albums, playlists, artists, and songs. Your connection and downloaded files will not be removed.', 'Reset index')) return;
    try {
      await ResetAppleMusicIndex();
      appleDetailCache = Object.create(null);
      appleLibrary = null;
      appleLibraryDetail = null;
      showAppleLibraryDetail = false;
      appleIndexStarted = false;
      appleIndexing = false;
      appleIndexPercent = 0;
      appleIndexLabel = '';
      await loadAppleMusicLibrary(true);
    } catch (e: any) {
      appleLibraryError = e?.message || String(e);
      showToast(`Could not reset the library index: ${appleLibraryError}`);
    }
  }

  function downloadSelectedLibraryItems() {
    const urls = [...selectedLibraryItems];
    if (!urls.length) return;
    inputUrl = urls.join('\n');
    selectedLibraryItems = new Set();
    if (uiPreferences.open_downloads_on_add) currentPage = 'downloads';
    startDownload();
  }

  async function chooseCustomDestination() {
    const dir = await PickDirectory();
    if (dir) customDestination = dir;
  }

  async function startCustomDownload() {
    if (!inputUrl.trim()) return;
    if (customDestination && customDestination !== config.download_path) {
      config.download_path = customDestination;
      await saveConfigSerialized();
    }
    pendingDownloadIPodDestinationId = customIPodDestinationId;
    customIPodDestinationId = '';
    showCustomDownload = false;
    await startDownload();
  }

  // Trigger a download by pasting a URL and starting immediately
  function downloadPlaylistUrl(url: string) {
    inputUrl = url;
    activeTab = 'url';
    if (uiPreferences.open_downloads_on_add) currentPage = 'downloads';
    startDownload();
  }

  // ── Auto-sync ───────────────────────────────────────────────────────────────
  let autoSyncRunning = false;
  let autoSyncLastResult = '';

  async function runAutoSyncNow() {
    autoSyncRunning = true;
    autoSyncLastResult = '';
    try {
      const result = await RunAutoSync();
      autoSyncLastResult = result || 'Auto-sync complete.';
    } catch (e: any) {
      autoSyncLastResult = `Error: ${e?.message || e}`;
    } finally {
      autoSyncRunning = false;
    }
  }

  function togglePlaylistSync(pl: { url: string; name: string; artwork_url?: string; is_algorithmic?: boolean }) {
    const list = [...((config.tracked_playlists || []) as any[])];
    const idx = list.findIndex((p: any) => p.url === pl.url);
    if (idx >= 0) {
      list[idx] = { ...list[idx], sync_enabled: !list[idx].sync_enabled };
    } else {
      list.push({
        url: pl.url,
        name: pl.name,
        artwork_url: pl.artwork_url || '',
        is_algorithmic: pl.is_algorithmic || false,
        sync_enabled: true,
        last_track_ids: [],
        last_sync_ts: 0,
      });
    }
    config.tracked_playlists = list;
    void saveConfigSerialized();
  }

  function isPlaylistSyncing(url: string): boolean {
    const entry = ((config.tracked_playlists || []) as any[]).find((p: any) => p.url === url);
    if (!entry) return false;
    return entry.sync_enabled !== false;
  }

  function getTrackedEntry(url: string): any {
    return ((config.tracked_playlists || []) as any[]).find((p: any) => p.url === url);
  }

  // ── Source health check ─────────────────────────────────────────────────────
  interface EndpointStatus { url: string; alive: boolean; latency_ms: number; }
  interface SourceHealth { source: string; total: number; live: number; endpoints: EndpointStatus[]; }
  let healthCache: Record<string, SourceHealth> = {};
  let healthPopoverSource = '';
  let healthLoading = false;
  let showHealthPopover = false;

  // Gist-sourced source status — fetched once on startup from the public status Gist.
  // Default: all true (green) so chips don't flash red before the fetch completes.
  let gistStatus: Record<string, boolean> = { hifi: true, amazon: true, qobuz: true, apple: true, deezer: true };

  async function fetchGistStatus() {
    try {
      const res = await fetch(
        'https://gist.githubusercontent.com/anandprtp/fdc2c16b7bfdc2d337fbc86161b79371/raw/status.json',
        { cache: 'no-store' }
      );
      if (res.ok) {
        const data = await res.json();
        gistStatus = {
          hifi:   !!(data['hifi']   ?? data['tidal'] ?? true),
          amazon: !!(data['amazon'] ?? true),
          qobuz:  !!(data['qobuz']  ?? true),
          apple:  !!(data['apple']  ?? true),
          deezer: !!(data['deezer'] ?? true),
        };
      }
    } catch {
      // Fetch failed — keep defaults (all true). Downloads still work; status is unknown.
    }
  }

  const healthSources = [
    { key: 'hifi',   label: 'Tidal',   abbr: 'T', bg: '#1a1a2e', bgEnabled: 'rgba(29,185,222,0.14)',  border: '#1DB9DE', text: '#1DB9DE' },
    { key: 'apple',  label: 'Apple',   abbr: '',  bg: '#230a10', bgEnabled: 'rgba(252,60,68,0.14)',   border: '#fc3c44', text: '#fc3c44' },
    { key: 'amazon', label: 'Amazon',  abbr: 'a', bg: '#1a1200', bgEnabled: 'rgba(255,153,0,0.14)',   border: '#FF9900', text: '#FF9900' },
    { key: 'qobuz',  label: 'Qobuz',   abbr: 'Q', bg: '#0d0d1f', bgEnabled: 'rgba(123,94,167,0.18)',  border: '#7B5EA7', text: '#7B5EA7' },
    { key: 'deezer', label: 'Deezer',  abbr: 'D', bg: '#001219', bgEnabled: 'rgba(0,196,80,0.14)',    border: '#00C450', text: '#00C450' },
  ];
  const downloadSourceOptions = [
    { value: 'auto',    label: 'Auto',        icon: null },
    { value: 'tidal',   label: 'Tidal',       icon: '/icons/tidal.webp' },
    { value: 'qobuz',   label: 'Qobuz',       icon: '/icons/qobuz.png' },
    { value: 'apple',   label: 'Apple Music', icon: '/icons/apple-music.png' },
    { value: 'amazon',  label: 'Amazon',      icon: '/icons/amazon-music.jpg' },
    { value: 'deezer',  label: 'Deezer',      icon: '/icons/deezer.webp' },
  ];
  const concreteDownloadSources = downloadSourceOptions.filter(src => src.value !== 'auto').map(src => src.value);
  let selectedDownloadSources: string[] = ['auto'];

  function normalizeDownloadSources(): string[] {
    const raw = config.download_sources && config.download_sources.length
      ? config.download_sources
      : [config.download_source || 'auto'];
    const cleaned = Array.from(new Set(raw.filter(Boolean)));
    if (!cleaned.length || cleaned.includes('auto')) return ['auto'];
    const known = cleaned.filter(src => concreteDownloadSources.includes(src));
    return known.length ? known : ['auto'];
  }

  function setDownloadSources(sources: string[]) {
    selectedDownloadSources = sources;
    config = {
      ...config,
      download_sources: sources,
      download_source: sources.length === 1 ? sources[0] : 'custom',
    } as main.Config;
  }

  function toggleDownloadSource(value: string) {
    if (value === 'auto') {
      setDownloadSources(['auto']);
      autoSaveSettings();
      return;
    }
    let selected = selectedDownloadSources.filter(src => src !== 'auto');
    if (selected.includes(value)) {
      selected = selected.filter(src => src !== value);
    } else {
      selected = [...selected, value];
    }
    setDownloadSources(selected.length ? selected : ['auto']);
    autoSaveSettings();
  }
  const formatOptions = [
    { value: 'auto',     name: 'Auto', label: 'Best available — lossless preferred, MP3 fallback' },
    { value: 'lossless', name: 'FLAC', label: 'FLAC lossless — highest quality from any source' },
    { value: 'alac',     name: 'ALAC', label: 'Apple Lossless .m4a — iPhone / Apple Music compatible' },
    { value: 'aac',      name: 'AAC',  label: '~320kbps AAC — uses JioSaavn directly' },
    { value: 'mp3',      name: 'MP3',  label: '~320kbps MP3 — uses JioSaavn / NetEase directly' },
  ];

  // Derive parent format and bit-depth from the stored output_format value
  // e.g. 'lossless-16' → parent='lossless', bitDepth='16'
  $: _fmtBase       = (config.output_format || 'auto').replace(/-16$|-24$/, '');
  $: _fmtBitDepth   = config.output_format?.endsWith('-16') ? '16' : config.output_format?.endsWith('-24') ? '24' : '';
  $: showBitDepthRow = _fmtBase === 'lossless' || _fmtBase === 'alac';

  async function setParentFormat(val: string) {
    // Preserve bit-depth selection when switching between FLAC and ALAC
    if ((val === 'lossless' || val === 'alac') && _fmtBitDepth) {
      config.output_format = val + '-' + _fmtBitDepth;
    } else {
      config.output_format = val;
    }
    await saveConfigSerialized();
  }

  async function setBitDepth(depth: string) {
    config.output_format = _fmtBase + '-' + depth;
    await saveConfigSerialized();
  }

  async function checkHealth(src: string, opts: { openPopover?: boolean } = {}) {
    const { openPopover = true } = opts;
    healthPopoverSource = src;
    healthLoading = true;
    if (openPopover) {
      showHealthPopover = true;
    }
    try {
      const raw = await CheckSourceHealth(src);
      healthCache[src] = JSON.parse(raw);
      healthCache = { ...healthCache };
    } catch (e) { console.error(e); }
    finally { healthLoading = false; }
  }

  // Chip liveness: green when endpoint health cache shows at least one live endpoint.
  $: chipLive = Object.fromEntries(
    healthSources.map(s => [s.key, !!(healthCache[s.key] && healthCache[s.key].live > 0)])
  );

  // Chip enabled: sourced from the public Gist status (fetched on startup).
  // True = source is online per the Gist; false = source is down or status unknown.
  $: chipEnabled = Object.fromEntries([
    ['hifi',   gistStatus['hifi']],
    ['apple',  gistStatus['apple']],
    ['amazon', gistStatus['amazon']],
    ['qobuz',  gistStatus['qobuz']],
    ['deezer', gistStatus['deezer']],
  ] as [string, boolean][]);

  async function openSettingsAt(sectionId: string) {
    await openSettings(sectionId);
  }

  function handleChipClick(src: string) {
    checkHealth(src);
  }

  // ── Tracklist scroll state ─────────────────────────────────────────────────
  let tracklistEl: any;
  let tracklistAtBottom = true;
  let tracklistHasScrolled = false;

  function updateTracklistScroll(event: CustomEvent<{ atBottom: boolean }>) {
    tracklistAtBottom = event.detail.atBottom;
    tracklistHasScrolled = true;
  }

  function scrollTracklistToBottom() {
    tracklistEl?.scrollToEnd('auto');
    tracklistAtBottom = true;
  }

  async function autoScrollTracklist(trackKey = '') {
    await tick();
    if (!tracklistEl) return;
    if (trackKey) {
      tracklistEl.scrollToKey(trackKey, motionReduced ? 'auto' : 'smooth');
      return;
    }
    tracklistEl.scrollToEnd(motionReduced ? 'auto' : 'smooth');
  }

  async function pasteClipboardIntoUrlBox(event: MouseEvent) {
    if (isDownloading || !inputUrlEl) return;

    event.preventDefault();

    try {
      const clipboardText = await ClipboardGetText();
      if (!clipboardText) return;

      const start = inputUrlEl.selectionStart ?? inputUrl.length;
      const end = inputUrlEl.selectionEnd ?? inputUrl.length;
      inputUrl = inputUrl.slice(0, start) + clipboardText + inputUrl.slice(end);

      await tick();
      const caret = start + clipboardText.length;
      inputUrlEl.focus();
      inputUrlEl.setSelectionRange(caret, caret);
    } catch (error) {
      console.error('Right-click paste failed:', error);
    }
  }

  interface LogEntry { id: number; type: string; text: string; isRawHtml?: boolean; }
  let logs: LogEntry[] = [];
  const logBuffer = new BoundedDeque<LogEntry>(500);
  let logCommitScheduled = false;
  let logId = 0;
  let trackLabels: Record<string, string> = {};
  let playlistTitle = '';
  let playlistArtwork = '';
  let playlistArtists = '';
  let playlistReleaseDate = '';
  let playlistContentType = '';
  let playlistQualityBadge = '';
  let playlistTotalDurationMs = 0;
  let playlistTotalTracks = 0;

  const trackActivity = new TrackActivityModel();
  let activeTracks: Record<string, ActiveTrackState> = {};
  let queueTrackKeys: string[] = [];
  let failedTrackKeys: string[] = [];
  let trackActivityCounts: TrackActivityCounts = {
    finished: 0,
    resolving: 0,
    transferring: 0,
    processing: 0,
    retryWait: 0,
  };
  let trackActivityCommitFrame: number | null = null;
  let committedTrackStructureVersion = -1;
  let committedFailedTrackVersion = -1;
  let committedTrackCountsVersion = -1;
  let currentPlaylistTrackKeysByIndex: Record<number, string> = {};
  let trackKeysByStableId: Record<string, string> = {};
  let currentPlaylistTrackCount = 0;
  let currentPlaylistKeyPrefix = 'track:pending';
  let playlistKeySequence = 0;
  let trackIngestGeneration = 0;
  let trackIngestFrame: number | null = null;
  const pendingTrackBatches: Array<{
    tracks: any[];
    startIndex: number;
    cursor: number;
    generation: number;
  }> = [];
  let workerActive = 0;
  let workerConfigured = 2;
  let workerCeiling = 8;
  let retryClock = Date.now();
  $: queueFinishedCount = trackActivityCounts.finished;
  $: queueOverallProgress = queueTrackKeys.length ? Math.round((queueFinishedCount / queueTrackKeys.length) * 100) : 0;
  $: resolvingTrackCount = trackActivityCounts.resolving;
  $: transferringTrackCount = trackActivityCounts.transferring;
  $: processingTrackCount = trackActivityCounts.processing;
  $: retryWaitTrackCount = trackActivityCounts.retryWait;

  $: failedEntries = failedTrackKeys
    .map(k => ({
      key: k,
      label: trackLabels[k] || k,
      error: activeTracks[k]?.error || activeTracks[k]?.text || 'Failed',
    }));

  function commitTrackActivity() {
    const snapshot = trackActivity.snapshot();
    activeTracks = snapshot.states;
    if (snapshot.structureVersion !== committedTrackStructureVersion) {
      committedTrackStructureVersion = snapshot.structureVersion;
      queueTrackKeys = snapshot.keys;
    }
    if (snapshot.failedVersion !== committedFailedTrackVersion) {
      committedFailedTrackVersion = snapshot.failedVersion;
      failedTrackKeys = snapshot.failedKeys;
    }
    if (snapshot.countsVersion !== committedTrackCountsVersion) {
      committedTrackCountsVersion = snapshot.countsVersion;
      trackActivityCounts = snapshot.counts;
    }
  }

  function scheduleTrackActivityCommit() {
    if (trackActivityCommitFrame !== null) return;
    trackActivityCommitFrame = scheduleAnimationFrame(() => {
      trackActivityCommitFrame = null;
      commitTrackActivity();
    });
  }

  function resetTrackActivity(
    keys: readonly string[] = [],
    states: Record<string, ActiveTrackState> = {},
  ) {
    trackIngestGeneration += 1;
    pendingTrackBatches.length = 0;
    cancelScheduledAnimationFrame(trackIngestFrame);
    trackIngestFrame = null;
    cancelScheduledAnimationFrame(trackActivityCommitFrame);
    trackActivityCommitFrame = null;
    trackActivity.reset(keys, states);
    commitTrackActivity();
  }

  function currentTrackKey(index: number): string {
    return `${currentPlaylistKeyPrefix}:${index}`;
  }

  function enqueueTrackBatch(tracks: any[], startIndex: number) {
    if (!tracks.length) return;
    pendingTrackBatches.push({
      tracks,
      startIndex,
      cursor: 0,
      generation: trackIngestGeneration,
    });
    scheduleTrackIngest();
  }

  function scheduleTrackIngest() {
    if (trackIngestFrame !== null || !pendingTrackBatches.length) return;
    trackIngestFrame = scheduleAnimationFrame(() => {
      trackIngestFrame = null;
      processTrackIngestFrame();
    });
  }

  function processTrackIngestFrame() {
    const frameBudget = 300;
    let processed = 0;
    let durationAdded = 0;
    let labelsChanged = false;

    while (pendingTrackBatches.length && processed < frameBudget) {
      const batch = pendingTrackBatches[0];
      if (batch.generation !== trackIngestGeneration) {
        pendingTrackBatches.shift();
        continue;
      }
      while (batch.cursor < batch.tracks.length && processed < frameBudget) {
        const track = batch.tracks[batch.cursor];
        const absoluteIndex = batch.startIndex + batch.cursor;
        const rowKey = currentTrackKey(absoluteIndex);
        currentPlaylistTrackKeysByIndex[absoluteIndex] = rowKey;
        const label = makeTrackDisplayName(track?.artist, track?.title);
        if (trackLabels[rowKey] !== label) {
          trackLabels[rowKey] = label;
          labelsChanged = true;
        }
        trackActivity.add(rowKey);
        durationAdded += Number(track?.duration_ms || 0);
        batch.cursor += 1;
        processed += 1;
      }
      if (batch.cursor >= batch.tracks.length) pendingTrackBatches.shift();
    }

    if (durationAdded) playlistTotalDurationMs += durationAdded;
    if (labelsChanged) trackLabels = invalidateTrackLabels(trackLabels);
    if (processed) scheduleTrackActivityCommit();
    if (pendingTrackBatches.length) scheduleTrackIngest();
  }

  function invalidateTrackLabels(labels: Record<string, string>): Record<string, string> {
    // The assignment at the call site invalidates this object in legacy Svelte.
    // Returning the same reference keeps each 300-track frame bounded.
    return labels;
  }

  function makeTrackDisplayName(artist?: string | null, title?: string | null) {
    const artistPart = String(artist || '').trim();
    const titlePart = String(title || '').trim();
    if (artistPart && titlePart) return `${artistPart} - ${titlePart}`;
    return titlePart || artistPart || 'Unknown Track';
  }

  function normalizeTrackKeyPart(value: string) {
    return String(value || '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, ' ')
      .trim();
  }

  function fallbackTrackKey(artist?: string | null, title?: string | null, trackData?: any) {
    const artistPart = normalizeTrackKeyPart(artist || trackData?.artist_string || (Array.isArray(trackData?.artists) ? trackData.artists.join(' ') : ''));
    const titlePart = normalizeTrackKeyPart(title || trackData?.title || '');
    const durationPart = trackData?.duration_ms ? String(trackData.duration_ms) : '';
    return `fallback::${artistPart}::${titlePart}::${durationPart}`;
  }

  function resolveTrackEventKey(data: any) {
    const stableId = data?.job_id && data?.track_id ? `${data.job_id}:${data.track_id}` : '';
    if (stableId && trackKeysByStableId[stableId]) return trackKeysByStableId[stableId];

    const idx = Number(data?.track_index || 0);
    if (idx > 0) {
      const indexedKey = currentPlaylistTrackKeysByIndex[idx] || currentTrackKey(idx);
      currentPlaylistTrackKeysByIndex[idx] = indexedKey;
      if (stableId) {
        trackKeysByStableId[stableId] = indexedKey;
      }
      return indexedKey;
    }

    if (stableId) {
      const stableKey = `track:${stableId}`;
      trackKeysByStableId[stableId] = stableKey;
      return stableKey;
    }

    const td = data?.track_data || {};
    if (td.spotify_id) return `spotify:${td.spotify_id}`;
    if (td.apple_music_id) return `apple:${td.apple_music_id}`;
    if (td.deezer_track_id) return `deezer:${td.deezer_track_id}`;
    if (td.tidal_track_id) return `tidal:${td.tidal_track_id}`;
    if (td.isrc && td.album_id && td.track_number) return `albumtrack:${td.album_id}:${td.disc_number || 1}:${td.track_number}:${td.isrc}`;
    return fallbackTrackKey(data?.artist, data?.track, td);
  }

  function updateActiveTrack(
    trackName: string,
    patch: Partial<ActiveTrackState>,
    options: { allowTerminalReset?: boolean } = {},
  ) {
    trackActivity.patch(trackName, patch, options);
    scheduleTrackActivityCommit();
  }

  function formatBytes(bytes?: number): string {
    if (!Number.isFinite(bytes) || Number(bytes) < 0) return '';
    const value = Number(bytes);
    if (value < 1024) return `${Math.round(value)} B`;
    if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
    if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
    return `${(value / 1024 ** 3).toFixed(1)} GB`;
  }

  function transferDetail(state?: ActiveTrackState): string {
    if (!state) return '';
    const transferred = formatBytes(state.bytesDownloaded);
    const total = formatBytes(state.bytesTotal);
    const speed = formatBytes(state.speedBps);
    return [
      transferred && total ? `${transferred} of ${total}` : transferred,
      speed ? `${speed}/s` : '',
    ].filter(Boolean).join(' · ');
  }

  function retryStatus(state?: ActiveTrackState): string {
    if (!state) return '';
    const remaining = state.retryAt ? Math.max(0, Math.ceil((state.retryAt - retryClock) / 1000)) : 0;
    const countdown = remaining > 0 ? `Retrying automatically in ${remaining}s` : 'Retrying automatically now';
    return state.error ? `${countdown} · ${state.error}` : countdown;
  }

  interface PerformancePlaylistTrack {
    title?: string;
    artist?: string;
    duration_ms?: number;
    [key: string]: unknown;
  }

  interface PerformanceHarnessSnapshot {
    albumCount: number;
    queueTrackCount: number;
    pendingIngestBatchCount: number;
    ingestAnimationFramePending: boolean;
    currentPage: AppPage;
    libraryView: LibraryView;
  }

  interface VelaPerformanceHarness {
    injectAppleLibrary(library: AppleLibraryData): void;
    loadPlaylist(tracks: PerformancePlaylistTrack[], jobId?: string): void;
    showPage(page: AppPage, view?: LibraryView): void;
    snapshot(): PerformanceHarnessSnapshot;
  }

  type PerformanceHarnessWindow = Window & {
    __VELA_PERF__?: VelaPerformanceHarness;
  };

  onMount(() => {
    componentDestroyed = false;
    markFrontendPerformance('mount');
    const cleanups: Array<() => void> = [];
    const registerCleanup = (cleanup: () => void) => {
      if (componentDestroyed) {
        cleanup();
        return;
      }
      cleanups.push(cleanup);
    };
    if (import.meta.env.DEV && isLocalPerformanceEnabled()) {
      const performanceWindow = window as PerformanceHarnessWindow;
      const performanceHarness: VelaPerformanceHarness = {
        injectAppleLibrary(library) {
          appleLibrary = library;
          ingestAppleLibrarySnapshot(library);
          appleLibraryLoading = false;
          appleLibraryError = '';
          showAppleLibraryDetail = false;
          appleLibraryDetailLoading = false;
          currentPage = 'library';
          libraryView = 'albums';
        },
        loadPlaylist(tracks, jobId) {
          handleEvent({
            type: 'playlist_loaded',
            title: 'Performance test playlist',
            job_id: jobId ?? 'vela-performance-harness',
            tracks,
          });
          currentPage = 'downloads';
        },
        showPage(page, view) {
          currentPage = page;
          if (view !== undefined) libraryView = view;
        },
        snapshot() {
          return {
            albumCount: appleLibrary?.albums.length || 0,
            queueTrackCount: queueTrackKeys.length,
            pendingIngestBatchCount: pendingTrackBatches.length,
            ingestAnimationFramePending: trackIngestFrame !== null,
            currentPage,
            libraryView,
          };
        },
      };
      performanceWindow.__VELA_PERF__ = performanceHarness;
      registerCleanup(() => {
        if (performanceWindow.__VELA_PERF__ === performanceHarness) {
          delete performanceWindow.__VELA_PERF__;
        }
      });
    }
    const subscribe = (eventName: string, callback: (...data: any[]) => void) => {
      if (!componentDestroyed) registerCleanup(EventsOn(eventName, callback));
    };
    const scheduleShellReady = (deferStartupWork: boolean) => {
      void tick().then(() => {
        if (componentDestroyed) return;
        scheduleAnimationFrame(() => {
          markFrontendPerformance('shell-interactive');
          if (deferStartupWork) {
            scheduleIdleWork(() => {
              void openHistory();
              void fetchGistStatus();
              void refreshDownloadedMusicLibrary();
              for (const src of healthSources) {
                void checkHealth(src.key, { openPopover: false });
              }
            });
          }
        });
      });
    };

    const retryTimer = window.setInterval(() => { retryClock = Date.now(); }, 1000);
    registerCleanup(() => window.clearInterval(retryTimer));

    // Go owns device watching and emits the initial connected-device events.
    let initializationReady = false;
    const pendingIPodEvents: IPodEventPayload[] = [];
    const handleMountedIPodEvent = (value: unknown) => {
      const payload = parseIPodEventPayload(value);
      if (!payload) return;
      if (!initializationReady) {
        pendingIPodEvents.push(payload);
        return;
      }
      handleIPodEvent(payload);
    };
    if (!uiDemoMode) subscribe("ipod-event", handleMountedIPodEvent);

    const initialize = async () => {
      if (uiDemoMode) {
      config = {
        ...config,
        first_run_complete: true,
        download_path: 'C:\\Music\\Vela Library',
        apple_enabled: true,
        apple_authorization_token: 'demo-local-token',
        apple_music_user_token: 'demo-local-token',
        theme: 'system',
      } as main.Config;
      uiPreferences = { ...DEFAULT_UI_PREFERENCES };
      syncUIPreferencesToConfig();
      applyAppearance('system', false);
      selectedDownloadSources = ['auto'];
      appleLibrary = {
        saved_songs_count: 1842,
        albums: [
          { id: 'album-1', name: 'Afterglow', url: 'apple-music://library/album/album-1', image_url: '/demo-art.svg', track_count: 10, artist_name: 'Nova Lane' },
          { id: 'album-2', name: 'Blue Hours', url: 'apple-music://library/album/album-2', image_url: '/demo-art.svg', track_count: 12, artist_name: 'The Still' },
        ],
        playlists: [
          { id: 'demo-1', name: 'Late Night Drive', url: 'apple-music://demo/late-night', image_url: null, track_count: 42, is_algorithmic: false },
          { id: 'demo-2', name: 'New Music Mix', url: 'apple-music://demo/new-music', image_url: null, track_count: 25, is_algorithmic: true },
          { id: 'demo-3', name: 'Sunday Morning', url: 'apple-music://demo/sunday', image_url: null, track_count: 31, is_algorithmic: false },
          { id: 'demo-4', name: 'Heavy Rotation', url: 'apple-music://demo/rotation', image_url: null, track_count: 18, is_algorithmic: true },
          { id: 'demo-5', name: 'Focus', url: 'apple-music://demo/focus', image_url: null, track_count: 56, is_algorithmic: false },
        ],
      };
      discoveryGenres = [{ id: '14', name: 'Pop' }, { id: '21', name: 'Rock' }, { id: '18', name: 'Hip-Hop/Rap' }];
      discoveryData = {
        top_albums: [
          { name: 'Afterglow', artist_name: 'Nova Lane', artwork_url: '/demo-art.svg', url: 'https://music.apple.com/demo/1' },
          { name: 'Blue Hours', artist_name: 'The Still', artwork_url: '/demo-art.svg', url: 'https://music.apple.com/demo/2' },
          { name: 'Parallel Lines', artist_name: 'Mira', artwork_url: '/demo-art.svg', url: 'https://music.apple.com/demo/3' },
          { name: 'Northbound', artist_name: 'Glass Atlas', artwork_url: '/demo-art.svg', url: 'https://music.apple.com/demo/4' },
        ],
        top_playlists: [
          { name: 'Today’s Hits', curator_name: 'Apple Music', artwork_url: '/demo-art.svg', url: 'https://music.apple.com/demo/5' },
          { name: 'ALT CTRL', curator_name: 'Apple Music', artwork_url: '/demo-art.svg', url: 'https://music.apple.com/demo/6' },
        ],
      };
      downloadedLibrary = {
        albums: [
          { kind: 'album', relative_path: 'demo/afterglow', title: 'Afterglow', artist: 'Nova Lane', year: '2026', track_count: 10 },
          { kind: 'album', relative_path: 'demo/blue-hours', title: 'Blue Hours', artist: 'The Still', year: '2025', track_count: 12 },
          { kind: 'album', relative_path: 'demo/northbound', title: 'Northbound', artist: 'Glass Atlas', year: '2024', track_count: 9 },
        ],
        playlists: [{ kind: 'playlist', relative_path: 'demo/night-drive', title: 'Late Night Drive', artist: 'Playlist', track_count: 42 }],
      };
      downloadedSelectedPath = 'demo/afterglow';
      downloadedSelectedRelease = {
        ...downloadedLibrary.albums[0],
        tracks: [
          { title: 'Open Skies', artist: 'Nova Lane', file_name: '01 - Open Skies.flac', file_path: 'demo/01.flac', track_number: 1, duration_seconds: 218, audio_url: '' },
          { title: 'Afterglow', artist: 'Nova Lane', file_name: '02 - Afterglow.flac', file_path: 'demo/02.flac', track_number: 2, duration_seconds: 247, audio_url: '' },
          { title: 'Slow Motion', artist: 'Nova Lane', file_name: '03 - Slow Motion.flac', file_path: 'demo/03.flac', track_number: 3, duration_seconds: 203, audio_url: '' },
        ],
      };
      replaceHistoryItems([
        { date: new Date().toISOString(), url: 'https://music.apple.com/demo/album', title: 'Afterglow', total: 10, downloaded: 10, failed: 0 },
        { date: new Date(Date.now() - 86400000).toISOString(), url: 'https://music.apple.com/demo/playlist', title: 'Late Night Drive', total: 42, downloaded: 40, failed: 2 },
      ]);
      downloadJobs = [
        { id: 'demo-job-1', url: 'apple-music://demo/afterglow', title: 'Afterglow', status: 'downloading', total: 10, completed: 3 },
        { id: 'demo-job-2', url: 'apple-music://demo/night-drive', title: 'Late Night Drive', status: 'waiting', total: 42, completed: 0 },
      ];
      trackLabels = { 'demo-track-1': 'Nova Lane — Open Skies', 'demo-track-2': 'The Still — Blue Hours', 'demo-track-3': 'Mira — Parallel Lines' };
      resetTrackActivity(['demo-track-1', 'demo-track-2', 'demo-track-3'], {
        'demo-track-1': { mode: 'status', text: 'Downloaded', status: 'done' },
        'demo-track-2': { mode: 'determinate', progress: 64, text: 'Transferring audio', status: 'downloading', bytesDownloaded: 41943040, bytesTotal: 65536000, speedBps: 5242880 },
        'demo-track-3': { mode: 'status', text: 'Waiting…', status: 'waiting' },
      });
      workerActive = 1;
      workerConfigured = 2;
      workerCeiling = 8;
      isDownloading = true;
      playlistTitle = 'UI Preview Queue';
      playlistTotalTracks = 3;
      markFrontendPerformance('config-ready');
      await loadIPodDevices();
      isLoading = false;
      scheduleShellReady(false);
      return;
      }
      try {
      config = await GetConfig();
      try {
        workerCeiling = Math.max(1, Number(await GetDownloadWorkerCapacity() || 8));
      } catch {
        workerCeiling = 8;
      }
      if (!config.first_run_complete) {
        setupMode = true;
      }
      if (!config.output_format) {
        config.output_format = 'lossless';
      }
      if (!config.max_retries || config.max_retries < 1) {
        config.max_retries = 3;
      }
      if (!config.max_concurrent_jobs || config.max_concurrent_jobs < 1) config.max_concurrent_jobs = 2;
      config.max_concurrent_jobs = Math.min(workerCeiling, config.max_concurrent_jobs);
      workerConfigured = config.max_concurrent_jobs;
      lastAppliedWorkerCount = workerConfigured;
      if (!config.sources_enabled) {
        config.sources_enabled = [];
      }
      config.sources_enabled = config.sources_enabled.filter(source =>
        source === 'auto' || concreteDownloadSources.includes(source)
      );
      if (!config.qobuz_app_id) {
        config.qobuz_app_id = '285473059';
      }
      if (!config.library_mode) {
        config.library_mode = 'smart_dedup';
      }
      if (config.prefer_explicit === undefined || config.prefer_explicit === null) {
        config.prefer_explicit = true;
      }
      if (!config.folder_structure) {
        config.folder_structure = 'standard';
      }
      if (!config.album_folder_structure) {
        config.album_folder_structure = config.folder_structure || 'standard';
      }
      if (!config.playlist_folder_structure) {
        config.playlist_folder_structure = config.folder_structure || 'standard';
      }
      if (!config.single_track_structure) {
        config.single_track_structure = 'album_numbered';
      }
      if (!config.filename_format) {
        config.filename_format = 'default';
      }
      if (config.spotify_sp_dc === undefined || config.spotify_sp_dc === null) {
        config.spotify_sp_dc = '';
      }
      if (config.apple_storefront === undefined || config.apple_storefront === null || !config.apple_storefront) {
        config.apple_storefront = 'gb';
      }
      if (config.amazon_wvd_path === undefined || config.amazon_wvd_path === null) {
        config.amazon_wvd_path = '';
      }
      if (config.strict_matching === undefined || config.strict_matching === null) {
        config.strict_matching = false;
      }
      if (!config.download_source) {
        config.download_source = 'auto';
      }
      selectedDownloadSources = normalizeDownloadSources();
      config = { ...config, download_sources: selectedDownloadSources } as main.Config;
      if (typeof config.save_cover_art_sidecar !== 'boolean') {
        config.save_cover_art_sidecar = true;
      }
      // Template defaults
      if (!config.single_track_filename_template) config.single_track_filename_template = '{artist} - {title}';
      if (!config.album_track_filename_template)  config.album_track_filename_template  = '{track} - {title}';
      if (!config.folder_structure_template)      config.folder_structure_template      = '{album_artist}/{year} - {album}';
      if (!config.illegal_character_replacement)  config.illegal_character_replacement  = '_';
      if (!config.whitespace_handling)            config.whitespace_handling            = 'keep';
      if (!config.track_number_padding)           config.track_number_padding           = 2;
      if (!config.multi_disc_handling || config.multi_disc_handling === 'prefix') {
        config.multi_disc_handling = 'track_only';
        await saveConfigSerialized();
      }
      uiPreferences = normalizeUIPreferences(config.ui);
      syncUIPreferencesToConfig();
      const savedAppearance = ['system', 'light', 'dark'].includes(config.theme || '')
        ? config.theme as Appearance
        : 'system';
      applyAppearance(savedAppearance, false);
      applyStartupDestination();

      // Auto-sync defaults
      if (config.auto_sync_enabled === undefined) config.auto_sync_enabled = false;
      if (!config.auto_sync_hour && config.auto_sync_hour !== 0) config.auto_sync_hour = 6;
      if (config.auto_sync_minute === undefined) config.auto_sync_minute = 0;
      if (!config.auto_sync_days) config.auto_sync_days = 127;
      config.tracked_playlists = (config.tracked_playlists || []).filter((entry: any) =>
        String(entry?.url || '').startsWith('apple-music://')
      );

      } catch (e) {
        console.error('Failed to load config', e);
        setupMode = true;
      }

      markFrontendPerformance('config-ready');
      if (componentDestroyed) return;
      restoreDownloadQueue();
      initializationReady = true;
      pendingIPodEvents.splice(0).forEach(handleIPodEvent);
      if (config.apple_music_user_token && config.apple_authorization_token) {
        void loadAppleMusicLibrary(); // non-blocking — Apple Music library updates when ready
      }
      const systemThemeQuery = window.matchMedia('(prefers-color-scheme: dark)');
      const reducedMotionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
      const handleSystemTheme = (event: MediaQueryListEvent) => {
        systemDark = event.matches;
        applyCurrentUIPreferences();
      };
      const handleSystemMotion = (event: MediaQueryListEvent) => {
        systemReducedMotion = event.matches;
        applyCurrentUIPreferences();
      };
      systemThemeQuery.addEventListener('change', handleSystemTheme);
      registerCleanup(() => systemThemeQuery.removeEventListener('change', handleSystemTheme));
      reducedMotionQuery.addEventListener('change', handleSystemMotion);
      registerCleanup(() => reducedMotionQuery.removeEventListener('change', handleSystemMotion));
      isLoading = false;
      scheduleShellReady(true);

      // Listen to backend events. The iPod listener is registered before async config work.
      subscribe("backend-event", handleEvent);
      if (!queuePaused) scheduleTimeout(startNextQueuedJob);
      subscribe("apple-index-event", (payload: any) => {
      if (payload?.type === 'apple_index_progress') {
        appleIndexing = true;
        appleIndexPercent = Math.round(Math.max(0, Math.min(99, Number(payload.percent || 0))));
        appleIndexLabel = payload.label || '';
      } else if (payload?.type === 'apple_index_complete') {
        appleIndexPercent = 100;
        appleIndexLabel = 'Library indexed';
        appleIndexStarted = false;
        void loadAppleMusicLibrary(false, false);
        scheduleTimeout(() => { appleIndexing = false; }, 1800);
      } else if (payload?.type === 'apple_index_incomplete') {
        const completed = Number(payload?.data?.completed || 0);
        const total = Number(payload?.data?.total || 0);
        appleIndexStarted = false;
        appleIndexing = true;
        appleIndexPercent = Math.round(Math.max(0, Math.min(99, Number(payload?.data?.percent || 0))));
        appleIndexLabel = `Index incomplete · ${Math.max(0, total - completed)} remaining`;
        const message = payload?.data?.errors?.[0]?.message || payload?.data?.errors?.[0] || 'Library indexing stopped before it finished. It will resume from the local checkpoint.';
        showToast(String(message), 'warning');
      } else if (payload?.type === 'apple_index_paused') {
        appleIndexing = true;
        appleIndexStarted = false;
        appleIndexLabel = payload.label || 'Index paused while downloading';
      } else if (payload?.type === 'apple_index_error' || payload?.type === 'error') {
        appleIndexStarted = false;
        appleIndexing = false;
        showToast(payload.message || 'Library indexing failed.');
      }
      });
      subscribe("downloaded-index-event", (payload: any) => {
      if (payload?.type === 'progress') {
        downloadedIndexing = true;
        downloadedIndexPercent = Number(payload.percent || 0);
        downloadedIndexLabel = payload.label || '';
      } else if (payload?.type === 'complete') {
        downloadedIndexPercent = 100;
        downloadedIndexLabel = 'Downloads indexed';
        downloadedIndexing = false;
      } else if (payload?.type === 'error') {
        downloadedIndexing = false;
        downloadedIndexLabel = '';
        showToast(payload.message || 'Downloaded music indexing failed.');
      } else if (payload?.type === 'warning') {
        showToast(payload.message || 'Some downloaded releases could not be indexed.', 'warning');
      }
      if (payload?.type === 'complete' && payload.library) {
        downloadedLibraryRequestId++;
        const selected = applyDownloadedLibrarySnapshot(payload.library);
        if (selected && currentPage === 'downloaded') void openDownloadedRelease(selected);
      }
      });

      // Listen to TIDAL OAuth events
      subscribe("tidal-oauth-event", (payload: any) => {
      if (!payload || !payload.type) return;
      switch (payload.type) {
        case 'tidal_oauth_status':
          tidalOAuth = { ...tidalOAuth, phase: 'starting', message: payload.message };
          break;
        case 'tidal_oauth_url':
          tidalOAuth = {
            phase: 'waiting_browser',
            url: payload.url,
            code: payload.code || '',
            message: 'Open the link below in your browser and log in to TIDAL:',
          };
          break;
        case 'tidal_oauth_success':
          tidalOAuth = {
            phase: 'success',
            displayName: payload.display_name || '',
            countryCode: payload.country_code || '',
            sessionJson: payload.session_json || '',
            message: payload.message || 'Login successful!',
          };
          // Auto-populate config fields and re-load config to pick up saved values
          if (payload.session_json) {
            config.tidal_enabled = true;
            config.tidal_auth_mode = 'session_json';
            config.tidal_session_json = payload.session_json;
          }
          // Trigger validation automatically so user sees the green tick
          tidalValidationStatus = {
            ok: true,
            message: payload.message || 'TIDAL session is valid.',
            display_name: payload.display_name,
            country_code: payload.country_code,
          };
          break;
        case 'tidal_oauth_error':
          tidalOAuth = { phase: 'error', message: payload.message || 'OAuth login failed.' };
          break;
        case 'tidal_oauth_done':
          // Process ended — if still in starting/waiting state, mark as error
          if (tidalOAuth.phase === 'starting' || tidalOAuth.phase === 'waiting_browser') {
            tidalOAuth = { ...tidalOAuth, phase: 'error', message: tidalOAuth.message || 'OAuth process ended unexpectedly.' };
          }
          break;
      }
      tidalOAuth = { ...tidalOAuth }; // trigger reactivity
      });

      subscribe("apple-login-event", (payload: any) => {
      if (!payload || !payload.type) return;
      switch (payload.type) {
        case 'apple_login_status':
          appleLogin = { phase: 'starting', message: payload.message || 'Opening Apple Music login...' };
          break;
        case 'apple_login_success':
          appleLogin = { phase: 'success', message: payload.message || 'Apple Music connected!' };
          config.apple_enabled = true;
          if (payload.authorization_token) config.apple_authorization_token = payload.authorization_token;
          if (payload.music_user_token) config.apple_music_user_token = payload.music_user_token;
          if (payload.storefront) config.apple_storefront = payload.storefront;
          void saveConfigSerialized();
          if (!appleLibrary && !appleLibraryLoading) loadAppleMusicLibrary();
          else scheduleTimeout(startAppleIndexOnce, 500);
          scheduleTimeout(() => { appleLogin = { phase: 'idle' }; }, 4000);
          break;
        case 'apple_login_error':
          appleLogin = { phase: 'error', message: payload.message || 'Apple Music login failed.' };
          break;
        case 'apple_login_done':
          if (appleLogin.phase === 'starting') {
            appleLogin = { phase: 'error', message: appleLogin.message || 'Apple Music login ended unexpectedly.' };
          }
          break;
      }
      });

      subscribe("amazon-login-event", (payload: any) => {
      if (!payload || !payload.type) return;
      switch (payload.type) {
        case 'amazon_login_status':
          if (payload.phase === 'waiting_for_user') {
            amazonLogin = { phase: 'waiting_for_user', message: payload.message || 'Sign in to Amazon Music in your browser, then click \'I\'m Signed In\' below.' };
          } else if (payload.phase === 'capturing') {
            amazonLogin = { phase: 'capturing', message: payload.message || 'Reading your browser session…' };
          } else {
            amazonLogin = { phase: 'starting', message: payload.message || 'Opening Amazon Music login…' };
          }
          break;
        case 'amazon_login_success':
          amazonLogin = {
            phase: 'success',
            message: payload.message || 'Amazon Music connected.',
            detail: payload.has_wvd_path ? '' : 'A Widevine device path is still required for downloads.',
          };
          config.amazon_enabled = true;
          if (payload.direct_creds_json) config.amazon_direct_creds_json = payload.direct_creds_json;
          break;
        case 'amazon_login_error':
          amazonLogin = { phase: 'error', message: payload.message || 'Amazon Music login failed.' };
          break;
        case 'amazon_login_done':
          if (amazonLogin.phase === 'starting' || amazonLogin.phase === 'waiting_for_user' || amazonLogin.phase === 'capturing') {
            amazonLogin = { phase: 'error', message: amazonLogin.message || 'Amazon Music login ended unexpectedly.' };
          }
          break;
      }
      });

      subscribe("sp-dc-event", (payload: any) => {
      if (!payload || !payload.type) return;
      switch (payload.type) {
        case 'sp_dc_status':
          spDcCapture = { phase: payload.status === 'waiting' ? 'waiting_for_user' : 'starting', message: payload.message || 'Opening browser...' };
          break;
        case 'sp_dc_captured':
          config.spotify_sp_dc = payload.sp_dc;
          spDcCapture = { phase: 'success', message: 'Spotify account connected!' };
          void saveConfigSerialized();
          scheduleTimeout(() => { spDcCapture = { phase: 'idle' }; }, 4000);
          break;
        case 'sp_dc_error':
          spDcCapture = { phase: 'error', message: payload.message || 'Failed to capture sp_dc.' };
          break;
        case 'sp_dc_done':
          if (spDcCapture.phase === 'starting' || spDcCapture.phase === 'waiting_for_user') {
            spDcCapture = { phase: 'error', message: 'Login ended without capturing a session. Try again.' };
          }
          break;
      }
      });

      const handleWindowResize = () => {
      };
      const handleGlobalKeydown = (event: KeyboardEvent) => {
        if (event.key === 'Escape' && showAppleLibraryDetail && libraryView !== 'favourites') closeAppleLibraryDetail();
      };
      window.addEventListener('resize', handleWindowResize);
      registerCleanup(() => window.removeEventListener('resize', handleWindowResize));
      window.addEventListener('keydown', handleGlobalKeydown);
      registerCleanup(() => window.removeEventListener('keydown', handleGlobalKeydown));
    };

    void initialize();
    return () => {
      componentDestroyed = true;
      appleLibraryRequestId += 1;
      downloadedLibraryRequestId += 1;
      downloadedDetailRequestId += 1;
      appleDetailRequestId += 1;
      artistSearchReqId += 1;
      discographyReqId += 1;
      trackIngestGeneration += 1;
      pendingTrackBatches.length = 0;
      pendingIPodEvents.length = 0;
      for (const cleanup of cleanups.reverse()) {
        try {
          cleanup();
        } catch (error) {
          console.error('Failed to clean up a frontend lifecycle resource', error);
        }
      }
      clearScheduledWork();
    };
  });

  function addLog(type: string, text: string, isRawHtml: boolean = false) {
    logBuffer.pushBack({ id: logId++, type, text, isRawHtml });
    if (logCommitScheduled) return;
    logCommitScheduled = true;
    scheduleAnimationFrame(() => {
      logCommitScheduled = false;
      logs = logBuffer.toArray();
    });
  }

  function formatDuration(ms: number): string {
    const totalSec = Math.floor(ms / 1000);
    const hours = Math.floor(totalSec / 3600);
    const mins = Math.floor((totalSec % 3600) / 60);
    if (hours > 0) return `${hours} hr ${mins} min`;
    if (mins > 0) return `${mins} min`;
    return `${totalSec} sec`;
  }

  function formatAsciiRundown(summary: any): string {
    const downloaded = summary.downloaded || 0;
    const skipped = summary.skipped || 0;
    const failed = summary.failed || 0;
    const total = summary.total || 0;
    const totalMb: number | null = typeof summary.total_mb === 'number' ? summary.total_mb : null;
    const elapsed: number | null = typeof summary.elapsed_seconds === 'number' ? summary.elapsed_seconds : null;

    const sep = '═'.repeat(56);
    const pad = (label: string, value: string) => {
      const full = `  ${label}${value}`;
      return full;
    };

    const lines = [
      `<span style="color:var(--accent-color)">${sep}</span>`,
      pad('Tracks added      : ', `<span style="color:#4ade80">${downloaded} / ${total}</span>`),
      pad('Already downloaded: ', `<span style="color:#facc15">${skipped}</span>`),
      pad('Could not source  : ', `<span style="color:${failed > 0 ? 'var(--error-color)' : '#94a3b8'}">${failed}</span>`),
      ...(totalMb !== null ? [pad('Total size        : ', `<span style="color:#94a3b8">${totalMb} MB</span>`)] : []),
      ...(elapsed !== null ? [pad('Time taken        : ', `<span style="color:#94a3b8">${elapsed}s</span>`)] : []),
      `<span style="color:var(--accent-color)">${sep}</span>`,
    ];

    return `<div style="font-family:var(--font-mono);font-size:13px;line-height:1.7;margin-top:8px">${lines.join('<br>')}</div>`;
  }

  function handleEvent(payload: any) {
    if (payload.type === 'job_preparing') {
      jobPreparationStatus = payload.message || 'Reading release information…';
      return;
    }
    if (payload.type === 'playlist_loaded') {
      playlistTitle = payload.title || '';
      playlistArtwork = payload.artwork_url || '';
      playlistArtists = payload.artists_string || '';
      playlistReleaseDate = payload.release_date || '';
      playlistContentType = payload.content_type || '';
      playlistQualityBadge = payload.quality_badge || '';
      const trkList: any[] = payload.tracks || [];
      playlistTotalTracks = trkList.length;
      const activeJobIndex = downloadJobs.findIndex(job => job.status === 'downloading');
      const jobIndex = activeJobIndex >= 0 ? activeJobIndex : downloadJobs.findIndex(job => job.status === 'waiting');
      if (jobIndex >= 0) {
        downloadJobs[jobIndex] = { ...downloadJobs[jobIndex], status: 'downloading', title: payload.title || downloadJobs[jobIndex].title, artwork: payload.artwork_url || downloadJobs[jobIndex].artwork, total: trkList.length };
        downloadJobs = [...downloadJobs];
        persistDownloadQueue();
      }
      playlistTotalDurationMs = 0;
      resetTrackActivity();
      trackLabels = {};
      currentPlaylistTrackKeysByIndex = {};
      trackKeysByStableId = {};
      currentPlaylistKeyPrefix = `track:${payload.job_id || `playlist-${++playlistKeySequence}`}`;
      currentPlaylistTrackCount = trkList.length;
      enqueueTrackBatch(trkList, 1);
      jobPreparationStatus = 'Starting song downloads…';
      return;
    }

    if (payload.type === 'tracks_appended') {
      const trkList2: any[] = payload.tracks || [];
      const startIndex = currentPlaylistTrackCount + 1;
      currentPlaylistTrackCount += trkList2.length;
      playlistTotalTracks = (playlistTotalTracks || 0) + trkList2.length;
      enqueueTrackBatch(trkList2, startIndex);
      return;
    }

    if (payload.type === 'library_update') {
      collectValidatedCompletionFiles(payload.completed_files);
      return;
    }

    if (payload.type === 'process_ended') {
      isDownloading = false;
      const activeJob = downloadJobs.findIndex(job => job.status === 'downloading');
      if (payload.status === 'paused') {
        if (activeJob >= 0) downloadJobs[activeJob] = { ...downloadJobs[activeJob], status: 'paused' };
        addLog('warning', 'Download paused at its latest completed song.');
      } else if (payload.status === 'cancelled') {
        if (activeJob >= 0) downloadJobs[activeJob] = { ...downloadJobs[activeJob], status: 'cancelled' };
        addLog('warning', 'Download cancelled');
      } else if (payload.status === 'failed') {
        if (activeJob >= 0) downloadJobs[activeJob] = { ...downloadJobs[activeJob], status: 'failed' };
        addLog('error', 'Download stopped with errors');
      } else {
        if (activeJob >= 0) downloadJobs[activeJob] = { ...downloadJobs[activeJob], status: 'downloaded' };
        addLog('success', 'Download completed');
      }
      downloadJobs = [...downloadJobs];
      persistDownloadQueue();
      if (priorityJobId && payload.status === 'paused') priorityJobId = '';
      if (!queuePaused && payload.status !== 'cancelled') {
        void ResumeDownload();
        scheduleTimeout(startNextQueuedJob);
      }
      return;
    }

    if (payload.type === 'log') {
      if (typeof payload.message === 'string' && payload.message.includes('HTTP Error') && payload.message.includes('403')) {
        return; // hide spotify irrelevant 403 error
      }
      if (typeof payload.message === 'string' && payload.message.includes('0xc000013a')) {
        return; // standard cancel status on windows
      }
      // Hide adapter startup / source chain logs — the health chips already show this info
      if (typeof payload.message === 'string') {
        const msg = payload.message;
        if (
          /^\[OK\].*adapter enabled/.test(msg) ||
          /^\[Sources\] Active download chain:/.test(msg) ||
          /^Enriching tracks with album metadata/.test(msg) ||
          /^\[Spotify\] Partner API: \d+ tracks for album/.test(msg) ||
          /^\[Spotify\] Used partner GraphQL API for album/.test(msg)
        ) {
          return;
        }
      }
      addLog(payload.level, payload.message);
    } else if (payload.type === 'progress') {
      addLog('info', `[Bulk Progress] ${payload.message}`);
    } else if (payload.type === 'event') {
      const name = payload.name;
      const data = payload.payload;

      if (name === 'worker_state') {
        workerActive = Math.max(0, Number(data.active_workers || 0));
        workerConfigured = Math.max(1, Number(data.configured_workers || config.max_concurrent_jobs || 2));
        workerCeiling = Math.max(1, Number(data.worker_ceiling || workerCeiling));
        return;
      }

      const trackKey = resolveTrackEventKey(data);
      const trackLabel = makeTrackDisplayName(data.artist, data.track);
      if (!trackLabels[trackKey]) {
        trackLabels[trackKey] = trackLabel;
        trackLabels = { ...trackLabels };
      }

      if (name === 'track_started') {
        updateActiveTrack(trackKey, {
          mode: 'indeterminate',
          progress: undefined,
          text: 'Resolving best source…',
          error: undefined,
          status: 'resolving',
          phase: 'resolving',
          bytesDownloaded: undefined,
          bytesTotal: undefined,
          speedBps: undefined,
          retryAt: undefined,
        }, { allowTerminalReset: true });
        jobPreparationStatus = `Finding an audio source for ${trackLabel}…`;
        void autoScrollTracklist(trackKey);

      } else if (name === 'track_resolved') {
        let displaySource = data.source || 'auto';
        if (displaySource === 'hifi') displaySource = 'Tidal';
        else if (displaySource === 'apple') displaySource = 'Apple';
        else if (displaySource === 'amazon') displaySource = 'Amazon';
        else displaySource = displaySource.charAt(0).toUpperCase() + displaySource.slice(1);

        updateActiveTrack(trackKey, {
          mode: 'status',
          progress: undefined,
          text: `Accepted via ${displaySource}${data.quality_label ? ` • ${data.quality_label}` : ''}`,
          status: 'resolving',
          phase: 'resolved',
        });

      } else if (name === 'track_download_attempt') {
        const source = String(data.source || 'auto');
        const attempt = data.attempt ?? 1;

        const attemptSuffix = attempt > 1 ? ` • Attempt ${attempt}` : '';
        let displaySource = source;
        if (displaySource === 'hifi') displaySource = 'Tidal';
        else if (displaySource === 'apple') displaySource = 'Apple';
        else if (displaySource === 'amazon') displaySource = 'Amazon';
        else displaySource = displaySource.charAt(0).toUpperCase() + displaySource.slice(1);

        updateActiveTrack(trackKey, {
          mode: 'indeterminate',
          progress: undefined,
          text: `Downloading from ${displaySource}${data.quality_label ? ` • ${data.quality_label}` : ''}${attemptSuffix}`,
          error: undefined,
          status: 'downloading',
          phase: 'transferring',
          bytesDownloaded: undefined,
          bytesTotal: undefined,
          speedBps: undefined,
          retryAt: undefined,
          attempt,
        });

      } else if (name === 'track_progress') {
        const bytesTotal = data.bytes_total == null ? NaN : Number(data.bytes_total);
        const progressPercent = data.progress_percent == null ? NaN : Number(data.progress_percent);
        const measured = Number.isFinite(bytesTotal) && bytesTotal > 0 && Number.isFinite(progressPercent);
        updateActiveTrack(trackKey, {
          mode: measured ? 'determinate' : 'indeterminate',
          progress: measured ? Math.min(100, Math.max(0, progressPercent)) : undefined,
          text: 'Transferring audio',
          error: undefined,
          status: 'downloading',
          phase: data.phase || 'transferring',
          bytesDownloaded: data.bytes_downloaded != null && Number.isFinite(Number(data.bytes_downloaded)) ? Number(data.bytes_downloaded) : undefined,
          bytesTotal: Number.isFinite(bytesTotal) && bytesTotal > 0 ? bytesTotal : undefined,
          speedBps: data.speed_bps != null && Number.isFinite(Number(data.speed_bps)) ? Number(data.speed_bps) : undefined,
          retryAt: undefined,
        });

      } else if (name === 'track_phase') {
        const phase = String(data.phase || 'resolving');
        if (phase === 'processing') {
          updateActiveTrack(trackKey, {
            mode: 'indeterminate',
            progress: undefined,
            text: 'Processing, tagging, and organizing…',
            error: undefined,
            status: 'processing',
            phase,
            speedBps: undefined,
          });
        } else if (phase === 'transferring') {
          updateActiveTrack(trackKey, {
            mode: 'indeterminate',
            progress: undefined,
            text: 'Starting measured transfer…',
            error: undefined,
            status: 'downloading',
            phase,
          });
        } else {
          updateActiveTrack(trackKey, {
            mode: 'indeterminate',
            progress: undefined,
            text: 'Resolving best source…',
            error: undefined,
            status: 'resolving',
            phase,
          });
        }

      } else if (name === 'track_retry_scheduled') {
        const retryAfterSeconds = Math.max(0, Number(data.retry_after_seconds || 0));
        updateActiveTrack(trackKey, {
          mode: 'status',
          progress: undefined,
          text: 'Automatic retry scheduled',
          error: data.error || 'No suitable source was available.',
          status: 'retry_wait',
          phase: 'retry_wait',
          bytesDownloaded: undefined,
          bytesTotal: undefined,
          speedBps: undefined,
          retryAt: Date.now() + retryAfterSeconds * 1000,
          retryDeadline: data.retry_deadline != null && Number.isFinite(Number(data.retry_deadline)) ? Number(data.retry_deadline) * 1000 : undefined,
          attempt: Number(data.attempt || 0),
        });
        addLog('warning', `[Retry] ${trackLabel} — ${data.error || 'Source unavailable'}; retrying in ${Math.ceil(retryAfterSeconds)}s`);

      } else if (name === 'track_retry_exhausted') {
        const reason = data.error || 'Automatic retries were exhausted.';
        updateActiveTrack(trackKey, {
          mode: 'status',
          progress: undefined,
          text: 'Automatic retries exhausted',
          error: reason,
          status: 'failed',
          phase: 'failed',
          retryAt: undefined,
          speedBps: undefined,
        });
        addLog('error', `[FAIL] ${trackLabel} — ${reason}`);

      } else if (name === 'track_completed') {
        addLog('success', `[✓] Downloaded: ${trackLabel}`);
        collectValidatedCompletionFiles([data.final_file_path]);
        updateActiveTrack(trackKey, {
          mode: 'determinate',
          progress: 100,
          text: 'Downloaded',
          error: undefined,
          status: 'done',
          phase: 'complete',
          retryAt: undefined,
          speedBps: undefined,
        });
      } else if (name === 'track_failed') {
        const reason = data.error || 'Download failed';
        if (trackActivity.get(trackKey)?.status !== 'failed') addLog('error', `[FAIL] ${trackLabel} — ${reason}`);
        updateActiveTrack(trackKey, {
          mode: 'status',
          progress: undefined,
          text: 'Download failed permanently',
          error: reason,
          status: 'failed',
          phase: 'failed',
          retryAt: undefined,
          speedBps: undefined,
        });
      } else if (name === 'track_skipped') {
        addLog('warning', `[—] Already downloaded: ${trackLabel}`);
        updateActiveTrack(trackKey, {
          mode: 'status',
          text: 'Already downloaded',
          status: 'skipped',
          phase: 'complete',
          retryAt: undefined,
        });
      } else if (name === 'playlist_started') {
        addLog('info', `Creating playlist structure and syncing tracks: ${data.message}`);
      }
    } else if (payload.type === 'playlist_summary') {
      const htmlRundown = formatAsciiRundown(payload);
      addLog('terminal-rundown', htmlRundown, true);
      AddHistory(payload).catch(err => console.error("Failed to add history:", err));
      prependHistoryItem(payload);
      requestDesktopNotification(
        'completion',
        payload.error ? 'Download finished with errors' : 'Download complete',
        payload.title || payload.url || 'Your music is ready.',
      );
      const jobIndex = downloadJobs.findIndex(job => job.url === payload.url && job.status !== 'downloaded');
      if (jobIndex >= 0) {
        const completedFiles = Array.isArray(payload.completed_files)
          ? eligibleCompletionFiles(payload.completed_files, downloadJobs[jobIndex].ipodDestinationPath || '')
          : downloadJobs[jobIndex].completedFiles || [];
        downloadJobs[jobIndex] = { ...downloadJobs[jobIndex], title: payload.title || downloadJobs[jobIndex].title, artwork: payload.artwork_url || downloadJobs[jobIndex].artwork, status: payload.error ? 'failed' : 'downloaded', total: payload.total || 0, completed: (payload.downloaded || 0) + (payload.skipped || 0), completedFiles };
        const completedJob = downloadJobs[jobIndex];
        downloadJobs = [...downloadJobs];
        persistDownloadQueue();
        if (completedJob.ipodDestinationId && completedJob.ipodDestinationPath) {
          void openCompletedDownloadReview(completedJob);
        }
      }

    } else if (payload.type === 'done') {
      // JSON CLI said done
    }
  }

  // Tab mode
  let activeTab: 'library' | 'url' | 'artist' | 'discover' = 'library';
  let searchQuery = '';

  // Discovery variables
  let discoveryRegion = 'gb';
  let discoverySearch = '';
  let discoveryGenre = '';
  let discoveryData: any = null;
  let discoveryLoading = false;
  let discoveryGenres: any[] = [];
  let discoveryGenresLoading = false;

  function matchesDiscovery(item: any): boolean {
    const query = discoverySearch.trim().toLowerCase();
    if (!query) return true;
    return [item?.name, item?.artist_name, item?.curator_name]
      .filter(Boolean)
      .some(value => String(value).toLowerCase().includes(query));
  }

  async function loadDiscoveryGenres() {
    discoveryGenresLoading = true;
    try {
      // @ts-ignore
      const raw = await window.go.main.App.GetDiscoveryGenres(discoveryRegion);
      const parsed = JSON.parse(raw);
      if (parsed.type === 'discovery_genres') {
        discoveryGenres = parsed.data || [];
      } else {
        addLog('error', `Failed to load genres: ${parsed.error || parsed.message}`);
      }
    } catch (e) {
      console.error(e);
    }
    discoveryGenresLoading = false;
  }

  async function loadDiscoveryData() {
    discoveryLoading = true;
    try {
      // @ts-ignore
      const raw = await window.go.main.App.GetDiscoveryData(discoveryRegion, discoveryGenre, discoveryGenres.find(g => g.id === discoveryGenre)?.name || '');
      const parsed = JSON.parse(raw);
      if (parsed.type === 'discovery') {
        discoveryData = parsed.data;
      } else {
        addLog('error', `Failed to load discovery: ${parsed.error || parsed.message}`);
      }
    } catch (e) {
      console.error(e);
    }
    discoveryLoading = false;
  }

  function handleDiscoveryClick(url: string) {
    activeTab = 'url';
    currentPage = 'downloads';
    inputUrl = url;
    // Auto-focus text area or user can click download
  }
  let searchSource: 'spotify' | 'apple' = 'apple';
  let showArtistSearch = false;
  let artistSearchResults: any[] = [];
  let artistSearchLoading = false;
  let artistSearchReqId = 0;

  // Discography modal
  let showDiscography = false
  let discographyLoading = false
  let discographyArtist: any = null
  let discographySelected: Set<string> = new Set()
  let discographyReqId = 0  // incremented on each new request; stale responses are ignored

  function selectAllDiscographyAlbums() {
    const albums = Array.isArray(discographyArtist?.albums) ? discographyArtist.albums : [];
    discographySelected = new Set(
      albums.map((album: any) => String(album?.url || '')).filter(Boolean),
    );
  }

  function toggleDiscographySelection(url: string) {
    discographySelected = discographySelected.has(url)
      ? new Set([...discographySelected].filter(item => item !== url))
      : new Set([...discographySelected, url]);
  }

  function discographyReleaseKey(album: any) {
    const name = String(album?.name ?? '').toLowerCase().replace(/\s+/g, ' ').trim();
    const type = String(album?.type ?? 'album');
    const year = Number(album?.year ?? 0);
    const trackCount = Number(album?.track_count ?? 0);
    return `${type}::${year}::${trackCount}::${name}`;
  }

  function discographyReleaseScore(album: any) {
    const name = String(album?.name ?? '');
    const isCleanNamed = /\b(clean|edited|radio edit|censored)\b/i.test(name);
    const explicitScore =
      album?.is_explicit === true ? 2 :
      album?.is_explicit === false ? 0 :
      isCleanNamed ? 0 : 1;
    return [
      explicitScore,
      album?.artwork_url ? 1 : 0,
      Number(album?.track_count ?? 0),
      String(album?.id ?? ''),
    ];
  }

  function isBetterDiscographyRelease(candidate: any, current: any) {
    const candidateScore = discographyReleaseScore(candidate);
    const currentScore = discographyReleaseScore(current);
    for (let i = 0; i < candidateScore.length; i++) {
      if (candidateScore[i] === currentScore[i]) continue;
      return candidateScore[i] > currentScore[i];
    }
    return false;
  }

  function dedupeDiscographyAlbums(albums: any[]) {
    const grouped: Record<string, any[]> = Object.create(null);
    for (const album of albums || []) {
      const key = discographyReleaseKey(album);
      const group = grouped[key] ?? [];
      group.push(album);
      grouped[key] = group;
    }

    const deduped: any[] = [];
    for (const group of Object.values(grouped)) {
      let best = group[0];
      for (const candidate of group.slice(1)) {
        if (isBetterDiscographyRelease(candidate, best)) {
          best = candidate;
        }
      }
      deduped.push(best);
    }
    return deduped;
  }

  function formatPlaybackTime(seconds: number): string {
    if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
    const whole = Math.floor(seconds);
    const mins = Math.floor(whole / 60);
    const secs = whole % 60;
    return `${mins}:${String(secs).padStart(2, '0')}`;
  }

  function releaseMetaLine(release: LibraryReleaseSummary | LibraryReleaseDetail): string {
    const parts = [];
    if (release.artist && release.artist !== 'Playlist') parts.push(release.artist);
    if (release.year) parts.push(release.year);
    parts.push(`${release.track_count} track${release.track_count === 1 ? '' : 's'}`);
    return parts.join(' · ');
  }

  function downloadedReleaseKey(release: LibraryReleaseSummary): string {
    return release.relative_path;
  }

  function downloadedTrackKey(track: LibraryReleaseTrack): string {
    return track.file_path;
  }

  function artistSearchItemKey(artist: any, index: number): string {
    return String(artist?.artist_id || artist?.url || `${artist?.name || 'artist'}:${index}`);
  }

  function discographyAlbumKey(album: any, index: number): string {
    return String(album?.id || album?.url || `${album?.name || 'album'}:${index}`);
  }

  async function openDownloadedMusic() {
    currentPage = 'downloaded';
    await refreshDownloadedMusicLibrary();
  }

  function applyDownloadedLibrarySnapshot(parsed: any): LibraryReleaseSummary | null {
    return measureFrontendPerformance('downloaded-payload-apply', () => {
      downloadedDetailRequestId++;
      downloadedDetailCache = Object.create(null);
      downloadedLibrary = {
        albums: Array.isArray(parsed.albums) ? parsed.albums : [],
        playlists: Array.isArray(parsed.playlists) ? parsed.playlists : [],
        error: parsed.error
      };
      downloadedLibraryError = parsed.error || '';

      const selected = [...downloadedLibrary.albums, ...downloadedLibrary.playlists]
        .find((item: LibraryReleaseSummary) => item.relative_path === downloadedSelectedPath) || null;
      if (!selected) {
        downloadedSelectedRelease = null;
        downloadedSelectedPath = '';
        downloadedSelectedReleaseLoading = false;
      }
      return selected;
    });
  }

  async function refreshDownloadedMusicLibrary() {
    const requestId = ++downloadedLibraryRequestId;
    downloadedLibraryLoading = !downloadedLibrary.albums.length && !downloadedLibrary.playlists.length;
    downloadedLibraryError = '';
    try {
      const raw = await GetDownloadedMusicLibrary();
      const parsed = measureFrontendPerformance('downloaded-payload-parse', () => JSON.parse(raw || '{}'));
      if (requestId !== downloadedLibraryRequestId) return;
      const selected = applyDownloadedLibrarySnapshot(parsed);
      if (selected && currentPage === 'downloaded') void openDownloadedRelease(selected);
    } catch (e: any) {
      if (requestId !== downloadedLibraryRequestId) return;
      downloadedLibraryError = String(e);
      downloadedLibrary = { albums: [], playlists: [] };
    } finally {
      if (requestId === downloadedLibraryRequestId) downloadedLibraryLoading = false;
    }
  }

  async function openDownloadedRelease(release: LibraryReleaseSummary) {
    const requestId = ++downloadedDetailRequestId;
    const visibleDetail = downloadedSelectedPath === release.relative_path ? downloadedSelectedRelease : null;
    downloadedSelectedPath = release.relative_path;
    const cached = downloadedDetailCache[release.relative_path];
    if (cached) downloadedSelectedRelease = cached;
    else if (!visibleDetail) downloadedSelectedRelease = null;
    downloadedSelectedReleaseLoading = !cached && !visibleDetail;
    if (cached) return;
    try {
      const raw = await GetDownloadedRelease(release.relative_path);
      const parsed = measureFrontendPerformance('downloaded-payload-parse', () => JSON.parse(raw || '{}'));
      if (requestId !== downloadedDetailRequestId || currentPage !== 'downloaded' || downloadedSelectedPath !== release.relative_path) return;
      if (parsed?.error) {
        downloadedLibraryError = parsed.error;
        return;
      }
      measureFrontendPerformance('downloaded-payload-apply', () => {
        downloadedSelectedRelease = parsed;
        downloadedDetailCache[release.relative_path] = parsed as LibraryReleaseDetail;
        downloadedView = release.kind === 'playlist' ? 'playlists' : 'albums';
      });
    } catch (e: any) {
      if (requestId !== downloadedDetailRequestId) return;
      downloadedLibraryError = String(e);
    } finally {
      if (requestId === downloadedDetailRequestId) downloadedSelectedReleaseLoading = false;
    }
  }

  async function playDownloadedTrack(index: number) {
    if (!downloadedSelectedRelease?.tracks?.length || !playerBarEl) return;
    await playerBarEl.playQueue(
      downloadedSelectedRelease.tracks,
      index,
      downloadedSelectedRelease.title,
      downloadedSelectedRelease.artwork_url || '',
    );
  }

  function handlePlayerVolumeChange(event: CustomEvent<{ volume: number }>) {
    const nextVolume = Math.min(1, Math.max(0, Number(event.detail.volume)));
    if (!Number.isFinite(nextVolume) || Math.abs(nextVolume - uiPreferences.player_volume) < 0.005) return;
    uiPreferences = { ...uiPreferences, player_volume: nextVolume };
    syncUIPreferencesToConfig();
    void autoSaveSettings();
  }

  async function pickDir() {
    const dir = await PickDirectory();
    if (dir) {
      config.download_path = dir;
      if (!setupMode) await autoSaveSettings();
    }
  }

  async function useSuggestedDownloadLocation(kind: 'music' | 'downloads') {
    const location = await GetSuggestedDownloadLocation(kind);
    if (!location) return;
    config.download_path = location;
    config.download_path_is_library_root = true;
    if (!setupMode) await autoSaveSettings();
  }

  function closeDownloadedRelease() {
    downloadedDetailRequestId++;
    downloadedSelectedRelease = null;
    downloadedSelectedPath = '';
    downloadedSelectedReleaseLoading = false;
  }

  async function forceRefreshDownloadedMusicLibrary() {
    const requestId = ++downloadedLibraryRequestId;
    downloadedIndexing = true;
    downloadedIndexPercent = 0;
    try {
      const raw = await RefreshDownloadedMusicLibrary();
      const parsed = measureFrontendPerformance('downloaded-payload-parse', () => JSON.parse(raw || '{}'));
      if (requestId !== downloadedLibraryRequestId) return;
      const selected = applyDownloadedLibrarySnapshot(parsed);
      if (selected && currentPage === 'downloaded') void openDownloadedRelease(selected);
    } catch (e: any) {
      if (requestId !== downloadedLibraryRequestId) return;
      downloadedLibraryError = String(e);
      downloadedIndexing = false;
    }
  }

  async function saveSetup() {
    if (!config.download_path) {
      showToast('Please select your Music Library folder.', 'warning');
      return;
    }
    await saveConfigSerialized();
    setupMode = false;
  }

  async function startDownload() {
    if (!inputUrl) return;

    // Accept one URL per line, or comma-separated, or both
    let urls = inputUrl
      .split(/[\n,]+/)
      .map(s => s.trim())
      .filter(s => s.startsWith('http') || s.startsWith('apple-music://'));
    if (urls.length === 0) return;

    const isArtistUrl = (u: string) => {
      const norm = u.replace(/spotify\.com\/intl-[a-z]+\//, 'spotify.com/');
      return norm.includes('spotify.com/artist/') ||
        (u.includes('music.apple.com') && u.includes('/artist/')) ||
        (u.includes('music.amazon.com') && u.includes('/artists/'));
    };
    const artistUrls = urls.filter(isArtistUrl);
    const otherUrls = urls.filter(u => !isArtistUrl(u));

    if (otherUrls.length > 0) {
      const stamp = Date.now();
      const target = writableIPodDevices.find(device => device.device_id === pendingDownloadIPodDestinationId);
      downloadJobs = [...downloadJobs, ...otherUrls.map((url, index) => ({
        id: `${stamp}-${index}`,
        url,
        title: url,
        status: 'waiting' as const,
        total: 0,
        completed: 0,
        ipodDestinationId: target?.device_id,
        ipodDestinationPath: target?.path,
        completedFiles: [],
      }))];
      pendingDownloadIPodDestinationId = '';
      persistDownloadQueue();
      if (uiPreferences.open_downloads_on_add) {
        currentPage = 'downloads';
        rememberCurrentDestination();
      }
      inputUrl = '';
      if (!isDownloading && !queuePaused) await startNextQueuedJob();
    }

    for (const artistUrl of artistUrls) {
      const reqId = ++discographyReqId;
      discographyLoading = true;
      showDiscography = true;
      discographyArtist = null;
      discographySelected = new Set();
      inputUrl = '';
      try {
        const raw = await GetArtistDiscography(artistUrl);
        // If user closed the modal while loading, reqId won't match — ignore result
        if (reqId !== discographyReqId) break;
        const parsed = JSON.parse(raw);
        if (parsed?.error) {
          addLog('error', `Discography error: ${parsed.error}`);
          showDiscography = false;
        } else {
          discographyArtist = {
            ...parsed,
            albums: dedupeDiscographyAlbums(Array.isArray(parsed?.albums) ? parsed.albums : []),
          };
          if (discographyArtist?.albums) {
            discographySelected = new Set(discographyArtist.albums.map((a: any) => a.url));
          }
        }
      } catch (e) {
        if (reqId === discographyReqId) {
          addLog('error', `Failed to fetch discography: ${e}`);
          showDiscography = false;
        }
      } finally {
        if (reqId === discographyReqId) discographyLoading = false;
      }
    }
  }

  async function startArtistSearch() {
    if (!searchQuery.trim()) return;
    const reqId = ++artistSearchReqId;
    artistSearchLoading = true;
    showArtistSearch = true;
    artistSearchResults = [];
    try {
      const raw = await SearchArtists(searchQuery.trim(), searchSource);
      if (reqId !== artistSearchReqId) return;
      const parsed = JSON.parse(raw);
      if (parsed?.error) {
        addLog('error', `Artist search error: ${parsed.error}`);
        showArtistSearch = false;
      } else {
        artistSearchResults = Array.isArray(parsed) ? parsed : [];
      }
    } catch (e) {
      if (reqId === artistSearchReqId) {
        addLog('error', `Artist search failed: ${e}`);
        showArtistSearch = false;
      }
    } finally {
      if (reqId === artistSearchReqId) artistSearchLoading = false;
    }
  }

  async function downloadSelectedDiscography() {
    const albumUrls = [...discographySelected];
    if (!albumUrls.length) return;
    showDiscography = false;
    if (uiPreferences.open_downloads_on_add) currentPage = 'downloads';
    inputUrl = albumUrls.join('\n');
    await startDownload();
  }

  // Open the discography modal directly from a Spotify artist URL
  // (used by the Followed Artists cards in the My Library tab).
  async function openArtistFromUrl(profileUrl: string) {
    if (!profileUrl) return;
    await openArtistFromSearch({ profile_url: profileUrl });
  }

  async function openArtistFromSearch(artist: any) {
    showArtistSearch = false;
    activeTab = 'url';
    const reqId = ++discographyReqId;
    discographyLoading = true;
    showDiscography = true;
    discographyArtist = null;
    discographySelected = new Set();
    try {
      const raw = await GetArtistDiscography(artist.profile_url);
      if (reqId !== discographyReqId) return;
      const parsed = JSON.parse(raw);
      if (parsed?.error) {
        addLog('error', `Discography error: ${parsed.error}`);
        showDiscography = false;
      } else {
        discographyArtist = {
          ...parsed,
          albums: dedupeDiscographyAlbums(Array.isArray(parsed?.albums) ? parsed.albums : []),
        };
        if (discographyArtist?.albums) {
          discographySelected = new Set(discographyArtist.albums.map((a: any) => a.url));
        }
      }
    } catch (e) {
      if (reqId === discographyReqId) {
        addLog('error', `Failed to fetch discography: ${e}`);
        showDiscography = false;
      }
    } finally {
      if (reqId === discographyReqId) discographyLoading = false;
    }
  }

  async function cancelDownload() {
    // A queue may be populated before the backend process reports itself as
    // active. Cancellation must still clear that queued-only state.
    if (!isDownloading && !downloadJobs.some(job => ['waiting', 'paused', 'downloading'].includes(job.status))) return;
    if (!await requestConfirmation('Cancel all downloads?', 'The active download and every waiting or paused job will be removed from the queue.', 'Cancel downloads')) return;
    try {
      if (isDownloading) {
        await CancelDownload();
        addLog('warning', 'Library build cancelled.');
      }
      isDownloading = false;
      queuePaused = false;
      downloadJobs = downloadJobs.map(job => ['waiting', 'paused', 'downloading'].includes(job.status) ? { ...job, status: 'cancelled' } : job);
      persistDownloadQueue();
      resetTrackActivity();
      trackLabels = {};
      currentPlaylistTrackKeysByIndex = {};
      trackKeysByStableId = {};
      currentPlaylistTrackCount = 0;
    } catch (err) {
      console.error(err);
    }
  }

  async function openHistory() {
    try {
      replaceHistoryItems(await GetHistory() || []);
    } catch (e) {
      console.error(e);
      replaceHistoryItems([]);
    }
  }

  async function clearHistory() {
    if(await requestConfirmation('Clear download history?', 'Completed job records will be removed. Downloaded music files will stay in your library.', 'Clear history')) {
      await ClearHistory();
      historyBuffer.clear();
      historyItems = [];
    }
  }

  function validateSettings(): string {
    if (!config.max_retries || config.max_retries < 1 || config.max_retries > 20) return 'Retries must be between 1 and 20.';
    if (!config.max_concurrent_jobs || config.max_concurrent_jobs < 1 || config.max_concurrent_jobs > workerCeiling) return `Concurrent downloads must be between 1 and ${workerCeiling}.`;
    if (!/^[a-z]{2}$/i.test(config.apple_storefront || '')) return 'Apple storefront must be a two-letter country code.';
    return '';
  }

  function resetActiveJobView() {
    logBuffer.clear();
    logs = [];
    trackLabels = {};
    playlistTitle = '';
    playlistArtwork = '';
    playlistArtists = '';
    playlistReleaseDate = '';
    playlistContentType = '';
    playlistQualityBadge = '';
    playlistTotalDurationMs = 0;
    playlistTotalTracks = 0;
    resetTrackActivity();
    currentPlaylistTrackKeysByIndex = {};
    trackKeysByStableId = {};
    currentPlaylistTrackCount = 0;
    currentPlaylistKeyPrefix = `track:playlist-${++playlistKeySequence}`;
    jobPreparationStatus = 'Reading release information…';
  }

  async function startNextQueuedJob() {
    if (isDownloading || queuePaused) return;
    const next = downloadJobs.findIndex(job => job.status === 'waiting' || job.status === 'paused');
    if (next < 0) return;
    resetActiveJobView();
    downloadJobs[next] = { ...downloadJobs[next], status: 'downloading' };
    downloadJobs = [...downloadJobs];
    persistDownloadQueue();
    isDownloading = true;
    jobPreparationStatus = 'Starting the local download engine…';
    addLog('info', 'Preparing download…');
    try {
      await StartDownload([downloadJobs[next].url]);
    } catch (err) {
      downloadJobs[next] = { ...downloadJobs[next], status: 'failed' };
      downloadJobs = [...downloadJobs];
      isDownloading = false;
      persistDownloadQueue();
      addLog('error', `Library engine error: ${err}`);
      scheduleTimeout(startNextQueuedJob);
    }
  }

  async function toggleQueuePause(event?: MouseEvent) {
    event?.stopPropagation();
    queuePaused = !queuePaused;
    persistDownloadQueue();
    if (queuePaused) await PauseDownload();
    else {
      await ResumeDownload();
      if (!isDownloading) await startNextQueuedJob();
    }
  }

  function moveQueuedJob(jobId: string, direction: -1 | 1) {
    const pendingIndexes = downloadJobs
      .map((job, index) => ({ job, index }))
      .filter(item => item.job.status === 'waiting' || item.job.status === 'paused');
    const position = pendingIndexes.findIndex(item => item.job.id === jobId);
    const swapPosition = position + direction;
    if (position < 0 || swapPosition < 0 || swapPosition >= pendingIndexes.length) return;
    const from = pendingIndexes[position].index;
    const to = pendingIndexes[swapPosition].index;
    [downloadJobs[from], downloadJobs[to]] = [downloadJobs[to], downloadJobs[from]];
    downloadJobs = [...downloadJobs];
    persistDownloadQueue();
  }

  async function runQueuedJobNow(jobId: string) {
    const target = downloadJobs.find(job => job.id === jobId && (job.status === 'waiting' || job.status === 'paused'));
    if (!target) return;
    downloadJobs = [target, ...downloadJobs.filter(job => job.id !== jobId)];
    queuePaused = false;
    priorityJobId = jobId;
    persistDownloadQueue();
    if (isDownloading) {
      await PauseDownload();
    } else {
      priorityJobId = '';
      await ResumeDownload();
      await startNextQueuedJob();
    }
  }

  async function removeQueuedJob(jobId: string) {
    const job = downloadJobs.find(item => item.id === jobId);
    if (!job || !await requestConfirmation('Remove queued download?', `Remove “${job.title}” from the queue?`, 'Remove')) return;
    downloadJobs = downloadJobs.filter(item => item.id !== jobId);
    persistDownloadQueue();
  }

  let settingsSaveTimer: TimerHandle | null = null;
  let settingsSaveChain: Promise<void> = Promise.resolve();
  let pendingSaveResolvers: Array<() => void> = [];
  let lastAppliedWorkerCount = 2;

  function configSnapshot(): main.Config {
    return JSON.parse(JSON.stringify(config)) as main.Config;
  }

  function saveConfigSerialized(snapshot = configSnapshot()): Promise<void> {
    settingsSaveChain = settingsSaveChain.catch(() => {}).then(() => SaveConfig(snapshot));
    return settingsSaveChain;
  }

  function flushSettingsSave() {
    settingsSaveTimer = null;
    const snapshot = configSnapshot();
    const nextWorkerCount = snapshot.max_concurrent_jobs || 2;
    const resolvers = pendingSaveResolvers;
    pendingSaveResolvers = [];
    settingsSaveChain = settingsSaveChain.catch(() => {}).then(async () => {
      await SaveConfig(snapshot);
      if (nextWorkerCount !== lastAppliedWorkerCount) {
        await SetDownloadWorkerCount(nextWorkerCount);
        lastAppliedWorkerCount = nextWorkerCount;
      }
      workerConfigured = nextWorkerCount;
      settingsSaveState = 'saved';
      scheduleTimeout(() => { if (settingsSaveState === 'saved') settingsSaveState = 'idle'; }, 1500);
    }).catch((e: any) => {
      settingsError = e?.message || String(e);
      settingsSaveState = 'error';
    }).finally(() => {
      resolvers.forEach(resolve => resolve());
    });
  }

  function autoSaveSettings(): Promise<void> {
    settingsError = validateSettings();
    if (settingsError) {
      settingsSaveState = 'error';
      return Promise.resolve();
    }
    settingsSaveState = 'saving';
    syncUIPreferencesToConfig();
    if (settingsSaveTimer) cancelScheduledTimeout(settingsSaveTimer);
    settingsSaveTimer = scheduleTimeout(flushSettingsSave, 180);
    return new Promise(resolve => pendingSaveResolvers.push(resolve));
  }

  async function openFolderSettings(event?: MouseEvent) {
    event?.stopPropagation();
    if (folderSettingsSaving) return;
    focusedTemplateEl = null;
    showFolderSettings = true;
    await tick();
  }

  function closeFolderSettings() {
    if (folderSettingsSaving) return;
    focusedTemplateEl = null;
    showFolderSettings = false;
  }

  async function saveFolderSettings() {
    if (folderSettingsSaving) return;
    folderSettingsSaving = true;
    focusedTemplateEl = null;
    try {
      await saveConfigSerialized();
      showFolderSettings = false;
    } finally {
      folderSettingsSaving = false;
    }
  }

  async function openSettings(section = 'settings-general') {
    const map: Record<string, SettingsPage> = {
      'settings-general': 'general', 'settings-apple': 'apple', 'settings-downloads': 'downloads',
      'settings-audio': 'audio', 'settings-discovery': 'discovery', 'settings-naming': 'naming',
      'settings-providers': 'providers', 'settings-appearance': 'appearance',
    };
    settingsPage = map[section] || 'general';
    showSettings = true;
  }

  async function validateTidalSettings() {
    tidalValidationLoading = true;
    tidalValidationStatus = null;
    try {
      // Sanitize tidal_session_json: strip all control characters, normalize whitespace,
      // re-serialize to ensure clean JSON before saving to disk.
      if (config.tidal_session_json && config.tidal_session_json.trim()) {
        try {
          const cleaned = config.tidal_session_json
            .replace(/[\r\n\t]/g, ' ')          // replace CR/LF/tabs with space
            .replace(/[\u0000-\u001F\u007F]/g, '') // strip remaining control chars
            .replace(/\s+/g, ' ')               // collapse runs of spaces
            .trim();
          const parsed = JSON.parse(cleaned);
          config.tidal_session_json = JSON.stringify(parsed); // re-serialize to compact, clean JSON
        } catch (parseErr) {
          tidalValidationStatus = { ok: false, message: `Invalid session JSON: ${String(parseErr)}` };
          tidalValidationLoading = false;
          return;
        }
      }
      await saveConfigSerialized();
      const raw = await ValidateTidalAuth();
      tidalValidationStatus = JSON.parse(raw);
    } catch (e) {
      tidalValidationStatus = { ok: false, message: String(e) };
    } finally {
      tidalValidationLoading = false;
    }
  }

  async function startTidalOAuth() {
    tidalOAuth = { phase: 'starting', message: 'Connecting to TIDAL...' };
    tidalValidationStatus = null;
    try {
      await StartTidalOAuthLogin();
    } catch (e) {
      tidalOAuth = { phase: 'error', message: `Failed to start OAuth: ${e}` };
    }
  }

  async function startAppleLogin() {
    appleLogin = { phase: 'starting', message: 'Opening Apple Music login...' };
    try {
      await saveConfigSerialized();
      await StartAppleBrowserLogin();
    } catch (e) {
      appleLogin = { phase: 'error', message: `Failed to start Apple Music login: ${e}` };
    }
  }

  async function startSpotifyDownloaderCapture() {
    spDcCapture = { phase: 'starting', message: 'Opening Spotify login…' };
    try {
      await CaptureSpDC();
    } catch (e) {
      spDcCapture = { phase: 'error', message: `Failed to start Spotify login: ${e}` };
    }
  }

  async function confirmAmazonBrowserLogin() {
    amazonLogin = { phase: 'capturing', message: 'Reading your local browser session…' };
    try {
      await ConfirmAmazonLogin();
    } catch (e) {
      amazonLogin = { phase: 'error', message: `Failed to capture Amazon session: ${e}` };
    }
  }

  // Parse the Amazon session capture time from the credentials JSON.
  // csrf_ts is a Unix timestamp (seconds) of when the session was captured.
  // Amazon Atna tokens typically expire within ~24 hours of capture.
  function amazonSessionInfo(): { capturedAt: string; expiresNote: string } | null {
    if (!config.amazon_direct_creds_json) return null;
    try {
      const creds = JSON.parse(config.amazon_direct_creds_json);
      if (!creds.authorization || !creds.csrf_token) return null;
      const csrfTs = parseInt(creds.csrf_ts || '0', 10);
      if (!csrfTs) return null;
      const capturedDate = new Date(csrfTs * 1000);
      const expiresDate = new Date((csrfTs + 86400) * 1000); // +24h estimate
      const now = Date.now();
      const msLeft = expiresDate.getTime() - now;
      const capturedAt = capturedDate.toLocaleString();
      let expiresNote: string;
      if (msLeft <= 0) {
        expiresNote = 'Session likely expired — re-login recommended.';
      } else {
        const hLeft = Math.floor(msLeft / 3600000);
        const mLeft = Math.floor((msLeft % 3600000) / 60000);
        expiresNote = hLeft > 0
          ? `Expires in ~${hLeft}h ${mLeft}m (est.)`
          : `Expires in ~${mLeft}m (est.)`;
      }
      return { capturedAt, expiresNote };
    } catch {
      return null;
    }
  }

  async function startAmazonLogin() {
    amazonLogin = { phase: 'starting', message: 'Opening Amazon Music login...' };
    try {
      await saveConfigSerialized();
      await StartAmazonBrowserLogin();
    } catch (e) {
      amazonLogin = { phase: 'error', message: `Failed to start Amazon Music login: ${e}` };
    }
  }

</script>

{#if isLoading}
  <main class="launch-screen" aria-label="Loading Vela">
    <strong class="launch-name">Vela</strong>
    <div class="spinner"></div>
  </main>
{:else if setupMode}
  <main class="setup-screen">
    <section class="setup-card">
      <p class="eyebrow">Welcome to Vela</p>
      <h1>Build a library that stays yours.</h1>
      <p class="lede">Choose where your music should live. You can change the location and naming rules later.</p>
      <label class="field-label" for="setup-library">Music library folder</label>
      <div class="input-row">
        <input id="setup-library" readonly value={config.download_path} placeholder="Choose a folder…" />
        <button class="secondary" on:click={pickDir}>Choose</button>
      </div>
      <button class="primary setup-continue" disabled={!config.download_path} on:click={saveSetup}>Continue</button>
    </section>
  </main>
{:else}
  <svg class="icon-sprite" aria-hidden="true">
    <symbol id="i-library" viewBox="0 0 24 24"><path d="M4 5h16v14H4zM8 5v14M12 9h5M12 13h5"/></symbol>
    <symbol id="i-plus" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></symbol>
    <symbol id="i-artist" viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4.5 20c.8-4 3.3-6 7.5-6s6.7 2 7.5 6"/></symbol>
    <symbol id="i-discover" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2 5-5 2 2-5z"/></symbol>
    <symbol id="i-download" viewBox="0 0 24 24"><path d="M12 3v12m-4-4 4 4 4-4M5 20h14"/></symbol>
    <symbol id="i-history" viewBox="0 0 24 24"><path d="M4 5v5h5M5 10a8 8 0 1 1 2 7M12 7v5l3 2"/></symbol>
    <symbol id="i-settings" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.4-2.4 1A7 7 0 0 0 15 6l-.3-2.6h-4L10.4 6a7 7 0 0 0-1.6 1L6.5 6 4.5 9.5l2 1.5a7 7 0 0 0 0 2l-2 1.5 2 3.4 2.4-1a7 7 0 0 0 1.6 1l.3 2.6h4l.3-2.6a7 7 0 0 0 1.6-1l2.4 1 2-3.4-2-1.5a7 7 0 0 0 .1-1z"/></symbol>
    <symbol id="i-queue" viewBox="0 0 24 24"><path d="M4 6h12M4 11h12M4 16h8M19 14v6m-3-3h6"/></symbol>
    <symbol id="i-play" viewBox="0 0 24 24"><path d="m9 6 9 6-9 6z"/></symbol>
    <symbol id="i-search" viewBox="0 0 24 24"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></symbol>
  </svg>

  <div class="app-shell" use:dismissMenusOnPointerDown>
    <aside class="sidebar">
      <nav aria-label="Main navigation">
        <p class="nav-label library-label" on:contextmenu={openLibraryNavMenu}>Library{#if !config.apple_music_user_token}<WifiOff size={13} class="offline-icon" />{/if}</p>
        <div class="library-subnav">
          <button class:active={currentPage === 'library' && libraryView === 'recent'} on:click={() => openLibraryView('recent')}><Clock3 size={17}/><span>Recently Added</span></button>
          <button class:active={currentPage === 'library' && libraryView === 'albums'} on:click={() => openLibraryView('albums')}><Album size={17}/><span>Albums</span></button>
          <button class:active={currentPage === 'library' && libraryView === 'playlists'} on:click={() => openLibraryView('playlists')}><ListMusic size={17}/><span>Playlists</span></button>
          <button class:active={currentPage === 'library' && libraryView === 'favourites'} on:click={openFavourites}><Star size={17} class="favourite-nav-icon"/><span>Favourites</span></button>
          <button class:active={currentPage === 'library' && libraryView === 'artists'} on:click={() => openLibraryView('artists')}><UserRound size={17}/><span>Artists</span></button>
          <button class:active={currentPage === 'downloaded'} on:click={() => selectPage('downloaded')}><HardDriveDownload size={17}/><span>Downloaded</span></button>
        </div>
        {#each ipodDevices as device (`${device.device_id}|${device.path}`)}<button class:active={currentPage === 'devices' && selectedIPodId === device.device_id} on:click={() => openIPodDevice(device)}><Smartphone size={18}/><span>{device.name || 'iPod'}</span></button>{/each}

      </nav>

      <div class="sidebar-footer">
        {#if appleIndexing || downloadedIndexing}<div class="sidebar-index" title={downloadedIndexing ? downloadedIndexLabel : appleIndexLabel}><span><span class="rotating-loader" aria-hidden="true"><LoaderCircle size={13}/></span>{downloadedIndexing ? `Indexing downloads · ${Math.round(downloadedIndexPercent)}%` : `Indexing library · ${Math.round(appleIndexPercent)}%`}</span><progress max="100" value={downloadedIndexing ? downloadedIndexPercent : appleIndexPercent}></progress></div>{/if}
        <button class="settings-nav" class:active={currentPage === 'downloads'} on:click={() => selectPage('downloads')}><Download size={18}/><span>Downloads</span></button>
        <button class="settings-nav" class:active={showSettings} on:click={() => openSettings()}><Settings size={18}/><span>Settings</span></button>
      </div>
    </aside>

    <section class="workspace">
      {#if !(currentPage === 'library' && libraryView === 'favourites')}
      <header class="topbar">
        <div>
          {#if currentPage === 'library' && showAppleLibraryDetail && libraryView !== 'favourites'}<button class="topbar-back" aria-label="Back to library" on:click={closeAppleLibraryDetail}><ArrowLeft size={20}/></button>{/if}
          {#if currentPage === 'downloaded' && downloadedSelectedRelease}<button class="topbar-back" aria-label="Back to downloaded music" on:click={closeDownloadedRelease}><ArrowLeft size={20}/></button>{/if}
          <p class="eyebrow">{currentPage === 'downloads' ? 'Activity' : currentPage === 'downloaded' ? 'On This Device' : ''}</p>
          <h1>{currentPage === 'library' ? ({ recent: 'Recently Added', albums: 'Albums', playlists: 'Playlists', favourites: 'Favourite Songs', artists: 'Artists' }[libraryView]) : currentPage === 'downloads' ? 'Downloads' : currentPage === 'downloaded' ? 'Downloaded' : currentPage === 'devices' ? (selectedIPodDevice?.name || 'iPod devices') : 'Settings'}</h1>
        </div>
        <div class="top-actions">
          {#if uiDemoMode}<span class="demo-badge">UI Preview</span>{/if}
          {#if currentPage === 'library' && !config.apple_music_user_token}<span class="demo-badge"><WifiOff size={14}/> Disconnected</span>{/if}
          {#if currentPage === 'downloads'}<button class="primary compact add-custom-button" on:click={() => { customDestination = config.download_path; showCustomDownload = true; }}><Plus size={16}/><span>Add custom</span></button>{/if}
        </div>
      </header>
      {/if}

      <main class="page-content">
        {#if currentPage === 'library'}
          {#if showAppleLibraryDetail && appleLibraryDetail}
            <section class="library-detail-page">
              {#if appleLibraryDetail.image_url}<div class="library-detail-backdrop" aria-hidden="true"><ArtworkImage src={appleLibraryDetail.image_url} displaySize={384} loading="lazy" fetchPriority="low" /></div>{:else}<div class="library-detail-backdrop favourite-backdrop"></div>{/if}
              <div class="library-detail-hero">
                <div class:favourite-art={libraryView === 'favourites' && !appleLibraryDetail.image_url} class="library-detail-art"><ArtworkImage src={appleLibraryDetail.image_url} displaySize={384} loading="eager" fetchPriority="high">{#if appleLibraryDetail.content_type === 'artist'}<UserRound size={76}/>{:else if libraryView === 'favourites'}<Star size={82}/>{:else}<Album size={72}/>{/if}</ArtworkImage></div>
                <div class="library-detail-copy"><p class="eyebrow">{appleLibraryDetail.content_type || 'Apple Music library'}</p><h2>{appleLibraryDetail.name}</h2><strong>{appleLibraryDetail.track_count || appleLibraryDetail.tracks.length} songs</strong><span>{appleLibraryDetail.content_type === 'artist' ? 'Songs by this artist in your library' : 'Stored in your local Vela index'}</span>{#if appleLibraryDetail.content_type !== 'artist'}<div class="detail-primary-actions"><button class="primary detail-download" on:click={downloadOpenApplePlaylist}><Download size={17}/><span>Download</span></button><div class="tool-menu"><button class="icon-button" aria-label="More release options" on:click={() => showDetailMenu = !showDetailMenu}><MoreHorizontal size={18}/></button>{#if showDetailMenu}<div class="context-menu detail-popover"><button on:click={() => { showDetailMenu = false; downloadOpenApplePlaylist(); }}><Download size={16}/> Download release</button></div>{/if}</div></div>{/if}</div>
              </div>
              <div class="detail-track-tools"><div class="search-bar detail-search"><Search size={16}/><input bind:value={libraryDetailFilter} placeholder="Search songs" /></div><select bind:value={libraryDetailSort} aria-label="Sort songs"><option value="position">Playlist order</option><option value="title">Title</option><option value="artist">Artist</option><option value="album">Album</option></select><button class="secondary detail-order" on:click={() => libraryDetailDescending = !libraryDetailDescending}><ArrowUpDown size={16}/>{libraryDetailDescending ? 'Descending' : 'Ascending'}</button></div>
              {#if appleLibraryDetailError}<div class="detail-inline-error">{appleLibraryDetailError}</div>{/if}
              {#if appleLibraryDetailLoading && !appleLibraryDetail.tracks.length}<p class="detail-index-note">{appleLibrary?.index_complete ? 'Opening cached songs…' : (appleIndexing ? 'Indexing this release locally…' : 'Opening indexed songs…')}</p>{/if}
              {#if visibleLibraryDetailTracks.length}
                <VirtualList
                  items={visibleLibraryDetailTracks}
                  itemKey={appleTrackKey}
                  rowHeight={63}
                  maxHeight="62vh"
                  viewportClass="library-detail-tracks"
                  restoreKey={`apple:${appleLibraryDetail.url || appleLibraryDetail.name}:${libraryDetailFilter}:${libraryDetailSort}:${libraryDetailDescending}`}
                  ariaLabel={`Songs in ${appleLibraryDetail.name}`}
                  let:item
                  let:index
                >
                  <article>
                    <span class="detail-track-number">{index + 1}</span>
                    <span class="detail-track-placeholder"><ArtworkImage src={item.artwork_url} displaySize={64} fetchPriority="low"><Album size={17}/></ArtworkImage></span>
                    <strong>{item.title}</strong><span>{item.artist}</span><span>{item.album}</span>
                    <small>{formatPlaybackTime((item.duration_ms || 0) / 1000)}</small>
                    <div class="track-more tool-menu"><button aria-label={`More options for ${item.title}`} on:click={() => detailTrackMenuIndex = detailTrackMenuIndex === index ? null : index}><MoreHorizontal size={17}/></button>{#if detailTrackMenuIndex === index}<div class="context-menu track-popover"><button on:click={() => { detailTrackMenuIndex = null; downloadOpenApplePlaylist(); }}><Download size={16}/> Download release</button></div>{/if}</div>
                  </article>
                </VirtualList>
              {/if}
            </section>
          {:else}
          <div class="library-tools">
            <div class="search-bar compact-search"><Search size={17}/><input bind:value={libraryFilter} placeholder="Search your indexed library" /></div>
            <div class="tool-menu"><button class="library-tool-button" title="Sort" aria-label="Sort library" on:click={() => { showSortMenu = !showSortMenu; showFilterMenu = false; }}><ArrowUpDown size={17}/></button>{#if showSortMenu}<div class="context-menu tool-popover"><button on:click={() => { librarySort = 'recent'; showSortMenu = false; }}><Clock3 size={15}/> Recently added</button><button on:click={() => { librarySort = 'title'; showSortMenu = false; }}><ArrowUpDown size={15}/> Title</button><button on:click={() => { librarySort = 'artist'; showSortMenu = false; }}><UserRound size={15}/> Artist</button><button on:click={() => { librarySortDirection = librarySortDirection === 'ascending' ? 'descending' : 'ascending'; showSortMenu = false; }}><ArrowUpDown size={15}/> {librarySortDirection === 'ascending' ? 'Reverse order' : 'Use ascending order'}</button></div>{/if}</div>
            <div class="tool-menu"><button class="library-tool-button" title="Filter" aria-label="Filter library" on:click={() => { showFilterMenu = !showFilterMenu; showSortMenu = false; }}><SlidersHorizontal size={18}/></button>{#if showFilterMenu}<div class="context-menu tool-popover"><button on:click={() => { libraryKindFilter = 'all'; showFilterMenu = false; }}>All</button><button on:click={() => { libraryView = 'albums'; libraryKindFilter = 'albums'; showFilterMenu = false; }}><Album size={15}/> Albums</button><button on:click={() => { libraryView = 'playlists'; libraryKindFilter = 'playlists'; showFilterMenu = false; }}><ListMusic size={15}/> Playlists</button></div>{/if}</div>
          </div>
          {#if selectedLibraryItems.size}<div class="selection-toolbar"><strong>{selectedLibraryItems.size} selected</strong><button class="primary compact" on:click={downloadSelectedLibraryItems}><Download size={16}/> Download</button><button class="icon-button" aria-label="More actions"><MoreHorizontal size={18}/></button><button class="icon-button" aria-label="Clear selection" on:click={() => selectedLibraryItems = new Set()}><X size={17}/></button></div>{/if}

          {#if !config.apple_music_user_token || !config.apple_authorization_token}
            <section class="notice-card privacy-card">
              <div class="notice-icon">♪</div>
              <div><h3>Connect Apple Music</h3><p>Your Apple authorization and Music User Token are stored locally and used only for Apple endpoints. Vela does not upload them to a mirror or shared service.</p></div>
              <button class="primary" on:click={startAppleLogin} disabled={appleLogin.phase === 'starting'}>{appleLogin.phase === 'starting' ? 'Opening…' : 'Connect'}</button>
            </section>
          {:else if appleLibraryLoading}
            <div class="state-card"><div class="spinner"></div><p>Loading your Apple Music library…</p></div>
          {:else if appleLibraryError}
            <div class="state-card error"><h3>Library unavailable</h3><p>{appleLibraryError}</p><button class="secondary" on:click={() => loadAppleMusicLibrary()}>Try again</button></div>
          {:else if appleLibrary}
            {#if libraryView === 'recent' || libraryView === 'albums'}<section class="section-block">
              <div class="section-heading"><div><h2>Albums</h2></div><span>{appleLibrary.albums?.length || 0} albums · {appleLibrary.saved_songs_count} songs</span></div>
              <div class="art-grid">
                <ProgressiveCollection items={visibleLibraryAlbums} itemKey={appleAlbumKey} initialCount={24} chunkSize={24} itemLabel="albums" ariaLabel="Apple Music albums" let:item let:index>
                  <article class:selected={selectedLibraryItems.has(item.url)} class="music-card selectable" on:contextmenu={(event) => openLibraryItemMenu(event, item)}><button class="select-release" class:selected={selectedLibraryItems.has(item.url)} aria-label={`Select ${item.name}`} on:click|stopPropagation={() => toggleLibrarySelection(item.url)}>{#if selectedLibraryItems.has(item.url)}<Check size={15}/>{/if}</button><button class="artwork" on:click={() => openAppleLibraryDetail(item.url, item.name, item.image_url || '')}><ArtworkImage src={item.image_url} displaySize={Math.min(512, Math.round(uiPreferences.artwork_size * 2))} loading={index < 8 ? 'eager' : 'lazy'} fetchPriority={index < 8 ? 'high' : 'low'}><Library size={34}/></ArtworkImage></button><div class="card-copy"><strong title={item.name}>{item.name}</strong><span>{item.artist_name} · {item.track_count} songs</span></div></article>
                </ProgressiveCollection>
              </div>
            </section>{/if}
            {#if libraryView === 'recent' || libraryView === 'playlists'}<section class="section-block"><div class="section-heading"><div><h2>Playlists</h2></div><span>{appleLibrary.playlists.length} playlists</span></div><div class="art-grid"><ProgressiveCollection items={visibleLibraryPlaylists} itemKey={applePlaylistKey} initialCount={24} chunkSize={24} itemLabel="playlists" ariaLabel="Apple Music playlists" let:item let:index><article class:selected={selectedLibraryItems.has(item.url)} class="music-card selectable" on:contextmenu={(event) => openLibraryItemMenu(event, item)}><button class="select-release" class:selected={selectedLibraryItems.has(item.url)} aria-label={`Select ${item.name}`} on:click|stopPropagation={() => toggleLibrarySelection(item.url)}>{#if selectedLibraryItems.has(item.url)}<Check size={15}/>{/if}</button><button class="artwork" on:click={() => openAppleLibraryDetail(item.url, item.name, item.image_url || '')}><ArtworkImage src={item.image_url} displaySize={Math.min(512, Math.round(uiPreferences.artwork_size * 2))} loading={libraryView === 'playlists' && index < 8 ? 'eager' : 'lazy'} fetchPriority={libraryView === 'playlists' && index < 8 ? 'high' : 'low'}><ListMusic size={34}/></ArtworkImage></button><div class="card-copy"><strong title={item.name}>{item.name}</strong><span>{item.track_count ? `${item.track_count} songs` : 'Indexed playlist'}</span></div></article></ProgressiveCollection></div></section>{/if}
            {#if libraryView === 'favourites'}<section class="section-block">{#if favouriteSongsPlaylist()}<div class="art-grid"><article class="music-card featured-card"><button class="artwork gradient-art" on:click={openFavourites}><Star size={42}/></button><div class="card-copy"><strong>{favouriteSongsPlaylist()?.name}</strong><span>{favouriteSongsPlaylist()?.track_count || 0} songs</span></div></article></div>{:else}<div class="state-card"><Star size={34} class="favourite-empty-icon"/><h3>Favourite Songs wasn’t found</h3><p>Open Apple Music once so its automatic Favourite Songs playlist is available, then refresh the library index.</p></div>{/if}</section>{/if}
            {#if libraryView === 'artists'}<VirtualList items={visibleLibraryArtists} itemKey={appleArtistKey} rowHeight={65} maxHeight="68vh" viewportClass="artist-library-list" restoreKey={`apple-artists:${libraryFilter}:${librarySortDirection}`} ariaLabel="Apple Music artists" let:item let:index><button on:click={() => openAppleArtistDetail(item.name, item.image || '')}><span class="artist-avatar"><ArtworkImage src={item.image} displaySize={96} loading={index < 10 ? 'eager' : 'lazy'} fetchPriority={index < 10 ? 'high' : 'low'}><UserRound size={22}/></ArtworkImage></span><span><strong>{item.name}</strong><small>{item.trackCount} song{item.trackCount === 1 ? '' : 's'} in your library</small></span><ChevronDown size={16}/></button></VirtualList>{/if}
          {/if}
          {/if}

        {:else if currentPage === 'downloads'}
          {#if isDownloading && !playlistTitle}
            <section class="panel preparation-panel" aria-live="polite"><span class="rotating-loader" aria-hidden="true"><LoaderCircle size={22}/></span><div><p class="eyebrow">Preparing download</p><h2>Getting the job ready</h2><span>{jobPreparationStatus}</span></div></section>
          {/if}
          {#if playlistTitle || queueTrackKeys.length}
            <section class="panel session-panel">
              <div class="release-header"><div class="release-art"><ArtworkImage src={playlistArtwork} displaySize={160} loading="eager" fetchPriority="high"><Download size={22}/></ArtworkImage></div><div><p class="eyebrow">Downloading now</p><h2>{playlistTitle || 'Preparing music…'}</h2><p>{queueFinishedCount}/{queueTrackKeys.length || playlistTotalTracks || '…'} songs</p></div><button class="icon-button" aria-label={queuePaused ? 'Resume queue' : 'Pause queue'} on:click={toggleQueuePause}>{#if queuePaused}<Play size={18}/>{:else}<Pause size={18}/>{/if}</button></div>
              <div class="download-worker-summary">
                <div><strong>{workerActive} / {workerConfigured} workers active</strong><span>Device ceiling: {workerCeiling}</span></div>
                <div class="phase-counts"><span>{resolvingTrackCount} resolving</span><span>{transferringTrackCount} transferring</span><span>{processingTrackCount} processing</span>{#if retryWaitTrackCount}<span>{retryWaitTrackCount} retrying</span>{/if}</div>
              </div>
              <VirtualList
                items={queueTrackKeys}
                rowHeight={76}
                maxHeight="min(42vh, 360px)"
                viewportClass="download-track-list"
                restoreKey="downloads:active-tracks"
                ariaLabel="Current download tracks"
                bind:this={tracklistEl}
                on:scrollstate={updateTracklistScroll}
                let:item
              >
                  <article data-track-key={item} class:failed={activeTracks[item]?.status === 'failed'}>
                    <span class="track-state-icon">
                      {#if activeTracks[item]?.status === 'done' || activeTracks[item]?.status === 'skipped'}<Check size={16}/>
                      {:else if activeTracks[item]?.status === 'failed'}<X size={16}/>
                      {:else if activeTracks[item]?.status === 'waiting' || activeTracks[item]?.status === 'retry_wait'}<Clock3 size={16}/>
                      {:else}<span class="rotating-loader" aria-hidden="true"><LoaderCircle size={16}/></span>{/if}
                    </span>
                    <div>
                      <strong>{trackLabels[item] || item}</strong>
                      {#if activeTracks[item]?.status === 'retry_wait'}
                        <span>{retryStatus(activeTracks[item])}</span>
                      {:else if activeTracks[item]?.status === 'failed'}
                        <span>{activeTracks[item]?.text} · {activeTracks[item]?.error}</span>
                      {:else if !['done','skipped'].includes(activeTracks[item]?.status)}
                        <span>{activeTracks[item]?.text || 'Waiting…'}</span>
                      {/if}
                      {#if activeTracks[item]?.status === 'downloading' && activeTracks[item]?.mode === 'determinate'}
                        <div class="measured-progress"><progress max="100" value={activeTracks[item]?.progress ?? 0}></progress><small>{Math.round(activeTracks[item]?.progress ?? 0)}%{#if transferDetail(activeTracks[item])} · {transferDetail(activeTracks[item])}{/if}</small></div>
                      {:else if activeTracks[item]?.status === 'downloading' && activeTracks[item]?.mode === 'indeterminate'}
                        <div class="measured-progress"><progress max="100"></progress><small>{transferDetail(activeTracks[item]) || 'Transfer size is not available yet'}</small></div>
                      {/if}
                    </div>
                  </article>
              </VirtualList>
              {#if failedEntries.length}
                <section class="failure-panel" aria-label="Final download failures">
                  <header><strong>{failedEntries.length} final failure{failedEntries.length === 1 ? '' : 's'}</strong><button on:click={() => showDownloadLogs = true}><FileText size={15}/> Show log</button></header>
                  <VirtualList items={failedEntries} rowHeight={58} maxHeight="280px" viewportClass="failure-list" restoreKey="downloads:failures" ariaLabel="Final download failures" let:item><article><X size={15}/><div><strong>{item.label}</strong><span>{item.error}</span></div></article></VirtualList>
                </section>
              {/if}
              <button class="log-toggle" on:click={() => showDownloadLogs = !showDownloadLogs}><FileText size={15}/>{showDownloadLogs ? 'Hide log' : 'Show more'}</button>{#if showDownloadLogs}<div class="clean-log">{#each logs as log (log.id)}<p class={log.type}>{log.text.replace(/<[^>]+>/g, '')}</p>{/each}</div>{/if}
            </section>
          {/if}
          {#if queuedJobs.length}<div class="history-toolbar"><p>Queue</p><span>{queuedJobs.length} queued</span></div><VirtualList items={queuedJobs} itemKey={downloadJobKey} rowHeight={84} maxHeight="420px" viewportClass="history-list job-list" restoreKey="downloads:queued-jobs" ariaLabel="Queued downloads" let:item let:index><article><div class="history-art"><ArtworkImage src={item.artwork} displaySize={96} loading={index < 4 ? 'eager' : 'lazy'} fetchPriority={index < 4 ? 'high' : 'low'}>{#if item.status === 'paused'}<Pause size={20}/>{:else}<Clock3 size={20}/>{/if}</ArtworkImage></div><div><strong>{item.title}</strong><span>{item.status === 'paused' ? 'Paused · completed songs are saved' : 'Waiting'}{item.ipodDestinationId ? ' · iPod review after download' : ''}</span></div><div class="queued-job-actions"><button aria-label="Move job up" title="Move up" disabled={index === 0} on:click={() => moveQueuedJob(item.id, -1)}><ChevronUp size={17}/></button><button aria-label="Download this job now" title="Download now" on:click={() => runQueuedJobNow(item.id)}><SkipForward size={17}/></button><button aria-label="Remove queued job" title="Remove" on:click={() => removeQueuedJob(item.id)}><Trash2 size={16}/></button></div></article></VirtualList>{/if}
          <div class="history-toolbar"><p>{historyItems.length} completed job{historyItems.length === 1 ? '' : 's'}</p>{#if historyItems.length}<button class="danger-link" on:click={clearHistory}>Clear history</button>{/if}</div>
          {#if !historyItems.length && !queueTrackKeys.length}<div class="state-card"><p>Queued and completed albums, playlists, and songs will appear here.</p></div>{:else if historyItems.length}<VirtualList items={historyItems} itemKey={historyItemKey} rowHeight={88} maxHeight="520px" viewportClass="history-list" restoreKey="downloads:history" ariaLabel="Completed download history" let:item let:index><article class:error={!!item.error}><div class="history-art"><ArtworkImage src={item.artwork_url} displaySize={96} loading={index < 6 ? 'eager' : 'lazy'} fetchPriority={index < 6 ? 'high' : 'low'}><Download size={20}/></ArtworkImage></div><div><strong>{item.title || item.url}</strong><span>{item.total || 0} songs · {new Date(item.date).toLocaleDateString()}</span>{#if item.error}<small>{item.error}</small>{/if}</div><details class="job-options"><summary aria-label="Job options"><MoreHorizontal size={18}/></summary><div><button on:click={() => { inputUrl = item.url; startDownload(); }}>Download again</button><button on:click={() => navigator.clipboard?.writeText(item.url || '')}>Copy link</button></div></details></article></VirtualList>{/if}

        {:else if currentPage === 'downloaded'}
          {#if downloadedSelectedPath}
            <section class="library-detail-page downloaded-detail-page">
              {#if downloadedSelectedRelease?.artwork_url}<div class="library-detail-backdrop" aria-hidden="true"><ArtworkImage src={downloadedSelectedRelease.artwork_url} displaySize={384} loading="lazy" fetchPriority="low" /></div>{:else}<div class="library-detail-backdrop favourite-backdrop"></div>{/if}
              {#if downloadedSelectedReleaseLoading}<div class="state-card"><div class="spinner"></div><p>Opening downloaded release…</p></div>{:else if downloadedSelectedRelease}
                <div class="library-detail-hero"><div class="library-detail-art"><ArtworkImage src={downloadedSelectedRelease.artwork_url} displaySize={384} loading="eager" fetchPriority="high"><Album size={68}/></ArtworkImage></div><div class="library-detail-copy"><p class="eyebrow">Downloaded {downloadedSelectedRelease.kind}</p><h2>{downloadedSelectedRelease.title}</h2><strong>{downloadedSelectedRelease.track_count} songs</strong><span>{releaseMetaLine(downloadedSelectedRelease)}</span><button class="primary detail-download" on:click={() => playDownloadedTrack(0)}><Play size={17}/><span>Play</span></button></div></div>
                <VirtualList items={downloadedSelectedRelease.tracks} itemKey={downloadedTrackKey} rowHeight={63} maxHeight="62vh" viewportClass="local-detail-tracks" restoreKey={`downloaded:${downloadedSelectedRelease.relative_path}`} ariaLabel={`Downloaded songs in ${downloadedSelectedRelease.title}`} let:item let:index><button class:playing={currentPlayerTrack?.file_path === item.file_path} on:click={() => playDownloadedTrack(index)}><span class="detail-track-number">{#if currentPlayerTrack?.file_path === item.file_path}<Play size={14}/>{:else}{index + 1}{/if}</span><span class="detail-track-placeholder"><Album size={17}/></span><strong>{item.title}</strong><span>{item.artist || downloadedSelectedRelease.artist || 'Unknown Artist'}</span><span>{item.album || downloadedSelectedRelease.title}</span><small>{formatPlaybackTime(item.duration_seconds || 0)}</small></button></VirtualList>
              {/if}
            </section>
          {:else}
            <div class="library-tabs"><button class:active={downloadedView === 'albums'} on:click={() => { downloadedView = 'albums'; closeDownloadedRelease(); }}>Albums <span>{downloadedLibrary.albums.length}</span></button><button class:active={downloadedView === 'playlists'} on:click={() => { downloadedView = 'playlists'; closeDownloadedRelease(); }}>Playlists <span>{downloadedLibrary.playlists.length}</span></button><button class="secondary compact" on:click={forceRefreshDownloadedMusicLibrary}>Rescan</button></div>
            {#if downloadedLibraryLoading}<div class="state-card"><div class="spinner"></div><p>Scanning your library…</p></div>{:else if downloadedLibraryError}<div class="state-card error"><p>{downloadedLibraryError}</p></div>{:else}<div class="release-grid downloaded-release-grid"><ProgressiveCollection items={downloadedView === 'albums' ? downloadedLibrary.albums : downloadedLibrary.playlists} itemKey={downloadedReleaseKey} itemLabel={downloadedView} ariaLabel={`Downloaded ${downloadedView}`} let:item let:index><button class="release-tile" on:click={() => openDownloadedRelease(item)}><span class="release-placeholder"><ArtworkImage src={item.artwork_url} displaySize={Math.min(512, Math.round(uiPreferences.artwork_size * 2))} loading={index < 8 ? 'eager' : 'lazy'} fetchPriority={index < 8 ? 'high' : 'low'}><Library size={34}/></ArtworkImage></span><strong>{item.title}</strong><span>{releaseMetaLine(item)}</span></button></ProgressiveCollection></div>{/if}
          {/if}

        {:else if currentPage === 'devices'}
          <section class="device-heading"><div><p class="eyebrow">Powered by iOpenPod</p><h2>Connected iPods</h2><p>Mounted Classic, Mini, and Nano devices appear here, along with Mac-formatted iPods found through read-only scanning.</p></div><button class="secondary" on:click={loadIPodDevices} disabled={ipodDevicesLoading}>{ipodDevicesLoading ? 'Scanning…' : 'Scan Again'}</button></section>
          {#if ipodDevicesLoading && !ipodDevices.length}<div class="state-card"><div class="spinner"></div><p>Scanning attached iPods…</p></div>
          {:else if selectedIPodDevice}
            {#await loadIPodManager()}
              <div class="state-card"><div class="spinner"></div><p>Opening iPod manager…</p></div>
            {:then ipodManagerModule}
              {#key selectedIPodDevice.firewire_guid || `${selectedIPodDevice.device_id}|${selectedIPodDevice.path}`}<svelte:component this={ipodManagerModule.default} bind:this={ipodManagerEl} device={selectedIPodDevice} connected={selectedIPodConnected} localReleases={[...downloadedLibrary.albums, ...downloadedLibrary.playlists]} ipodEvent={latestIPodEvent} downloadBusy={isDownloading} demoMode={uiDemoMode} on:eject={removeEjectedIPod} on:notify={(event) => showToast(event.detail.message, event.detail.tone)} />{/key}
            {:catch}
              <div class="state-card error"><p>The iPod manager could not be loaded.</p></div>
            {/await}
          {:else if selectedIPodId}<div class="state-card error"><h3>The selected iPod disconnected</h3><p>Reconnect the same verified device and volume to continue. Vela will not substitute another connected iPod.</p><button class="secondary" on:click={loadIPodDevices}>Scan for the same iPod</button></div>
          {:else if ipodDevicesError}<div class="state-card error"><p>{ipodDevicesError}</p></div>
          {:else if !ipodDevices.length}<div class="state-card"><h3>No iPod found</h3><p>Connect the iPod, wait a moment, then scan again. On Windows, Vela also checks for Mac-formatted HFS+ iPods using read-only raw access.</p></div>
          {:else}<div class="device-grid">{#each ipodDevices as device (`${device.device_id}|${device.path}`)}<button class="device-choice" on:click={() => openIPodDevice(device)}><Smartphone size={24}/><span><strong>{device.name}</strong><small>{device.model_family}{device.generation ? ` · ${device.generation}` : ''}</small></span></button>{/each}</div>{/if}

        {/if}
        {#if showSettings}
          <div class="settings-overlay" role="presentation" on:click|self={() => showSettings = false}>
            <div class="settings-dialog" role="dialog" aria-modal="true" aria-label="Settings" tabindex="-1" use:focusDialog>
              <header class="settings-dialog-header"><div><p class="eyebrow">Preferences</p><h1>Settings</h1></div><div class="settings-header-actions"><button class="close-settings" aria-label="Close settings" on:click={() => showSettings = false}><X size={18}/></button></div></header>
              <div class="settings-shell">
                <nav class="settings-tabs" aria-label="Settings pages"><button class:active={settingsPage === 'general'} on:click={() => settingsPage = 'general'}><Settings size={17}/> General</button><button class:active={settingsPage === 'appearance'} on:click={() => settingsPage = 'appearance'}><Palette size={17}/> Appearance</button><button class:active={settingsPage === 'apple'} on:click={() => settingsPage = 'apple'}><Library size={17}/> Apple Music</button><button class:active={settingsPage === 'downloads'} on:click={() => settingsPage = 'downloads'}><Download size={17}/> Downloads</button><button class:active={settingsPage === 'audio'} on:click={() => settingsPage = 'audio'}><SlidersHorizontal size={17}/> Audio & sources</button><button class:active={settingsPage === 'discovery'} on:click={() => settingsPage = 'discovery'}><Compass size={17}/> Discover</button><button class:active={settingsPage === 'naming'} on:click={() => settingsPage = 'naming'}><FolderOpen size={17}/> File naming</button><button class:active={settingsPage === 'providers'} on:click={() => settingsPage = 'providers'}><MoreHorizontal size={17}/> Providers</button><button class:active={settingsPage === 'about'} on:click={() => settingsPage = 'about'}><FileText size={17}/> About</button></nav>
                <div class="settings-layout" data-page={settingsPage} on:change={handleSettingsChange}>
            {#if settingsPage === 'downloads'}<section class="settings-section" id="settings-downloads"><div class="settings-heading"><div><p class="eyebrow">Downloads</p><h2>Performance</h2></div><span class="worker-capacity">{workerActive} active · {workerConfigured} configured · {workerCeiling} maximum</span></div><div class="setting-row"><div><strong>Concurrent song downloads</strong><span>One queued release runs at a time. Vela can start up to {workerCeiling} songs in parallel on this device. Changes apply live without cancelling active songs; higher concurrency can increase throttling, CPU use, and disk contention instead of improving speed.</span></div><input class="number-input" type="number" min="1" max={workerCeiling} bind:value={config.max_concurrent_jobs} /></div><div class="setting-row"><div><strong>Maximum retries</strong><span>Retry transient source failures before marking a song failed.</span></div><input class="number-input" type="number" min="1" max="20" bind:value={config.max_retries} /></div><div class="setting-row download-location-row"><div class="path-copy"><strong>Music library location</strong><span>{config.download_path || 'Not selected'}</span></div><button class="secondary browse-location" on:click={pickDir}><FolderOpen size={16}/> Browse</button></div></section>{/if}
            {#if settingsPage === 'general'}<section class="settings-section" id="settings-general"><div class="settings-heading"><div><p class="eyebrow">General</p><h2>Startup & activity</h2></div></div><div class="setting-row"><div><strong>Startup destination</strong><span>Choose the first page shown when Vela opens.</span></div><select bind:value={uiPreferences.startup_destination}><option value="recently-added">Recently Added</option><option value="albums">Albums</option><option value="playlists">Playlists</option><option value="favourites">Favourites</option><option value="artists">Artists</option><option value="downloaded">Downloaded</option><option value="downloads">Downloads</option></select></div><div class="setting-row"><div><strong>Remember last page</strong><span>Resume where you left off instead of the startup destination.</span></div><label class="switch"><input type="checkbox" bind:checked={uiPreferences.remember_last_page} /><span></span></label></div><div class="setting-row"><div><strong>Open Downloads when adding music</strong><span>Move to activity automatically after a request is queued.</span></div><label class="switch"><input type="checkbox" bind:checked={uiPreferences.open_downloads_on_add} /><span></span></label></div><div class="setting-row"><div><strong>Completion notifications</strong><span>Allow the desktop notification hook when a download completes.</span></div><label class="switch"><input type="checkbox" bind:checked={uiPreferences.completion_notifications} /><span></span></label></div><div class="setting-row"><div><strong>Device notifications</strong><span>Allow the desktop notification hook when an iPod connects or disconnects.</span></div><label class="switch"><input type="checkbox" bind:checked={uiPreferences.device_notifications} /><span></span></label></div><div class="setting-row"><div><strong>Completed history retention</strong><span>Keep a bounded number of completed jobs.</span></div><select bind:value={uiPreferences.completed_history_retention}><option value={25}>25 jobs</option><option value={50}>50 jobs</option><option value={100}>100 jobs</option><option value={250}>250 jobs</option><option value={500}>500 jobs</option><option value={1000}>1,000 jobs</option></select></div><div class="setting-row"><div><strong>Music library folder</strong><span>{config.download_path || 'Not selected'}</span></div><button class="secondary" on:click={() => settingsPage = 'downloads'}>Location options</button></div><div class="setting-row"><div><strong>Library mode</strong><span>Reuse local audio while materializing every requested album and playlist folder.</span></div><select bind:value={config.library_mode}><option value="smart_dedup">Reuse local audio</option><option value="full_albums">Download every copy</option></select></div></section>{/if}
            {#if settingsPage === 'appearance'}
              {#await loadAppearanceSettings()}
                <div class="state-card"><div class="spinner"></div><p>Loading appearance settings…</p></div>
              {:then appearanceModule}
                <svelte:component this={appearanceModule.default} {appearance} preferences={uiPreferences} on:appearance={(event) => applyAppearance(event.detail)} on:preferences={(event) => updateUIPreferences(event.detail)} on:reset={resetAppearancePreferences} />
              {:catch}
                <div class="state-card error"><p>Appearance settings could not be loaded.</p></div>
              {/await}
            {/if}
            {#if settingsPage === 'about'}
              {#await loadAboutSettings()}
                <div class="state-card"><div class="spinner"></div><p>Loading third-party notices…</p></div>
              {:then aboutModule}
                <svelte:component this={aboutModule.default} demoMode={uiDemoMode} />
              {:catch}
                <div class="state-card error"><p>About settings could not be loaded.</p></div>
              {/await}
            {/if}

            {#if settingsPage === 'audio'}<section class="settings-section" id="settings-audio"><div class="settings-heading"><div><p class="eyebrow">Add Music</p><h2>Audio Format & Sources</h2></div></div><div class="choice-grid">{#each formatOptions as fmt (fmt.value)}<button class:active={_fmtBase === fmt.value} on:click={() => setParentFormat(fmt.value)}><strong>{fmt.name}</strong><span>{fmt.label}</span></button>{/each}</div>{#if showBitDepthRow}<div class="segmented"><button class:active={!_fmtBitDepth} on:click={() => setParentFormat(_fmtBase)}>Best</button><button class:active={_fmtBitDepth === '16'} on:click={() => setBitDepth('16')}>16-bit</button><button class:active={_fmtBitDepth === '24'} on:click={() => setBitDepth('24')}>24-bit</button></div>{/if}<div class="panel-heading source-heading"><h3>Sources</h3><span>Auto selects the best available match.</span></div><div class="source-grid">{#each downloadSourceOptions as src (src.value)}<button class:active={selectedDownloadSources.includes(src.value)} on:click={() => toggleDownloadSource(src.value)}>{#if src.icon}<img src={src.icon} alt="" />{:else}<span class="auto-source">A</span>{/if}<span>{src.label}</span></button>{/each}</div></section>{/if}

            {#if settingsPage === 'discovery'}<section class="settings-section" id="settings-discovery"><div class="settings-heading"><div><p class="eyebrow">Discover</p><h2>Storefront & Genre</h2></div></div><div class="setting-row"><label for="region">Storefront</label><select id="region" bind:value={discoveryRegion} on:change={() => { config.apple_storefront = discoveryRegion; loadDiscoveryGenres(); loadDiscoveryData(); }}><option value="gb">United Kingdom</option><option value="us">United States</option><option value="ca">Canada</option><option value="au">Australia</option><option value="de">Germany</option><option value="fr">France</option><option value="jp">Japan</option><option value="in">India</option></select></div><div class="setting-row"><label for="genre">Genre</label><select id="genre" bind:value={discoveryGenre} on:change={loadDiscoveryData}><option value="">All genres</option>{#each discoveryGenres as genre (genre.id)}<option value={genre.id}>{genre.name}</option>{/each}</select></div></section>{/if}

            {#if settingsPage === 'apple'}<section class="settings-section" id="settings-apple"><div class="settings-heading"><div><p class="eyebrow">Connected library</p><h2>Apple Music</h2></div><span class:connected={!!config.apple_music_user_token} class="connection-badge">{config.apple_music_user_token ? 'Connected' : 'Not connected'}</span></div><div class="privacy-banner"><strong>Local credentials only</strong><span>Tokens are stored in Vela’s local configuration and sent only to Apple’s authenticated endpoints. They are never uploaded to a Vela mirror.</span></div><div class="setting-row"><div><strong>Browser connection</strong><span>Capture a valid Apple Music browser session locally.</span>{#if appleLogin.message}<small class:error-text={appleLogin.phase === 'error'}>{appleLogin.message}</small>{/if}</div><button class="primary" on:click={startAppleLogin}>{config.apple_music_user_token ? 'Reconnect' : 'Connect'}</button></div><details><summary>Manual credentials</summary><label>Authorization token<input type="password" bind:value={config.apple_authorization_token} autocomplete="off" /></label><label>Music User Token<input type="password" bind:value={config.apple_music_user_token} autocomplete="off" /></label><label>Storefront<input bind:value={config.apple_storefront} maxlength="2" /></label></details><div class="index-maintenance"><div><strong>Local library index</strong><span>Check for changes keeps the current cache visible while updating it. Reset is only needed if cached data is wrong or damaged.</span></div><div><button class="secondary" on:click={() => loadAppleMusicLibrary(true)} disabled={appleLibraryLoading}><RefreshCw size={15}/> Check for changes</button><button class="danger-link" on:click={resetAppleIndex}>Reset index</button></div></div></section>{/if}

            {#if settingsPage === 'apple'}<section class="settings-section" id="settings-apple-sync"><div class="settings-heading"><div><p class="eyebrow">Automation</p><h2>Apple Playlist Sync</h2></div><label class="switch"><input type="checkbox" bind:checked={config.auto_sync_enabled} /><span></span></label></div><p class="section-note">Runs only while Vela is open. Only Apple Music playlists can be tracked.</p><div class="sync-controls"><label>Hour<input type="number" min="0" max="23" bind:value={config.auto_sync_hour} /></label><label>Minute<input type="number" min="0" max="59" bind:value={config.auto_sync_minute} /></label><button class="secondary" on:click={runAutoSyncNow} disabled={autoSyncRunning}>{autoSyncRunning ? 'Syncing…' : 'Sync Now'}</button></div>{#if autoSyncLastResult}<p class="result-note">{autoSyncLastResult}</p>{/if}</section>{/if}

            {#if settingsPage === 'downloads'}<section class="settings-section" id="settings-download-options"><div class="settings-heading"><div><p class="eyebrow">Downloads</p><h2>Output & Matching</h2></div></div><div class="setting-row"><div><strong>Strict matching</strong><span>Prefer a clear failure over a risky recording match.</span></div><label class="switch"><input type="checkbox" bind:checked={config.strict_matching} /><span></span></label></div><div class="setting-row"><div><strong>Prefer explicit versions</strong><span>Avoid clean or radio edits when possible.</span></div><label class="switch"><input type="checkbox" bind:checked={config.prefer_explicit} /><span></span></label></div><div class="setting-row"><div><strong>Fetch lyrics</strong><span>Save synced or plain lyrics when available.</span></div><label class="switch"><input type="checkbox" bind:checked={config.fetch_lyrics} /><span></span></label></div><div class="setting-row"><div><strong>Save cover sidecar</strong><span>Write cover artwork alongside the audio.</span></div><label class="switch"><input type="checkbox" bind:checked={config.save_cover_art_sidecar} /><span></span></label></div></section>{/if}

            {#if settingsPage === 'naming'}<section class="settings-section" id="settings-naming"><div class="settings-heading"><div><p class="eyebrow">Organization</p><h2>File Naming</h2></div></div><label>Single track filename<input use:captureFocusedTemplate bind:value={config.single_track_filename_template} /><small>{renderPreview(config.single_track_filename_template)}.flac</small></label><label>Album track filename<input bind:value={config.album_track_filename_template} on:focus={(e) => focusedTemplateEl = e.currentTarget} /><small>{renderPreview(config.album_track_filename_template)}.flac</small></label><label>Folder structure<input bind:value={config.folder_structure_template} on:focus={(e) => focusedTemplateEl = e.currentTarget} /><small>{renderPreview(config.folder_structure_template)}/</small></label><div class="token-row">{#each ['{title}','{artist}','{album_artist}','{album}','{year}','{track}','{disc}','{quality}'] as token (token)}<button on:click={() => insertToken(token)}>{token}</button>{/each}</div></section>{/if}

            {#if settingsPage === 'providers'}<section class="settings-section advanced" id="settings-providers"><div class="settings-heading"><div><p class="eyebrow">Advanced downloader</p><h2>Provider Credentials</h2></div></div><p class="section-note">These accounts are optional resolver inputs, not connected libraries. Spotify account-library sync is not available.</p><details><summary>TIDAL</summary><label class="check-row"><input type="checkbox" bind:checked={config.tidal_enabled} /> Enable TIDAL resolver</label><button class="secondary" on:click={startTidalOAuth}>Connect TIDAL</button>{#if tidalOAuth.message}<p class="result-note">{tidalOAuth.message}</p>{/if}</details><details><summary>Amazon Music</summary><label class="check-row"><input type="checkbox" bind:checked={config.amazon_enabled} /> Enable Amazon resolver</label><button class="secondary" on:click={startAmazonLogin}>Connect Amazon</button>{#if amazonLogin.phase === 'waiting_for_user'}<button class="primary" on:click={confirmAmazonBrowserLogin}>I’m Signed In</button>{/if}{#if amazonLogin.message}<p class="result-note">{amazonLogin.message}</p>{/if}</details><details><summary>Spotify downloader session</summary><button class="secondary" on:click={startSpotifyDownloaderCapture}>Capture browser session</button>{#if spDcCapture.message}<p class="result-note">{spDcCapture.message}</p>{/if}<label>sp_dc cookie<input type="password" bind:value={config.spotify_sp_dc} autocomplete="off" /></label><p class="fine-print">Used only by retained Spotify URL and podcast downloader paths. It does not create or sync a Spotify library.</p></details><details><summary>Qobuz & Deezer</summary><label class="check-row"><input type="checkbox" bind:checked={config.qobuz_enabled} /> Enable Qobuz resolver</label><label>Qobuz email<input bind:value={config.qobuz_email} /></label><label>Qobuz password<input type="password" bind:value={config.qobuz_password} /></label><label>Deezer ARL<input type="password" bind:value={config.deezer_arl_token} /></label></details></section>{/if}
            <div class="settings-auto-status" class:error={settingsSaveState === 'error'}>{settingsSaveState === 'saving' ? 'Saving…' : settingsSaveState === 'saved' ? 'Saved' : settingsError}</div>
              </div>
              </div>
            </div>
          </div>
        {/if}
      </main>
    </section>

    {#if (isDownloading || queuedJobs.length) && playlistTitle && (playlistTotalTracks > 0 || queueTrackKeys.length > 0)}
      <aside class:player-open={!!currentPlayerTrack} class="queue-panel compact-queue">
        <button class="queue-header" on:click={() => selectPage('downloads')}>
          <span class="queue-art"><ArtworkImage src={playlistArtwork} displaySize={84} loading="eager" fetchPriority="high"><Download size={18}/></ArtworkImage></span>
          <div><strong>{queuePaused ? 'Downloads paused' : (playlistTitle || 'Preparing download')}</strong><span>{queueFinishedCount}/{queueTrackKeys.length || '…'} songs · {queuedJobs.length} queued</span></div>
        </button>
        <div class="queue-controls"><button aria-label={queuePaused ? 'Resume queue' : 'Pause queue'} on:click={toggleQueuePause}>{#if queuePaused}<Play size={17}/>{:else}<Pause size={17}/>{/if}</button><button aria-label="Cancel queue" on:click|stopPropagation={cancelDownload}><X size={17}/></button></div>
        <div class="queue-overall-progress" style:--queue-progress={`${queueOverallProgress}%`}></div>
      </aside>
    {/if}

    <PlayerBar
      bind:this={playerBarEl}
      bind:currentTrack={currentPlayerTrack}
      initialVolume={uiPreferences.player_volume}
      on:volumechange={handlePlayerVolumeChange}
    />
  </div>

  {#if toastMessage}<div class:warning={toastTone === 'warning'} class:success={toastTone === 'success'} class="app-toast" role="status"><span>{toastMessage}</span><button aria-label="Dismiss notification" on:click={() => toastMessage = ''}><X size={16}/></button></div>{/if}
  {#if confirmDialog}<div class="modal-backdrop confirm-backdrop" role="presentation" on:click|self={() => resolveConfirmation(false)}><div class="modal-card confirm-card" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" tabindex="-1" use:focusDialog><header><div><p class="eyebrow">Please confirm</p><h2 id="confirm-title">{confirmDialog.title}</h2></div></header><p>{confirmDialog.message}</p><footer class="modal-actions"><button class="secondary" on:click={() => resolveConfirmation(false)}>Keep</button><button class:danger={confirmDialog.danger} class="primary" on:click={() => resolveConfirmation(true)}>{confirmDialog.confirmLabel}</button></footer></div></div>{/if}
  {#if showCustomDownload}<div class="modal-backdrop" role="presentation" on:click|self={() => showCustomDownload = false}><div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="custom-download-title" tabindex="-1" use:focusDialog><header><div><p class="eyebrow">Downloads</p><h2 id="custom-download-title">Add custom link</h2></div><button aria-label="Close" on:click={() => showCustomDownload = false}><X size={18}/></button></header><label class="field-label" for="custom-links">Track, album, playlist, or artist links</label><textarea id="custom-links" class="custom-links" use:captureInputUrl bind:value={inputUrl} placeholder="One link per line"></textarea><div class="custom-destination"><div><strong>Local music destination</strong><span>{customDestination || config.download_path}</span></div><button class="secondary" on:click={chooseCustomDestination}><FolderOpen size={16}/> Choose</button></div><label class="ipod-job-destination"><span><strong>Review for iPod after download</strong><small>Optional. Files stay local; Vela opens a staged incremental plan when the job completes.</small></span><select bind:value={customIPodDestinationId}><option value="">No iPod</option>{#each writableIPodDevices as device (`${device.device_id}|${device.path}`)}<option value={device.device_id}>{device.name} · {device.model_family}</option>{/each}</select></label><footer class="modal-actions"><button class="icon-button" aria-label="Audio and source settings" on:click={() => { showCustomDownload = false; openSettings('settings-audio'); }}><Settings size={18}/></button><button class="primary" disabled={!inputUrl.trim()} on:click={startCustomDownload}><Download size={16}/> {isDownloading ? 'Add to queue' : 'Download'}</button></footer></div></div>{/if}

  {#if libraryContextItem}<button class="context-dismiss" aria-label="Close library item menu" on:click={() => libraryContextItem = null} on:contextmenu|preventDefault={() => libraryContextItem = null}></button><div class="context-menu" style={`left:${libraryContextX}px;top:${libraryContextY}px`}><button on:click={() => { if (libraryContextItem) openAppleLibraryDetail(libraryContextItem.url, libraryContextItem.name, libraryContextItem.image_url || ''); libraryContextItem = null; }}><Library size={16}/> View songs</button><button on:click={() => { if (libraryContextItem) downloadPlaylistUrl(libraryContextItem.url); libraryContextItem = null; }}><Download size={16}/> Download</button><button on:click={() => { if (libraryContextItem) toggleLibrarySelection(libraryContextItem.url); libraryContextItem = null; }}><Circle size={16}/> {selectedLibraryItems.has(libraryContextItem.url) ? 'Deselect' : 'Select'}</button></div>{/if}
  {#if showLibraryNavMenu}<button class="context-dismiss" aria-label="Close library menu" on:click={() => showLibraryNavMenu = false}></button><div class="context-menu" style={`left:${libraryNavMenuX}px;top:${libraryNavMenuY}px`}><button on:click={() => { showLibraryNavMenu = false; loadAppleMusicLibrary(true); }}><RefreshCw size={16}/> Refresh library</button></div>{/if}

  {#if showArtistSearch}
    <div class="modal-backdrop" role="presentation" on:click|self={() => { artistSearchReqId++; showArtistSearch = false; }}>
      <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="artist-results-title" tabindex="-1" use:focusDialog><header><div><p class="eyebrow">Apple Music</p><h2 id="artist-results-title">Artist results</h2></div><button on:click={() => { artistSearchReqId++; showArtistSearch = false; }}>×</button></header>{#if artistSearchLoading}<div class="state-card"><div class="spinner"></div></div>{:else}<VirtualList items={artistSearchResults} itemKey={artistSearchItemKey} rowHeight={68} maxHeight="58vh" viewportClass="artist-results" restoreKey={`artist-search:${searchQuery}`} ariaLabel="Apple Music artist results" let:item let:index><button on:click={() => openArtistFromSearch(item)}><div class="artist-placeholder"><ArtworkImage src={item.artwork_url} displaySize={96} loading={index < 6 ? 'eager' : 'lazy'} fetchPriority={index < 6 ? 'high' : 'low'}><UserRound size={22}/></ArtworkImage></div><span><strong>{item.name}</strong><small>{item.genres?.slice(0,2).join(' · ') || 'Artist'}</small></span><i>›</i></button></VirtualList>{/if}</div>
    </div>
  {/if}

  {#if showDiscography}
    <div class="modal-backdrop" role="presentation" on:click|self={() => { discographyReqId++; showDiscography = false; }}>
      <div class="modal-card wide" role="dialog" aria-modal="true" aria-labelledby="discography-title" tabindex="-1" use:focusDialog><header><div><p class="eyebrow">Select releases</p><h2 id="discography-title">{discographyArtist?.artist_name || 'Loading discography…'}</h2></div><button on:click={() => { discographyReqId++; showDiscography = false; }}>×</button></header>{#if discographyLoading}<div class="state-card"><div class="spinner"></div></div>{:else if discographyArtist}<div class="select-actions"><button class="text-button" on:click={selectAllDiscographyAlbums}>Select all</button><button class="text-button" on:click={() => discographySelected = new Set()}>Select none</button><span>{discographySelected.size} selected</span></div><div class="discography-grid"><ProgressiveCollection items={discographyArtist.albums} itemKey={discographyAlbumKey} initialCount={24} chunkSize={24} itemLabel="releases" ariaLabel="Artist releases" let:item let:index><label class:selected={discographySelected.has(item.url)}><div class="release-placeholder"><ArtworkImage src={item.artwork_url} displaySize={256} loading={index < 8 ? 'eager' : 'lazy'} fetchPriority={index < 8 ? 'high' : 'low'}><Album size={34}/></ArtworkImage></div><input type="checkbox" checked={discographySelected.has(item.url)} on:change={() => toggleDiscographySelection(item.url)} /><strong>{item.name}</strong><span>{item.year || '—'} · {item.track_count} tracks</span></label></ProgressiveCollection></div><footer><button class="primary" disabled={!discographySelected.size} on:click={downloadSelectedDiscography}>Add {discographySelected.size} release{discographySelected.size === 1 ? '' : 's'} to queue</button></footer>{/if}</div>
    </div>
  {/if}
{/if}

<style>
  :global(*) { box-sizing: border-box; }
  :global(:root) { color-scheme: light; --accent:#fa2d55; --accent-soft:rgba(250,45,85,.11); --bg:#f5f5f7; --sidebar:rgba(242,242,247,.94); --surface:#fff; --surface-2:#ececf0; --surface-hover:#e5e5ea; --text:#17171a; --muted:#6e6e73; --faint:#98989d; --line:rgba(0,0,0,.09); --shadow:0 18px 55px rgba(0,0,0,.14); --ui-scale:1;--ui-scale-inverse:1;--sidebar-width:240px;--artwork-size:170px;--density-space:1;--error-color:#c92a2a;--warning-color:#8a6100;--success-color:#237a3b;--focus-ring:color-mix(in srgb,var(--accent) 72%,var(--text));--disabled-opacity:.45;--overlay-scrim:rgba(0,0,0,.48);--font-mono:"Fira Code","JetBrains Mono",Consolas,monospace; }
  :global(:root[data-appearance='dark']) { color-scheme: dark; --accent:#ff375f; --accent-soft:rgba(255,55,95,.16); --bg:#101012; --sidebar:rgba(27,27,30,.96); --surface:#202023; --surface-2:#2b2b2f; --surface-hover:#343439; --text:#f5f5f7; --muted:#a1a1a6; --faint:#737378; --line:rgba(255,255,255,.1); --shadow:0 22px 65px rgba(0,0,0,.46);--error-color:#ff6961;--warning-color:#ffb340;--success-color:#32d74b;--overlay-scrim:rgba(0,0,0,.62); }
  :global(html), :global(body), :global(#app) { margin:0; width:100%; height:100%; overflow:hidden; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",sans-serif; }
  :global(button), :global(input), :global(textarea), :global(select) { font:inherit; }
  :global(button) { color:inherit; }
  button { border:0; cursor:pointer; }
  .icon-sprite { position:absolute; width:0; height:0; overflow:hidden; }
  svg { fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }
  .launch-screen,.setup-screen,.app-shell { zoom:var(--ui-scale);width:calc(100% * var(--ui-scale-inverse));height:calc(100% * var(--ui-scale-inverse)); }
  .launch-screen,.setup-screen { min-height:100%; display:grid; place-items:center; background:radial-gradient(circle at 50% 30%,var(--accent-soft),transparent 38%),var(--bg); }
  .launch-screen { align-content:center; gap:22px; }.launch-name{font-size:36px;letter-spacing:-.06em}.spinner{width:22px;height:22px;border:2px solid var(--line);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
  .setup-card{width:min(540px,calc(100vw - 40px));padding:44px;border:1px solid var(--line);border-radius:24px;background:var(--surface);box-shadow:var(--shadow)}.setup-card h1{font-size:34px;letter-spacing:-.04em;margin:4px 0 12px}.lede{color:var(--muted);line-height:1.55;margin:0 0 28px}.field-label,.settings-section>label,.settings-section details label{display:grid;gap:7px;font-size:13px;font-weight:600;margin:14px 0}.input-row{display:flex;gap:9px}.input-row input{flex:1}.setup-continue{width:100%;margin-top:18px}
  .app-shell{display:grid;grid-template-columns:var(--sidebar-width) minmax(0,1fr);background:var(--bg)}
  .sidebar{display:flex;flex-direction:column;min-width:0;padding:22px 14px 14px;background:var(--sidebar);border-right:1px solid var(--line);backdrop-filter:blur(30px);z-index:3}.brand-lockup{padding:2px 10px 24px;font-size:22px;font-weight:760;letter-spacing:-.055em}.sidebar nav{display:flex;flex-direction:column;gap:3px}.nav-label{margin:18px 10px 6px;color:var(--faint);font-size:11px;font-weight:650;text-transform:uppercase;letter-spacing:.08em}.sidebar nav button,.settings-nav{height:38px;display:flex;align-items:center;gap:11px;padding:0 10px;border-radius:9px;background:transparent;color:var(--muted);font-size:14px;text-align:left}.sidebar nav button:hover,.settings-nav:hover{background:var(--surface-hover);color:var(--text)}.sidebar nav button.active,.settings-nav.active{background:var(--accent-soft);color:var(--accent);font-weight:630}:global(.sidebar nav svg),:global(.settings-nav svg){width:19px;height:19px}.sidebar-footer{margin-top:auto;display:grid;gap:10px}.settings-nav{width:100%}.appearance-control{display:grid;grid-template-columns:repeat(3,1fr);gap:3px;padding:3px;background:var(--surface-2);border-radius:9px}:global(.appearance-control button){padding:6px 3px;border-radius:7px;background:transparent;color:var(--muted);font-size:11px}:global(.appearance-control button.active){background:var(--surface);color:var(--text);box-shadow:0 1px 4px rgba(0,0,0,.12)}.appearance-control.large{width:260px}:global(.appearance-control.large button){font-size:13px;padding:8px}.privacy-status{display:flex;align-items:center;gap:7px;padding:4px 10px;color:var(--faint);font-size:11px}:global(.privacy-status span),.queue-status{width:7px;height:7px;border-radius:50%;background:var(--faint)}:global(.privacy-status span.connected),.queue-status.is-active{background:#30d158;box-shadow:0 0 0 4px rgba(48,209,88,.12)}
  .workspace{min-width:0;height:100%;display:flex;flex-direction:column}.topbar{height:82px;flex:0 0 auto;display:flex;align-items:center;justify-content:space-between;padding:15px 34px;border-bottom:1px solid var(--line);background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(24px);z-index:2}.topbar h1{margin:1px 0 0;font-size:25px;line-height:1.05;letter-spacing:-.035em}.eyebrow{margin:0;color:var(--accent);font-size:11px;font-weight:720;text-transform:uppercase;letter-spacing:.09em}.top-actions{display:flex;gap:8px}.page-content{flex:1;min-height:0;overflow:auto;padding:calc(30px * var(--density-space)) calc(34px * var(--density-space)) 126px}.page-content>*{max-width:1240px;margin-left:auto;margin-right:auto}
  .primary,.secondary{min-height:38px;padding:0 16px;border-radius:10px;font-weight:650;font-size:13px}.primary{background:var(--accent);color:#fff}.primary:hover{filter:brightness(1.05)}.primary:disabled,.secondary:disabled{opacity:.45;cursor:default}.secondary{background:var(--surface-2);color:var(--text)}.secondary:hover{background:var(--surface-hover)}.compact{min-height:32px;padding:0 12px;font-size:12px}.text-button,.danger-link{padding:4px;background:transparent;color:var(--accent);font-weight:600}.danger-link{color:#ff453a}.queue-trigger{height:38px;display:flex;align-items:center;gap:8px;padding:0 13px;border-radius:10px;background:var(--surface-2);font-size:12px;font-weight:650}:global(.queue-trigger svg){width:17px;height:17px}
  .demo-badge{display:inline-flex;align-items:center;height:28px;padding:0 9px;border-radius:99px;background:var(--accent-soft);color:var(--accent);font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em}
  input,textarea,select{border:1px solid var(--line);border-radius:10px;background:var(--surface-2);color:var(--text);outline:none;padding:10px 12px}input:focus,textarea:focus,select:focus{border-color:color-mix(in srgb,var(--accent) 60%,var(--line));box-shadow:0 0 0 3px var(--accent-soft)}
  .hero{min-height:235px;border-radius:24px;padding:36px;display:flex;align-items:center;justify-content:space-between;overflow:hidden}.library-hero{background:linear-gradient(120deg,#fa2d55 0%,#ff6482 46%,#ff9d80 100%);color:#fff;box-shadow:0 18px 48px rgba(250,45,85,.22)}:global(.library-hero .eyebrow){color:rgba(255,255,255,.8)}:global(.hero h2){max-width:650px;margin:8px 0 12px;font-size:38px;line-height:1.06;letter-spacing:-.045em}:global(.hero p:last-child){max-width:610px;margin:0;color:rgba(255,255,255,.82);font-size:15px;line-height:1.5}.hero-art{width:170px;height:170px;display:grid;place-items:center;border-radius:40px;background:linear-gradient(145deg,rgba(255,255,255,.34),rgba(255,255,255,.08));box-shadow:inset 0 0 0 1px rgba(255,255,255,.25),0 20px 40px rgba(126,0,31,.2);transform:rotate(7deg)}:global(.hero-art span){font-size:74px}
  .notice-card,.panel,.settings-section{background:var(--surface);border:1px solid var(--line);border-radius:18px}.notice-card{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:18px;padding:22px;margin-top:24px}.notice-icon{width:48px;height:48px;display:grid;place-items:center;border-radius:13px;background:var(--accent-soft);color:var(--accent);font-size:24px}.notice-card h3{margin:0 0 5px}.notice-card p{margin:0;color:var(--muted);font-size:13px;line-height:1.5}.state-card{min-height:220px;display:grid;place-items:center;align-content:center;gap:12px;color:var(--muted);text-align:center}.state-card.error{color:#ff453a}.section-block{margin-top:34px}.section-heading{display:flex;align-items:end;justify-content:space-between;margin-bottom:15px}.section-heading h2{font-size:24px;letter-spacing:-.03em;margin:4px 0 0}.section-heading>span{color:var(--muted);font-size:12px}.art-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(155px,1fr));gap:24px 18px}.music-card{position:relative;min-width:0}.artwork{position:relative;display:block;width:100%;aspect-ratio:1;border-radius:13px;overflow:hidden;background:var(--surface-2);box-shadow:0 8px 22px rgba(0,0,0,.12)}.gradient-art{background:linear-gradient(145deg,#ff375f,#b91460);color:#fff;font-size:52px}.art-placeholder,.release-placeholder{width:100%;height:100%;display:grid;place-items:center;color:var(--faint);font-size:36px}.card-copy{display:grid;gap:3px;padding:9px 2px 0;min-width:0}.card-copy strong,.card-copy span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.card-copy strong{font-size:13px}.card-copy span{color:var(--muted);font-size:11px}.sync-pill{position:absolute;right:2px;top:calc(100% + 37px);padding:3px 8px;border-radius:99px;background:var(--surface-2);color:var(--muted);font-size:10px}.sync-pill.enabled{background:var(--accent-soft);color:var(--accent)}
  .composer-card{display:flex;gap:18px;padding:22px;border-radius:20px;background:var(--surface);border:1px solid var(--line)}.composer-icon{width:48px;height:48px;flex:0 0 auto;display:grid;place-items:center;border-radius:14px;background:var(--accent-soft);color:var(--accent)}:global(.composer-icon svg){width:24px;height:24px}.composer-main{flex:1;display:grid;gap:10px}:global(.composer-main label){font-size:14px;font-weight:650}:global(.composer-main textarea){min-height:92px;resize:vertical;line-height:1.45}.composer-actions{display:flex;align-items:center;justify-content:space-between;gap:16px}:global(.composer-actions span),.fine-print{color:var(--muted);font-size:11px}.control-grid{display:grid;grid-template-columns:1.15fr .85fr;gap:18px;margin-top:18px}.panel{padding:22px}.panel-heading{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:14px}.panel-heading h3{margin:0;font-size:16px}.panel-heading span{color:var(--muted);font-size:11px}.choice-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:7px}.choice-grid button{display:grid;gap:5px;min-height:72px;padding:10px;border:1px solid var(--line);border-radius:11px;background:var(--surface-2);text-align:left}.choice-grid button.active{border-color:var(--accent);background:var(--accent-soft);color:var(--accent)}.choice-grid strong{font-size:13px}.choice-grid span{color:var(--muted);font-size:9px;line-height:1.25}.segmented{display:flex;gap:3px;width:max-content;margin-top:13px;padding:3px;background:var(--surface-2);border-radius:9px}.segmented button{padding:5px 12px;border-radius:7px;background:transparent;color:var(--muted);font-size:11px}.segmented button.active{background:var(--surface);color:var(--text)}.source-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}.source-grid button{display:flex;align-items:center;gap:7px;min-height:41px;padding:7px;border:1px solid var(--line);border-radius:10px;background:var(--surface-2);font-size:11px}.source-grid button.active{border-color:var(--accent);background:var(--accent-soft);color:var(--accent)}.source-grid img,.auto-source{width:22px;height:22px;display:grid;place-items:center;border-radius:6px;object-fit:cover;background:var(--surface);font-weight:750}.session-panel{margin-top:18px}.release-header{display:flex;align-items:center;gap:16px}:global(.release-header>img){width:80px;height:80px;border-radius:12px;object-fit:cover}.release-header>div{flex:1}.release-header h2{margin:4px 0;font-size:22px}.release-header p:last-child{margin:0;color:var(--muted);font-size:12px}.failure-strip{display:flex;justify-content:space-between;margin-top:16px;padding:12px;border-radius:10px;background:rgba(255,69,58,.1);color:#ff453a}:global(.failure-strip button){background:transparent;color:inherit}.log-button{margin-top:14px}.log-card{max-height:300px;overflow:auto;margin-top:10px;padding:16px;border-radius:14px;background:#111;color:#c7c7cc;font:11px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace}:global(.log-card .error){color:#ff6961}:global(.log-card .success){color:#64d27b}
  .search-hero{max-width:850px!important;padding:60px 0 30px;text-align:center}:global(.search-hero h2){margin:8px 0 12px;font-size:40px;letter-spacing:-.045em}:global(.search-hero>p:last-of-type){color:var(--muted);line-height:1.5}.search-bar{height:56px;display:flex;align-items:center;gap:10px;margin-top:26px;padding:6px 7px 6px 16px;border:1px solid var(--line);border-radius:15px;background:var(--surface);box-shadow:0 10px 34px rgba(0,0,0,.08)}:global(.search-bar svg){width:21px;height:21px;color:var(--muted)}.search-bar input{flex:1;border:0;background:transparent;box-shadow:none!important}.empty-feature{max-width:650px!important;margin-top:50px;text-align:center;color:var(--muted)}:global(.empty-feature h3){color:var(--text)}.vinyl{width:100px;height:100px;display:grid;place-items:center;margin:auto;border-radius:50%;background:repeating-radial-gradient(circle,#252527 0 7px,#111 8px 12px);color:#fff;font-size:28px}.filter-bar{display:flex;align-items:end;gap:10px;padding:16px;border:1px solid var(--line);border-radius:16px;background:var(--surface)}:global(.filter-bar div){display:grid;gap:5px}:global(.filter-bar label){font-size:11px;color:var(--muted)}
  .library-tabs{display:flex;align-items:center;gap:4px;margin-bottom:18px}.library-tabs button{padding:8px 13px;border-radius:9px;background:transparent;color:var(--muted)}.library-tabs button.active{background:var(--surface-2);color:var(--text);font-weight:650}.library-tabs button span{font-size:10px}.library-tabs .secondary{margin-left:auto}.downloaded-layout{display:grid;grid-template-columns:minmax(360px,.9fr) minmax(430px,1.1fr);gap:20px}.release-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));align-content:start;gap:18px 13px}.release-tile{display:grid;gap:5px;padding:7px;border-radius:13px;background:transparent;text-align:left}.release-tile:hover,.release-tile.selected{background:var(--surface-2)}:global(.release-tile img),.release-tile .release-placeholder{width:100%;aspect-ratio:1;border-radius:10px;object-fit:cover;background:var(--surface-2)}.release-tile strong,.release-tile span{overflow:hidden;white-space:nowrap;text-overflow:ellipsis}.release-tile strong{font-size:12px}.release-tile span{font-size:10px;color:var(--muted)}.release-detail{position:sticky;top:0;max-height:calc(100vh - 150px);overflow:auto}.detail-head{display:flex;align-items:end;gap:18px}:global(.detail-head img){width:130px;height:130px;border-radius:14px;object-fit:cover}:global(.detail-head h2){margin:5px 0;font-size:27px}:global(.detail-head p:last-child){margin:0;color:var(--muted);font-size:12px}.track-table{margin-top:20px}:global(.track-table button){width:100%;display:grid;grid-template-columns:28px 1fr auto;align-items:center;gap:8px;padding:9px 7px;border-radius:8px;background:transparent;text-align:left}:global(.track-table button:hover),:global(.track-table button.playing){background:var(--surface-2)}:global(.track-table button.playing){color:var(--accent)}:global(.track-table button>span:nth-child(2)){display:grid;gap:2px}:global(.track-table small){color:var(--muted)}.track-index{text-align:center;color:var(--muted);font-size:11px}.empty-detail{min-height:260px;display:grid;place-items:center;color:var(--muted)}
  .history-toolbar{display:flex;justify-content:space-between;align-items:center;color:var(--muted)}:global(.history-list){margin-bottom:8px}:global(.history-list) article{height:80px;display:grid;grid-template-columns:54px 1fr auto;align-items:center;gap:13px;padding:11px;border:1px solid var(--line);border-radius:13px;background:var(--surface)}:global(.history-list) article.error{border-color:rgba(255,69,58,.35)}:global(.history-list) .history-art{width:54px;height:54px;border-radius:9px;object-fit:cover;background:var(--surface-2);display:grid;place-items:center}:global(.history-list) article>div:nth-child(2){min-width:0;display:grid;gap:4px}:global(.history-list) strong,:global(.history-list) span{overflow:hidden;white-space:nowrap;text-overflow:ellipsis}:global(.history-list) span{color:var(--muted);font-size:11px}:global(.history-list) small{color:#ff453a}
  .settings-layout{max-width:900px!important;display:grid;gap:16px}.settings-section{padding:23px}.settings-heading{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.settings-heading h2{margin:4px 0 0;font-size:20px}.setting-row{min-height:64px;display:flex;align-items:center;justify-content:space-between;gap:24px;padding:12px 0;border-top:1px solid var(--line)}.setting-row>div{display:grid;gap:4px}.setting-row span,.section-note,.settings-section small{color:var(--muted);font-size:11px}.setting-row select{min-width:170px}.connection-badge{padding:5px 9px;border-radius:99px;background:var(--surface-2);color:var(--muted);font-size:10px}.connection-badge.connected{background:rgba(48,209,88,.13);color:#28a745}.privacy-banner{display:grid;gap:5px;margin:13px 0;padding:13px;border-radius:11px;background:var(--accent-soft);color:var(--accent);font-size:12px}.privacy-banner span{color:var(--muted);line-height:1.5}.settings-section details{padding:12px 0;border-top:1px solid var(--line)}.settings-section summary{cursor:pointer;font-size:13px;font-weight:650}.settings-section details input:not([type=checkbox]){width:100%}.check-row{display:flex!important;align-items:center;gap:8px}.switch{position:relative;width:42px;height:25px;margin:0!important}.switch input{opacity:0;width:0;height:0}.switch span{position:absolute;inset:0;border-radius:99px;background:var(--surface-2);transition:.2s}.switch span:after{content:"";position:absolute;width:19px;height:19px;left:3px;top:3px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.25);transition:.2s}.switch input:checked+span{background:var(--accent)}.switch input:checked+span:after{transform:translateX(17px)}.sync-controls{display:flex;align-items:end;gap:10px}.sync-controls label{display:grid;gap:5px;color:var(--muted);font-size:11px}.sync-controls input{width:85px}.result-note{padding:10px;border-radius:9px;background:var(--surface-2);color:var(--muted);font-size:11px}.error-text{color:#ff453a!important}.token-row{display:flex;flex-wrap:wrap;gap:5px}.token-row button{padding:4px 7px;border-radius:7px;background:var(--surface-2);color:var(--accent);font:10px ui-monospace,monospace}.save-bar{position:sticky;bottom:-90px;display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border:1px solid var(--line);border-radius:14px;background:color-mix(in srgb,var(--surface) 92%,transparent);box-shadow:var(--shadow);backdrop-filter:blur(20px)}:global(.save-bar span){color:var(--muted);font-size:11px}
  .queue-panel{position:fixed;right:22px;bottom:22px;width:320px;z-index:20;border:1px solid var(--line);border-radius:16px;background:color-mix(in srgb,var(--surface) 94%,transparent);box-shadow:var(--shadow);backdrop-filter:blur(30px);overflow:hidden}.queue-panel.expanded{width:390px}.queue-header{width:100%;height:66px;display:flex;align-items:center;gap:11px;padding:0 15px;background:transparent;text-align:left}.queue-header>div{flex:1;display:grid;gap:3px}.queue-header strong{font-size:13px}.queue-header span{color:var(--muted);font-size:10px}:global(.queue-header .queue-status){flex:0 0 auto}.chevron{font-size:17px!important}.queue-body{max-height:380px;overflow:auto;border-top:1px solid var(--line)}:global(.queue-body article){display:grid;grid-template-columns:9px 1fr auto;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid var(--line)}:global(.queue-body article>div){min-width:0;display:grid;gap:3px}:global(.queue-body strong),:global(.queue-body span){overflow:hidden;white-space:nowrap;text-overflow:ellipsis}:global(.queue-body strong){font-size:11px}:global(.queue-body span){color:var(--muted);font-size:9px}:global(.queue-body article button){background:transparent;color:var(--accent);font-size:10px}.state-dot{width:7px;height:7px;border-radius:50%;background:var(--faint)}.state-dot.downloading{background:#0a84ff}.state-dot.done{background:#30d158}.state-dot.failed{background:#ff453a}.state-dot.skipped{background:#ff9f0a}:global(.queue-body progress){width:100%;height:3px;accent-color:var(--accent)}.queue-separator{padding:6px 14px;background:var(--surface-2);color:var(--muted);font-size:9px;font-weight:700;text-transform:uppercase}.queue-footer{display:flex;justify-content:space-between;padding:10px 14px}.player-bar{position:fixed;left:262px;right:22px;bottom:18px;height:64px;z-index:15;display:grid;grid-template-columns:minmax(180px,.8fr) auto minmax(280px,1.2fr);align-items:center;gap:18px;padding:8px 17px;border:1px solid var(--line);border-radius:16px;background:color-mix(in srgb,var(--surface) 94%,transparent);box-shadow:var(--shadow);backdrop-filter:blur(30px)}.player-title{display:grid;gap:2px;min-width:0}:global(.player-title strong),:global(.player-title span){overflow:hidden;white-space:nowrap;text-overflow:ellipsis}:global(.player-title span){color:var(--muted);font-size:10px}.player-controls{display:flex;align-items:center;gap:5px}:global(.player-controls button){width:28px;height:28px;border-radius:50%;background:transparent}:global(.player-controls .play-button){background:var(--text);color:var(--surface)}.seek{display:grid;grid-template-columns:32px 1fr 32px;align-items:center;gap:7px;color:var(--muted);font-size:9px}:global(.seek input){width:100%;padding:0;border:0;box-shadow:none}
  .modal-backdrop{position:fixed;inset:0;z-index:50;display:grid;place-items:center;padding:28px;background:rgba(0,0,0,.38);backdrop-filter:blur(14px)}.modal-card{width:min(560px,100%);max-height:80vh;display:flex;flex-direction:column;padding:22px;border:1px solid var(--line);border-radius:20px;background:var(--surface);box-shadow:var(--shadow)}.modal-card.wide{width:min(880px,100%)}.modal-card header{display:flex;align-items:center;justify-content:space-between;padding-bottom:15px;border-bottom:1px solid var(--line)}.modal-card header h2{margin:4px 0 0}.modal-card header>button{width:32px;height:32px;border-radius:50%;background:var(--surface-2);font-size:20px}.artist-results{overflow:auto;display:grid;gap:4px;padding-top:10px}:global(.artist-results>button){display:grid;grid-template-columns:48px 1fr auto;align-items:center;gap:12px;padding:8px;border-radius:11px;background:transparent;text-align:left}:global(.artist-results>button:hover){background:var(--surface-2)}:global(.artist-results img),.artist-placeholder{width:48px;height:48px;border-radius:50%;object-fit:cover;background:var(--surface-2);display:grid;place-items:center}:global(.artist-results span){display:grid;gap:3px}:global(.artist-results small){color:var(--muted)}:global(.artist-results i){font-size:24px;color:var(--muted)}.select-actions{display:flex;align-items:center;gap:10px;padding:12px 0}.select-actions span{margin-left:auto;color:var(--muted);font-size:11px}.discography-grid{overflow:auto;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.discography-grid label{position:relative;min-width:0;padding:7px;border:1px solid transparent;border-radius:12px;cursor:pointer}.discography-grid label.selected{border-color:var(--accent);background:var(--accent-soft)}:global(.discography-grid img),.discography-grid .release-placeholder{width:100%;aspect-ratio:1;border-radius:9px;object-fit:cover;background:var(--surface-2)}.discography-grid input{position:absolute;right:12px;top:12px;accent-color:var(--accent)}.discography-grid strong,.discography-grid span{display:block;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}.discography-grid strong{margin-top:7px;font-size:11px}.discography-grid span{margin-top:3px;color:var(--muted);font-size:9px}.modal-card footer{padding-top:15px}.modal-card footer .primary{width:100%}
  /* Structural overrides keep the inner page as the sole scroll container in
     Wails/WebView2. Every flex/grid parent must allow its child to shrink. */
  .app-shell{min-height:0;overflow:hidden}
  .sidebar{min-height:0;padding-top:8px}
  .workspace{min-height:0;overflow:hidden}
  .page-content{overflow-x:hidden;overflow-y:auto;overscroll-behavior:contain;-webkit-overflow-scrolling:touch}
  .artwork{padding:0;box-shadow:none}
  .icon-button{width:38px;height:38px;flex:0 0 auto;display:grid;place-items:center;padding:0;border-radius:10px;background:var(--surface-2);color:var(--muted)}
  .icon-button:hover{background:var(--surface-hover);color:var(--text)}
  :global(.icon-button svg){width:19px;height:19px}
  .discover-search{max-width:760px!important;margin-bottom:28px}
  :global(.discover-search .search-bar){margin-top:0}
  .settings-overlay{position:fixed;inset:0;z-index:60;display:grid;place-items:center;padding:24px;background:rgba(0,0,0,.4);backdrop-filter:blur(16px)}
  .settings-dialog{width:min(980px,100%);height:min(860px,calc(100vh - 48px));min-height:0;display:flex;flex-direction:column;overflow:hidden;border:1px solid var(--line);border-radius:22px;background:var(--bg);box-shadow:var(--shadow)}
  .settings-dialog-header{height:76px;flex:0 0 auto;display:flex;align-items:center;justify-content:space-between;padding:14px 22px;border-bottom:1px solid var(--line);background:var(--surface)}
  .settings-dialog-header h1{margin:2px 0 0;font-size:24px}
  .settings-header-actions{display:flex;align-items:center;gap:8px}.settings-dialog-header .close-settings{width:34px;height:34px;display:grid;place-items:center;padding:0;border-radius:50%;background:var(--surface-2)}
  .settings-shell{display:grid;grid-template-columns:190px minmax(0,1fr);min-height:0;flex:1}.settings-tabs{display:flex;flex-direction:column;gap:4px;padding:16px 10px;border-right:1px solid var(--line);background:var(--sidebar)}.settings-tabs button{display:flex;align-items:center;gap:9px;padding:9px 10px;border-radius:9px;background:transparent;color:var(--muted);text-align:left}.settings-tabs button:hover{background:var(--surface-hover);color:var(--text)}.settings-tabs button.active{background:var(--accent-soft);color:var(--accent);font-weight:650}
  .settings-dialog .settings-layout{width:100%;max-width:none!important;min-height:0;overflow-y:auto;padding:20px 24px 54px}
  .settings-auto-status{position:absolute;right:72px;top:29px;color:var(--muted);font-size:11px}.settings-auto-status.error{color:#ff453a}.number-input{width:72px}.hidden{display:none!important}
  .settings-section{scroll-margin-top:18px}
  .library-tools{display:flex;align-items:center;gap:9px}.compact-search{flex:1;max-width:440px;padding:0 12px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}.compact-search input{border:0;background:transparent;box-shadow:none}.icon-select{display:flex;align-items:center;gap:7px;padding:0 9px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}:global(.icon-select select){border:0;background:transparent;box-shadow:none}.selection-toolbar{position:sticky;top:-30px;z-index:5;display:flex;align-items:center;gap:9px;margin:14px 0;padding:10px 12px;border:1px solid var(--accent);border-radius:12px;background:var(--surface);box-shadow:var(--shadow)}.selection-toolbar strong{margin-right:auto}.music-card.selectable.selected{padding:5px;margin:-5px;border:2px solid var(--accent);border-radius:17px}.select-release{position:absolute;z-index:3;left:9px;top:9px;width:24px;height:24px;display:grid;place-items:center;border:2px solid rgba(255,255,255,.9);border-radius:50%;background:rgba(0,0,0,.28);color:white;opacity:0;box-shadow:0 1px 5px rgba(0,0,0,.25)}.music-card:hover .select-release,.select-release.selected{opacity:1}.select-release.selected{border-color:var(--accent);background:var(--accent)}.offline-icon{margin-left:auto}
  .custom-links{min-height:130px;margin-top:8px;resize:vertical}.custom-destination{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-top:14px;padding:13px;border:1px solid var(--line);border-radius:12px}.custom-destination>div{display:grid;gap:4px;min-width:0}.custom-destination span{overflow:hidden;text-overflow:ellipsis;color:var(--muted);font-size:11px}.ipod-job-destination{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:9px 0 14px;padding:13px;border:1px solid var(--line);border-radius:12px}.ipod-job-destination>span{display:grid;gap:4px}.ipod-job-destination small{color:var(--muted);font-size:10px;line-height:1.35}.ipod-job-destination select{max-width:190px}.modal-actions{display:flex;justify-content:flex-end;gap:9px}.context-dismiss{position:fixed;inset:0;z-index:79}.context-menu{position:fixed;z-index:80;width:205px;display:grid;gap:3px;padding:6px;border:1px solid var(--line);border-radius:11px;background:var(--surface);box-shadow:var(--shadow)}.context-menu button{display:flex;align-items:center;gap:9px;padding:9px;border-radius:7px;background:transparent;text-align:left}.context-menu button:hover{background:var(--surface-2)}
  .job-status{padding:4px 8px;border-radius:99px;background:var(--surface-2);color:var(--muted);font-size:9px;text-transform:capitalize}.job-status.downloading{background:var(--accent-soft);color:var(--accent)}.history-toolbar>span{color:var(--muted);font-size:11px}
  .track-state-icon{display:grid;place-items:center;color:var(--muted)}:global(.queue-body article:has(.track-state-icon)) {grid-template-columns:18px 1fr auto}:global(.queue-body article:has(.track-state-icon) .track-state-icon:has(svg)){width:18px;height:18px}.rotating-loader{display:inline-grid;flex:0 0 auto;place-items:center;line-height:0;transform-origin:50% 50%;will-change:transform;animation:spin .72s linear infinite!important}:global(.rotating-loader svg){display:block}.queue-overall-progress{height:3px;background:linear-gradient(90deg,var(--accent) var(--queue-progress),var(--line) var(--queue-progress));transition:background .2s}:global(.queue-footer>span){color:var(--muted);font-size:10px}.queue-panel{padding-bottom:0}:global(.queue-header>svg){color:var(--muted);flex:0 0 auto}
  .source-heading{margin-top:22px;padding-top:18px;border-top:1px solid var(--line)}
  .apple-detail-card{height:min(760px,80vh)}
  .apple-detail-summary{display:flex;align-items:center;gap:14px;padding:16px 0;border-bottom:1px solid var(--line)}
  :global(.apple-detail-summary img){width:72px;height:72px;flex:0 0 auto;border-radius:10px;object-fit:cover}
  :global(.apple-detail-summary>div){flex:1;display:grid;gap:4px}
  :global(.apple-detail-summary span){color:var(--muted);font-size:11px}
  .apple-track-list{min-height:0;overflow-y:auto;padding-top:8px}
  :global(.apple-track-list article){display:grid;grid-template-columns:28px 42px minmax(0,1fr) auto;align-items:center;gap:10px;padding:7px;border-radius:9px}
  :global(.apple-track-list article:hover){background:var(--surface-2)}
  :global(.apple-track-list article>span),:global(.apple-track-list article>small){color:var(--muted);font-size:10px;text-align:center}
  :global(.apple-track-list img){width:42px;height:42px;border-radius:6px;object-fit:cover}
  :global(.apple-track-list article>div){min-width:0;display:grid;gap:3px}
  :global(.apple-track-list strong),:global(.apple-track-list article>div small){overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
  :global(.apple-track-list strong){font-size:12px}:global(.apple-track-list article>div small){color:var(--muted);font-size:10px}
  .device-heading{display:flex;align-items:end;justify-content:space-between;margin-bottom:20px}.device-heading h2{margin:5px 0;font-size:30px}.device-heading p:last-child{margin:0;color:var(--muted)}
  .device-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:16px}
  .device-choice{min-height:86px;display:grid;grid-template-columns:44px minmax(0,1fr);align-items:center;gap:12px;padding:14px;border:1px solid var(--line);border-radius:14px;background:var(--surface);color:var(--text);text-align:left}.device-choice>span{display:grid;gap:4px}.device-choice small{color:var(--muted)}
  .device-title{display:flex;align-items:center;gap:14px}:global(.device-title h2){margin:0 0 4px}:global(.device-title p){margin:0;color:var(--muted);font-size:12px}.ipod-glyph{width:54px;height:70px;display:grid;place-items:center;border-radius:8px;background:linear-gradient(145deg,#d8d8dc,#8e8e93);color:#fff;font-size:20px;box-shadow:inset 0 0 0 1px rgba(0,0,0,.12)}
  .device-storage{display:grid;gap:8px;margin:20px 0}:global(.device-storage>div){display:flex;justify-content:space-between;color:var(--muted);font-size:11px}:global(.device-storage strong){color:var(--text)}:global(.device-storage progress){width:100%;height:7px;accent-color:var(--accent)}
  :global(.device-grid dl){margin:0}:global(.device-grid dl>div){display:flex;justify-content:space-between;gap:20px;padding:8px 0;border-top:1px solid var(--line);font-size:11px}:global(.device-grid dt){color:var(--muted)}:global(.device-grid dd){margin:0;text-align:right;overflow:hidden;text-overflow:ellipsis}.capability-row{display:flex;flex-wrap:wrap;gap:5px;margin-top:14px}:global(.capability-row span){padding:4px 8px;border-radius:99px;background:var(--surface-2);color:var(--muted);font-size:9px;text-transform:uppercase}
  .library-label{display:flex;align-items:center;gap:7px;cursor:context-menu}:global(.library-label .offline-icon){margin-left:auto}.library-subnav{display:grid;gap:2px;padding:0 0 8px 16px}.library-subnav button{height:36px!important}.sidebar-index{display:grid;gap:5px;padding:9px 10px;color:var(--muted);font-size:10px}.sidebar-index>span{display:flex;align-items:center;gap:6px;white-space:nowrap}.sidebar-index progress{width:100%;height:3px;accent-color:var(--accent)}
  .library-tools{position:relative}.library-tool-button{width:38px;height:38px;display:grid;place-items:center;border:1px solid var(--line);border-radius:11px;background:var(--surface);color:var(--muted)}.tool-menu{position:relative}.tool-popover{position:absolute;right:0;top:44px;background:var(--surface)!important;color:var(--text)!important}.tool-popover button{color:var(--text)!important}.compact-search{height:38px!important;margin-top:0!important}.selection-toolbar{left:auto;right:auto;max-width:620px;margin:14px auto!important;border-color:var(--line)!important}:global(.select-release svg){display:block;margin:auto}.artist-library-list{display:grid}:global(.artist-library-list>button){display:grid;grid-template-columns:48px 1fr auto;align-items:center;gap:13px;padding:10px;border-bottom:1px solid var(--line);background:transparent;text-align:left}:global(.artist-library-list>button:hover){background:var(--surface-2)}.artist-avatar,:global(.artist-avatar img){width:44px;height:44px;display:grid;place-items:center;border-radius:50%;object-fit:cover;background:var(--surface-2)}:global(.artist-library-list>button>span:nth-child(2)){display:grid;gap:3px}:global(.artist-library-list small){color:var(--muted)}
  .download-track-list{max-height:min(42vh,360px);overflow:auto;border-top:1px solid var(--line)}:global(.download-track-list article){display:grid;grid-template-columns:22px 1fr;align-items:center;gap:10px;padding:9px 14px;border-bottom:1px solid var(--line)}:global(.download-track-list article>div){display:grid;gap:4px}:global(.download-track-list span){color:var(--muted);font-size:10px}:global(.download-track-list progress){width:100%;height:3px;accent-color:var(--accent)}.log-toggle{display:flex;align-items:center;gap:7px;margin:10px 14px;padding:7px 9px;border-radius:8px;background:var(--surface-2);color:var(--muted)}.clean-log{max-height:220px;overflow:auto;margin:0 14px 14px;padding:10px;border-radius:9px;background:var(--bg);font:10px/1.45 ui-monospace,monospace}.clean-log p{margin:3px 0;color:var(--muted)}.clean-log p.error{color:#ff453a}.clean-log p.success{color:#30b056}.job-options{position:relative}.job-options summary{width:34px;height:34px;display:grid;place-items:center;border-radius:9px;list-style:none;cursor:pointer}.job-options summary::-webkit-details-marker{display:none}.job-options>div{position:absolute;right:0;top:38px;z-index:8;width:150px;display:grid;padding:5px;border:1px solid var(--line);border-radius:9px;background:var(--surface);box-shadow:var(--shadow)}.job-options button{padding:8px;border-radius:6px;background:transparent;text-align:left;color:var(--text)}.job-options button:hover{background:var(--surface-2)}
  .download-worker-summary{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:16px 0 10px;padding:11px 13px;border:1px solid var(--line);border-radius:11px;background:var(--surface-2)}.download-worker-summary>div:first-child{display:grid;gap:2px}.download-worker-summary strong{font-size:12px}.download-worker-summary span,.worker-capacity{color:var(--muted);font-size:10px}.phase-counts{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:5px}.phase-counts span{padding:4px 7px;border-radius:99px;background:var(--surface);color:var(--muted)}:global(.download-track-list article).failed{background:color-mix(in srgb,var(--surface) 88%,var(--error-color,#ff453a) 12%)}.measured-progress{display:grid;grid-template-columns:minmax(120px,1fr) auto;align-items:center;gap:9px}.measured-progress small{color:var(--muted);font-size:9px;white-space:nowrap}.failure-panel{display:grid;gap:7px;margin-top:14px;padding:12px;border:1px solid color-mix(in srgb,var(--error-color,#ff453a) 42%,var(--line));border-radius:11px;background:color-mix(in srgb,var(--surface) 94%,var(--error-color,#ff453a) 6%)}.failure-panel header{display:flex;align-items:center;justify-content:space-between}.failure-panel header>button{display:flex;align-items:center;gap:6px;padding:5px 8px;border-radius:7px;background:var(--surface-2);color:var(--muted)}.failure-panel>article{display:grid;grid-template-columns:18px 1fr;align-items:start;gap:8px;padding-top:7px;border-top:1px solid var(--line);color:var(--error-color,#ff453a)}.failure-panel>article>div{display:grid;gap:2px}.failure-panel>article span{color:var(--muted);font-size:10px;line-height:1.4}.worker-capacity{font-weight:500}
  .preparation-panel{display:flex;align-items:center;gap:14px;padding:18px 20px}:global(.preparation-panel>svg){flex:0 0 auto;color:var(--accent)}.preparation-panel h2{margin:3px 0;font-size:18px}.preparation-panel span{color:var(--muted);font-size:11px}.location-actions{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:7px}.download-location-row>div:first-child{min-width:0}.download-location-row>div:first-child span{display:block;max-width:460px;overflow:hidden;text-overflow:ellipsis}:global(.settings-section .location-actions button){white-space:nowrap}
  .compact-queue{display:grid;grid-template-columns:1fr auto;padding-bottom:3px!important}.compact-queue .queue-header{min-width:0}.queue-art{width:42px;height:42px;flex:0 0 42px;border-radius:8px;object-fit:cover}.queue-art.placeholder{display:grid;place-items:center;background:var(--surface-2)}.queue-controls{display:flex;align-items:center;padding-right:10px}.queue-controls button{width:30px;height:30px;display:grid;place-items:center;border-radius:50%;background:transparent;color:var(--muted)}.queue-controls button:hover{background:var(--surface-2);color:var(--text)}.compact-queue .queue-overall-progress{grid-column:1/-1}
  .queued-job-actions{display:flex!important;align-items:center;gap:4px!important}.queued-job-actions button{width:30px;height:30px;display:grid;place-items:center;border-radius:50%;background:transparent;color:var(--muted)}.queued-job-actions button:hover:not(:disabled){background:var(--surface-2);color:var(--text)}.queued-job-actions button:disabled{opacity:.28}.index-maintenance{display:grid;gap:13px;margin-top:15px;padding:15px;border:1px solid var(--line);border-radius:13px;background:var(--surface-2)}.index-maintenance>div{display:grid;gap:4px}.index-maintenance>div:last-child{display:flex;justify-content:flex-end;gap:9px}.index-maintenance span,.path-copy span{color:var(--muted);font-size:11px}.path-copy{min-width:0;display:grid;gap:5px}.path-copy span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.browse-location{flex:0 0 auto}.download-location-row{grid-template-columns:minmax(0,1fr) auto!important}
  .app-toast{position:fixed;z-index:90;right:24px;top:24px;width:min(430px,calc(100vw - 48px));display:flex;align-items:flex-start;gap:12px;padding:14px 15px;border:1px solid rgba(255,69,58,.35);border-radius:13px;background:color-mix(in srgb,var(--surface) 94%,#ff453a 6%);box-shadow:var(--shadow);font-size:12px}.app-toast.warning{border-color:rgba(255,159,10,.4);background:color-mix(in srgb,var(--surface) 94%,#ff9f0a 6%)}.app-toast.success{border-color:rgba(48,209,88,.4)}.app-toast span{flex:1;line-height:1.45}.app-toast button{width:24px;height:24px;display:grid;place-items:center;border-radius:50%;background:var(--surface-2)}.confirm-backdrop{z-index:100}.confirm-card{width:min(430px,100%)}.confirm-card>p{margin:18px 0 4px;color:var(--muted);line-height:1.5}.confirm-card .modal-actions{justify-content:flex-end}.confirm-card .primary.danger{background:#ff453a;color:white}
  .release-art{width:80px;height:80px;flex:0 0 80px;border-radius:12px;overflow:hidden;background:var(--surface-2)}.release-header>div:not(.release-art){flex:1}
  .add-custom-button{display:inline-flex!important;align-items:center!important;justify-content:center;gap:7px;line-height:1}:global(.add-custom-button svg){display:block;flex:0 0 auto}.add-custom-button span{display:block;line-height:1}
  .topbar>div:first-child{display:grid;grid-template-columns:auto 1fr;align-items:center;column-gap:10px}.topbar>div:first-child>.eyebrow,.topbar>div:first-child>h1{grid-column:2}.topbar-back{grid-row:1/3;grid-column:1;width:34px;height:34px;display:grid;place-items:center;border-radius:50%;background:var(--surface-2)}
  .library-detail-page{position:relative;min-height:calc(100vh - 145px);margin:-30px -34px -110px!important;padding:38px 34px 120px;overflow:hidden}.library-detail-backdrop{position:absolute;z-index:0;inset:-80px -50px auto;height:430px;background-position:center 32%;background-size:cover;filter:blur(55px) saturate(.9);opacity:.42;transform:scale(1.12);mask-image:linear-gradient(#000 20%,transparent 100%)}.favourite-backdrop{background:radial-gradient(circle at 38% 30%,var(--accent),#43383c 62%,transparent 78%)}.library-detail-page>*:not(.library-detail-backdrop){position:relative;z-index:1}.library-detail-hero{min-height:280px;display:flex;align-items:end;gap:30px;padding:18px 6px 34px}.library-detail-art{width:230px;height:230px;flex:0 0 230px;border-radius:16px;object-fit:cover;box-shadow:0 20px 55px rgba(0,0,0,.28)}.favourite-art{display:grid;place-items:center;background:linear-gradient(145deg,#f4f4f6,#d9d9de);color:var(--accent)}.library-detail-copy{display:grid;align-content:end;gap:6px;min-width:0;padding-bottom:4px}.library-detail-copy h2{margin:2px 0;font-size:42px;line-height:1.02;letter-spacing:-.045em}.library-detail-copy>strong,.library-detail-copy>span{text-transform:uppercase;color:var(--muted);font-size:11px}.detail-download{width:max-content;margin-top:14px}.detail-track-tools{display:flex;justify-content:flex-end;align-items:center;gap:9px;margin:12px 0}.detail-search{height:38px;width:min(310px,100%);margin-right:auto}.detail-track-tools select,.detail-order{height:38px}.detail-order{display:flex;align-items:center;gap:7px}.library-detail-tracks{display:grid;gap:5px}:global(.library-detail-tracks article){min-height:58px;display:grid;grid-template-columns:28px 44px minmax(170px,1.35fr) minmax(130px,.7fr) minmax(160px,1fr) 42px 32px;align-items:center;gap:10px;padding:6px 9px;border-radius:10px;background:color-mix(in srgb,var(--surface) 77%,transparent);backdrop-filter:blur(18px)}:global(.library-detail-tracks article:hover){background:color-mix(in srgb,var(--surface) 92%,transparent)}:global(.library-detail-tracks img),.detail-track-placeholder{width:44px;height:44px;display:grid;place-items:center;border-radius:7px;object-fit:cover;background:var(--surface-2)}:global(.library-detail-tracks strong),:global(.library-detail-tracks article>span){overflow:hidden;white-space:nowrap;text-overflow:ellipsis}:global(.library-detail-tracks article>span),:global(.library-detail-tracks small){color:var(--muted);font-size:11px}:global(.library-detail-tracks article>button){width:30px;height:30px;display:grid;place-items:center;border-radius:50%;background:transparent}.detail-track-number{text-align:center}.detail-index-note,.detail-inline-error{padding:18px;border-radius:11px;background:var(--surface);color:var(--muted)}.detail-inline-error{color:#ff453a}
  .settings-overlay{width:100vw;max-width:none!important;margin:0!important}
  .library-subnav{padding-left:2px}.favourite-nav-icon,.favourite-empty-icon{color:var(--accent);fill:var(--accent)}
  .detail-primary-actions{display:flex;align-items:center;gap:8px;margin-top:14px}.detail-primary-actions .detail-download{margin-top:0}.detail-primary-actions .tool-menu{position:relative}.detail-download{height:38px;display:inline-flex;align-items:center;justify-content:center;gap:7px;line-height:1}.detail-download span{display:block;line-height:1}.detail-popover{position:absolute;top:44px;left:0}.detail-track-tools select,.detail-order{font-size:12px;line-height:1}.detail-track-tools select{padding-top:0;padding-bottom:0}
  :global(.library-detail-tracks article){position:relative;background:color-mix(in srgb,var(--surface) 86%,transparent);backdrop-filter:none;content-visibility:auto;contain:layout paint style}.track-more{position:relative}.track-more>button{width:30px;height:30px;display:grid;place-items:center;border-radius:50%;background:transparent}.track-popover{position:absolute;right:0;top:34px}.local-placeholder-art{display:grid;place-items:center;background:var(--surface-2);color:var(--faint)}
  .downloaded-release-grid{grid-template-columns:repeat(auto-fill,minmax(155px,1fr));gap:24px 18px}.local-detail-tracks{display:grid;gap:5px}:global(.local-detail-tracks>button){width:100%;min-height:58px;display:grid;grid-template-columns:28px 44px minmax(170px,1.35fr) minmax(130px,.7fr) minmax(160px,1fr) 52px;align-items:center;gap:10px;padding:6px 9px;border-radius:10px;background:color-mix(in srgb,var(--surface) 86%,transparent);color:var(--text);text-align:left;content-visibility:auto;contain:layout paint style}:global(.local-detail-tracks>button:hover),:global(.local-detail-tracks>button.playing){background:color-mix(in srgb,var(--surface) 96%,transparent)}:global(.local-detail-tracks>button.playing){color:var(--accent)}:global(.local-detail-tracks img){width:44px;height:44px;border-radius:7px;object-fit:cover}:global(.local-detail-tracks>button>span),:global(.local-detail-tracks small){overflow:hidden;white-space:nowrap;text-overflow:ellipsis;color:var(--muted);font-size:11px}
  :global(.detail-track-number svg){display:block;margin:auto}.art-grid,.downloaded-release-grid{grid-template-columns:repeat(auto-fill,minmax(var(--artwork-size),1fr));gap:calc(24px * var(--density-space)) calc(18px * var(--density-space))}.setting-row{min-height:calc(64px * var(--density-space));padding:calc(12px * var(--density-space)) 0}.queue-panel.player-open{bottom:110px}.settings-layout[data-page="appearance"]{overflow:hidden}.settings-layout[data-page="appearance"] :global(#settings-appearance){height:100%;overflow-y:auto}
  .library-detail-backdrop{overflow:hidden}
  :global(.artist-library-list){position:relative;z-index:1}
  :global(.artist-library-list) button{width:100%;height:61px;display:grid;grid-template-columns:48px 1fr auto;align-items:center;gap:13px;padding:8px 10px;border-bottom:1px solid var(--line);background:transparent;text-align:left}
  :global(.artist-library-list) button:hover{background:var(--surface-2)}
  :global(.artist-library-list) button>span:nth-child(2){display:grid;gap:3px}
  :global(.artist-library-list) small{color:var(--muted)}
  :global(.artist-results) button{width:100%;height:64px;display:grid;grid-template-columns:48px 1fr auto;align-items:center;gap:12px;padding:8px;border-radius:11px;background:transparent;text-align:left}
  :global(.artist-results) button:hover{background:var(--surface-2)}
  :global(.artist-results) button>span{display:grid;gap:3px}
  :global(.artist-results) button small{color:var(--muted)}
  :global(.download-track-list){border-top:1px solid var(--line)}
  :global(.download-track-list) article{width:100%;height:72px;display:grid;grid-template-columns:22px 1fr;align-items:center;gap:10px;padding:9px 14px;border-bottom:1px solid var(--line)}
  :global(.download-track-list) article>div{display:grid;gap:4px}
  :global(.download-track-list) article span{color:var(--muted);font-size:10px}
  :global(.download-track-list) article progress{width:100%;height:3px;accent-color:var(--accent)}
  :global(.download-track-list) article.failed{background:color-mix(in srgb,var(--surface) 88%,var(--error-color,#ff453a) 12%)}
  :global(.failure-list) article{width:100%;height:54px;display:grid;grid-template-columns:18px 1fr;align-items:start;gap:8px;padding:7px 0;border-top:1px solid var(--line);color:var(--error-color,#ff453a)}
  :global(.failure-list) article>div{display:grid;gap:2px}
  :global(.failure-list) article span{color:var(--muted);font-size:10px;line-height:1.4}
  :global(.library-detail-tracks){position:relative;z-index:1}
  :global(.library-detail-tracks) article{position:relative;width:100%;height:58px;display:grid;grid-template-columns:28px 44px minmax(170px,1.35fr) minmax(130px,.7fr) minmax(160px,1fr) 42px 32px;align-items:center;gap:10px;padding:6px 9px;border-radius:10px;background:color-mix(in srgb,var(--surface) 86%,transparent);contain:layout paint style}
  :global(.library-detail-tracks) article:hover{background:color-mix(in srgb,var(--surface) 96%,transparent)}
  :global(.library-detail-tracks) article strong,:global(.library-detail-tracks) article>span{overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
  :global(.library-detail-tracks) article>span,:global(.library-detail-tracks) article small{color:var(--muted);font-size:11px}
  :global(.local-detail-tracks){position:relative;z-index:1}
  :global(.local-detail-tracks) button{width:100%;height:58px;display:grid;grid-template-columns:28px 44px minmax(170px,1.35fr) minmax(130px,.7fr) minmax(160px,1fr) 52px;align-items:center;gap:10px;padding:6px 9px;border-radius:10px;background:color-mix(in srgb,var(--surface) 86%,transparent);color:var(--text);text-align:left}
  :global(.local-detail-tracks) button:hover,:global(.local-detail-tracks) button.playing{background:color-mix(in srgb,var(--surface) 96%,transparent)}
  :global(.local-detail-tracks) button.playing{color:var(--accent)}
  :global(.local-detail-tracks) button>span,:global(.local-detail-tracks) button small{overflow:hidden;white-space:nowrap;text-overflow:ellipsis;color:var(--muted);font-size:11px}
  :global(:root[data-motion='reduced'] *),:global(:root[data-motion='reduced'] *::before),:global(:root[data-motion='reduced'] *::after){scroll-behavior:auto!important;animation-duration:.001ms!important;animation-iteration-count:1!important;transition-duration:.001ms!important}
  @media(max-width:1000px){.control-grid,.downloaded-layout{grid-template-columns:1fr}.choice-grid{grid-template-columns:repeat(3,1fr)}.release-detail{position:static}.hero-art{display:none}.discography-grid{grid-template-columns:repeat(3,1fr)}}
  @media(max-width:760px){.app-shell{grid-template-columns:70px minmax(0,1fr)}.sidebar{padding:18px 9px}.brand-lockup{padding:2px 4px 20px;font-size:15px;text-align:center}.sidebar nav span,.nav-label,:global(.sidebar-footer .appearance-control),.privacy-status,.settings-nav span{display:none}.sidebar nav button,.settings-nav{justify-content:center;padding:0}.page-content{padding:22px 18px 110px}.topbar{padding:14px 18px}.hero{padding:25px}:global(.hero h2){font-size:30px}.notice-card{grid-template-columns:auto 1fr}.notice-card .primary{grid-column:1/-1}.art-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.control-grid{grid-template-columns:1fr}.choice-grid{grid-template-columns:repeat(2,1fr)}.downloaded-layout{grid-template-columns:1fr}.release-grid{grid-template-columns:repeat(2,1fr)}.queue-panel,.queue-panel.expanded{right:10px;bottom:10px;width:calc(100vw - 90px)}.player-bar{left:80px;right:10px;grid-template-columns:1fr auto}.seek{display:none}.discography-grid{grid-template-columns:repeat(2,1fr)}}
  .library-detail-page{width:calc(100% + 68px);max-width:none!important}
  @media(max-width:760px){.library-detail-page{width:calc(100% + 36px);margin:-22px -18px -110px!important;padding:28px 18px 120px}.library-detail-hero{align-items:center}.library-detail-art{width:130px;height:130px;flex-basis:130px}.library-detail-copy h2{font-size:30px}:global(.library-detail-tracks) article{grid-template-columns:24px 40px minmax(120px,1fr) 34px}:global(.library-detail-tracks) article>span:nth-of-type(n+2),:global(.library-detail-tracks) article>small{display:none}:global(.local-detail-tracks) button{grid-template-columns:24px 40px minmax(120px,1fr) 42px}:global(.local-detail-tracks) button>span:nth-of-type(n+2),:global(.local-detail-tracks) button>strong~span,:global(.local-detail-tracks) button>small{display:none}.detail-track-tools{flex-wrap:wrap}}
  @media(max-width:760px){.queue-panel.player-open{bottom:154px}.settings-overlay{padding:10px}.settings-dialog{height:calc(100vh - 20px)}.settings-shell{grid-template-columns:1fr;grid-template-rows:auto minmax(0,1fr)}.settings-tabs{max-width:100%;flex-direction:row;overflow-x:auto;padding:8px;border-right:0;border-bottom:1px solid var(--line)}.settings-tabs button{flex:0 0 auto;white-space:nowrap}.settings-dialog .settings-layout{padding:14px 14px 48px}.setting-row{align-items:flex-start;flex-direction:column;gap:10px}.setting-row>select{width:100%}}
</style>
