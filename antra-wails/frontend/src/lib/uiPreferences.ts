export type AppearancePreference = 'system' | 'light' | 'dark';
export type DensityPreference = 'compact' | 'comfortable' | 'spacious';
export type MotionPreference = 'system' | 'reduced' | 'full';
export type StartupDestination =
  | 'recently-added'
  | 'albums'
  | 'playlists'
  | 'favourites'
  | 'artists'
  | 'downloaded'
  | 'downloads';

export interface UIPreferences {
  scale: number;
  density: DensityPreference;
  sidebar_width: number;
  artwork_size: number;
  motion: MotionPreference;
  player_volume: number;
  startup_destination: StartupDestination;
  remember_last_page: boolean;
  open_downloads_on_add: boolean;
  completion_notifications: boolean;
  device_notifications: boolean;
  completed_history_retention: number;
}

export type ConfigWithUI<T extends object> = T & {
  config_schema_version?: number;
  ui?: Partial<UIPreferences>;
};

export const DEFAULT_UI_PREFERENCES: UIPreferences = {
  scale: 1,
  density: 'comfortable',
  sidebar_width: 240,
  artwork_size: 170,
  motion: 'system',
  player_volume: 0.8,
  startup_destination: 'recently-added',
  remember_last_page: true,
  open_downloads_on_add: true,
  completion_notifications: true,
  device_notifications: true,
  completed_history_retention: 100,
};

function clamp(value: unknown, min: number, max: number, fallback: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return allowed.includes(value as T) ? value as T : fallback;
}

function booleanValue(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback;
}

export function normalizeUIPreferences(value: unknown): UIPreferences {
  const ui = value && typeof value === 'object' ? value as Partial<UIPreferences> : {};
  return {
    scale: clamp(ui.scale, 0.85, 1.25, DEFAULT_UI_PREFERENCES.scale),
    density: oneOf(ui.density, ['compact', 'comfortable', 'spacious'] as const, DEFAULT_UI_PREFERENCES.density),
    sidebar_width: Math.round(clamp(ui.sidebar_width, 210, 300, DEFAULT_UI_PREFERENCES.sidebar_width)),
    artwork_size: Math.round(clamp(ui.artwork_size, 130, 210, DEFAULT_UI_PREFERENCES.artwork_size)),
    motion: oneOf(ui.motion, ['system', 'reduced', 'full'] as const, DEFAULT_UI_PREFERENCES.motion),
    player_volume: clamp(ui.player_volume, 0, 1, DEFAULT_UI_PREFERENCES.player_volume),
    startup_destination: oneOf(
      ui.startup_destination,
      ['recently-added', 'albums', 'playlists', 'favourites', 'artists', 'downloaded', 'downloads'] as const,
      DEFAULT_UI_PREFERENCES.startup_destination,
    ),
    remember_last_page: booleanValue(ui.remember_last_page, DEFAULT_UI_PREFERENCES.remember_last_page),
    open_downloads_on_add: booleanValue(ui.open_downloads_on_add, DEFAULT_UI_PREFERENCES.open_downloads_on_add),
    completion_notifications: booleanValue(ui.completion_notifications, DEFAULT_UI_PREFERENCES.completion_notifications),
    device_notifications: booleanValue(ui.device_notifications, DEFAULT_UI_PREFERENCES.device_notifications),
    completed_history_retention: Math.round(clamp(
      ui.completed_history_retention,
      10,
      1000,
      DEFAULT_UI_PREFERENCES.completed_history_retention,
    )),
  };
}

export function applyUIPreferences(
  appearance: AppearancePreference,
  ui: UIPreferences,
  systemDark: boolean,
  systemReducedMotion: boolean,
): boolean {
  const root = document.documentElement;
  const dark = appearance === 'dark' || (appearance === 'system' && systemDark);
  const reduced = systemReducedMotion || ui.motion === 'reduced';
  root.dataset.appearance = dark ? 'dark' : 'light';
  root.dataset.density = ui.density;
  root.dataset.motion = reduced ? 'reduced' : (ui.motion === 'full' ? 'full' : 'system');
  root.style.setProperty('--ui-scale', String(ui.scale));
  root.style.setProperty('--ui-scale-inverse', String(1 / ui.scale));
  root.style.setProperty('--sidebar-width', `${ui.sidebar_width}px`);
  root.style.setProperty('--artwork-size', `${ui.artwork_size}px`);
  root.style.setProperty('--density-space', ui.density === 'compact' ? '0.82' : ui.density === 'spacious' ? '1.18' : '1');
  return reduced;
}
