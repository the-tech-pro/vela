export type TrackStatus =
  | 'waiting'
  | 'resolving'
  | 'downloading'
  | 'processing'
  | 'retry_wait'
  | 'done'
  | 'failed'
  | 'skipped';

export interface ActiveTrackState {
  progress?: number;
  text: string;
  error?: string;
  mode: 'status' | 'determinate' | 'indeterminate';
  status: TrackStatus;
  phase?: string;
  bytesDownloaded?: number;
  bytesTotal?: number;
  speedBps?: number;
  retryAt?: number;
  retryDeadline?: number;
  attempt?: number;
}

export interface TrackActivityCounts {
  finished: number;
  resolving: number;
  transferring: number;
  processing: number;
  retryWait: number;
}

export interface TrackActivitySnapshot {
  states: Record<string, ActiveTrackState>;
  keys: string[];
  failedKeys: string[];
  counts: TrackActivityCounts;
  structureVersion: number;
  failedVersion: number;
  countsVersion: number;
}

const WAITING_STATE: ActiveTrackState = {
  mode: 'status',
  text: 'Waiting…',
  status: 'waiting',
};

const TERMINAL_STATUSES = new Set<TrackStatus>(['done', 'failed', 'skipped']);

function emptyCounts(): TrackActivityCounts {
  return {
    finished: 0,
    resolving: 0,
    transferring: 0,
    processing: 0,
    retryWait: 0,
  };
}

function adjustCount(counts: TrackActivityCounts, status: TrackStatus, amount: -1 | 1): void {
  if (TERMINAL_STATUSES.has(status)) counts.finished += amount;
  if (status === 'resolving') counts.resolving += amount;
  if (status === 'downloading') counts.transferring += amount;
  if (status === 'processing') counts.processing += amount;
  if (status === 'retry_wait') counts.retryWait += amount;
}

export class TrackActivityModel {
  private states: Record<string, ActiveTrackState> = Object.create(null);
  private keys: string[] = [];
  private keySet = new Set<string>();
  private failedKeys = new Set<string>();
  private failedKeysCache: string[] = [];
  private failedKeysCacheVersion = -1;
  private counts = emptyCounts();
  private structureVersion = 0;
  private failedVersion = 0;
  private countsVersion = 0;

  clear(): void {
    this.states = Object.create(null);
    this.keys = [];
    this.keySet.clear();
    this.failedKeys.clear();
    this.failedKeysCache = [];
    this.failedKeysCacheVersion = -1;
    this.counts = emptyCounts();
    this.structureVersion += 1;
    this.failedVersion += 1;
    this.countsVersion += 1;
  }

  reset(keys: readonly string[], states: Record<string, ActiveTrackState>): void {
    this.clear();
    keys.forEach(key => this.add(key, states[key] || WAITING_STATE));
  }

  add(key: string, initialState: ActiveTrackState = WAITING_STATE): boolean {
    if (!key || this.keySet.has(key)) return false;
    const state = this.states[key] || { ...initialState };
    this.keySet.add(key);
    this.keys.push(key);
    this.states[key] = state;
    this.structureVersion += 1;
    adjustCount(this.counts, state.status, 1);
    this.countsVersion += 1;
    if (state.status === 'failed') {
      this.failedKeys.add(key);
      this.failedVersion += 1;
    }
    return true;
  }

  get(key: string): ActiveTrackState | undefined {
    return this.states[key];
  }

  patch(
    key: string,
    patch: Partial<ActiveTrackState>,
    options: { allowTerminalReset?: boolean } = {},
  ): ActiveTrackState {
    this.add(key);
    const previous = this.states[key] || WAITING_STATE;
    const next = { ...previous, ...patch };

    if (
      TERMINAL_STATUSES.has(previous.status)
      && !TERMINAL_STATUSES.has(next.status)
      && !options.allowTerminalReset
    ) {
      return previous;
    }

    if (previous.status !== next.status) {
      adjustCount(this.counts, previous.status, -1);
      adjustCount(this.counts, next.status, 1);
      this.countsVersion += 1;
      if (previous.status === 'failed') {
        this.failedKeys.delete(key);
        this.failedVersion += 1;
      }
      if (next.status === 'failed') {
        this.failedKeys.add(key);
        this.failedVersion += 1;
      }
    }
    this.states[key] = next;
    return next;
  }

  snapshot(): TrackActivitySnapshot {
    if (this.failedKeysCacheVersion !== this.failedVersion) {
      this.failedKeysCache = [...this.failedKeys];
      this.failedKeysCacheVersion = this.failedVersion;
    }
    return {
      states: this.states,
      keys: this.keys,
      failedKeys: this.failedKeysCache,
      counts: this.counts,
      structureVersion: this.structureVersion,
      failedVersion: this.failedVersion,
      countsVersion: this.countsVersion,
    };
  }
}
