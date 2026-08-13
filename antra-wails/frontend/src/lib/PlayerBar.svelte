<script lang="ts">
  import { createEventDispatcher, tick } from 'svelte';
  import { Pause, Play, SkipBack, SkipForward, Volume1, Volume2, VolumeX } from 'lucide-svelte';
  import ArtworkImage from './ArtworkImage.svelte';
  import type { PlayerTrack } from './playerTypes';

  export let currentTrack: PlayerTrack | null = null;
  export let initialVolume = 0.8;

  const dispatch = createEventDispatcher<{
    volumechange: { volume: number };
    playbackerror: { message: string };
  }>();

  let audioEl: HTMLAudioElement;
  let queue: PlayerTrack[] = [];
  let trackIndex = -1;
  let releaseTitle = '';
  let artworkUrl = '';
  let duration = 0;
  let currentTime = 0;
  let playerIsPlaying = false;
  let playerSeeking = false;
  let playerError = '';
  let volume = 0.8;
  let muted = false;
  let previousVolume = 0.8;

  $: queuePosition = trackIndex >= 0 ? `${trackIndex + 1} of ${queue.length}` : '';
  $: canGoPrevious = trackIndex > 0 || currentTime > 3;
  $: canGoNext = trackIndex >= 0 && trackIndex < queue.length - 1;
  $: seekPercent = duration > 0 ? Math.min(100, Math.max(0, currentTime / duration * 100)) : 0;
  $: volumePercent = muted ? 0 : Math.round(volume * 100);

  function captureAudio(node: HTMLAudioElement) {
    audioEl = node;
    volume = clampVolume(initialVolume);
    previousVolume = volume || 0.8;
    audioEl.volume = volume;
  }

  function clampVolume(value: number): number {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.min(1, Math.max(0, parsed)) : 0.8;
  }

  function readableError(error: unknown): string {
    if (error instanceof DOMException && error.name === 'NotAllowedError') return 'Playback was blocked. Press Play to try again.';
    if (error instanceof Error && error.message) return error.message;
    return 'This local audio file could not be played.';
  }

  function formatTime(seconds: number): string {
    if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
    const minutes = Math.floor(seconds / 60);
    return `${minutes}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`;
  }

  function setError(error: unknown) {
    playerError = readableError(error);
    dispatch('playbackerror', { message: playerError });
  }

  async function loadTrack(index: number) {
    if (!audioEl || index < 0 || index >= queue.length) return;
    trackIndex = index;
    currentTrack = queue[index];
    currentTime = 0;
    duration = currentTrack.duration_seconds || 0;
    playerError = '';
    audioEl.src = currentTrack.audio_url;
    audioEl.load();
    try {
      await audioEl.play();
    } catch (error) {
      setError(error);
    }
  }

  export async function playQueue(
    tracks: PlayerTrack[],
    index: number,
    nextReleaseTitle: string,
    nextArtworkUrl = '',
  ) {
    queue = [...tracks];
    releaseTitle = nextReleaseTitle;
    artworkUrl = nextArtworkUrl;
    await tick();
    await loadTrack(index);
  }

  async function togglePlayback() {
    if (!audioEl || !currentTrack) return;
    if (playerIsPlaying) {
      audioEl.pause();
      return;
    }
    try {
      playerError = '';
      await audioEl.play();
    } catch (error) {
      setError(error);
    }
  }

  async function previous() {
    if (!audioEl || !currentTrack) return;
    if (currentTime > 3 || trackIndex <= 0) {
      audioEl.currentTime = 0;
      currentTime = 0;
      return;
    }
    await loadTrack(trackIndex - 1);
  }

  async function next() {
    if (canGoNext) await loadTrack(trackIndex + 1);
  }

  function handleTimeUpdate() {
    if (!audioEl || playerSeeking) return;
    currentTime = audioEl.currentTime || 0;
  }

  function handleLoadedMetadata() {
    if (!audioEl) return;
    duration = Number.isFinite(audioEl.duration) ? audioEl.duration : duration;
  }

  function beginSeek() {
    playerSeeking = true;
  }

  function updateSeek(event: Event) {
    playerSeeking = true;
    currentTime = Number((event.currentTarget as HTMLInputElement).value);
  }

  function commitSeek(event: Event) {
    const nextTime = Number((event.currentTarget as HTMLInputElement).value);
    currentTime = nextTime;
    if (audioEl) audioEl.currentTime = nextTime;
    playerSeeking = false;
  }

  function handleAudioError() {
    const mediaError = audioEl?.error;
    const messages: Record<number, string> = {
      1: 'Playback was stopped before the file finished loading.',
      2: 'The local audio file could not be read.',
      3: 'The audio format could not be decoded.',
      4: 'This audio format is not supported.',
    };
    setError(mediaError ? messages[mediaError.code] : undefined);
  }

  async function handleEnded() {
    if (canGoNext) await loadTrack(trackIndex + 1);
  }

  function setVolume(event: Event) {
    const nextVolume = clampVolume(Number((event.currentTarget as HTMLInputElement).value));
    volume = nextVolume;
    muted = nextVolume === 0;
    if (nextVolume > 0) previousVolume = nextVolume;
    if (audioEl) {
      audioEl.muted = muted;
      audioEl.volume = nextVolume;
    }
  }

  function toggleMute() {
    if (!audioEl) return;
    if (muted || volume === 0) {
      volume = previousVolume || 0.8;
      muted = false;
    } else {
      previousVolume = volume;
      muted = true;
    }
    audioEl.muted = muted;
    audioEl.volume = volume;
    dispatch('volumechange', { volume: muted ? 0 : volume });
  }

  function handleVolumeChange() {
    if (!audioEl) return;
    muted = audioEl.muted;
    volume = audioEl.volume;
    if (!muted && volume > 0) previousVolume = volume;
    dispatch('volumechange', { volume: muted ? 0 : volume });
  }
