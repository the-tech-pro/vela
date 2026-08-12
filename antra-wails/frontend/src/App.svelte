<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { GetConfig, SaveConfig, PickDirectory, StartDownload, RetryTrackDownload, CancelDownload, PauseDownload, ResumeDownload, SetDownloadWorkerCount, GetHistory, AddHistory, ClearHistory, ValidateTidalAuth, StartTidalOAuthLogin, StartAppleBrowserLogin, StartAmazonBrowserLogin, ConfirmAmazonLogin, CaptureSpDC } from '../wailsjs/go/main/App.js';
  import { GetArtistDiscography, SearchArtists, CheckSourceHealth, GetDownloadedMusicLibrary, RefreshDownloadedMusicLibrary, GetDownloadedRelease, GetAppleMusicLibrary, RefreshAppleMusicLibrary, GetAppleMusicPlaylistDetail, GetAppleMusicArtistDetail, StartAppleMusicIndex, ResetAppleMusicIndex, GetIPodDevices, RunAutoSync, GetTrackLyrics } from '../wailsjs/go/main/App.js';
  import { EventsOn, ClipboardGetText } from '../wailsjs/runtime/runtime.js';
  import type { main } from '../wailsjs/go/models';
  import { Library, Download, HardDriveDownload, Users, Compass, Settings, Plus, Search, Smartphone,
    WifiOff, SlidersHorizontal, ArrowUpDown, MoreHorizontal, Check, Circle,
    LoaderCircle, Clock3, ChevronDown, RefreshCw, X, FolderOpen, ArrowLeft,
    Star, Album, ListMusic, UserRound, Pause, Play, FileText } from 'lucide-svelte';

  let config: main.Config = {
    download_path: '',
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
    save_cover_art_sidecar: false,
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
  };
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

  interface FailedTrackPayload {
    title: string;
    artists: string[];
    album: string;
    playlist_name?: string;
    playlist_owner?: string;
    playlist_description?: string;
    playlist_position?: number;
    release_year?: number;
    release_date?: string;
    track_number?: number;
    disc_number?: number;
    total_tracks?: number;
    total_discs?: number;
    duration_ms?: number;
    isrc?: string;
    spotify_id?: string;
    album_id?: string;
    spotify_url?: string;
    amazon_asin?: string;
    upc?: string;
    iswc?: string;
    audio_traits?: string[];
    genres?: string[];
    album_artists?: string[];
    artwork_url?: string;
    playlist_artwork_url?: string;
    is_explicit?: boolean;
    lyrics?: string;
    synced_lyrics?: string;
  }

  // ── Theme system ────────────────────────────────────────────────────────────
  type Appearance = 'system' | 'light' | 'dark';
  const uiDemoMode = new URLSearchParams(window.location.search).get('demo') === '1';
  let appearance: Appearance = 'system';
  function applyAppearance(value: Appearance, persist = true) {
    appearance = value;
    const dark = value === 'dark' || (value === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.documentElement.setAttribute('data-appearance', dark ? 'dark' : 'light');
    config.theme = value;
    if (persist && !uiDemoMode) SaveConfig(config);
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
  type SettingsPage = 'general' | 'apple' | 'downloads' | 'audio' | 'discovery' | 'naming' | 'providers';
  let settingsPage: SettingsPage = 'general';
  let settingsSaveState: 'idle' | 'saving' | 'saved' | 'error' = 'idle';
  let settingsError = '';
  let showDownloadedMusic = false;
  let settingsButtonEl: HTMLButtonElement | null = null;
  let historyItems: any[] = [];
  let inputUrl = '';
  let inputUrlEl: HTMLTextAreaElement | null = null;
  let showCustomDownload = false;
  let customDestination = '';
  let isDownloading = false;
  interface DownloadJob { id: string; url: string; title: string; artwork?: string; status: 'waiting' | 'downloading' | 'downloaded' | 'failed' | 'cancelled'; total: number; completed: number; }
  let downloadJobs: DownloadJob[] = [];
  type AppPage = 'library' | 'downloads' | 'downloaded' | 'devices' | 'settings';
  let currentPage: AppPage = 'library';
  let queuePaused = false;
  let showDownloadLogs = false;
  const queueStorageKey = 'vela-download-queue-v2';

  function persistDownloadQueue() {
    if (!uiDemoMode) localStorage.setItem(queueStorageKey, JSON.stringify({ jobs: downloadJobs, paused: queuePaused }));
  }

  function restoreDownloadQueue() {
    try {
      const saved = JSON.parse(localStorage.getItem(queueStorageKey) || '{}');
      downloadJobs = (saved.jobs || []).map((job: DownloadJob) => job.status === 'downloading' ? { ...job, status: 'waiting' as const } : job);
      queuePaused = !!saved.paused;
    } catch {
      downloadJobs = [];
      queuePaused = false;
    }
  }

  async function selectPage(page: AppPage) {
    if (page !== 'library') {
      showAppleLibraryDetail = false;
      showDetailMenu = false;
    }
    if (page !== 'downloaded') {
      downloadedSelectedRelease = null;
      downloadedSelectedPath = '';
    }
    currentPage = page;
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
  const downloadedDetailCache = new Map<string, LibraryReleaseDetail>();
  let brokenArtwork = new Set<string>();
  function markArtworkBroken(key: string) {
    brokenArtwork = new Set([...brokenArtwork, key]);
  }
  let audioEl: HTMLAudioElement;
  let playerQueue: LibraryReleaseTrack[] = [];
  let playerTrackIndex = -1;
  let playerCurrentTime = 0;
  let playerDuration = 0;
  let playerSeeking = false;
  let playerVolume = 1;
  let playerError = '';
  let playerReleaseTitle = '';
  $: currentPlayerTrack = playerTrackIndex >= 0 ? playerQueue[playerTrackIndex] : null;

  // ── Synced Lyrics (SF-2) ────────────────────────────────────────────────────
  interface LyricsLine { time_ms: number; text: string; }
  let lyricsLines: LyricsLine[] = [];
  let lyricsSynced = false;
  let lyricsLoading = false;
  let showLyrics = false;
  let lyricsContainerEl: HTMLDivElement;

  // Index of the last lyrics line whose time_ms <= current playback position.
  $: activeLyricIdx = (lyricsSynced && lyricsLines.length > 0)
    ? lyricsLines.reduce((best, line, i) =>
        line.time_ms <= playerCurrentTime * 1000 ? i : best, -1)
    : -1;

  // Auto-scroll the active line into view when it changes.
  $: if (activeLyricIdx >= 0 && lyricsContainerEl) {
    const el = lyricsContainerEl.children[activeLyricIdx] as HTMLElement;
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

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
  }
  let appleLibrary: AppleLibraryData | null = null;
  let appleLibraryLoading = false;
  let appleLibraryError = '';
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

  interface IPodDevice {
    path: string;
    name: string;
    model_family: string;
    generation: string;
    model_number: string;
    capacity: string;
    disk_size_gb: number;
    free_space_gb: number;
    firmware: string;
    filesystem_type: string;
    checksum_type: string;
    audio_codecs: string[];
    podcasts_supported: boolean;
    voice_memos_supported: boolean;
    uses_sqlite_db: boolean;
  }
  let ipodDevices: IPodDevice[] = [];
  let ipodDevicesLoading = false;
  let ipodDevicesError = '';

  async function loadIPodDevices() {
    if (uiDemoMode) return;
    ipodDevicesLoading = true;
    ipodDevicesError = '';
    try {
      const raw = await GetIPodDevices();
      const data = typeof raw === 'string' ? JSON.parse(raw) : raw;
      if (data?.error) ipodDevicesError = data.error;
      else ipodDevices = data?.devices || [];
    } catch (e: any) {
      ipodDevicesError = e?.message || String(e);
    } finally {
      ipodDevicesLoading = false;
    }
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
  const appleDetailCache = new Map<string, AppleLibraryDetail>();

  function closeAppleLibraryDetail() {
    if (libraryView === 'favourites') return;
    showAppleLibraryDetail = false;
    appleLibraryDetailError = '';
    showDetailMenu = false;
    detailTrackMenuIndex = null;
  }

  function openLibraryView(view: LibraryView) {
    libraryView = view;
    showAppleLibraryDetail = false;
    appleLibraryDetail = null;
    showDetailMenu = false;
    void selectPage('library');
  }

  async function openAppleLibraryDetail(url: string, fallbackName: string, fallbackImage = '') {
    showAppleLibraryDetail = true;
    appleLibraryDetailError = '';
    const cached = appleDetailCache.get(url);
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
      appleDetailCache.set(url, appleLibraryDetail);
      return;
    }
    try {
      const raw = await GetAppleMusicPlaylistDetail(url);
      const data = typeof raw === 'string' ? JSON.parse(raw) : raw;
      if (data?.error) appleLibraryDetailError = data.error;
      else {
        appleLibraryDetail = data as AppleLibraryDetail;
        appleDetailCache.set(url, appleLibraryDetail);
      }
    } catch (e: any) {
      appleLibraryDetailError = e?.message || String(e);
    } finally {
      appleLibraryDetailLoading = false;
    }
  }

  async function openAppleArtistDetail(name: string, artwork = '') {
    showAppleLibraryDetail = true;
    appleLibraryDetailError = '';
    const key = `artist:${name.toLocaleLowerCase()}`;
    const cached = appleDetailCache.get(key);
    appleLibraryDetail = cached || { name, image_url: artwork, content_type: 'artist', track_count: 0, tracks: [] };
    appleLibraryDetailLoading = !cached;
    if (cached) return;
    try {
      const detail = JSON.parse(await GetAppleMusicArtistDetail(name) || '{}');
      if (detail.error) throw new Error(detail.error);
      appleLibraryDetail = { ...detail, image_url: artwork || detail.image_url || '' };
      appleDetailCache.set(key, appleLibraryDetail);
    } catch (e: any) {
      appleLibraryDetailError = e?.message || String(e);
    } finally {
      appleLibraryDetailLoading = false;
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
    StartAppleMusicIndex().catch(() => {
      appleIndexing = false;
      appleIndexStarted = false;
    });
  }

  async function loadAppleMusicLibrary(forceRefresh = false) {
    if (!config.apple_music_user_token || !config.apple_authorization_token) return;
    appleLibraryLoading = !appleLibrary;
    appleLibraryError = '';
    try {
      const raw = forceRefresh ? await RefreshAppleMusicLibrary() : await GetAppleMusicLibrary();
      const data = typeof raw === 'string' ? JSON.parse(raw) : raw;
      if (data.error) {
        appleLibraryError = data.error;
      } else {
        appleLibrary = data as AppleLibraryData;
        for (const imageURL of [...appleLibrary.albums, ...appleLibrary.playlists].map(item => item.image_url).filter(Boolean)) {
          const image = new Image();
          image.decoding = 'async';
          image.src = imageURL as string;
        }
        setTimeout(startAppleIndexOnce, 500);
        if (libraryView === 'favourites' && !showAppleLibraryDetail) setTimeout(openFavourites, 0);
      }
    } catch (e: any) {
      appleLibraryError = e?.message || String(e);
    } finally {
      appleLibraryLoading = false;
    }
  }

  function toggleLibrarySelection(url: string) {
    const next = new Set(selectedLibraryItems);
    next.has(url) ? next.delete(url) : next.add(url);
    selectedLibraryItems = next;
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
  function filteredLibraryAlbums() { return computeLibraryAlbums(appleLibrary?.albums || [], libraryFilter, librarySort, librarySortDirection); }
  function filteredLibraryPlaylists() { return computeLibraryPlaylists(appleLibrary?.playlists || [], libraryFilter, librarySort, librarySortDirection); }
  $: visibleLibraryDetailTracks = [...(appleLibraryDetail?.tracks || [])]
    .filter(track => !libraryDetailFilter.trim() || `${track.title} ${track.artist} ${track.album}`.toLocaleLowerCase().includes(libraryDetailFilter.trim().toLocaleLowerCase()))
    .sort((a, b) => {
      if (libraryDetailSort === 'title') return a.title.localeCompare(b.title);
      if (libraryDetailSort === 'artist') return a.artist.localeCompare(b.artist);
      if (libraryDetailSort === 'album') return a.album.localeCompare(b.album);
      return (a.position || 0) - (b.position || 0);
    });
  $: if (libraryDetailDescending) visibleLibraryDetailTracks.reverse();

  function libraryArtists(): { name: string; albums: AppleLibraryAlbumItem[]; image?: string | null }[] {
    const groups = new Map<string, AppleLibraryAlbumItem[]>();
    for (const album of appleLibrary?.albums || []) {
      const name = album.artist_name || 'Unknown Artist';
      groups.set(name, [...(groups.get(name) || []), album]);
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([name, albums]) => ({ name, albums, image: albums.find(a => a.image_url)?.image_url }));
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

  async function resetAppleIndex() {
    if (!confirm('Reset the local Apple Music index? Your connection and downloaded files will not be removed.')) return;
    try {
      await ResetAppleMusicIndex();
      appleDetailCache.clear();
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
    }
  }

  function downloadSelectedLibraryItems() {
    const urls = [...selectedLibraryItems];
    if (!urls.length) return;
    inputUrl = urls.join('\n');
    selectedLibraryItems = new Set();
    currentPage = 'downloads';
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
      await SaveConfig(config);
    }
    showCustomDownload = false;
    await startDownload();
  }

  // Trigger a download by pasting a URL and starting immediately
  function downloadPlaylistUrl(url: string) {
    inputUrl = url;
    activeTab = 'url';
    currentPage = 'downloads';
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
    SaveConfig(config);
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
    };
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
    await SaveConfig(config);
  }

  async function setBitDepth(depth: string) {
    config.output_format = _fmtBase + '-' + depth;
    await SaveConfig(config);
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
  let tracklistEl: HTMLDivElement;
  let tracklistAtBottom = true;
  let tracklistHasScrolled = false;

  function updateTracklistScroll() {
    if (!tracklistEl) return;
    const d = tracklistEl.scrollHeight - tracklistEl.scrollTop - tracklistEl.clientHeight;
    tracklistAtBottom = d <= 40;
    tracklistHasScrolled = true;
  }

  function scrollTracklistToBottom() {
    if (tracklistEl) { tracklistEl.scrollTop = tracklistEl.scrollHeight; tracklistAtBottom = true; }
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

  // ── Multi-URL separators ────────────────────────────────────────────────────
  let separatorMeta: Record<string, { title: string; artwork: string }> = {};

  // Logs terminal
  let logs: {id: number, type: string, text: string, isRawHtml?: boolean}[] = [];
  let logId = 0;
  let terminalContainer: HTMLDivElement;
  let terminalEnd: HTMLElement;
  let shouldAutoScroll = true;
  let logAtBottom = true;
  let showLog = false;
  let trackOrder: string[] = [];
  let trackLabels: Record<string, string> = {};
  let playlistTitle = '';
  let playlistArtwork = '';
  let playlistArtists = '';
  let playlistReleaseDate = '';
  let playlistContentType = '';
  let playlistQualityBadge = '';
  let playlistTotalDurationMs = 0;
  let playlistTotalTracks = 0;

  // Track progress mapping
  let activeTracks: Record<string, {
    progress?: number,
    text: string,
    error?: string,
    mode: 'status' | 'progress',
    status: 'resolving' | 'downloading' | 'done' | 'failed' | 'skipped',
    retrying?: boolean,
    trackData?: FailedTrackPayload,
  }> = {};
  let currentPlaylistTrackKeysByIndex: Record<number, string> = {};
  let currentPlaylistTrackCount = 0;
  $: queueTrackKeys = trackOrder.filter(key => !key.startsWith('__SEP__'));
  $: queueFinishedCount = queueTrackKeys.filter(key => ['done', 'skipped', 'failed'].includes(activeTracks[key]?.status)).length;
  $: queueOverallProgress = queueTrackKeys.length ? Math.round((queueFinishedCount / queueTrackKeys.length) * 100) : 0;

  // ── Failed Tracks Panel (ST-4) ──────────────────────────────────────────────
  let dismissedFailures = new Set<string>();
  let retryQueue: string[] = [];
  let retryQueueTotal = 0;
  let failedPanelCollapsed = false;

  $: failedEntries = trackOrder
    .filter(k => !k.startsWith('__SEP__') && activeTracks[k]?.status === 'failed' && !dismissedFailures.has(k))
    .map(k => ({
      key: k,
      label: trackLabels[k] || k,
      error: activeTracks[k]?.error || activeTracks[k]?.text || 'Failed',
      trackData: activeTracks[k]?.trackData,
    }));

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
    const idx = Number(data?.track_index || 0);
    if (idx > 0 && currentPlaylistTrackKeysByIndex[idx]) {
      return currentPlaylistTrackKeysByIndex[idx];
    }
    const td = data?.track_data || {};
    if (td.spotify_id) return `spotify:${td.spotify_id}`;
    if (td.apple_music_id) return `apple:${td.apple_music_id}`;
    if (td.deezer_track_id) return `deezer:${td.deezer_track_id}`;
    if (td.tidal_track_id) return `tidal:${td.tidal_track_id}`;
    if (td.isrc && td.album_id && td.track_number) return `albumtrack:${td.album_id}:${td.disc_number || 1}:${td.track_number}:${td.isrc}`;
    return fallbackTrackKey(data?.artist, data?.track, td);
  }

  function updateActiveTrack(trackName: string, patch: Partial<typeof activeTracks[string]>) {
    const existing = activeTracks[trackName] || { mode: 'status' as const, text: 'Resolving source...', status: 'resolving' as const };
    activeTracks[trackName] = { ...existing, ...patch };
    activeTracks = { ...activeTracks };
  }

  function clearTrackInterval(trackName: string) {
    const intervalId = (activeTracks[trackName] as any)?._intervalId;
    if (intervalId) {
      clearInterval(intervalId);
      delete (activeTracks[trackName] as any)._intervalId;
    }
  }

  onMount(async () => {
    if (uiDemoMode) {
      config = {
        ...config,
        first_run_complete: true,
        download_path: 'C:\\Music\\Vela Library',
        apple_enabled: true,
        apple_authorization_token: 'demo-local-token',
        apple_music_user_token: 'demo-local-token',
        theme: 'system',
      };
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
      historyItems = [
        { date: new Date().toISOString(), url: 'https://music.apple.com/demo/album', title: 'Afterglow', total: 10, downloaded: 10, failed: 0 },
        { date: new Date(Date.now() - 86400000).toISOString(), url: 'https://music.apple.com/demo/playlist', title: 'Late Night Drive', total: 42, downloaded: 40, failed: 2 },
      ];
      downloadJobs = [
        { id: 'demo-job-1', url: 'apple-music://demo/afterglow', title: 'Afterglow', status: 'downloading', total: 10, completed: 3 },
        { id: 'demo-job-2', url: 'apple-music://demo/night-drive', title: 'Late Night Drive', status: 'waiting', total: 42, completed: 0 },
      ];
      trackOrder = ['demo-track-1', 'demo-track-2', 'demo-track-3'];
      trackLabels = { 'demo-track-1': 'Nova Lane — Open Skies', 'demo-track-2': 'The Still — Blue Hours', 'demo-track-3': 'Mira — Parallel Lines' };
      activeTracks = {
        'demo-track-1': { mode: 'status', text: 'Downloaded', status: 'done' },
        'demo-track-2': { mode: 'progress', progress: 64, text: 'Downloading from Tidal · FLAC 24-bit', status: 'downloading' },
        'demo-track-3': { mode: 'status', text: 'Waiting…', status: 'resolving' },
      };
      isDownloading = true;
      playlistTitle = 'UI Preview Queue';
      playlistTotalTracks = 3;
      isLoading = false;
      return;
    }
    try {
      config = await GetConfig();
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
      config = { ...config, download_sources: selectedDownloadSources };
      if (typeof config.save_cover_art_sidecar !== 'boolean') {
        config.save_cover_art_sidecar = false;
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
        await SaveConfig(config);
      }
      const savedAppearance = ['system', 'light', 'dark'].includes(config.theme || '')
        ? config.theme as Appearance
        : 'system';
      applyAppearance(savedAppearance, false);

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

    restoreDownloadQueue();
    openHistory();
    fetchGistStatus(); // non-blocking — chips update when Gist responds
    if (config.apple_music_user_token && config.apple_authorization_token) {
      loadAppleMusicLibrary(); // non-blocking — Apple Music library updates when ready
    }
    loadIPodDevices();
    setTimeout(() => { void refreshDownloadedMusicLibrary(); }, 900);
    window.addEventListener('focus', () => loadIPodDevices());
    isLoading = false;

    // Listen to backend events
    EventsOn("backend-event", handleEvent);
    if (!queuePaused) setTimeout(startNextQueuedJob, 0);
    EventsOn("apple-index-event", (payload: any) => {
      if (payload?.type === 'apple_index_progress') {
        appleIndexing = true;
        appleIndexPercent = Math.max(0, Math.min(99.9, Number(payload.percent || 0)));
        appleIndexLabel = payload.label || '';
      } else if (payload?.type === 'apple_index_complete') {
        appleIndexPercent = 100;
        appleIndexLabel = 'Library indexed';
        appleIndexStarted = false;
        setTimeout(() => { appleIndexing = false; }, 1800);
      } else if (payload?.type === 'apple_index_incomplete') {
        const completed = Number(payload?.data?.completed || 0);
        const total = Number(payload?.data?.total || 0);
        appleIndexStarted = false;
        appleIndexing = true;
        appleIndexPercent = Math.max(0, Math.min(99.9, Number(payload?.data?.percent || 0)));
        appleIndexLabel = `Index incomplete · ${Math.max(0, total - completed)} remaining`;
      }
    });
    EventsOn("downloaded-index-event", (payload: any) => {
      if (payload?.type === 'progress') {
        downloadedIndexing = true;
        downloadedIndexPercent = Number(payload.percent || 0);
        downloadedIndexLabel = payload.label || '';
      } else if (payload?.type === 'complete') {
        downloadedIndexPercent = 100;
        downloadedIndexLabel = 'Downloads indexed';
        downloadedIndexing = false;
      }
      if (payload?.type === 'complete' && payload.library) {
        downloadedLibrary = {
          albums: Array.isArray(payload.library.albums) ? payload.library.albums : downloadedLibrary.albums,
          playlists: Array.isArray(payload.library.playlists) ? payload.library.playlists : downloadedLibrary.playlists,
        };
      }
    });

    // Listen to TIDAL OAuth events
    EventsOn("tidal-oauth-event", (payload: any) => {
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

    EventsOn("apple-login-event", (payload: any) => {
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
          SaveConfig(config);
          if (!appleLibrary && !appleLibraryLoading) loadAppleMusicLibrary();
          else setTimeout(startAppleIndexOnce, 500);
          setTimeout(() => { appleLogin = { phase: 'idle' }; }, 4000);
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

    EventsOn("amazon-login-event", (payload: any) => {
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

    EventsOn("sp-dc-event", (payload: any) => {
      if (!payload || !payload.type) return;
      switch (payload.type) {
        case 'sp_dc_status':
          spDcCapture = { phase: payload.status === 'waiting' ? 'waiting_for_user' : 'starting', message: payload.message || 'Opening browser...' };
          break;
        case 'sp_dc_captured':
          config.spotify_sp_dc = payload.sp_dc;
          spDcCapture = { phase: 'success', message: 'Spotify account connected!' };
          SaveConfig(config);
          setTimeout(() => { spDcCapture = { phase: 'idle' }; }, 4000);
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

    // Kick off health checks for all VPS endpoints on startup
    for (const src of healthSources) {
      checkHealth(src.key, { openPopover: false }).catch(() => {});
    }

    const handleWindowResize = () => {
    };
    const handleGlobalKeydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && showAppleLibraryDetail && libraryView !== 'favourites') closeAppleLibraryDetail();
    };
    window.addEventListener('resize', handleWindowResize);
    window.addEventListener('keydown', handleGlobalKeydown);
    return () => {
      window.removeEventListener('resize', handleWindowResize);
      window.removeEventListener('keydown', handleGlobalKeydown);
    };
  });

  function updateAutoScrollState() {
    if (!terminalContainer) return;
    const distanceFromBottom =
      terminalContainer.scrollHeight - terminalContainer.scrollTop - terminalContainer.clientHeight;
    shouldAutoScroll = distanceFromBottom <= 80;
    logAtBottom = distanceFromBottom <= 40;
  }

  function scrollToBottom(force: boolean = false) {
    if (terminalContainer && terminalEnd && (force || shouldAutoScroll)) {
      setTimeout(() => {
        terminalContainer.scrollTo({
          top: terminalContainer.scrollHeight,
          behavior: force ? 'auto' : 'smooth'
        });
      }, 50);
    }
  }

  function addLog(type: string, text: string, isRawHtml: boolean = false) {
    logs = [...logs, { id: logId++, type, text, isRawHtml }];
    scrollToBottom();
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
      playlistTotalDurationMs = trkList.reduce((sum: number, t: any) => sum + (t.duration_ms || 0), 0);
      // Insert a visual separator when a second+ URL's tracks arrive
      if (trackOrder.length > 0) {
        const sepKey = `__SEP__${Date.now()}`;
        separatorMeta[sepKey] = { title: payload.title || '', artwork: payload.artwork_url || '' };
        separatorMeta = { ...separatorMeta };
        trackOrder = [...trackOrder, sepKey];
      }

      currentPlaylistTrackKeysByIndex = {};
      currentPlaylistTrackCount = 0;

      // Pre-populate the full tracklist in waiting state (Set-based O(N) dedup)
      const seen = new Set(trackOrder);
      const newTracks: string[] = [];
      trkList.forEach((t: any, idx: number) => {
        const rowKey = `track:${Date.now()}:${idx + 1}`;
        const label = makeTrackDisplayName(t.artist, t.title);
        currentPlaylistTrackKeysByIndex[idx + 1] = rowKey;
        currentPlaylistTrackCount = idx + 1;
        trackLabels[rowKey] = label;
        if (!seen.has(rowKey)) {
          seen.add(rowKey);
          newTracks.push(rowKey);
          if (!activeTracks[rowKey]) {
            activeTracks[rowKey] = { mode: 'status', text: 'Waiting...', status: 'resolving' };
          }
        }
      });
      if (newTracks.length > 0) {
        trackOrder = [...trackOrder, ...newTracks];
      }
      trackLabels = { ...trackLabels };
      activeTracks = { ...activeTracks };
      return;
    }

    if (payload.type === 'tracks_appended') {
      // Progressive playlist loading: append new tracks without resetting existing rows
      const trkList2: any[] = payload.tracks || [];
      const seen2 = new Set(trackOrder);
      const newTracks2: string[] = [];
      trkList2.forEach((t: any, idx: number) => {
        const absoluteIndex = currentPlaylistTrackCount + idx + 1;
        const rowKey = `track:${Date.now()}:${absoluteIndex}`;
        const label = makeTrackDisplayName(t.artist, t.title);
        currentPlaylistTrackKeysByIndex[absoluteIndex] = rowKey;
        trackLabels[rowKey] = label;
        if (!seen2.has(rowKey)) {
          seen2.add(rowKey);
          newTracks2.push(rowKey);
          if (!activeTracks[rowKey]) {
            activeTracks[rowKey] = { mode: 'status', text: 'Waiting...', status: 'resolving' };
          }
        }
      });
      if (newTracks2.length > 0) {
        trackOrder = [...trackOrder, ...newTracks2];
        activeTracks = { ...activeTracks };
        trackLabels = { ...trackLabels };
        playlistTotalTracks = (playlistTotalTracks || 0) + newTracks2.length;
        playlistTotalDurationMs = (playlistTotalDurationMs || 0) + trkList2.reduce((s: number, t: any) => s + (t.duration_ms || 0), 0);
        currentPlaylistTrackCount += newTracks2.length;
      }
      return;
    }

    if (payload.type === 'process_ended') {
      isDownloading = false;
      Object.keys(activeTracks).forEach(clearTrackInterval);
      const activeJob = downloadJobs.findIndex(job => job.status === 'downloading');
      if (payload.status === 'cancelled') {
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
      if (!queuePaused && payload.status !== 'cancelled') setTimeout(startNextQueuedJob, 0);
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

      const trackKey = resolveTrackEventKey(data);
      const trackLabel = makeTrackDisplayName(data.artist, data.track);
      if (!trackLabels[trackKey]) {
        trackLabels[trackKey] = trackLabel;
        trackLabels = { ...trackLabels };
      }

      if (name === 'track_started') {
        if (!trackOrder.includes(trackKey)) {
          trackOrder = [...trackOrder, trackKey];
        }
        updateActiveTrack(trackKey, {
          mode: 'status',
          progress: undefined,
          text: 'Resolving best source...',
          status: 'resolving',
          retrying: false,
          trackData: data.track_data || activeTracks[trackKey]?.trackData,
        });

      } else if (name === 'track_resolved') {
        clearTrackInterval(trackKey);
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
          retrying: false,
          trackData: data.track_data || activeTracks[trackKey]?.trackData,
        });

      } else if (name === 'track_download_attempt') {
        const source = String(data.source || 'auto');
        const attempt = data.attempt ?? 1;
        clearTrackInterval(trackKey);

        const attemptSuffix = attempt > 1 ? ` • Retry ${attempt}` : '';
        let displaySource = source;
        if (displaySource === 'hifi') displaySource = 'Tidal';
        else if (displaySource === 'apple') displaySource = 'Apple';
        else if (displaySource === 'amazon') displaySource = 'Amazon';
        else displaySource = displaySource.charAt(0).toUpperCase() + displaySource.slice(1);

        updateActiveTrack(trackKey, {
          mode: 'progress',
          progress: 8,
          text: `Downloading from ${displaySource}${data.quality_label ? ` • ${data.quality_label}` : ''}${attemptSuffix}`,
          status: 'downloading',
          retrying: false,
          trackData: data.track_data || activeTracks[trackKey]?.trackData,
        });

        const intervalId = setInterval(() => {
          if (activeTracks[trackKey] && activeTracks[trackKey].mode === 'progress' && (activeTracks[trackKey].progress ?? 0) < 85) {
            updateActiveTrack(trackKey, {
              progress: Math.min(85, (activeTracks[trackKey].progress ?? 0) + Math.random() * 5)
            });
          } else {
            clearInterval(intervalId);
          }
        }, 800);

        (activeTracks[trackKey] as any)._intervalId = intervalId;
        activeTracks = { ...activeTracks };

      } else if (name === 'track_completed') {
        addLog('success', `[✓] Downloaded: ${trackLabel}`);
        clearTrackInterval(trackKey);
        updateActiveTrack(trackKey, {
          mode: 'progress',
          progress: 100,
          text: 'Downloaded',
          error: undefined,
          status: 'done',
          retrying: false,
          trackData: data.track_data || activeTracks[trackKey]?.trackData,
        });
      } else if (name === 'track_failed') {
        addLog('error', `[FAIL] ${trackLabel} - ${data.error}`);
        clearTrackInterval(trackKey);
        updateActiveTrack(trackKey, {
          mode: 'status',
          progress: undefined,
          text: 'Download failed',
          error: data.error || 'Failed',
          status: 'failed',
          retrying: false,
          trackData: data.track_data || activeTracks[trackKey]?.trackData,
        });
      } else if (name === 'track_skipped') {
        addLog('warning', `[—] Already downloaded: ${trackLabel}`);
        updateActiveTrack(trackKey, {
          mode: 'status',
          text: 'Already downloaded',
          status: 'skipped',
          retrying: false,
          trackData: data.track_data || activeTracks[trackKey]?.trackData,
        });
      } else if (name === 'playlist_started') {
        addLog('info', `Creating playlist structure and syncing tracks: ${data.message}`);
      }
    } else if (payload.type === 'playlist_summary') {
      const htmlRundown = formatAsciiRundown(payload);
      addLog('terminal-rundown', htmlRundown, true);
      AddHistory(payload).catch(err => console.error("Failed to add history:", err));
      historyItems = [payload, ...historyItems];
      const jobIndex = downloadJobs.findIndex(job => job.url === payload.url && job.status !== 'downloaded');
      if (jobIndex >= 0) {
        downloadJobs[jobIndex] = { ...downloadJobs[jobIndex], title: payload.title || downloadJobs[jobIndex].title, artwork: payload.artwork_url || downloadJobs[jobIndex].artwork, status: payload.error ? 'failed' : 'downloaded', total: payload.total || 0, completed: (payload.downloaded || 0) + (payload.skipped || 0) };
        downloadJobs = [...downloadJobs];
        persistDownloadQueue();
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
    const grouped = new Map<string, any[]>();
    for (const album of albums || []) {
      const key = discographyReleaseKey(album);
      const group = grouped.get(key) ?? [];
      group.push(album);
      grouped.set(key, group);
    }

    const deduped: any[] = [];
    for (const group of grouped.values()) {
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

  async function openDownloadedMusic() {
    currentPage = 'downloaded';
    await refreshDownloadedMusicLibrary();
  }

  async function refreshDownloadedMusicLibrary() {
    downloadedLibraryLoading = !downloadedLibrary.albums.length && !downloadedLibrary.playlists.length;
    downloadedLibraryError = '';
    try {
      const raw = await GetDownloadedMusicLibrary();
      const parsed = JSON.parse(raw || '{}');
      downloadedLibrary = {
        albums: Array.isArray(parsed.albums) ? parsed.albums : [],
        playlists: Array.isArray(parsed.playlists) ? parsed.playlists : [],
        error: parsed.error
      };
      downloadedLibraryError = parsed.error || '';

      const selectedStillExists = [...downloadedLibrary.albums, ...downloadedLibrary.playlists]
        .some((item: LibraryReleaseSummary) => item.relative_path === downloadedSelectedPath);
      if (!selectedStillExists) {
        downloadedSelectedRelease = null;
        downloadedSelectedPath = '';
      }

    } catch (e: any) {
      downloadedLibraryError = String(e);
      downloadedLibrary = { albums: [], playlists: [] };
    } finally {
      downloadedLibraryLoading = false;
    }
  }

  async function openDownloadedRelease(release: LibraryReleaseSummary) {
    downloadedSelectedPath = release.relative_path;
    const cached = downloadedDetailCache.get(release.relative_path);
    if (cached) downloadedSelectedRelease = cached;
    downloadedSelectedReleaseLoading = !cached;
    if (cached) return;
    try {
      const raw = await GetDownloadedRelease(release.relative_path);
      const parsed = JSON.parse(raw || '{}');
      if (parsed?.error) {
        downloadedLibraryError = parsed.error;
        return;
      }
      downloadedSelectedRelease = parsed;
      downloadedDetailCache.set(release.relative_path, parsed as LibraryReleaseDetail);
      downloadedView = release.kind === 'playlist' ? 'playlists' : 'albums';
    } catch (e: any) {
      downloadedLibraryError = String(e);
    } finally {
      downloadedSelectedReleaseLoading = false;
    }
  }

  async function playDownloadedTrack(index: number) {
    if (!downloadedSelectedRelease?.tracks?.length || !audioEl) return;
    playerQueue = downloadedSelectedRelease.tracks;
    playerTrackIndex = index;
    playerReleaseTitle = downloadedSelectedRelease.title;
    playerError = '';
    playerCurrentTime = 0;
    playerDuration = downloadedSelectedRelease.tracks[index]?.duration_seconds || 0;
    audioEl.src = downloadedSelectedRelease.tracks[index].audio_url;
    audioEl.load();
    try {
      await audioEl.play();
    } catch (e: any) {
      playerError = String(e);
    }
  }

  async function togglePlayback() {
    if (!audioEl) return;
    if (!currentPlayerTrack && downloadedSelectedRelease?.tracks?.length) {
      await playDownloadedTrack(0);
      return;
    }
    if (audioEl.paused) {
      try {
        await audioEl.play();
        playerError = '';
      } catch (e: any) {
        playerError = String(e);
      }
    } else {
      audioEl.pause();
    }
  }

  async function playNextTrack() {
    if (playerTrackIndex < 0 || playerTrackIndex >= playerQueue.length - 1) return;
    await playQueuedTrack(playerTrackIndex + 1);
  }

  async function playPreviousTrack() {
    if (playerTrackIndex <= 0) {
      if (audioEl) audioEl.currentTime = 0;
      return;
    }
    await playQueuedTrack(playerTrackIndex - 1);
  }

  async function playQueuedTrack(index: number) {
    if (!playerQueue.length || index < 0 || index >= playerQueue.length || !audioEl) return;
    playerTrackIndex = index;
    playerError = '';
    playerCurrentTime = 0;
    playerDuration = playerQueue[index]?.duration_seconds || 0;
    audioEl.src = playerQueue[index].audio_url;
    audioEl.load();
    try {
      await audioEl.play();
    } catch (e: any) {
      playerError = String(e);
    }
  }

  function handleAudioTimeUpdate() {
    if (!audioEl || playerSeeking) return;
    playerCurrentTime = audioEl.currentTime || 0;
  }

  async function loadLyrics(filePath: string | undefined) {
    lyricsLines = [];
    lyricsSynced = false;
    if (!filePath) return;
    lyricsLoading = true;
    try {
      const raw = await GetTrackLyrics(filePath);
      const parsed = JSON.parse(raw) as { lines: LyricsLine[]; synced: boolean };
      lyricsLines = parsed.lines || [];
      lyricsSynced = parsed.synced || false;
      // Auto-show the panel when the track has lyrics; auto-hide when it doesn't.
      if (lyricsLines.length > 0) showLyrics = true;
    } catch { lyricsLines = []; }
    finally { lyricsLoading = false; }
  }

  // Load lyrics whenever the active track changes.
  $: loadLyrics(currentPlayerTrack?.file_path);

  function handleAudioLoadedMetadata() {
    if (!audioEl) return;
    playerDuration = audioEl.duration || playerDuration;
  }

  async function handleAudioEnded() {
    if (playerTrackIndex >= 0 && playerTrackIndex < playerQueue.length - 1) {
      await playQueuedTrack(playerTrackIndex + 1);
    }
  }

  function handleSeekInput(event: Event) {
    const target = event.currentTarget as HTMLInputElement;
    playerCurrentTime = Number(target.value);
  }

  function handleSeekCommit(event: Event) {
    const target = event.currentTarget as HTMLInputElement;
    const nextTime = Number(target.value);
    playerSeeking = false;
    playerCurrentTime = nextTime;
    if (audioEl) audioEl.currentTime = nextTime;
  }

  async function pickDir() {
    const dir = await PickDirectory();
    if (dir) {
      config.download_path = dir;
      if (!setupMode) await autoSaveSettings();
    }
  }

  function closeDownloadedRelease() {
    downloadedSelectedRelease = null;
    downloadedSelectedPath = '';
    downloadedSelectedReleaseLoading = false;
  }

  async function forceRefreshDownloadedMusicLibrary() {
    downloadedIndexing = true;
    downloadedIndexPercent = 0;
    try {
      const parsed = JSON.parse(await RefreshDownloadedMusicLibrary() || '{}');
      downloadedLibrary = { albums: parsed.albums || [], playlists: parsed.playlists || [] };
    } catch (e: any) {
      downloadedLibraryError = String(e);
      downloadedIndexing = false;
    }
  }

  async function saveSetup() {
    if (!config.download_path) {
      alert("Please select your Music Library folder.");
      return;
    }
    await SaveConfig(config);
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
      downloadJobs = [...downloadJobs, ...otherUrls.map((url, index) => ({ id: `${stamp}-${index}`, url, title: url, status: 'waiting' as const, total: 0, completed: 0 }))];
      persistDownloadQueue();
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
    currentPage = 'downloads';
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
    if (!isDownloading && !downloadJobs.some(job => ['waiting', 'downloading'].includes(job.status))) return;
    if (!confirm('Cancel the current download and every waiting job?')) return;
    try {
      if (isDownloading) {
        await CancelDownload();
        addLog('warning', 'Library build cancelled.');
      }
      isDownloading = false;
      queuePaused = false;
      downloadJobs = downloadJobs.map(job => ['waiting', 'downloading'].includes(job.status) ? { ...job, status: 'cancelled' } : job);
      persistDownloadQueue();
      Object.keys(activeTracks).forEach(clearTrackInterval);
      activeTracks = {};
      trackOrder = [];
      trackLabels = {};
      separatorMeta = {};
      currentPlaylistTrackKeysByIndex = {};
      currentPlaylistTrackCount = 0;
    } catch (err) {
      console.error(err);
    }
  }

  async function openHistory() {
    try {
      historyItems = await GetHistory() || [];
    } catch (e) {
      console.error(e);
      historyItems = [];
    }
  }

  async function clearHistory() {
    if(confirm("Are you sure you want to clear your library build history?")) {
      await ClearHistory();
      historyItems = [];
    }
  }

  function validateSettings(): string {
    if (!config.max_retries || config.max_retries < 1 || config.max_retries > 20) return 'Retries must be between 1 and 20.';
    if (!config.max_concurrent_jobs || config.max_concurrent_jobs < 1 || config.max_concurrent_jobs > 8) return 'Concurrent downloads must be between 1 and 8.';
    if (!/^[a-z]{2}$/i.test(config.apple_storefront || '')) return 'Apple storefront must be a two-letter country code.';
    return '';
  }

  function resetActiveJobView() {
    logs = [];
    trackOrder = [];
    trackLabels = {};
    playlistTitle = '';
    playlistArtwork = '';
    playlistArtists = '';
    playlistReleaseDate = '';
    playlistContentType = '';
    playlistQualityBadge = '';
    playlistTotalDurationMs = 0;
    playlistTotalTracks = 0;
    Object.keys(activeTracks).forEach(clearTrackInterval);
    activeTracks = {};
    currentPlaylistTrackKeysByIndex = {};
    currentPlaylistTrackCount = 0;
    separatorMeta = {};
    dismissedFailures = new Set();
    retryQueue = [];
    retryQueueTotal = 0;
  }

  async function startNextQueuedJob() {
    if (isDownloading || queuePaused) return;
    const next = downloadJobs.findIndex(job => job.status === 'waiting');
    if (next < 0) return;
    resetActiveJobView();
    downloadJobs[next] = { ...downloadJobs[next], status: 'downloading' };
    downloadJobs = [...downloadJobs];
    persistDownloadQueue();
    isDownloading = true;
    shouldAutoScroll = true;
    addLog('info', 'Preparing download…');
    try {
      await StartDownload([downloadJobs[next].url]);
    } catch (err) {
      downloadJobs[next] = { ...downloadJobs[next], status: 'failed' };
      downloadJobs = [...downloadJobs];
      isDownloading = false;
      persistDownloadQueue();
      addLog('error', `Library engine error: ${err}`);
      setTimeout(startNextQueuedJob, 0);
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

  async function autoSaveSettings() {
    settingsError = validateSettings();
    if (settingsError) {
      settingsSaveState = 'error';
      return;
    }
    settingsSaveState = 'saving';
    try {
      await SaveConfig(config);
      await SetDownloadWorkerCount(config.max_concurrent_jobs || 2);
      settingsSaveState = 'saved';
      setTimeout(() => { if (settingsSaveState === 'saved') settingsSaveState = 'idle'; }, 1500);
    } catch (e: any) {
      settingsError = e?.message || String(e);
      settingsSaveState = 'error';
    }
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
      await SaveConfig(config);
      showFolderSettings = false;
    } finally {
      folderSettingsSaving = false;
    }
  }

  async function openSettings(section = 'settings-general') {
    const map: Record<string, SettingsPage> = {
      'settings-general': 'general', 'settings-apple': 'apple', 'settings-downloads': 'downloads',
      'settings-audio': 'audio', 'settings-discovery': 'discovery', 'settings-naming': 'naming',
      'settings-providers': 'providers',
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
      await SaveConfig(config);
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
      await SaveConfig(config);
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
      await SaveConfig(config);
      await StartAmazonBrowserLogin();
    } catch (e) {
      amazonLogin = { phase: 'error', message: `Failed to start Amazon Music login: ${e}` };
    }
  }

  async function retryFailedTrack(trackName: string) {
    const state = activeTracks[trackName];
    if (!state?.trackData || state.retrying || isDownloading) return;

    clearTrackInterval(trackName);
    isDownloading = true;
    addLog('info', `[↻] Retrying failed track: ${trackName}`);
    updateActiveTrack(trackName, {
      mode: 'status',
      progress: undefined,
      text: 'Retrying failed track...',
      error: undefined,
      status: 'resolving',
      retrying: true,
    });

    try {
      await RetryTrackDownload(JSON.stringify(state.trackData));
    } catch (err) {
      addLog('error', `Retry failed to start for ${trackName}: ${err}`);
      isDownloading = false;
      updateActiveTrack(trackName, {
        mode: 'status',
        text: state.text || 'Retry failed',
        error: state.error || 'Retry failed',
        status: 'failed',
        retrying: false,
      });
    }
  }

  function dismissFailure(key: string) {
    dismissedFailures = new Set([...dismissedFailures, key]);
  }

  function dismissAllFailures() {
    dismissedFailures = new Set([...dismissedFailures, ...failedEntries.map(e => e.key)]);
  }

  async function processRetryQueue() {
    if (retryQueue.length === 0 || isDownloading) return;
    const nextKey = retryQueue[0];
    retryQueue = retryQueue.slice(1);
    if (nextKey) await retryFailedTrack(nextKey);
  }

  async function retryAllFailed() {
    const eligible = failedEntries.filter(e => e.trackData);
    if (eligible.length === 0 || isDownloading) return;
    retryQueue = eligible.map(e => e.key);
    retryQueueTotal = retryQueue.length;
    await processRetryQueue();
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

  <div class="app-shell" on:pointerdown={dismissOpenMenus}>
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
        {#each ipodDevices as device (device.path)}<button class:active={currentPage === 'devices'} on:click={() => selectPage('devices')}><Smartphone size={18}/><span>{device.name || 'iPod'}</span></button>{/each}

      </nav>

      <div class="sidebar-footer">
        {#if appleIndexing || downloadedIndexing}<div class="sidebar-index" title={downloadedIndexing ? downloadedIndexLabel : appleIndexLabel}><span>{downloadedIndexing ? `Indexing downloads · ${downloadedIndexPercent}%` : `Indexing library · ${appleIndexPercent}%`}</span><progress max="100" value={downloadedIndexing ? downloadedIndexPercent : appleIndexPercent}></progress></div>{/if}
        <button class="settings-nav" class:active={currentPage === 'downloads'} on:click={() => selectPage('downloads')}><Download size={18}/><span>Downloads</span></button>
        <button class="settings-nav" class:active={showSettings} on:click={() => openSettings()}><Settings size={18}/><span>Settings</span></button>
      </div>
    </aside>

    <section class="workspace">
      {#if !(currentPage === 'library' && libraryView === 'playlists' && !showAppleLibraryDetail)}
      <header class="topbar">
        <div>
          {#if currentPage === 'library' && showAppleLibraryDetail && libraryView !== 'favourites'}<button class="topbar-back" aria-label="Back to library" on:click={closeAppleLibraryDetail}><ArrowLeft size={20}/></button>{/if}
          {#if currentPage === 'downloaded' && downloadedSelectedRelease}<button class="topbar-back" aria-label="Back to downloaded music" on:click={closeDownloadedRelease}><ArrowLeft size={20}/></button>{/if}
          <p class="eyebrow">{currentPage === 'downloads' ? 'Activity' : currentPage === 'downloaded' ? 'On This Device' : ''}</p>
          <h1>{currentPage === 'library' ? ({ recent: 'Recently Added', albums: 'Albums', playlists: 'Playlists', favourites: 'Favourite Songs', artists: 'Artists' }[libraryView]) : currentPage === 'downloads' ? 'Downloads' : currentPage === 'downloaded' ? 'Downloaded' : currentPage === 'devices' ? (ipodDevices[0]?.name || 'iPod') : 'Settings'}</h1>
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
              {#if appleLibraryDetail.image_url}<div class="library-detail-backdrop" style={`background-image:url('${appleLibraryDetail.image_url}')`}></div>{:else}<div class="library-detail-backdrop favourite-backdrop"></div>{/if}
              <div class="library-detail-hero">
                {#if appleLibraryDetail.image_url}<img class="library-detail-art" src={appleLibraryDetail.image_url} alt="" />{:else}<div class="library-detail-art favourite-art"><Star size={82}/></div>{/if}
                <div class="library-detail-copy"><p class="eyebrow">{appleLibraryDetail.content_type || 'Apple Music library'}</p><h2>{appleLibraryDetail.name}</h2><strong>{appleLibraryDetail.track_count || appleLibraryDetail.tracks.length} songs</strong><span>{appleLibraryDetail.content_type === 'artist' ? 'Songs by this artist in your library' : 'Stored in your local Vela index'}</span>{#if appleLibraryDetail.content_type !== 'artist'}<div class="detail-primary-actions"><button class="primary detail-download" on:click={downloadOpenApplePlaylist}><Download size={17}/><span>Download</span></button><div class="tool-menu"><button class="icon-button" aria-label="More release options" on:click={() => showDetailMenu = !showDetailMenu}><MoreHorizontal size={18}/></button>{#if showDetailMenu}<div class="context-menu detail-popover"><button on:click={() => { showDetailMenu = false; downloadOpenApplePlaylist(); }}><Download size={16}/> Download release</button></div>{/if}</div></div>{/if}</div>
              </div>
              <div class="detail-track-tools"><div class="search-bar detail-search"><Search size={16}/><input bind:value={libraryDetailFilter} placeholder="Search songs" /></div><select bind:value={libraryDetailSort} aria-label="Sort songs"><option value="position">Playlist order</option><option value="title">Title</option><option value="artist">Artist</option><option value="album">Album</option></select><button class="secondary detail-order" on:click={() => libraryDetailDescending = !libraryDetailDescending}><ArrowUpDown size={16}/>{libraryDetailDescending ? 'Descending' : 'Ascending'}</button></div>
              {#if appleLibraryDetailError}<div class="detail-inline-error">{appleLibraryDetailError}</div>{/if}
              <div class="library-detail-tracks" class:loading={appleLibraryDetailLoading}>
                {#if appleLibraryDetailLoading && !appleLibraryDetail.tracks.length}<p class="detail-index-note">{appleIndexing ? 'Indexing this release locally…' : 'Opening indexed songs…'}</p>{/if}
                {#each visibleLibraryDetailTracks as track, i}<article><span class="detail-track-number">{i + 1}</span>{#if track.artwork_url}<img src={track.artwork_url} alt="" loading="lazy" decoding="async" />{:else}<span class="detail-track-placeholder"><Album size={17}/></span>{/if}<strong>{track.title}</strong><span>{track.artist}</span><span>{track.album}</span><small>{formatPlaybackTime((track.duration_ms || 0) / 1000)}</small><div class="track-more tool-menu"><button aria-label={`More options for ${track.title}`} on:click={() => detailTrackMenuIndex = detailTrackMenuIndex === i ? null : i}><MoreHorizontal size={17}/></button>{#if detailTrackMenuIndex === i}<div class="context-menu track-popover"><button on:click={() => { detailTrackMenuIndex = null; downloadOpenApplePlaylist(); }}><Download size={16}/> Download release</button></div>{/if}</div></article>{/each}
              </div>
            </section>
          {:else}
          <div class="library-tools">
            <div class="search-bar compact-search"><Search size={17}/><input bind:value={libraryFilter} placeholder="Search your indexed library" /></div>
            <div class="tool-menu"><button class="library-tool-button" title="Sort" aria-label="Sort library" on:click={() => { showSortMenu = !showSortMenu; showFilterMenu = false; }}><ArrowUpDown size={17}/></button>{#if showSortMenu}<div class="context-menu tool-popover"><button on:click={() => { librarySort = 'recent'; showSortMenu = false; }}><Clock3 size={15}/> Recently added</button><button on:click={() => { librarySort = 'title'; showSortMenu = false; }}><ArrowUpDown size={15}/> Title</button><button on:click={() => { librarySort = 'artist'; showSortMenu = false; }}><UserRound size={15}/> Artist</button><button on:click={() => { librarySortDirection = librarySortDirection === 'ascending' ? 'descending' : 'ascending'; showSortMenu = false; }}><ArrowUpDown size={15}/> {librarySortDirection === 'ascending' ? 'Reverse order' : 'Use ascending order'}</button></div>{/if}</div>
            <div class="tool-menu"><button class="library-tool-button" title="Filter" aria-label="Filter library" on:click={() => { showFilterMenu = !showFilterMenu; showSortMenu = false; }}><SlidersHorizontal size={18}/></button>{#if showFilterMenu}<div class="context-menu tool-popover"><button on:click={() => { libraryKindFilter = 'all'; showFilterMenu = false; }}>All</button><button on:click={() => { libraryView = 'albums'; libraryKindFilter = 'albums'; showFilterMenu = false; }}><Album size={15}/> Albums</button><button on:click={() => { libraryView = 'playlists'; libraryKindFilter = 'playlists'; showFilterMenu = false; }}><ListMusic size={15}/> Playlists</button></div>{/if}</div>
          </div>
          {#if selectedLibraryItems.size}<div class="selection-toolbar"><strong>{selectedLibraryItems.size} selected</strong><button class="primary compact" on:click={downloadSelectedLibraryItems}><Download size={16}/> Download</button><button class="icon-button" aria-label="More actions"><MoreHorizontal size={18}/></button><button class="icon-button" aria-label="Clear selection" on:click={() => selectedLibraryItems = new Set()}><X size={17}/></button></div>{/if}

          {#key `${libraryFilter}:${librarySort}:${librarySortDirection}:${libraryView}`}
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
                {#each filteredLibraryAlbums() as album (album.id)}<article class:selected={selectedLibraryItems.has(album.url)} class="music-card selectable" on:contextmenu={(event) => openLibraryItemMenu(event, album)}><button class="select-release" class:selected={selectedLibraryItems.has(album.url)} aria-label={`Select ${album.name}`} on:click|stopPropagation={() => toggleLibrarySelection(album.url)}>{#if selectedLibraryItems.has(album.url)}<Check size={15}/>{/if}</button><button class="artwork" on:click={() => openAppleLibraryDetail(album.url, album.name, album.image_url || '')}>{#if album.image_url}<img src={album.image_url} alt="" loading="lazy" />{:else}<span class="art-placeholder"><Library size={34}/></span>{/if}</button><div class="card-copy"><strong title={album.name}>{album.name}</strong><span>{album.artist_name} · {album.track_count} songs</span></div></article>{/each}
              </div>
            </section>{/if}
            {#if libraryView === 'recent' || libraryView === 'playlists'}<section class="section-block"><div class="section-heading"><div><h2>Playlists</h2></div><span>{appleLibrary.playlists.length} playlists</span></div><div class="art-grid">{#each filteredLibraryPlaylists() as pl (pl.id)}<article class:selected={selectedLibraryItems.has(pl.url)} class="music-card selectable" on:contextmenu={(event) => openLibraryItemMenu(event, pl)}><button class="select-release" class:selected={selectedLibraryItems.has(pl.url)} aria-label={`Select ${pl.name}`} on:click|stopPropagation={() => toggleLibrarySelection(pl.url)}>{#if selectedLibraryItems.has(pl.url)}<Check size={15}/>{/if}</button><button class="artwork" on:click={() => openAppleLibraryDetail(pl.url, pl.name, pl.image_url || '')}>{#if pl.image_url}<img src={pl.image_url} alt="" loading="lazy" />{:else}<span class="art-placeholder"><ListMusic size={34}/></span>{/if}</button><div class="card-copy"><strong title={pl.name}>{pl.name}</strong><span>{pl.track_count ? `${pl.track_count} songs` : 'Indexed playlist'}</span></div></article>{/each}</div></section>{/if}
            {#if libraryView === 'favourites'}<section class="section-block">{#if favouriteSongsPlaylist()}<div class="art-grid"><article class="music-card featured-card"><button class="artwork gradient-art" on:click={openFavourites}><Star size={42}/></button><div class="card-copy"><strong>{favouriteSongsPlaylist()?.name}</strong><span>{favouriteSongsPlaylist()?.track_count || 0} songs</span></div></article></div>{:else}<div class="state-card"><Star size={34} class="favourite-empty-icon"/><h3>Favourite Songs wasn’t found</h3><p>Open Apple Music once so its automatic Favourite Songs playlist is available, then refresh the library index.</p></div>{/if}</section>{/if}
            {#if libraryView === 'artists'}<section class="artist-library-list">{#each libraryArtists() as artist}<button on:click={() => openAppleArtistDetail(artist.name, artist.image || '')}><span class="artist-avatar">{#if artist.image}<img src={artist.image} alt="" />{:else}<UserRound size={22}/>{/if}</span><span><strong>{artist.name}</strong><small>{artist.albums.length} album{artist.albums.length === 1 ? '' : 's'} in your library</small></span><ChevronDown size={16}/></button>{/each}</section>{/if}
          {/if}
          {/key}
          {/if}

        {:else if currentPage === 'downloads'}
          {#if playlistTitle || trackOrder.length}
            <section class="panel session-panel">
              <div class="release-header">{#if playlistArtwork}<img src={playlistArtwork} alt="" />{:else}<div class="history-art"><Download size={22}/></div>{/if}<div><p class="eyebrow">Downloading now</p><h2>{playlistTitle || 'Preparing music…'}</h2><p>{queueFinishedCount}/{queueTrackKeys.length || playlistTotalTracks || '…'} songs</p></div><button class="icon-button" aria-label={queuePaused ? 'Resume queue' : 'Pause queue'} on:click={toggleQueuePause}>{#if queuePaused}<Play size={18}/>{:else}<Pause size={18}/>{/if}</button></div>
              <div class="download-track-list" bind:this={tracklistEl}>{#each trackOrder.filter(key => !key.startsWith('__SEP__')) as key (key)}<article><span class="track-state-icon">{#if activeTracks[key]?.status === 'done' || activeTracks[key]?.status === 'skipped'}<Check size={16}/>{:else if activeTracks[key]?.status === 'downloading'}<LoaderCircle size={16} class="spin"/>{:else if activeTracks[key]?.status === 'failed'}<X size={16}/>{:else}<Clock3 size={16}/>{/if}</span><div><strong>{trackLabels[key] || key}</strong>{#if !['done','skipped'].includes(activeTracks[key]?.status)}<span>{activeTracks[key]?.text || 'Waiting'}</span>{/if}{#if activeTracks[key]?.status === 'downloading'}<progress max="100" value={activeTracks[key]?.progress || 0}></progress>{/if}</div></article>{/each}</div>
              {#if failedEntries.length}<div class="failure-strip"><strong>{failedEntries.length} track{failedEntries.length === 1 ? '' : 's'} need attention</strong><button on:click={retryAllFailed} disabled={isDownloading}>Retry all</button></div>{/if}
              <button class="log-toggle" on:click={() => showDownloadLogs = !showDownloadLogs}><FileText size={15}/>{showDownloadLogs ? 'Hide log' : 'Show more'}</button>{#if showDownloadLogs}<div class="clean-log">{#each logs as log}<p class={log.type}>{log.text.replace(/<[^>]+>/g, '')}</p>{/each}</div>{/if}
            </section>
          {/if}
          {#if downloadJobs.some(job => job.status === 'waiting')}<div class="history-toolbar"><p>Queue</p><span>{downloadJobs.filter(job => job.status === 'waiting').length} waiting</span></div><div class="history-list job-list">{#each downloadJobs.filter(job => job.status === 'waiting') as job (job.id)}<article>{#if job.artwork}<img src={job.artwork} alt="" />{:else}<div class="history-art"><Clock3 size={20}/></div>{/if}<div><strong>{job.title}</strong><span>Waiting</span></div><span class="job-status waiting">queued</span></article>{/each}</div>{/if}
          <div class="history-toolbar"><p>{historyItems.length} completed job{historyItems.length === 1 ? '' : 's'}</p>{#if historyItems.length}<button class="danger-link" on:click={clearHistory}>Clear history</button>{/if}</div>
          {#if !historyItems.length && !trackOrder.length}<div class="state-card"><p>Queued and completed albums, playlists, and songs will appear here.</p></div>{:else}<div class="history-list">{#each historyItems as item}<article class:error={!!item.error}>{#if item.artwork_url}<img src={item.artwork_url} alt="" />{:else}<div class="history-art"><Download size={20}/></div>{/if}<div><strong>{item.title || item.url}</strong><span>{item.total || 0} songs · {new Date(item.date).toLocaleDateString()}</span>{#if item.error}<small>{item.error}</small>{/if}</div><details class="job-options"><summary aria-label="Job options"><MoreHorizontal size={18}/></summary><div><button on:click={() => { inputUrl = item.url; startDownload(); }}>Download again</button><button on:click={() => navigator.clipboard?.writeText(item.url || '')}>Copy link</button></div></details></article>{/each}</div>{/if}

        {:else if currentPage === 'downloaded'}
          {#if downloadedSelectedPath}
            <section class="library-detail-page downloaded-detail-page">
              {#if downloadedSelectedRelease?.artwork_url}<div class="library-detail-backdrop" style={`background-image:url('${downloadedSelectedRelease.artwork_url}')`}></div>{:else}<div class="library-detail-backdrop favourite-backdrop"></div>{/if}
              {#if downloadedSelectedReleaseLoading}<div class="state-card"><div class="spinner"></div><p>Opening downloaded release…</p></div>{:else if downloadedSelectedRelease}
                <div class="library-detail-hero"><div>{#if downloadedSelectedRelease.artwork_url}<img class="library-detail-art" src={downloadedSelectedRelease.artwork_url} alt="" />{:else}<div class="library-detail-art local-placeholder-art"><Album size={68}/></div>{/if}</div><div class="library-detail-copy"><p class="eyebrow">Downloaded {downloadedSelectedRelease.kind}</p><h2>{downloadedSelectedRelease.title}</h2><strong>{downloadedSelectedRelease.track_count} songs</strong><span>{releaseMetaLine(downloadedSelectedRelease)}</span><button class="primary detail-download" on:click={() => playDownloadedTrack(0)}><Play size={17}/><span>Play</span></button></div></div>
                <div class="local-detail-tracks">{#each downloadedSelectedRelease.tracks as track, i}<button class:playing={currentPlayerTrack?.file_path === track.file_path} on:click={() => playDownloadedTrack(i)}><span class="detail-track-number">{currentPlayerTrack?.file_path === track.file_path ? '▶' : i + 1}</span>{#if downloadedSelectedRelease.artwork_url}<img src={downloadedSelectedRelease.artwork_url} alt="" loading="lazy" decoding="async" />{:else}<span class="detail-track-placeholder"><Album size={17}/></span>{/if}<strong>{track.title}</strong><span>{track.artist || downloadedSelectedRelease.artist || 'Unknown Artist'}</span><span>{track.album || downloadedSelectedRelease.title}</span><small>{formatPlaybackTime(track.duration_seconds || 0)}</small></button>{/each}</div>
              {/if}
            </section>
          {:else}
            <div class="library-tabs"><button class:active={downloadedView === 'albums'} on:click={() => { downloadedView = 'albums'; closeDownloadedRelease(); }}>Albums <span>{downloadedLibrary.albums.length}</span></button><button class:active={downloadedView === 'playlists'} on:click={() => { downloadedView = 'playlists'; closeDownloadedRelease(); }}>Playlists <span>{downloadedLibrary.playlists.length}</span></button><button class="secondary compact" on:click={forceRefreshDownloadedMusicLibrary}>Rescan</button></div>
            {#if downloadedLibraryLoading}<div class="state-card"><div class="spinner"></div><p>Scanning your library…</p></div>{:else if downloadedLibraryError}<div class="state-card error"><p>{downloadedLibraryError}</p></div>{:else}<div class="release-grid downloaded-release-grid">{#each (downloadedView === 'albums' ? downloadedLibrary.albums : downloadedLibrary.playlists) as release (release.relative_path)}<button class="release-tile" on:click={() => openDownloadedRelease(release)}>{#if release.artwork_url && !brokenArtwork.has(release.relative_path)}<img src={release.artwork_url} alt="" loading="lazy" decoding="async" on:error={() => markArtworkBroken(release.relative_path)} />{:else}<div class="release-placeholder"><Library size={34}/></div>{/if}<strong>{release.title}</strong><span>{releaseMetaLine(release)}</span></button>{/each}</div>{/if}
          {/if}

        {:else if currentPage === 'devices'}
          <section class="device-heading"><div><p class="eyebrow">Powered by iOpenPod</p><h2>Connected iPods</h2><p>Classic, Mini, and Nano devices mounted as a drive appear here.</p></div><button class="secondary" on:click={loadIPodDevices} disabled={ipodDevicesLoading}>{ipodDevicesLoading ? 'Scanning…' : 'Scan Again'}</button></section>
          {#if ipodDevicesLoading}<div class="state-card"><div class="spinner"></div><p>Scanning mounted drives…</p></div>{:else if ipodDevicesError}<div class="state-card error"><p>{ipodDevicesError}</p></div>{:else if !ipodDevices.length}<div class="state-card"><h3>No mounted iPod found</h3><p>Connect the iPod, wait for Windows to mount it as a drive, then scan again.</p></div>{:else}<div class="device-grid">{#each ipodDevices as device (device.path)}<article class="settings-section"><div class="device-title"><div class="ipod-glyph">♫</div><div><h2>{device.name}</h2><p>{device.model_family}{device.generation ? ` · ${device.generation}` : ''}{device.capacity ? ` · ${device.capacity}` : ''}</p></div></div><div class="device-storage"><div><span>Used</span><strong>{Math.max(0, device.disk_size_gb - device.free_space_gb).toFixed(1)} GB of {device.disk_size_gb.toFixed(1)} GB</strong></div><progress max={device.disk_size_gb || 1} value={Math.max(0, device.disk_size_gb - device.free_space_gb)}></progress></div><dl><div><dt>Mount</dt><dd>{device.path}</dd></div><div><dt>Model</dt><dd>{device.model_number || 'Detected by device database'}</dd></div><div><dt>Firmware</dt><dd>{device.firmware || 'Unknown'}</dd></div><div><dt>Database</dt><dd>{device.uses_sqlite_db ? 'SQLite' : 'iTunesDB'} · {device.checksum_type || 'standard'}</dd></div></dl><div class="capability-row">{#if device.podcasts_supported}<span>Podcasts</span>{/if}{#if device.voice_memos_supported}<span>Voice Memos</span>{/if}{#each device.audio_codecs.slice(0,4) as codec}<span>{codec}</span>{/each}</div></article>{/each}</div>{/if}

        {/if}
        {#if showSettings}
          <div class="settings-overlay" role="presentation" on:click|self={() => showSettings = false}>
            <section class="settings-dialog" role="dialog" aria-modal="true" aria-label="Settings">
              <header class="settings-dialog-header"><div><p class="eyebrow">Preferences</p><h1>Settings</h1></div><div class="settings-header-actions">{#if settingsPage === 'apple'}<button class="secondary compact" on:click={() => loadAppleMusicLibrary(true)} disabled={appleLibraryLoading}><RefreshCw size={15}/> Refresh library index</button><button class="danger-link compact" on:click={resetAppleIndex}>Reset index</button>{/if}<button class="close-settings" aria-label="Close settings" on:click={() => showSettings = false}><X size={18}/></button></div></header>
              <div class="settings-shell">
                <nav class="settings-tabs" aria-label="Settings pages"><button class:active={settingsPage === 'general'} on:click={() => settingsPage = 'general'}><Settings size={17}/> General</button><button class:active={settingsPage === 'apple'} on:click={() => settingsPage = 'apple'}><Library size={17}/> Apple Music</button><button class:active={settingsPage === 'downloads'} on:click={() => settingsPage = 'downloads'}><Download size={17}/> Downloads</button><button class:active={settingsPage === 'audio'} on:click={() => settingsPage = 'audio'}><SlidersHorizontal size={17}/> Audio & sources</button><button class:active={settingsPage === 'discovery'} on:click={() => settingsPage = 'discovery'}><Compass size={17}/> Discover</button><button class:active={settingsPage === 'naming'} on:click={() => settingsPage = 'naming'}><FolderOpen size={17}/> File naming</button><button class:active={settingsPage === 'providers'} on:click={() => settingsPage = 'providers'}><MoreHorizontal size={17}/> Providers</button></nav>
                <div class="settings-layout" data-page={settingsPage} on:change={autoSaveSettings}>
            {#if settingsPage === 'downloads'}<section class="settings-section" id="settings-downloads"><div class="settings-heading"><div><p class="eyebrow">Downloads</p><h2>Performance</h2></div></div><div class="setting-row"><div><strong>Concurrent song downloads</strong><span>Vela uses a bounded worker pool. Two is a safe default; higher values may trigger provider limits.</span></div><input class="number-input" type="number" min="1" max="8" bind:value={config.max_concurrent_jobs} /></div><div class="setting-row"><div><strong>Destination</strong><span>{config.download_path || 'Not selected'}\Apple Music</span></div><button class="secondary" on:click={pickDir}>Change parent</button></div></section>{/if}
            {#if settingsPage === 'general'}<section class="settings-section" id="settings-general"><div class="settings-heading"><div><p class="eyebrow">General</p><h2>Appearance & Library</h2></div></div><div class="setting-row"><div><strong>Appearance</strong><span>Follow the system or choose a fixed mode.</span></div><div class="appearance-control large"><button class:active={appearance === 'system'} on:click={() => applyAppearance('system')}>System</button><button class:active={appearance === 'light'} on:click={() => applyAppearance('light')}>Light</button><button class:active={appearance === 'dark'} on:click={() => applyAppearance('dark')}>Dark</button></div></div><div class="setting-row"><div><strong>Apple Music folder</strong><span>{config.download_path || 'Not selected'}\Apple Music</span></div><button class="secondary" on:click={pickDir}>Change parent</button></div><div class="setting-row"><div><strong>Library mode</strong><span>Reuse local audio while materializing every requested album and playlist folder.</span></div><select bind:value={config.library_mode}><option value="smart_dedup">Reuse local audio</option><option value="full_albums">Download every copy</option></select></div></section>{/if}

            <section class="settings-section" id="settings-audio"><div class="settings-heading"><div><p class="eyebrow">Add Music</p><h2>Audio Format & Sources</h2></div></div><div class="choice-grid">{#each formatOptions as fmt}<button class:active={_fmtBase === fmt.value} on:click={() => setParentFormat(fmt.value)}><strong>{fmt.name}</strong><span>{fmt.label}</span></button>{/each}</div>{#if showBitDepthRow}<div class="segmented"><button class:active={!_fmtBitDepth} on:click={() => setParentFormat(_fmtBase)}>Best</button><button class:active={_fmtBitDepth === '16'} on:click={() => setBitDepth('16')}>16-bit</button><button class:active={_fmtBitDepth === '24'} on:click={() => setBitDepth('24')}>24-bit</button></div>{/if}<div class="panel-heading source-heading"><h3>Sources</h3><span>Auto selects the best available match.</span></div><div class="source-grid">{#each downloadSourceOptions as src}<button class:active={selectedDownloadSources.includes(src.value)} on:click={() => toggleDownloadSource(src.value)}>{#if src.icon}<img src={src.icon} alt="" />{:else}<span class="auto-source">A</span>{/if}<span>{src.label}</span></button>{/each}</div></section>

            <section class="settings-section" id="settings-discovery"><div class="settings-heading"><div><p class="eyebrow">Discover</p><h2>Storefront & Genre</h2></div></div><div class="setting-row"><label for="region">Storefront</label><select id="region" bind:value={discoveryRegion} on:change={() => { config.apple_storefront = discoveryRegion; loadDiscoveryGenres(); loadDiscoveryData(); }}><option value="gb">United Kingdom</option><option value="us">United States</option><option value="ca">Canada</option><option value="au">Australia</option><option value="de">Germany</option><option value="fr">France</option><option value="jp">Japan</option><option value="in">India</option></select></div><div class="setting-row"><label for="genre">Genre</label><select id="genre" bind:value={discoveryGenre} on:change={loadDiscoveryData}><option value="">All genres</option>{#each discoveryGenres as genre}<option value={genre.id}>{genre.name}</option>{/each}</select></div></section>

            <section class="settings-section"><div class="settings-heading"><div><p class="eyebrow">Connected library</p><h2>Apple Music</h2></div><span class:connected={!!config.apple_music_user_token} class="connection-badge">{config.apple_music_user_token ? 'Connected' : 'Not connected'}</span></div><div class="privacy-banner"><strong>Local credentials only</strong><span>Tokens are stored in Vela’s local configuration and sent only to Apple’s authenticated endpoints. They are never uploaded to a Vela mirror.</span></div><div class="setting-row"><div><strong>Browser connection</strong><span>Capture a valid Apple Music browser session locally.</span>{#if appleLogin.message}<small class:error-text={appleLogin.phase === 'error'}>{appleLogin.message}</small>{/if}</div><button class="primary" on:click={startAppleLogin}>{config.apple_music_user_token ? 'Reconnect' : 'Connect'}</button></div><details><summary>Manual credentials</summary><label>Authorization token<input type="password" bind:value={config.apple_authorization_token} autocomplete="off" /></label><label>Music User Token<input type="password" bind:value={config.apple_music_user_token} autocomplete="off" /></label><label>Storefront<input bind:value={config.apple_storefront} maxlength="2" /></label></details></section>

            <section class="settings-section"><div class="settings-heading"><div><p class="eyebrow">Automation</p><h2>Apple Playlist Sync</h2></div><label class="switch"><input type="checkbox" bind:checked={config.auto_sync_enabled} /><span></span></label></div><p class="section-note">Runs only while Vela is open. Only Apple Music playlists can be tracked.</p><div class="sync-controls"><label>Hour<input type="number" min="0" max="23" bind:value={config.auto_sync_hour} /></label><label>Minute<input type="number" min="0" max="59" bind:value={config.auto_sync_minute} /></label><button class="secondary" on:click={runAutoSyncNow} disabled={autoSyncRunning}>{autoSyncRunning ? 'Syncing…' : 'Sync Now'}</button></div>{#if autoSyncLastResult}<p class="result-note">{autoSyncLastResult}</p>{/if}</section>

            <section class="settings-section"><div class="settings-heading"><div><p class="eyebrow">Downloads</p><h2>Output & Matching</h2></div></div><div class="setting-row"><div><strong>Strict matching</strong><span>Prefer a clear failure over a risky recording match.</span></div><label class="switch"><input type="checkbox" bind:checked={config.strict_matching} /><span></span></label></div><div class="setting-row"><div><strong>Prefer explicit versions</strong><span>Avoid clean or radio edits when possible.</span></div><label class="switch"><input type="checkbox" bind:checked={config.prefer_explicit} /><span></span></label></div><div class="setting-row"><div><strong>Fetch lyrics</strong><span>Save synced or plain lyrics when available.</span></div><label class="switch"><input type="checkbox" bind:checked={config.fetch_lyrics} /><span></span></label></div><div class="setting-row"><div><strong>Save cover sidecar</strong><span>Write cover artwork alongside the audio.</span></div><label class="switch"><input type="checkbox" bind:checked={config.save_cover_art_sidecar} /><span></span></label></div></section>

            <section class="settings-section"><div class="settings-heading"><div><p class="eyebrow">Organization</p><h2>File Naming</h2></div></div><label>Single track filename<input bind:this={focusedTemplateEl} bind:value={config.single_track_filename_template} /><small>{renderPreview(config.single_track_filename_template)}.flac</small></label><label>Album track filename<input bind:value={config.album_track_filename_template} on:focus={(e) => focusedTemplateEl = e.currentTarget} /><small>{renderPreview(config.album_track_filename_template)}.flac</small></label><label>Folder structure<input bind:value={config.folder_structure_template} on:focus={(e) => focusedTemplateEl = e.currentTarget} /><small>{renderPreview(config.folder_structure_template)}/</small></label><div class="token-row">{#each ['{title}','{artist}','{album_artist}','{album}','{year}','{track}','{disc}','{quality}'] as token}<button on:click={() => insertToken(token)}>{token}</button>{/each}</div></section>

            <section class="settings-section advanced"><div class="settings-heading"><div><p class="eyebrow">Advanced downloader</p><h2>Provider Credentials</h2></div></div><p class="section-note">These accounts are optional resolver inputs, not connected libraries. Spotify account-library sync is not available.</p><details><summary>TIDAL</summary><label class="check-row"><input type="checkbox" bind:checked={config.tidal_enabled} /> Enable TIDAL resolver</label><button class="secondary" on:click={startTidalOAuth}>Connect TIDAL</button>{#if tidalOAuth.message}<p class="result-note">{tidalOAuth.message}</p>{/if}</details><details><summary>Amazon Music</summary><label class="check-row"><input type="checkbox" bind:checked={config.amazon_enabled} /> Enable Amazon resolver</label><button class="secondary" on:click={startAmazonLogin}>Connect Amazon</button>{#if amazonLogin.phase === 'waiting_for_user'}<button class="primary" on:click={confirmAmazonBrowserLogin}>I’m Signed In</button>{/if}{#if amazonLogin.message}<p class="result-note">{amazonLogin.message}</p>{/if}</details><details><summary>Spotify downloader session</summary><button class="secondary" on:click={startSpotifyDownloaderCapture}>Capture browser session</button>{#if spDcCapture.message}<p class="result-note">{spDcCapture.message}</p>{/if}<label>sp_dc cookie<input type="password" bind:value={config.spotify_sp_dc} autocomplete="off" /></label><p class="fine-print">Used only by retained Spotify URL and podcast downloader paths. It does not create or sync a Spotify library.</p></details><details><summary>Qobuz & Deezer</summary><label class="check-row"><input type="checkbox" bind:checked={config.qobuz_enabled} /> Enable Qobuz resolver</label><label>Qobuz email<input bind:value={config.qobuz_email} /></label><label>Qobuz password<input type="password" bind:value={config.qobuz_password} /></label><label>Deezer ARL<input type="password" bind:value={config.deezer_arl_token} /></label></details></section>
            <div class="settings-auto-status" class:error={settingsSaveState === 'error'}>{settingsSaveState === 'saving' ? 'Saving…' : settingsSaveState === 'saved' ? 'Saved' : settingsError}</div>
              </div>
              </div>
            </section>
          </div>
        {/if}
      </main>
    </section>

    {#if isDownloading || downloadJobs.some(job => job.status === 'waiting')}
      <aside class="queue-panel compact-queue" on:click={() => selectPage('downloads')}>
        <button class="queue-header" on:click={() => selectPage('downloads')}>
          {#if playlistArtwork}<img class="queue-art" src={playlistArtwork} alt="" />{:else}<span class="queue-art placeholder"><Download size={18}/></span>{/if}
          <div><strong>{queuePaused ? 'Downloads paused' : (playlistTitle || 'Preparing download')}</strong><span>{queueFinishedCount}/{queueTrackKeys.length || '…'} songs · {downloadJobs.filter(job => job.status === 'waiting').length} queued</span></div>
        </button>
        <div class="queue-controls"><button aria-label={queuePaused ? 'Resume queue' : 'Pause queue'} on:click={toggleQueuePause}>{#if queuePaused}<Play size={17}/>{:else}<Pause size={17}/>{/if}</button><button aria-label="Cancel queue" on:click|stopPropagation={cancelDownload}><X size={17}/></button></div>
        <div class="queue-overall-progress" style={`--queue-progress:${queueOverallProgress}%`}></div>
      </aside>
    {/if}

    {#if currentPlayerTrack}
      <div class="player-bar"><div class="player-title"><strong>{currentPlayerTrack.title}</strong><span>{currentPlayerTrack.artist || playerReleaseTitle}</span></div><div class="player-controls"><button on:click={playPreviousTrack}>‹</button><button class="play-button" on:click={togglePlayback}>{audioEl?.paused ? '▶' : 'Ⅱ'}</button><button on:click={playNextTrack}>›</button></div><div class="seek"><span>{formatPlaybackTime(playerCurrentTime)}</span><input type="range" min="0" max={playerDuration || 0} value={playerCurrentTime} on:input={handleSeekInput} on:change={handleSeekCommit} /><span>{formatPlaybackTime(playerDuration)}</span></div></div>
    {/if}
    <audio bind:this={audioEl} on:timeupdate={handleAudioTimeUpdate} on:loadedmetadata={handleAudioLoadedMetadata} on:ended={handleAudioEnded}></audio>
  </div>

  {#if showCustomDownload}<div class="modal-backdrop" role="presentation" on:click={() => showCustomDownload = false}><section class="modal-card" role="dialog" aria-modal="true" on:click|stopPropagation><header><div><p class="eyebrow">Downloads</p><h2>Add custom link</h2></div><button aria-label="Close" on:click={() => showCustomDownload = false}><X size={18}/></button></header><label class="field-label" for="custom-links">Track, album, playlist, or artist links</label><textarea id="custom-links" class="custom-links" bind:this={inputUrlEl} bind:value={inputUrl} placeholder="One link per line"></textarea><div class="custom-destination"><div><strong>Destination</strong><span>{customDestination || config.download_path}\Apple Music</span></div><button class="secondary" on:click={chooseCustomDestination}><FolderOpen size={16}/> Choose</button></div><footer class="modal-actions"><button class="icon-button" aria-label="Audio and source settings" on:click={() => { showCustomDownload = false; openSettings('settings-audio'); }}><Settings size={18}/></button><button class="primary" disabled={!inputUrl.trim()} on:click={startCustomDownload}><Download size={16}/> {isDownloading ? 'Add to queue' : 'Download'}</button></footer></section></div>{/if}

  {#if libraryContextItem}<div class="context-dismiss" on:click={() => libraryContextItem = null} on:contextmenu|preventDefault={() => libraryContextItem = null}></div><div class="context-menu" style={`left:${libraryContextX}px;top:${libraryContextY}px`}><button on:click={() => { if (libraryContextItem) openAppleLibraryDetail(libraryContextItem.url, libraryContextItem.name, libraryContextItem.image_url || ''); libraryContextItem = null; }}><Library size={16}/> View songs</button><button on:click={() => { if (libraryContextItem) downloadPlaylistUrl(libraryContextItem.url); libraryContextItem = null; }}><Download size={16}/> Download</button><button on:click={() => { if (libraryContextItem) toggleLibrarySelection(libraryContextItem.url); libraryContextItem = null; }}><Circle size={16}/> {selectedLibraryItems.has(libraryContextItem.url) ? 'Deselect' : 'Select'}</button></div>{/if}
  {#if showLibraryNavMenu}<div class="context-dismiss" on:click={() => showLibraryNavMenu = false}></div><div class="context-menu" style={`left:${libraryNavMenuX}px;top:${libraryNavMenuY}px`}><button on:click={() => { showLibraryNavMenu = false; loadAppleMusicLibrary(true); }}><RefreshCw size={16}/> Refresh library</button></div>{/if}

  {#if showArtistSearch}
    <div class="modal-backdrop" role="presentation" on:click={() => { artistSearchReqId++; showArtistSearch = false; }}>
      <!-- svelte-ignore a11y-click-events-have-key-events -->
      <section class="modal-card" role="dialog" aria-modal="true" on:click|stopPropagation><header><div><p class="eyebrow">Apple Music</p><h2>Artist results</h2></div><button on:click={() => showArtistSearch = false}>×</button></header>{#if artistSearchLoading}<div class="state-card"><div class="spinner"></div></div>{:else}<div class="artist-results">{#each artistSearchResults as artist (artist.artist_id)}<button on:click={() => openArtistFromSearch(artist)}>{#if artist.artwork_url}<img src={artist.artwork_url} alt="" />{:else}<div class="artist-placeholder">♪</div>{/if}<span><strong>{artist.name}</strong><small>{artist.genres?.slice(0,2).join(' · ') || 'Artist'}</small></span><i>›</i></button>{/each}</div>{/if}</section>
    </div>
  {/if}

  {#if showDiscography}
    <div class="modal-backdrop" role="presentation" on:click={() => { discographyReqId++; showDiscography = false; }}>
      <!-- svelte-ignore a11y-click-events-have-key-events -->
      <section class="modal-card wide" role="dialog" aria-modal="true" on:click|stopPropagation><header><div><p class="eyebrow">Select releases</p><h2>{discographyArtist?.artist_name || 'Loading discography…'}</h2></div><button on:click={() => showDiscography = false}>×</button></header>{#if discographyLoading}<div class="state-card"><div class="spinner"></div></div>{:else if discographyArtist}<div class="select-actions"><button class="text-button" on:click={() => discographySelected = new Set(discographyArtist.albums.map(a => a.url))}>Select all</button><button class="text-button" on:click={() => discographySelected = new Set()}>Select none</button><span>{discographySelected.size} selected</span></div><div class="discography-grid">{#each discographyArtist.albums as album (album.id)}<label class:selected={discographySelected.has(album.url)}>{#if album.artwork_url}<img src={album.artwork_url} alt="" />{:else}<div class="release-placeholder">♪</div>{/if}<input type="checkbox" checked={discographySelected.has(album.url)} on:change={() => { if (discographySelected.has(album.url)) discographySelected.delete(album.url); else discographySelected.add(album.url); discographySelected = new Set(discographySelected); }} /><strong>{album.name}</strong><span>{album.year || '—'} · {album.track_count} tracks</span></label>{/each}</div><footer><button class="primary" disabled={!discographySelected.size} on:click={downloadSelectedDiscography}>Add {discographySelected.size} release{discographySelected.size === 1 ? '' : 's'} to queue</button></footer>{/if}</section>
    </div>
  {/if}
{/if}

<style>
  :global(*) { box-sizing: border-box; }
  :global(:root) { color-scheme: light; --accent:#fa2d55; --accent-soft:rgba(250,45,85,.11); --bg:#f5f5f7; --sidebar:rgba(242,242,247,.94); --surface:#fff; --surface-2:#ececf0; --surface-hover:#e5e5ea; --text:#17171a; --muted:#6e6e73; --faint:#98989d; --line:rgba(0,0,0,.09); --shadow:0 18px 55px rgba(0,0,0,.14); }
  :global(:root[data-appearance='dark']) { color-scheme: dark; --accent:#ff375f; --accent-soft:rgba(255,55,95,.16); --bg:#101012; --sidebar:rgba(27,27,30,.96); --surface:#202023; --surface-2:#2b2b2f; --surface-hover:#343439; --text:#f5f5f7; --muted:#a1a1a6; --faint:#737378; --line:rgba(255,255,255,.1); --shadow:0 22px 65px rgba(0,0,0,.46); }
  :global(html), :global(body), :global(#app) { margin:0; width:100%; height:100%; overflow:hidden; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",sans-serif; }
  :global(button), :global(input), :global(textarea), :global(select) { font:inherit; }
  :global(button) { color:inherit; }
  button { border:0; cursor:pointer; }
  .icon-sprite { position:absolute; width:0; height:0; overflow:hidden; }
  svg { fill:none; stroke:currentColor; stroke-width:1.8; stroke-linecap:round; stroke-linejoin:round; }
  .launch-screen,.setup-screen { min-height:100%; display:grid; place-items:center; background:radial-gradient(circle at 50% 30%,var(--accent-soft),transparent 38%),var(--bg); }
  .launch-screen { align-content:center; gap:22px; }.launch-name{font-size:36px;letter-spacing:-.06em}.spinner{width:22px;height:22px;border:2px solid var(--line);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
  .setup-card{width:min(540px,calc(100vw - 40px));padding:44px;border:1px solid var(--line);border-radius:24px;background:var(--surface);box-shadow:var(--shadow)}.setup-card h1{font-size:34px;letter-spacing:-.04em;margin:4px 0 12px}.lede{color:var(--muted);line-height:1.55;margin:0 0 28px}.field-label,.settings-section>label,.settings-section details label{display:grid;gap:7px;font-size:13px;font-weight:600;margin:14px 0}.input-row{display:flex;gap:9px}.input-row input{flex:1}.setup-continue{width:100%;margin-top:18px}
  .app-shell{height:100%;display:grid;grid-template-columns:240px minmax(0,1fr);background:var(--bg)}
  .sidebar{display:flex;flex-direction:column;min-width:0;padding:22px 14px 14px;background:var(--sidebar);border-right:1px solid var(--line);backdrop-filter:blur(30px);z-index:3}.brand-lockup{padding:2px 10px 24px;font-size:22px;font-weight:760;letter-spacing:-.055em}.sidebar nav{display:flex;flex-direction:column;gap:3px}.nav-label{margin:18px 10px 6px;color:var(--faint);font-size:11px;font-weight:650;text-transform:uppercase;letter-spacing:.08em}.sidebar nav button,.settings-nav{height:38px;display:flex;align-items:center;gap:11px;padding:0 10px;border-radius:9px;background:transparent;color:var(--muted);font-size:14px;text-align:left}.sidebar nav button:hover,.settings-nav:hover{background:var(--surface-hover);color:var(--text)}.sidebar nav button.active,.settings-nav.active{background:var(--accent-soft);color:var(--accent);font-weight:630}.sidebar nav svg,.settings-nav svg{width:19px;height:19px}.sidebar-footer{margin-top:auto;display:grid;gap:10px}.settings-nav{width:100%}.appearance-control{display:grid;grid-template-columns:repeat(3,1fr);gap:3px;padding:3px;background:var(--surface-2);border-radius:9px}.appearance-control button{padding:6px 3px;border-radius:7px;background:transparent;color:var(--muted);font-size:11px}.appearance-control button.active{background:var(--surface);color:var(--text);box-shadow:0 1px 4px rgba(0,0,0,.12)}.appearance-control.large{width:260px}.appearance-control.large button{font-size:13px;padding:8px}.privacy-status{display:flex;align-items:center;gap:7px;padding:4px 10px;color:var(--faint);font-size:11px}.privacy-status span,.queue-status{width:7px;height:7px;border-radius:50%;background:var(--faint)}.privacy-status span.connected,.queue-status.is-active{background:#30d158;box-shadow:0 0 0 4px rgba(48,209,88,.12)}
  .workspace{min-width:0;height:100%;display:flex;flex-direction:column}.topbar{height:82px;flex:0 0 auto;display:flex;align-items:center;justify-content:space-between;padding:15px 34px;border-bottom:1px solid var(--line);background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(24px);z-index:2}.topbar h1{margin:1px 0 0;font-size:25px;line-height:1.05;letter-spacing:-.035em}.eyebrow{margin:0;color:var(--accent);font-size:11px;font-weight:720;text-transform:uppercase;letter-spacing:.09em}.top-actions{display:flex;gap:8px}.page-content{flex:1;min-height:0;overflow:auto;padding:30px 34px 110px}.page-content>*{max-width:1240px;margin-left:auto;margin-right:auto}
  .primary,.secondary{min-height:38px;padding:0 16px;border-radius:10px;font-weight:650;font-size:13px}.primary{background:var(--accent);color:#fff}.primary:hover{filter:brightness(1.05)}.primary:disabled,.secondary:disabled{opacity:.45;cursor:default}.secondary{background:var(--surface-2);color:var(--text)}.secondary:hover{background:var(--surface-hover)}.compact{min-height:32px;padding:0 12px;font-size:12px}.text-button,.danger-link{padding:4px;background:transparent;color:var(--accent);font-weight:600}.danger-link{color:#ff453a}.queue-trigger{height:38px;display:flex;align-items:center;gap:8px;padding:0 13px;border-radius:10px;background:var(--surface-2);font-size:12px;font-weight:650}.queue-trigger svg{width:17px;height:17px}
  .demo-badge{display:inline-flex;align-items:center;height:28px;padding:0 9px;border-radius:99px;background:var(--accent-soft);color:var(--accent);font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em}
  input,textarea,select{border:1px solid var(--line);border-radius:10px;background:var(--surface-2);color:var(--text);outline:none;padding:10px 12px}input:focus,textarea:focus,select:focus{border-color:color-mix(in srgb,var(--accent) 60%,var(--line));box-shadow:0 0 0 3px var(--accent-soft)}
  .hero{min-height:235px;border-radius:24px;padding:36px;display:flex;align-items:center;justify-content:space-between;overflow:hidden}.library-hero{background:linear-gradient(120deg,#fa2d55 0%,#ff6482 46%,#ff9d80 100%);color:#fff;box-shadow:0 18px 48px rgba(250,45,85,.22)}.library-hero .eyebrow{color:rgba(255,255,255,.8)}.hero h2{max-width:650px;margin:8px 0 12px;font-size:38px;line-height:1.06;letter-spacing:-.045em}.hero p:last-child{max-width:610px;margin:0;color:rgba(255,255,255,.82);font-size:15px;line-height:1.5}.hero-art{width:170px;height:170px;display:grid;place-items:center;border-radius:40px;background:linear-gradient(145deg,rgba(255,255,255,.34),rgba(255,255,255,.08));box-shadow:inset 0 0 0 1px rgba(255,255,255,.25),0 20px 40px rgba(126,0,31,.2);transform:rotate(7deg)}.hero-art span{font-size:74px}
  .notice-card,.panel,.settings-section{background:var(--surface);border:1px solid var(--line);border-radius:18px}.notice-card{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:18px;padding:22px;margin-top:24px}.notice-icon{width:48px;height:48px;display:grid;place-items:center;border-radius:13px;background:var(--accent-soft);color:var(--accent);font-size:24px}.notice-card h3{margin:0 0 5px}.notice-card p{margin:0;color:var(--muted);font-size:13px;line-height:1.5}.state-card{min-height:220px;display:grid;place-items:center;align-content:center;gap:12px;color:var(--muted);text-align:center}.state-card.error{color:#ff453a}.section-block{margin-top:34px}.section-heading{display:flex;align-items:end;justify-content:space-between;margin-bottom:15px}.section-heading h2{font-size:24px;letter-spacing:-.03em;margin:4px 0 0}.section-heading>span{color:var(--muted);font-size:12px}.art-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(155px,1fr));gap:24px 18px}.music-card{position:relative;min-width:0}.artwork{position:relative;display:block;width:100%;aspect-ratio:1;border-radius:13px;overflow:hidden;background:var(--surface-2);box-shadow:0 8px 22px rgba(0,0,0,.12)}.artwork img{width:100%;height:100%;object-fit:cover}.artwork i{position:absolute;right:10px;bottom:10px;width:38px;height:38px;display:grid;place-items:center;border-radius:50%;background:rgba(250,45,85,.94);color:#fff;opacity:0;transform:translateY(5px);transition:.18s}.artwork:hover i{opacity:1;transform:none}.artwork i svg{width:18px;height:18px}.gradient-art{background:linear-gradient(145deg,#ff375f,#b91460);color:#fff;font-size:52px}.art-placeholder,.release-placeholder{width:100%;height:100%;display:grid;place-items:center;color:var(--faint);font-size:36px}.card-copy{display:grid;gap:3px;padding:9px 2px 0;min-width:0}.card-copy strong,.card-copy span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.card-copy strong{font-size:13px}.card-copy span{color:var(--muted);font-size:11px}.sync-pill{position:absolute;right:2px;top:calc(100% + 37px);padding:3px 8px;border-radius:99px;background:var(--surface-2);color:var(--muted);font-size:10px}.sync-pill.enabled{background:var(--accent-soft);color:var(--accent)}
  .composer-card{display:flex;gap:18px;padding:22px;border-radius:20px;background:var(--surface);border:1px solid var(--line)}.composer-icon{width:48px;height:48px;flex:0 0 auto;display:grid;place-items:center;border-radius:14px;background:var(--accent-soft);color:var(--accent)}.composer-icon svg{width:24px;height:24px}.composer-main{flex:1;display:grid;gap:10px}.composer-main label{font-size:14px;font-weight:650}.composer-main textarea{min-height:92px;resize:vertical;line-height:1.45}.composer-actions{display:flex;align-items:center;justify-content:space-between;gap:16px}.composer-actions span,.fine-print{color:var(--muted);font-size:11px}.control-grid{display:grid;grid-template-columns:1.15fr .85fr;gap:18px;margin-top:18px}.panel{padding:22px}.panel-heading{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:14px}.panel-heading h3{margin:0;font-size:16px}.panel-heading span{color:var(--muted);font-size:11px}.choice-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:7px}.choice-grid button{display:grid;gap:5px;min-height:72px;padding:10px;border:1px solid var(--line);border-radius:11px;background:var(--surface-2);text-align:left}.choice-grid button.active{border-color:var(--accent);background:var(--accent-soft);color:var(--accent)}.choice-grid strong{font-size:13px}.choice-grid span{color:var(--muted);font-size:9px;line-height:1.25}.segmented{display:flex;gap:3px;width:max-content;margin-top:13px;padding:3px;background:var(--surface-2);border-radius:9px}.segmented button{padding:5px 12px;border-radius:7px;background:transparent;color:var(--muted);font-size:11px}.segmented button.active{background:var(--surface);color:var(--text)}.source-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}.source-grid button{display:flex;align-items:center;gap:7px;min-height:41px;padding:7px;border:1px solid var(--line);border-radius:10px;background:var(--surface-2);font-size:11px}.source-grid button.active{border-color:var(--accent);background:var(--accent-soft);color:var(--accent)}.source-grid img,.auto-source{width:22px;height:22px;display:grid;place-items:center;border-radius:6px;object-fit:cover;background:var(--surface);font-weight:750}.session-panel{margin-top:18px}.release-header{display:flex;align-items:center;gap:16px}.release-header>img{width:80px;height:80px;border-radius:12px;object-fit:cover}.release-header>div{flex:1}.release-header h2{margin:4px 0;font-size:22px}.release-header p:last-child{margin:0;color:var(--muted);font-size:12px}.failure-strip{display:flex;justify-content:space-between;margin-top:16px;padding:12px;border-radius:10px;background:rgba(255,69,58,.1);color:#ff453a}.failure-strip button{background:transparent;color:inherit}.log-button{margin-top:14px}.log-card{max-height:300px;overflow:auto;margin-top:10px;padding:16px;border-radius:14px;background:#111;color:#c7c7cc;font:11px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace}.log-card .error{color:#ff6961}.log-card .success{color:#64d27b}
  .search-hero{max-width:850px!important;padding:60px 0 30px;text-align:center}.search-hero h2{margin:8px 0 12px;font-size:40px;letter-spacing:-.045em}.search-hero>p:last-of-type{color:var(--muted);line-height:1.5}.search-bar{height:56px;display:flex;align-items:center;gap:10px;margin-top:26px;padding:6px 7px 6px 16px;border:1px solid var(--line);border-radius:15px;background:var(--surface);box-shadow:0 10px 34px rgba(0,0,0,.08)}.search-bar svg{width:21px;height:21px;color:var(--muted)}.search-bar input{flex:1;border:0;background:transparent;box-shadow:none!important}.empty-feature{max-width:650px!important;margin-top:50px;text-align:center;color:var(--muted)}.empty-feature h3{color:var(--text)}.vinyl{width:100px;height:100px;display:grid;place-items:center;margin:auto;border-radius:50%;background:repeating-radial-gradient(circle,#252527 0 7px,#111 8px 12px);color:#fff;font-size:28px}.filter-bar{display:flex;align-items:end;gap:10px;padding:16px;border:1px solid var(--line);border-radius:16px;background:var(--surface)}.filter-bar div{display:grid;gap:5px}.filter-bar label{font-size:11px;color:var(--muted)}
  .library-tabs{display:flex;align-items:center;gap:4px;margin-bottom:18px}.library-tabs button{padding:8px 13px;border-radius:9px;background:transparent;color:var(--muted)}.library-tabs button.active{background:var(--surface-2);color:var(--text);font-weight:650}.library-tabs button span{font-size:10px}.library-tabs .secondary{margin-left:auto}.downloaded-layout{display:grid;grid-template-columns:minmax(360px,.9fr) minmax(430px,1.1fr);gap:20px}.release-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));align-content:start;gap:18px 13px}.release-tile{display:grid;gap:5px;padding:7px;border-radius:13px;background:transparent;text-align:left}.release-tile:hover,.release-tile.selected{background:var(--surface-2)}.release-tile img,.release-tile .release-placeholder{width:100%;aspect-ratio:1;border-radius:10px;object-fit:cover;background:var(--surface-2)}.release-tile strong,.release-tile span{overflow:hidden;white-space:nowrap;text-overflow:ellipsis}.release-tile strong{font-size:12px}.release-tile span{font-size:10px;color:var(--muted)}.release-detail{position:sticky;top:0;max-height:calc(100vh - 150px);overflow:auto}.detail-head{display:flex;align-items:end;gap:18px}.detail-head img{width:130px;height:130px;border-radius:14px;object-fit:cover}.detail-head h2{margin:5px 0;font-size:27px}.detail-head p:last-child{margin:0;color:var(--muted);font-size:12px}.track-table{margin-top:20px}.track-table button{width:100%;display:grid;grid-template-columns:28px 1fr auto;align-items:center;gap:8px;padding:9px 7px;border-radius:8px;background:transparent;text-align:left}.track-table button:hover,.track-table button.playing{background:var(--surface-2)}.track-table button.playing{color:var(--accent)}.track-table button>span:nth-child(2){display:grid;gap:2px}.track-table small{color:var(--muted)}.track-index{text-align:center;color:var(--muted);font-size:11px}.empty-detail{min-height:260px;display:grid;place-items:center;color:var(--muted)}
  .history-toolbar{display:flex;justify-content:space-between;align-items:center;color:var(--muted)}.history-list{display:grid;gap:8px}.history-list article{display:grid;grid-template-columns:54px 1fr auto;align-items:center;gap:13px;padding:11px;border:1px solid var(--line);border-radius:13px;background:var(--surface)}.history-list article.error{border-color:rgba(255,69,58,.35)}.history-list img,.history-art{width:54px;height:54px;border-radius:9px;object-fit:cover;background:var(--surface-2);display:grid;place-items:center}.history-list article>div:nth-child(2){min-width:0;display:grid;gap:4px}.history-list strong,.history-list span{overflow:hidden;white-space:nowrap;text-overflow:ellipsis}.history-list span{color:var(--muted);font-size:11px}.history-list small{color:#ff453a}
  .settings-layout{max-width:900px!important;display:grid;gap:16px}.settings-section{padding:23px}.settings-heading{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.settings-heading h2{margin:4px 0 0;font-size:20px}.setting-row{min-height:64px;display:flex;align-items:center;justify-content:space-between;gap:24px;padding:12px 0;border-top:1px solid var(--line)}.setting-row>div{display:grid;gap:4px}.setting-row span,.section-note,.settings-section small{color:var(--muted);font-size:11px}.setting-row select{min-width:170px}.connection-badge{padding:5px 9px;border-radius:99px;background:var(--surface-2);color:var(--muted);font-size:10px}.connection-badge.connected{background:rgba(48,209,88,.13);color:#28a745}.privacy-banner{display:grid;gap:5px;margin:13px 0;padding:13px;border-radius:11px;background:var(--accent-soft);color:var(--accent);font-size:12px}.privacy-banner span{color:var(--muted);line-height:1.5}.settings-section details{padding:12px 0;border-top:1px solid var(--line)}.settings-section summary{cursor:pointer;font-size:13px;font-weight:650}.settings-section details input:not([type=checkbox]){width:100%}.check-row{display:flex!important;align-items:center;gap:8px}.switch{position:relative;width:42px;height:25px;margin:0!important}.switch input{opacity:0;width:0;height:0}.switch span{position:absolute;inset:0;border-radius:99px;background:var(--surface-2);transition:.2s}.switch span:after{content:"";position:absolute;width:19px;height:19px;left:3px;top:3px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.25);transition:.2s}.switch input:checked+span{background:var(--accent)}.switch input:checked+span:after{transform:translateX(17px)}.sync-controls{display:flex;align-items:end;gap:10px}.sync-controls label{display:grid;gap:5px;color:var(--muted);font-size:11px}.sync-controls input{width:85px}.result-note{padding:10px;border-radius:9px;background:var(--surface-2);color:var(--muted);font-size:11px}.error-text{color:#ff453a!important}.token-row{display:flex;flex-wrap:wrap;gap:5px}.token-row button{padding:4px 7px;border-radius:7px;background:var(--surface-2);color:var(--accent);font:10px ui-monospace,monospace}.save-bar{position:sticky;bottom:-90px;display:flex;align-items:center;justify-content:space-between;padding:14px 16px;border:1px solid var(--line);border-radius:14px;background:color-mix(in srgb,var(--surface) 92%,transparent);box-shadow:var(--shadow);backdrop-filter:blur(20px)}.save-bar span{color:var(--muted);font-size:11px}
  .queue-panel{position:fixed;right:22px;bottom:22px;width:320px;z-index:20;border:1px solid var(--line);border-radius:16px;background:color-mix(in srgb,var(--surface) 94%,transparent);box-shadow:var(--shadow);backdrop-filter:blur(30px);overflow:hidden}.queue-panel.expanded{width:390px}.queue-header{width:100%;height:66px;display:flex;align-items:center;gap:11px;padding:0 15px;background:transparent;text-align:left}.queue-header>div{flex:1;display:grid;gap:3px}.queue-header strong{font-size:13px}.queue-header span{color:var(--muted);font-size:10px}.queue-header .queue-status{flex:0 0 auto}.chevron{font-size:17px!important}.queue-body{max-height:380px;overflow:auto;border-top:1px solid var(--line)}.queue-body article{display:grid;grid-template-columns:9px 1fr auto;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid var(--line)}.queue-body article>div{min-width:0;display:grid;gap:3px}.queue-body strong,.queue-body span{overflow:hidden;white-space:nowrap;text-overflow:ellipsis}.queue-body strong{font-size:11px}.queue-body span{color:var(--muted);font-size:9px}.queue-body article button{background:transparent;color:var(--accent);font-size:10px}.state-dot{width:7px;height:7px;border-radius:50%;background:var(--faint)}.state-dot.downloading{background:#0a84ff}.state-dot.done{background:#30d158}.state-dot.failed{background:#ff453a}.state-dot.skipped{background:#ff9f0a}.queue-body progress{width:100%;height:3px;accent-color:var(--accent)}.queue-separator{padding:6px 14px;background:var(--surface-2);color:var(--muted);font-size:9px;font-weight:700;text-transform:uppercase}.queue-footer{display:flex;justify-content:space-between;padding:10px 14px}.player-bar{position:fixed;left:262px;right:22px;bottom:18px;height:64px;z-index:15;display:grid;grid-template-columns:minmax(180px,.8fr) auto minmax(280px,1.2fr);align-items:center;gap:18px;padding:8px 17px;border:1px solid var(--line);border-radius:16px;background:color-mix(in srgb,var(--surface) 94%,transparent);box-shadow:var(--shadow);backdrop-filter:blur(30px)}.player-title{display:grid;gap:2px;min-width:0}.player-title strong,.player-title span{overflow:hidden;white-space:nowrap;text-overflow:ellipsis}.player-title span{color:var(--muted);font-size:10px}.player-controls{display:flex;align-items:center;gap:5px}.player-controls button{width:28px;height:28px;border-radius:50%;background:transparent}.player-controls .play-button{background:var(--text);color:var(--surface)}.seek{display:grid;grid-template-columns:32px 1fr 32px;align-items:center;gap:7px;color:var(--muted);font-size:9px}.seek input{width:100%;padding:0;border:0;box-shadow:none}
  .modal-backdrop{position:fixed;inset:0;z-index:50;display:grid;place-items:center;padding:28px;background:rgba(0,0,0,.38);backdrop-filter:blur(14px)}.modal-card{width:min(560px,100%);max-height:80vh;display:flex;flex-direction:column;padding:22px;border:1px solid var(--line);border-radius:20px;background:var(--surface);box-shadow:var(--shadow)}.modal-card.wide{width:min(880px,100%)}.modal-card header{display:flex;align-items:center;justify-content:space-between;padding-bottom:15px;border-bottom:1px solid var(--line)}.modal-card header h2{margin:4px 0 0}.modal-card header>button{width:32px;height:32px;border-radius:50%;background:var(--surface-2);font-size:20px}.artist-results{overflow:auto;display:grid;gap:4px;padding-top:10px}.artist-results>button{display:grid;grid-template-columns:48px 1fr auto;align-items:center;gap:12px;padding:8px;border-radius:11px;background:transparent;text-align:left}.artist-results>button:hover{background:var(--surface-2)}.artist-results img,.artist-placeholder{width:48px;height:48px;border-radius:50%;object-fit:cover;background:var(--surface-2);display:grid;place-items:center}.artist-results span{display:grid;gap:3px}.artist-results small{color:var(--muted)}.artist-results i{font-size:24px;color:var(--muted)}.select-actions{display:flex;align-items:center;gap:10px;padding:12px 0}.select-actions span{margin-left:auto;color:var(--muted);font-size:11px}.discography-grid{overflow:auto;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.discography-grid label{position:relative;min-width:0;padding:7px;border:1px solid transparent;border-radius:12px;cursor:pointer}.discography-grid label.selected{border-color:var(--accent);background:var(--accent-soft)}.discography-grid img,.discography-grid .release-placeholder{width:100%;aspect-ratio:1;border-radius:9px;object-fit:cover;background:var(--surface-2)}.discography-grid input{position:absolute;right:12px;top:12px;accent-color:var(--accent)}.discography-grid strong,.discography-grid span{display:block;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}.discography-grid strong{margin-top:7px;font-size:11px}.discography-grid span{margin-top:3px;color:var(--muted);font-size:9px}.modal-card footer{padding-top:15px}.modal-card footer .primary{width:100%}
  /* Structural overrides keep the inner page as the sole scroll container in
     Wails/WebView2. Every flex/grid parent must allow its child to shrink. */
  .app-shell{min-height:0;overflow:hidden}
  .sidebar{min-height:0;padding-top:8px}
  .workspace{min-height:0;overflow:hidden}
  .page-content{overflow-x:hidden;overflow-y:auto;overscroll-behavior:contain;-webkit-overflow-scrolling:touch}
  .artwork{padding:0;box-shadow:none}
  .icon-button{width:38px;height:38px;flex:0 0 auto;display:grid;place-items:center;padding:0;border-radius:10px;background:var(--surface-2);color:var(--muted)}
  .icon-button:hover{background:var(--surface-hover);color:var(--text)}
  .icon-button svg{width:19px;height:19px}
  .discover-search{max-width:760px!important;margin-bottom:28px}
  .discover-search .search-bar{margin-top:0}
  .settings-overlay{position:fixed;inset:0;z-index:60;display:grid;place-items:center;padding:24px;background:rgba(0,0,0,.4);backdrop-filter:blur(16px)}
  .settings-dialog{width:min(980px,100%);height:min(860px,calc(100vh - 48px));min-height:0;display:flex;flex-direction:column;overflow:hidden;border:1px solid var(--line);border-radius:22px;background:var(--bg);box-shadow:var(--shadow)}
  .settings-dialog-header{height:76px;flex:0 0 auto;display:flex;align-items:center;justify-content:space-between;padding:14px 22px;border-bottom:1px solid var(--line);background:var(--surface)}
  .settings-dialog-header h1{margin:2px 0 0;font-size:24px}
  .settings-header-actions{display:flex;align-items:center;gap:8px}.settings-dialog-header .close-settings{width:34px;height:34px;display:grid;place-items:center;padding:0;border-radius:50%;background:var(--surface-2)}
  .settings-shell{display:grid;grid-template-columns:190px minmax(0,1fr);min-height:0;flex:1}.settings-tabs{display:flex;flex-direction:column;gap:4px;padding:16px 10px;border-right:1px solid var(--line);background:var(--sidebar)}.settings-tabs button{display:flex;align-items:center;gap:9px;padding:9px 10px;border-radius:9px;background:transparent;color:var(--muted);text-align:left}.settings-tabs button:hover{background:var(--surface-hover);color:var(--text)}.settings-tabs button.active{background:var(--accent-soft);color:var(--accent);font-weight:650}
  .settings-dialog .settings-layout{width:100%;max-width:none!important;min-height:0;overflow-y:auto;padding:20px 24px 54px}
  .settings-layout>.settings-section{display:none}.settings-layout[data-page="general"]>#settings-general,.settings-layout[data-page="downloads"]>#settings-downloads,.settings-layout[data-page="downloads"]>.settings-section:nth-of-type(6),.settings-layout[data-page="audio"]>#settings-audio,.settings-layout[data-page="discovery"]>#settings-discovery,.settings-layout[data-page="apple"]>.settings-section:nth-of-type(3),.settings-layout[data-page="apple"]>.settings-section:nth-of-type(4),.settings-layout[data-page="naming"]>.settings-section:nth-of-type(6),.settings-layout[data-page="providers"]>.settings-section:nth-of-type(7){display:block}
  .settings-auto-status{position:absolute;right:72px;top:29px;color:var(--muted);font-size:11px}.settings-auto-status.error{color:#ff453a}.number-input{width:72px}.hidden{display:none!important}
  .settings-section{scroll-margin-top:18px}
  .library-tools{display:flex;align-items:center;gap:9px}.compact-search{flex:1;max-width:440px;padding:0 12px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}.compact-search input{border:0;background:transparent;box-shadow:none}.icon-select{display:flex;align-items:center;gap:7px;padding:0 9px;border:1px solid var(--line);border-radius:10px;background:var(--surface)}.icon-select select{border:0;background:transparent;box-shadow:none}.selection-toolbar{position:sticky;top:-30px;z-index:5;display:flex;align-items:center;gap:9px;margin:14px 0;padding:10px 12px;border:1px solid var(--accent);border-radius:12px;background:var(--surface);box-shadow:var(--shadow)}.selection-toolbar strong{margin-right:auto}.music-card.selectable.selected{padding:5px;margin:-5px;border:2px solid var(--accent);border-radius:17px}.select-release{position:absolute;z-index:3;left:9px;top:9px;width:24px;height:24px;display:grid;place-items:center;border:2px solid rgba(255,255,255,.9);border-radius:50%;background:rgba(0,0,0,.28);color:white;opacity:0;box-shadow:0 1px 5px rgba(0,0,0,.25)}.music-card:hover .select-release,.select-release.selected{opacity:1}.select-release.selected{border-color:var(--accent);background:var(--accent)}.offline-icon{margin-left:auto}
  .custom-links{min-height:130px;margin-top:8px;resize:vertical}.custom-destination{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-top:14px;padding:13px;border:1px solid var(--line);border-radius:12px}.custom-destination>div{display:grid;gap:4px;min-width:0}.custom-destination span{overflow:hidden;text-overflow:ellipsis;color:var(--muted);font-size:11px}.modal-actions{display:flex;justify-content:flex-end;gap:9px}.context-dismiss{position:fixed;inset:0;z-index:79}.context-menu{position:fixed;z-index:80;width:205px;display:grid;gap:3px;padding:6px;border:1px solid var(--line);border-radius:11px;background:var(--surface);box-shadow:var(--shadow)}.context-menu button{display:flex;align-items:center;gap:9px;padding:9px;border-radius:7px;background:transparent;text-align:left}.context-menu button:hover{background:var(--surface-2)}
  .job-status{padding:4px 8px;border-radius:99px;background:var(--surface-2);color:var(--muted);font-size:9px;text-transform:capitalize}.job-status.downloading{background:var(--accent-soft);color:var(--accent)}.history-toolbar>span{color:var(--muted);font-size:11px}
  .track-state-icon{display:grid;place-items:center;color:var(--muted)}.queue-body article:has(.track-state-icon) {grid-template-columns:18px 1fr auto}.queue-body article:has(.track-state-icon) .track-state-icon:has(svg){width:18px;height:18px}.spin{animation:spin .9s linear infinite}.queue-overall-progress{height:3px;background:linear-gradient(90deg,var(--accent) var(--queue-progress),var(--line) var(--queue-progress));transition:background .2s}.queue-footer>span{color:var(--muted);font-size:10px}.queue-panel{padding-bottom:0}.queue-header>svg{color:var(--muted);flex:0 0 auto}
  .source-heading{margin-top:22px;padding-top:18px;border-top:1px solid var(--line)}
  .apple-detail-card{height:min(760px,80vh)}
  .apple-detail-summary{display:flex;align-items:center;gap:14px;padding:16px 0;border-bottom:1px solid var(--line)}
  .apple-detail-summary img{width:72px;height:72px;flex:0 0 auto;border-radius:10px;object-fit:cover}
  .apple-detail-summary>div{flex:1;display:grid;gap:4px}
  .apple-detail-summary span{color:var(--muted);font-size:11px}
  .apple-track-list{min-height:0;overflow-y:auto;padding-top:8px}
  .apple-track-list article{display:grid;grid-template-columns:28px 42px minmax(0,1fr) auto;align-items:center;gap:10px;padding:7px;border-radius:9px}
  .apple-track-list article:hover{background:var(--surface-2)}
  .apple-track-list article>span,.apple-track-list article>small{color:var(--muted);font-size:10px;text-align:center}
  .apple-track-list img{width:42px;height:42px;border-radius:6px;object-fit:cover}
  .apple-track-list article>div{min-width:0;display:grid;gap:3px}
  .apple-track-list strong,.apple-track-list article>div small{overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
  .apple-track-list strong{font-size:12px}.apple-track-list article>div small{color:var(--muted);font-size:10px}
  .device-heading{display:flex;align-items:end;justify-content:space-between;margin-bottom:20px}.device-heading h2{margin:5px 0;font-size:30px}.device-heading p:last-child{margin:0;color:var(--muted)}
  .device-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:16px}
  .device-title{display:flex;align-items:center;gap:14px}.device-title h2{margin:0 0 4px}.device-title p{margin:0;color:var(--muted);font-size:12px}.ipod-glyph{width:54px;height:70px;display:grid;place-items:center;border-radius:8px;background:linear-gradient(145deg,#d8d8dc,#8e8e93);color:#fff;font-size:20px;box-shadow:inset 0 0 0 1px rgba(0,0,0,.12)}
  .device-storage{display:grid;gap:8px;margin:20px 0}.device-storage>div{display:flex;justify-content:space-between;color:var(--muted);font-size:11px}.device-storage strong{color:var(--text)}.device-storage progress{width:100%;height:7px;accent-color:var(--accent)}
  .device-grid dl{margin:0}.device-grid dl>div{display:flex;justify-content:space-between;gap:20px;padding:8px 0;border-top:1px solid var(--line);font-size:11px}.device-grid dt{color:var(--muted)}.device-grid dd{margin:0;text-align:right;overflow:hidden;text-overflow:ellipsis}.capability-row{display:flex;flex-wrap:wrap;gap:5px;margin-top:14px}.capability-row span{padding:4px 8px;border-radius:99px;background:var(--surface-2);color:var(--muted);font-size:9px;text-transform:uppercase}
  .library-label{display:flex;align-items:center;gap:7px;cursor:context-menu}.library-label .offline-icon{margin-left:auto}.library-subnav{display:grid;gap:2px;padding:0 0 8px 16px}.library-subnav button{height:36px!important}.sidebar-index{display:grid;gap:5px;padding:9px 10px;color:var(--muted);font-size:10px}.sidebar-index progress{width:100%;height:3px;accent-color:var(--accent)}
  .library-tools{position:relative}.library-tool-button{width:38px;height:38px;display:grid;place-items:center;border:1px solid var(--line);border-radius:11px;background:var(--surface);color:var(--muted)}.tool-menu{position:relative}.tool-popover{position:absolute;right:0;top:44px;background:var(--surface)!important;color:var(--text)!important}.tool-popover button{color:var(--text)!important}.compact-search{height:38px!important;margin-top:0!important}.selection-toolbar{left:auto;right:auto;max-width:620px;margin:14px auto!important;border-color:var(--line)!important}.select-release svg{display:block;margin:auto}.artist-library-list{display:grid}.artist-library-list>button{display:grid;grid-template-columns:48px 1fr auto;align-items:center;gap:13px;padding:10px;border-bottom:1px solid var(--line);background:transparent;text-align:left}.artist-library-list>button:hover{background:var(--surface-2)}.artist-avatar,.artist-avatar img{width:44px;height:44px;display:grid;place-items:center;border-radius:50%;object-fit:cover;background:var(--surface-2)}.artist-library-list>button>span:nth-child(2){display:grid;gap:3px}.artist-library-list small{color:var(--muted)}
  .download-track-list{max-height:min(42vh,360px);overflow:auto;border-top:1px solid var(--line)}.download-track-list article{display:grid;grid-template-columns:22px 1fr;align-items:center;gap:10px;padding:9px 14px;border-bottom:1px solid var(--line)}.download-track-list article>div{display:grid;gap:4px}.download-track-list span{color:var(--muted);font-size:10px}.download-track-list progress{width:100%;height:3px;accent-color:var(--accent)}.log-toggle{display:flex;align-items:center;gap:7px;margin:10px 14px;padding:7px 9px;border-radius:8px;background:var(--surface-2);color:var(--muted)}.clean-log{max-height:220px;overflow:auto;margin:0 14px 14px;padding:10px;border-radius:9px;background:var(--bg);font:10px/1.45 ui-monospace,monospace}.clean-log p{margin:3px 0;color:var(--muted)}.clean-log p.error{color:#ff453a}.clean-log p.success{color:#30b056}.job-options{position:relative}.job-options summary{width:34px;height:34px;display:grid;place-items:center;border-radius:9px;list-style:none;cursor:pointer}.job-options summary::-webkit-details-marker{display:none}.job-options>div{position:absolute;right:0;top:38px;z-index:8;width:150px;display:grid;padding:5px;border:1px solid var(--line);border-radius:9px;background:var(--surface);box-shadow:var(--shadow)}.job-options button{padding:8px;border-radius:6px;background:transparent;text-align:left;color:var(--text)}.job-options button:hover{background:var(--surface-2)}
  .compact-queue{display:grid;grid-template-columns:1fr auto;padding-bottom:3px!important}.compact-queue .queue-header{min-width:0}.queue-art{width:42px;height:42px;flex:0 0 42px;border-radius:8px;object-fit:cover}.queue-art.placeholder{display:grid;place-items:center;background:var(--surface-2)}.queue-controls{display:flex;align-items:center;padding-right:10px}.queue-controls button{width:30px;height:30px;display:grid;place-items:center;border-radius:50%;background:transparent;color:var(--muted)}.queue-controls button:hover{background:var(--surface-2);color:var(--text)}.compact-queue .queue-overall-progress{grid-column:1/-1}
  .release-header>.history-art{flex:0 0 54px}.release-header>div:not(.history-art){flex:1}
  .add-custom-button{display:inline-flex!important;align-items:center!important;justify-content:center;gap:7px;line-height:1}.add-custom-button svg{display:block;flex:0 0 auto}.add-custom-button span{display:block;line-height:1}
  .topbar>div:first-child{display:grid;grid-template-columns:auto 1fr;align-items:center;column-gap:10px}.topbar>div:first-child>.eyebrow,.topbar>div:first-child>h1{grid-column:2}.topbar-back{grid-row:1/3;grid-column:1;width:34px;height:34px;display:grid;place-items:center;border-radius:50%;background:var(--surface-2)}
  .library-detail-page{position:relative;min-height:calc(100vh - 145px);margin:-30px -34px -110px!important;padding:38px 34px 120px;overflow:hidden}.library-detail-backdrop{position:absolute;z-index:0;inset:-80px -50px auto;height:430px;background-position:center 32%;background-size:cover;filter:blur(55px) saturate(.9);opacity:.42;transform:scale(1.12);mask-image:linear-gradient(#000 20%,transparent 100%)}.favourite-backdrop{background:radial-gradient(circle at 38% 30%,var(--accent),#43383c 62%,transparent 78%)}.library-detail-page>*:not(.library-detail-backdrop){position:relative;z-index:1}.library-detail-hero{min-height:280px;display:flex;align-items:end;gap:30px;padding:18px 6px 34px}.library-detail-art{width:230px;height:230px;flex:0 0 230px;border-radius:16px;object-fit:cover;box-shadow:0 20px 55px rgba(0,0,0,.28)}.favourite-art{display:grid;place-items:center;background:linear-gradient(145deg,#f4f4f6,#d9d9de);color:var(--accent)}.library-detail-copy{display:grid;align-content:end;gap:6px;min-width:0;padding-bottom:4px}.library-detail-copy h2{margin:2px 0;font-size:42px;line-height:1.02;letter-spacing:-.045em}.library-detail-copy>strong,.library-detail-copy>span{text-transform:uppercase;color:var(--muted);font-size:11px}.detail-download{width:max-content;margin-top:14px}.detail-track-tools{display:flex;justify-content:flex-end;align-items:center;gap:9px;margin:12px 0}.detail-search{height:38px;width:min(310px,100%);margin-right:auto}.detail-track-tools select,.detail-order{height:38px}.detail-order{display:flex;align-items:center;gap:7px}.library-detail-tracks{display:grid;gap:5px}.library-detail-tracks article{min-height:58px;display:grid;grid-template-columns:28px 44px minmax(170px,1.35fr) minmax(130px,.7fr) minmax(160px,1fr) 42px 32px;align-items:center;gap:10px;padding:6px 9px;border-radius:10px;background:color-mix(in srgb,var(--surface) 77%,transparent);backdrop-filter:blur(18px)}.library-detail-tracks article:hover{background:color-mix(in srgb,var(--surface) 92%,transparent)}.library-detail-tracks img,.detail-track-placeholder{width:44px;height:44px;display:grid;place-items:center;border-radius:7px;object-fit:cover;background:var(--surface-2)}.library-detail-tracks strong,.library-detail-tracks article>span{overflow:hidden;white-space:nowrap;text-overflow:ellipsis}.library-detail-tracks article>span,.library-detail-tracks small{color:var(--muted);font-size:11px}.library-detail-tracks article>button{width:30px;height:30px;display:grid;place-items:center;border-radius:50%;background:transparent}.detail-track-number{text-align:center}.detail-index-note,.detail-inline-error{padding:18px;border-radius:11px;background:var(--surface);color:var(--muted)}.detail-inline-error{color:#ff453a}
  .settings-overlay{width:100vw;max-width:none!important;margin:0!important}
  .library-subnav{padding-left:2px}.favourite-nav-icon,.favourite-empty-icon{color:var(--accent);fill:var(--accent)}
  .detail-primary-actions{display:flex;align-items:center;gap:8px;margin-top:14px}.detail-primary-actions .detail-download{margin-top:0}.detail-primary-actions .tool-menu{position:relative}.detail-download{height:38px;display:inline-flex;align-items:center;justify-content:center;gap:7px;line-height:1}.detail-download span{display:block;line-height:1}.detail-popover{position:absolute;top:44px;left:0}.detail-track-tools select,.detail-order{font-size:12px;line-height:1}.detail-track-tools select{padding-top:0;padding-bottom:0}
  .library-detail-tracks article{position:relative;background:color-mix(in srgb,var(--surface) 86%,transparent);backdrop-filter:none;content-visibility:auto;contain:layout paint style;contain-intrinsic-size:auto 58px}.track-more{position:relative}.track-more>button{width:30px;height:30px;display:grid;place-items:center;border-radius:50%;background:transparent}.track-popover{position:absolute;right:0;top:34px}.local-placeholder-art{display:grid;place-items:center;background:var(--surface-2);color:var(--faint)}
  .downloaded-release-grid{grid-template-columns:repeat(auto-fill,minmax(155px,1fr));gap:24px 18px}.local-detail-tracks{display:grid;gap:5px}.local-detail-tracks>button{width:100%;min-height:58px;display:grid;grid-template-columns:28px 44px minmax(170px,1.35fr) minmax(130px,.7fr) minmax(160px,1fr) 52px;align-items:center;gap:10px;padding:6px 9px;border-radius:10px;background:color-mix(in srgb,var(--surface) 86%,transparent);color:var(--text);text-align:left;content-visibility:auto;contain:layout paint style;contain-intrinsic-size:auto 58px}.local-detail-tracks>button:hover,.local-detail-tracks>button.playing{background:color-mix(in srgb,var(--surface) 96%,transparent)}.local-detail-tracks>button.playing{color:var(--accent)}.local-detail-tracks img{width:44px;height:44px;border-radius:7px;object-fit:cover}.local-detail-tracks>button>span,.local-detail-tracks small{overflow:hidden;white-space:nowrap;text-overflow:ellipsis;color:var(--muted);font-size:11px}
  @media(max-width:1000px){.app-shell{grid-template-columns:205px minmax(0,1fr)}.control-grid,.downloaded-layout{grid-template-columns:1fr}.choice-grid{grid-template-columns:repeat(3,1fr)}.release-detail{position:static}.hero-art{display:none}.player-bar{left:222px}.discography-grid{grid-template-columns:repeat(3,1fr)}}
  @media(max-width:760px){.app-shell{grid-template-columns:70px minmax(0,1fr)}.sidebar{padding:18px 9px}.brand-lockup{padding:2px 4px 20px;font-size:15px;text-align:center}.sidebar nav span,.nav-label,.sidebar-footer .appearance-control,.privacy-status,.settings-nav span{display:none}.sidebar nav button,.settings-nav{justify-content:center;padding:0}.page-content{padding:22px 18px 110px}.topbar{padding:14px 18px}.hero{padding:25px}.hero h2{font-size:30px}.notice-card{grid-template-columns:auto 1fr}.notice-card .primary{grid-column:1/-1}.art-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.control-grid{grid-template-columns:1fr}.choice-grid{grid-template-columns:repeat(2,1fr)}.downloaded-layout{grid-template-columns:1fr}.release-grid{grid-template-columns:repeat(2,1fr)}.queue-panel,.queue-panel.expanded{right:10px;bottom:10px;width:calc(100vw - 90px)}.player-bar{left:80px;right:10px;grid-template-columns:1fr auto}.seek{display:none}.discography-grid{grid-template-columns:repeat(2,1fr)}}
  .library-detail-page{width:calc(100% + 68px);max-width:none!important}
  @media(max-width:760px){.library-detail-page{width:calc(100% + 36px);margin:-22px -18px -110px!important;padding:28px 18px 120px}.library-detail-hero{align-items:center}.library-detail-art{width:130px;height:130px;flex-basis:130px}.library-detail-copy h2{font-size:30px}.library-detail-tracks article{grid-template-columns:24px 40px minmax(120px,1fr) 34px}.library-detail-tracks article>span:nth-of-type(n+2),.library-detail-tracks article>small{display:none}.detail-track-tools{flex-wrap:wrap}}
</style>