</script>

<audio
  use:captureAudio
  on:play={() => playerIsPlaying = true}
  on:pause={() => playerIsPlaying = false}
  on:ended={handleEnded}
  on:error={handleAudioError}
  on:timeupdate={handleTimeUpdate}
  on:loadedmetadata={handleLoadedMetadata}
  on:volumechange={handleVolumeChange}
></audio>

{#if currentTrack}
  <section class="player-bar" aria-label="Now playing">
    <div class="identity">
      <span class="player-art"><ArtworkImage src={artworkUrl} displaySize={96} loading="eager" fetchPriority="high"><Play size={20}/></ArtworkImage></span>
      <span class="identity-copy">
        <strong title={currentTrack.title}>{currentTrack.title}</strong>
        <small title={currentTrack.artist || releaseTitle}>{currentTrack.artist || releaseTitle}</small>
      </span>
    </div>

    <div class="transport">
      <div class="transport-controls">
        <button aria-label="Previous track" title="Previous" disabled={!canGoPrevious} on:click={previous}><SkipBack size={19}/></button>
        <button class="play-button" aria-label={playerIsPlaying ? 'Pause' : 'Play'} title={playerIsPlaying ? 'Pause' : 'Play'} on:click={togglePlayback}>
          {#if playerIsPlaying}<Pause size={20}/>{:else}<Play size={20}/>{/if}
        </button>
        <button aria-label="Next track" title="Next" disabled={!canGoNext} on:click={next}><SkipForward size={19}/></button>
      </div>
      <div class="timeline">
        <span>{formatTime(currentTime)}</span>
        <input
          aria-label="Playback position"
          type="range"
          min="0"
          max={duration || 0}
          step="0.1"
          value={currentTime}
          style:--range-progress={`${seekPercent}%`}
          on:pointerdown={beginSeek}
          on:input={updateSeek}
          on:change={commitSeek}
        />
        <span>{formatTime(duration)}</span>
      </div>
      {#if playerError}<p class="player-error" role="status">{playerError}</p>{/if}
    </div>

    <div class="volume">
      <span class="queue-position" aria-label={`Queue position ${queuePosition}`}>{queuePosition}</span>
      <button aria-label={muted || volume === 0 ? 'Unmute' : 'Mute'} title={muted || volume === 0 ? 'Unmute' : 'Mute'} on:click={toggleMute}>
        {#if muted || volume === 0}<VolumeX size={19}/>{:else if volume < 0.5}<Volume1 size={19}/>{:else}<Volume2 size={19}/>{/if}
      </button>
      <input
        aria-label="Volume"
        type="range"
        min="0"
        max="1"
        step="0.01"
        value={muted ? 0 : volume}
        style:--range-progress={`${volumePercent}%`}
        on:input={setVolume}
        on:change={setVolume}
      />
    </div>
  </section>
{/if}

<style>
  audio { display: none; }
  .player-bar { position:fixed;left:calc(var(--sidebar-width,240px) + 22px);right:22px;bottom:18px;min-height:76px;z-index:15;display:grid;grid-template-columns:minmax(180px,.8fr) minmax(300px,1.35fr) minmax(190px,.8fr);align-items:center;gap:18px;padding:9px 14px;border:1px solid var(--line);border-radius:16px;background:var(--overlay-surface);box-shadow:var(--shadow);backdrop-filter:blur(30px)}
  .identity { min-width:0;display:flex;align-items:center;gap:10px; }
  .player-art { width:48px;height:48px;flex:0 0 48px;display:grid;place-items:center;overflow:hidden;border-radius:9px;background:var(--surface-2);color:var(--faint); }
  .identity-copy { min-width:0;display:grid;gap:2px; }
  .identity-copy strong,.identity-copy small { overflow:hidden;white-space:nowrap;text-overflow:ellipsis; }
  .identity-copy strong { font-size:12px; }
  .identity-copy small,.queue-position { color:var(--muted);font-size:10px; }
  .transport { min-width:0;display:grid;justify-items:center;gap:4px; }
  .transport-controls { display:flex;align-items:center;gap:7px; }
  button { width:36px;height:36px;display:grid;place-items:center;padding:0;border:0;border-radius:50%;background:transparent;color:var(--text);cursor:pointer; }
  button:hover:not(:disabled) { background:var(--surface-2); }
  button:focus-visible,input:focus-visible { outline:2px solid var(--accent);outline-offset:2px; }
  button:disabled { opacity:.4;cursor:default; }
  .play-button { width:40px;height:40px;background:var(--text);color:var(--surface); }
  .play-button:hover:not(:disabled) { background:var(--text);filter:brightness(1.12); }
  .timeline { width:100%;display:grid;grid-template-columns:36px minmax(90px,1fr) 36px;align-items:center;gap:8px;color:var(--muted);font:9px/1 ui-monospace,SFMono-Regular,Consolas,monospace; }
  input[type='range'] { width:100%;height:18px;padding:0;border:0;background:transparent;box-shadow:none;accent-color:var(--accent);cursor:pointer; }
  input[type='range']::-webkit-slider-runnable-track { height:4px;border-radius:99px;background:linear-gradient(90deg,var(--accent) var(--range-progress),var(--surface-2) var(--range-progress)); }
  input[type='range']::-webkit-slider-thumb { width:12px;height:12px;margin-top:-4px;border:0;border-radius:50%;background:var(--accent);appearance:none;-webkit-appearance:none; }
  .player-error { width:100%;margin:0;color:var(--error-color,#ff453a);font-size:10px;text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis; }
  .volume { min-width:0;display:grid;grid-template-columns:auto 36px minmax(70px,120px);align-items:center;justify-content:end;gap:6px; }
  .volume input { min-width:70px; }
  @media(max-width:1000px) {
    .player-bar { left:calc(var(--sidebar-width,240px) + 16px);grid-template-columns:minmax(150px,.7fr) minmax(280px,1.3fr) minmax(150px,.65fr);gap:10px; }
    .queue-position { display:none; }
    .volume { grid-template-columns:36px minmax(60px,90px); }
  }
  @media(max-width:760px) {
    .player-bar { left:80px;right:10px;bottom:10px;grid-template-columns:minmax(0,1fr) auto;grid-template-areas:'identity volume' 'transport transport';gap:5px 10px;padding:8px 10px; }
    .identity { grid-area:identity; }
    .player-art { width:40px;height:40px;flex-basis:40px; }
    .transport { grid-area:transport;width:100%;grid-template-columns:auto minmax(0,1fr);align-items:center;gap:8px; }
    .transport-controls { grid-column:1;grid-row:1; }
    .timeline { grid-column:2;grid-row:1; }
    .player-error { grid-column:1/-1; }
    .volume { grid-area:volume;grid-template-columns:36px 72px; }
  }
  @media(max-width:520px) {
    .identity-copy small { display:none; }
    .volume { grid-template-columns:36px 56px; }
    .timeline { grid-template-columns:30px minmax(55px,1fr) 30px;gap:4px; }
  }
</style>
