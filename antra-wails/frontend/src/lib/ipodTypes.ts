export interface IPodDevice {
  device_id: string;
  path: string;
  name: string;
  model_family: string;
  generation: string;
  model_number: string;
  capacity: string;
  serial: string;
  firewire_guid: string;
  firmware: string;
  filesystem_type: string;
  filesystem_accessible: boolean;
  raw_read_only: boolean;
  access_state: string;
  access_message: string;
  raw_device_path: string;
  volume_identity_key: string;
  disk_size_gb: number;
  free_space_gb: number;
  uses_sqlite_db: boolean;
  checksum_type: number | string;
  audio_codecs: string[] | Record<string, unknown>;
  podcasts_supported: boolean;
  voice_memos_supported: boolean;
  supports_sparse_artwork: boolean;
  browse_only: boolean;
  needs_preparation: boolean;
  write_ready: boolean;
  filesystem_read_only: boolean;
  write_block_code: string;
  write_block_reason: string;
}

export interface LocalReleaseSummary {
  kind: string;
  relative_path: string;
  title: string;
  artist?: string;
  track_count: number;
  artwork_url?: string;
}

export interface IPodPlan {
  protocol_version: number;
  plan_id: string;
  additions: number;
  removals: number;
  updates: number;
  conversions: number;
  playlist_changes: number;
  warnings: number;
  unsupported: number;
  required_bytes: number;
  source_count: number;
  storage: IPodPlanStorage;
  groups: IPodPlanGroupDescriptor[];
  group_previews: Partial<Record<IPodPlanGroup, IPodPlanDetailItem[]>>;
  review_fingerprint: string;
  expires_at: number;
}

export type IPodPlanGroup =
  | 'additions'
  | 'removals'
  | 'metadata_updates'
  | 'artwork_updates'
  | 'conversions'
  | 'playlist_effects'
  | 'warnings'
  | 'unsupported';

export interface IPodPlanGroupDescriptor {
  group: IPodPlanGroup;
  total: number;
  page_size_max: number;
}

export interface IPodPlanStorage {
  bytes_to_add: number;
  bytes_to_remove: number;
  bytes_to_update: number;
  net_change_bytes: number;
  required_free_bytes: number;
  free_before_bytes: number;
  free_after_bytes: number;
}

export interface IPodPlanDetailItem {
  item_id: string;
  group: IPodPlanGroup;
  action: string;
  title?: string;
  artist?: string;
  album?: string;
  description?: string;
  source_path?: string;
  ipod_location?: string;
  db_track_id?: number | string;
  estimated_bytes?: number;
  removed_bytes?: number;
  metadata_fields?: string[] | Record<string, unknown>;
  conversion?: string | Record<string, unknown>;
  track_count?: number;
  skipped_count?: number;
  playlist_id?: number | string;
  code?: string;
  message?: string;
}

export interface IPodPlanDetailPage {
  protocol_version: number;
  plan_id: string;
  group: IPodPlanGroup;
  page: number;
  page_size: number;
  total: number;
  items: IPodPlanDetailItem[];
  expires_at: number;
}

export interface IPodProgress {
  stage: string;
  current: number;
  total: number;
  message: string;
}

export type UnknownRecord = Record<string, unknown>;

export interface IPodBrowseItem {
  persistent_id?: string;
  track_id?: string | number;
  album_id?: string | number;
  artist_id?: string | number;
  playlist_id?: string | number;
  title?: string;
  name?: string;
  album?: string;
  artist?: string;
  playlist_name?: string;
  genre?: string;
  track_count?: number;
  year?: string | number;
  [key: string]: unknown;
}

export interface IPodDownloadedTrack {
  file_path: string;
  title?: string;
  file_name?: string;
}

export interface IPodBackupDeviceMeta {
  stable_device_id?: string;
  model_family?: string;
  generation?: string;
  model_number?: string;
  firmware?: string;
  uses_sqlite_db?: boolean;
  [key: string]: unknown;
}

export interface IPodBackupDeviceArchive {
  archive_id: string;
  device_name: string;
  snapshot_count: number;
  identity_is_stable: boolean;
  repository_size_bytes: number;
  device_meta: IPodBackupDeviceMeta;
}

export interface IPodBackupDevicesResponse {
  protocol_version: number;
  total: number;
  truncated: boolean;
  repository_size_bytes: number;
  devices: IPodBackupDeviceArchive[];
}

export interface IPodBackupSnapshot {
  snapshot_id: string;
  timestamp: string;
  archive_id: string;
  device_name: string;
  file_count: number;
  total_size_bytes: number;
  reason: string;
  note: string;
  files_added?: number;
  files_removed?: number;
  files_changed?: number;
  device_meta: IPodBackupDeviceMeta;
  is_valid?: boolean;
  validation_error?: string;
  identity_is_stable?: boolean;
  source_verification?: string;
  manifest_version?: number;
  snapshot_fingerprint?: string;
}

export interface IPodBackupSnapshotsResponse {
  protocol_version: number;
  archive_id: string;
  page: number;
  page_size: number;
  total: number;
  repository_size_bytes: number;
  items: IPodBackupSnapshot[];
}

export interface IPodBackupScope {
  kind: 'full_regular_file_tree' | string;
  functional_backup: boolean;
  raw_disk_image: boolean;
  included_file_count: number;
  included_bytes: number;
  content_verification: string;
  restores_included_tree_exactly: boolean;
}

export interface IPodBackupExclusion {
  category: string;
  description: string;
}

export interface IPodBackupSnapshotDetails {
  protocol_version: number;
  archive_id: string;
  snapshot: IPodBackupSnapshot;
  scope: IPodBackupScope;
  exclusions: IPodBackupExclusion[];
  repository_size_bytes: number;
}

export interface IPodBackupVerification {
  protocol_version: number;
  operation_id: string;
  archive_id: string;
  snapshot_id: string;
  file_count: number;
  unique_blobs_verified: number;
  verified_bytes: number;
  verification: string;
  ok: boolean;
}

export interface IPodDatabaseGeneration {
  exists?: boolean;
  size?: number;
  digest?: string;
  modified_ns?: number;
  [key: string]: unknown;
}

export interface IPodRestoreTarget {
  device_id: string;
  archive_id: string;
  name: string;
  model_family: string;
  database_generation: IPodDatabaseGeneration;
}

export interface IPodRestorePreflightVerification {
  ok: boolean;
  method: string;
  file_count: number;
  unique_blobs_verified: number;
  verified_bytes: number;
  filesystem_names_valid: boolean;
}

export interface IPodRestorePreflightStorage {
  final_allocated_bytes: number;
  volume_total_bytes: number;
  volume_free_bytes: number;
  final_state_fits: boolean;
  atomic_temp_capacity_rechecked_on_execute: boolean;
}

export interface IPodRestorePreflight {
  protocol_version: number;
  restore_plan_id: string;
  source_archive_id: string;
  source_snapshot_id: string;
  snapshot: IPodBackupSnapshot;
  scope: IPodBackupScope;
  exclusions: IPodBackupExclusion[];
  target: IPodRestoreTarget;
  verification: IPodRestorePreflightVerification;
  storage: IPodRestorePreflightStorage;
  expires_at: number;
  confirmation_required: boolean;
  raw_replacement_restore_allowed: boolean;
}

export interface IPodMigrationEndpoint {
  archive_id: string;
  snapshot_id?: string;
  device_id: string;
  snapshot_fingerprint?: string;
  model_family?: string;
  generation?: string;
  uses_sqlite_db?: boolean;
  database_generation?: IPodDatabaseGeneration;
}

export interface IPodMigrationIssue {
  field: string;
  message?: string;
  source?: unknown;
  target?: unknown;
  count?: number;
  required_bytes?: number;
  available_bytes?: number;
}

export interface IPodMigrationMetadataLimitations {
  preserved: string[];
  not_preserved: string[];
  unresolved_source_tracks: number;
  skipped_playlists: number;
  unresolved_playlist_items: number;
}

export interface IPodMigrationBundle {
  schema_version: number;
  path: string;
  fingerprint: string;
  media_file_count: number;
  playlist_count: number;
  total_media_bytes: number;
}

export interface IPodMigrationPreflight {
  protocol_version: number;
  blocked: boolean;
  compatible: boolean;
  code: string;
  message: string;
  raw_restore_allowed: boolean;
  safe_migration_available: boolean;
  same_device: boolean;
  issues: IPodMigrationIssue[];
  requirements: string[];
  source: IPodMigrationEndpoint;
  target: IPodMigrationEndpoint;
  migration_plan_id?: string;
  confirmation_required?: boolean;
  target_safety_backup_required?: boolean;
  staging_bundle?: IPodMigrationBundle;
  metadata?: IPodMigrationMetadataLimitations;
  additions?: number;
  removals?: number;
  updates?: number;
  conversions?: number;
  playlist_changes?: number;
  warnings?: number;
  unsupported?: number;
  required_bytes?: number;
  source_count?: number;
  storage?: IPodPlanStorage;
  groups?: IPodPlanGroupDescriptor[];
  group_previews?: Partial<Record<IPodPlanGroup, IPodPlanDetailItem[]>>;
  review_fingerprint?: string;
  expires_at?: number;
}

export interface IPodRecoveryReconnect {
  required_device_id?: string;
  mount_path?: string;
  [key: string]: unknown;
}

export interface IPodRecoveryDetails {
  required?: boolean;
  code?: string;
  message?: string;
  device_dirty?: boolean;
  content_verified?: boolean;
  requires_safe_eject?: boolean;
  source_archive_id?: string;
  source_snapshot_id?: string;
  safety_snapshot_id?: string;
  next_action?: string;
  [key: string]: unknown;
}

export interface IPodRecoveryOperation {
  schema_version: number;
  revision: number;
  operation_id: string;
  kind: string;
  phase: string;
  can_cancel: boolean;
  target_id: string;
  source_id: string;
  target_archive_id: string;
  source_archive_id: string;
  snapshot_id: string;
  safety_snapshot_id: string;
  reconnect: IPodRecoveryReconnect;
  recovery: IPodRecoveryDetails;
  status: string;
  error: { code?: string; message?: string };
  metadata: UnknownRecord;
  started_at: number;
  updated_at: number;
  completed_at: number | null;
}

export interface IPodRecoveryState {
  protocol_version: number;
  journal_version: number;
  operation: IPodRecoveryOperation | null;
  incomplete: boolean;
  requires_recovery: boolean;
  reconnect: IPodRecoveryReconnect;
  recovery: IPodRecoveryDetails;
}

export type IPodCapacityUnlockState =
  | 'eligibility_checked'
  | 'environment_ready'
  | 'filesystem_backup_verified'
  | 'artifacts_verified'
  | 'awaiting_bootloader_install'
  | 'awaiting_syscfg_dump'
  | 'original_syscfg_verified'
  | 'candidate_syscfg_verified'
  | 'candidate_staged'
  | 'awaiting_manual_nor_flash'
  | 'nor_flash_attested'
  | 'awaiting_dfu'
  | 'itunes_handoff'
  | 'awaiting_restore'
  | 'postflight_verification'
  | 'complete'
  | 'recovery_required'
  | 'cancelled';

export type IPodCapacityUnlockAcknowledgement =
  | 'destructive_restore_erases_device'
  | 'nor_flash_can_make_device_unbootable'
  | 'manual_rockbox_nor_dfu_steps_required'
  | 'hardware_recovery_may_be_required'
  | 'itunes_restore_is_user_controlled'
  | 'cancellation_ends_after_nor_commit';

export interface IPodCapacityUnlockIssue {
  code: string;
  message: string;
}

export interface IPodCapacityUnlockProfile {
  model_number: string;
  generation: string;
  nominal_capacity_gb: number;
  color: string;
  expected_firmware: string;
}

export interface IPodCapacityUnlockEvidence {
  platform: string;
  model_family: string;
  generation: string;
  model_number: string;
  firmware_version: string;
  filesystem: string;
  serial_number: string;
  firewire_guid: string;
  serial_is_stable: boolean;
  firewire_is_stable: boolean;
  identity_conflict_count: number;
  writable: boolean;
  has_writable_evidence: boolean;
  storage_healthy: boolean;
  has_health_evidence: boolean;
  usb_vendor_id: number;
  usb_product_id: number;
  is_virtual: boolean;
  active_device_mutation: boolean;
}

export interface IPodCapacityUnlockArtifact {
  artifact_id: string;
  filename: string;
  url: string;
  expected_size: number;
  sha1?: string;
  sha256: string;
  kind: string;
  license_expression: string;
  source_revision: string;
  redistributable: boolean;
  executable: boolean;
  metadata_sha256: string;
}

export interface IPodCapacityUnlockHistoryItem {
  from_state: string | null;
  to_state: string;
  at: string;
  reason: string;
}

export interface IPodCapacityUnlockSession {
  session_id: string;
  revision: number;
  state: IPodCapacityUnlockState;
  created_at: string;
  updated_at: string;
  identity_fingerprint: string;
  firewire_fingerprint: string;
  source_profile_id: string;
  source_model_number: string;
  source_generation: string;
  source_firmware_version: string;
  target_firmware_version: string;
  nor_committed: boolean;
  can_cancel: boolean;
  terminal: boolean;
  details: UnknownRecord;
  history: IPodCapacityUnlockHistoryItem[];
  recovery_resume_state: IPodCapacityUnlockState | null;
}

export interface IPodCapacityUnlockCandidate {
  preset_id: string;
  preset_sha256: string;
  source_model_number: string;
  original_sha256: string;
  candidate_sha256: string;
  changed_tags: string[];
  candidate_size: number;
  path: string;
  preset?: UnknownRecord;
}

export interface IPodCapacityUnlockEligibilityResponse {
  protocol_version: number;
  experimental: boolean;
  eligibility: {
    eligible: boolean;
    issues: IPodCapacityUnlockIssue[];
    profile: IPodCapacityUnlockProfile | null;
    identity_fingerprint?: string;
    firewire_fingerprint?: string;
  };
  evidence: IPodCapacityUnlockEvidence;
  artifacts: IPodCapacityUnlockArtifact[];
  acknowledgement_fields: string[];
  actions: string[];
  current_session?: IPodCapacityUnlockSession | null;
}

export interface IPodCapacityUnlockResult {
  protocol_version: number;
  experimental: boolean;
  session?: IPodCapacityUnlockSession;
  sessions?: IPodCapacityUnlockSession[];
  candidate?: IPodCapacityUnlockCandidate;
}

export interface IPodRecoveryUSBDevice {
  vendor_id: string;
  product_id: string;
  mode: string;
  model_hint?: string;
  name?: string;
  instance_id?: string;
}

export interface IPodRecoveryUSBInspection {
  supported: boolean;
  available: boolean;
  read_only: boolean;
  platform: string;
  devices: IPodRecoveryUSBDevice[];
  message?: string;
  error?: string;
}

export interface IPodEventPayload {
  type: string;
  protocol_version: number;
  data?: unknown;
  details?: unknown;
  operation?: string;
  operation_id?: string;
  bridge_operation_id?: string;
  backend_operation_id?: string;
  backend_operation_kind?: string;
  operation_kind?: string;
  kind?: string;
  phase?: string;
  stage?: string;
  can_cancel?: boolean;
  current?: number;
  total?: number;
  message?: string;
  status?: string;
  code?: string;
  recovery?: IPodRecoveryDetails;
}

export interface IPodOperationEnvelope {
  type: string;
  operation: string;
  operation_id: string;
  kind: string;
  phase: string;
  can_cancel: boolean;
  current: number;
  total: number;
  message: string;
  status: string;
  recovery?: IPodRecoveryDetails;
}

export function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function recordString(record: UnknownRecord, key: string, fallback = ''): string {
  const value = record[key];
  return typeof value === 'string' ? value : fallback;
}

export function recordNumber(record: UnknownRecord, key: string, fallback = 0): number {
  const value = record[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function recordNonNegativeSafeInteger(
  record: UnknownRecord,
  key: string,
  fallback = 0,
): number {
  const value = record[key];
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
    ? value
    : fallback;
}

export function recordBoolean(record: UnknownRecord, key: string, fallback = false): boolean {
  const value = record[key];
  return typeof value === 'boolean' ? value : fallback;
}

export function recordObject(record: UnknownRecord, key: string): UnknownRecord {
  const value = record[key];
  return isRecord(value) ? value : {};
}

export function recordStringArray(record: UnknownRecord, key: string): string[] {
  const value = record[key];
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : [];
}

export class IPodBackendError extends Error {
  code: string;

  constructor(message: string, code = '') {
    super(message);
    this.name = 'IPodBackendError';
    this.code = code;
  }
}

export function parseIPodResponse(raw: string): UnknownRecord {
  let value: unknown;
  try {
    value = JSON.parse(raw || '{}');
  } catch {
    throw new Error('The iPod backend returned invalid data.');
  }
  if (!isRecord(value)) throw new Error('The iPod backend returned an unexpected response.');
  const error = recordString(value, 'error');
  if (error) {
    throw new IPodBackendError(
      recordString(value, 'message', error),
      recordString(value, 'code'),
    );
  }
  return value;
}

function optionalString(record: UnknownRecord, key: string): string | undefined {
  const value = recordString(record, key);
  return value || undefined;
}

function optionalNumber(record: UnknownRecord, key: string): number | undefined {
  const value = record[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function deviceMeta(value: unknown): IPodBackupDeviceMeta {
  if (!isRecord(value)) return {};
  return {
    ...value,
    stable_device_id: optionalString(value, 'stable_device_id'),
    model_family: optionalString(value, 'model_family'),
    generation: optionalString(value, 'generation'),
    model_number: optionalString(value, 'model_number'),
    firmware: optionalString(value, 'firmware'),
    uses_sqlite_db: typeof value.uses_sqlite_db === 'boolean' ? value.uses_sqlite_db : undefined,
  };
}

const ipodDeviceStringFields = [
  'device_id', 'path', 'name', 'model_family', 'generation', 'model_number',
  'capacity', 'serial', 'firewire_guid', 'firmware', 'filesystem_type',
  'access_state', 'access_message', 'raw_device_path', 'volume_identity_key',
  'write_block_code', 'write_block_reason',
] as const;

const ipodDeviceBooleanFields = [
  'filesystem_accessible', 'raw_read_only', 'uses_sqlite_db',
  'podcasts_supported', 'voice_memos_supported', 'supports_sparse_artwork',
  'browse_only', 'needs_preparation', 'write_ready', 'filesystem_read_only',
] as const;

export function isIPodDevice(value: unknown): value is IPodDevice {
  if (!isRecord(value)) return false;
  const checksumType = value.checksum_type;
  const audioCodecs = value.audio_codecs;
  return ipodDeviceStringFields.every(field => typeof value[field] === 'string')
    && ipodDeviceBooleanFields.every(field => typeof value[field] === 'boolean')
    && typeof value.disk_size_gb === 'number'
    && Number.isFinite(value.disk_size_gb)
    && typeof value.free_space_gb === 'number'
    && Number.isFinite(value.free_space_gb)
    && (
      typeof checksumType === 'string'
      || (typeof checksumType === 'number' && Number.isFinite(checksumType))
    )
    && (
      (Array.isArray(audioCodecs) && audioCodecs.every(codec => typeof codec === 'string'))
      || isRecord(audioCodecs)
    );
}

function normalizeIPodDevice(value: unknown): IPodDevice | null {
  if (!isRecord(value)) return null;
  const deviceId = recordString(value, 'device_id').trim();
  const path = recordString(value, 'path').trim();
  if (!deviceId || !path) return null;

  const hasFilesystemAccessible = Object.prototype.hasOwnProperty.call(
    value,
    'filesystem_accessible',
  );
  const filesystemAccessible = typeof value.filesystem_accessible === 'boolean'
    ? value.filesystem_accessible
    : !hasFilesystemAccessible;
  const accessMessage = recordString(
    value,
    'access_message',
    filesystemAccessible ? '' : 'The attached iPod filesystem is not mounted.',
  );
  const checksumType = value.checksum_type;
  const audioCodecs = value.audio_codecs;
  const rawReadOnly = recordBoolean(value, 'raw_read_only', !filesystemAccessible);
  const filesystemReadOnly = recordBoolean(value, 'filesystem_read_only', rawReadOnly);
  const browseOnly = recordBoolean(value, 'browse_only', !filesystemAccessible);
  const writeBlockReason = recordString(value, 'write_block_reason', accessMessage);
  const writeReady = recordBoolean(
    value,
    'write_ready',
    filesystemAccessible && !filesystemReadOnly && !browseOnly && !writeBlockReason,
  );

  return {
    device_id: deviceId,
    path,
    name: recordString(value, 'name', 'iPod'),
    model_family: recordString(value, 'model_family', 'iPod'),
    generation: recordString(value, 'generation'),
    model_number: recordString(value, 'model_number'),
    capacity: recordString(value, 'capacity'),
    serial: recordString(value, 'serial'),
    firewire_guid: recordString(value, 'firewire_guid'),
    firmware: recordString(value, 'firmware'),
    filesystem_type: recordString(value, 'filesystem_type'),
    filesystem_accessible: filesystemAccessible,
    raw_read_only: rawReadOnly,
    access_state: recordString(
      value,
      'access_state',
      filesystemAccessible ? 'mounted' : 'filesystem_unavailable',
    ),
    access_message: accessMessage,
    raw_device_path: recordString(value, 'raw_device_path'),
    volume_identity_key: recordString(value, 'volume_identity_key'),
    disk_size_gb: Math.max(0, recordNumber(value, 'disk_size_gb')),
    free_space_gb: Math.max(0, recordNumber(value, 'free_space_gb')),
    uses_sqlite_db: recordBoolean(value, 'uses_sqlite_db'),
    checksum_type: typeof checksumType === 'string'
      || (typeof checksumType === 'number' && Number.isFinite(checksumType))
      ? checksumType
      : '',
    audio_codecs: Array.isArray(audioCodecs)
      ? audioCodecs.filter((codec): codec is string => typeof codec === 'string')
      : isRecord(audioCodecs) ? { ...audioCodecs } : [],
    podcasts_supported: recordBoolean(value, 'podcasts_supported'),
    voice_memos_supported: recordBoolean(value, 'voice_memos_supported'),
    supports_sparse_artwork: recordBoolean(value, 'supports_sparse_artwork'),
    browse_only: browseOnly,
    needs_preparation: recordBoolean(value, 'needs_preparation', !filesystemAccessible),
    write_ready: writeReady,
    filesystem_read_only: filesystemReadOnly,
    write_block_code: recordString(
      value,
      'write_block_code',
      writeReady ? '' : filesystemAccessible ? 'write_not_ready' : 'filesystem_unavailable',
    ),
    write_block_reason: writeBlockReason,
  };
}

export function parseIPodDevicesResponse(raw: string): IPodDevice[] {
  const response = parseIPodResponse(raw);
  const devices = response.devices;
  return Array.isArray(devices)
    ? devices
        .map(normalizeIPodDevice)
        .filter((device): device is IPodDevice => device !== null)
    : [];
}

const capacityUnlockStates: IPodCapacityUnlockState[] = [
  'eligibility_checked',
  'environment_ready',
  'filesystem_backup_verified',
  'artifacts_verified',
  'awaiting_bootloader_install',
  'awaiting_syscfg_dump',
  'original_syscfg_verified',
  'candidate_syscfg_verified',
  'candidate_staged',
  'awaiting_manual_nor_flash',
  'nor_flash_attested',
  'awaiting_dfu',
  'itunes_handoff',
  'awaiting_restore',
  'postflight_verification',
  'complete',
  'recovery_required',
  'cancelled',
];

function isCapacityUnlockState(value: unknown): value is IPodCapacityUnlockState {
  return typeof value === 'string'
    && capacityUnlockStates.includes(value as IPodCapacityUnlockState);
}

function capacityUnlockSession(value: unknown): IPodCapacityUnlockSession | null {
  if (!isRecord(value)) return null;
  const sessionId = recordString(value, 'session_id');
  const revision = recordNumber(value, 'revision');
  const identityFingerprint = recordString(value, 'identity_fingerprint');
  const firewireFingerprint = recordString(value, 'firewire_fingerprint');
  if (
    !sessionId
    || !Number.isInteger(revision)
    || revision < 1
    || !identityFingerprint
    || !firewireFingerprint
    || typeof value.nor_committed !== 'boolean'
    || typeof value.can_cancel !== 'boolean'
    || typeof value.terminal !== 'boolean'
    || !isCapacityUnlockState(value.state)
  ) return null;
  const recoveryResumeState = isCapacityUnlockState(value.recovery_resume_state)
    ? value.recovery_resume_state
    : null;
  const history = Array.isArray(value.history)
    ? value.history.reduce<IPodCapacityUnlockHistoryItem[]>((items, entry) => {
        if (!isRecord(entry)) return items;
        items.push({
          from_state: typeof entry.from_state === 'string' ? entry.from_state : null,
          to_state: recordString(entry, 'to_state'),
          at: recordString(entry, 'at'),
          reason: recordString(entry, 'reason'),
        });
        return items;
      }, [])
    : [];
  return {
    session_id: sessionId,
    revision,
    state: value.state,
    created_at: recordString(value, 'created_at'),
    updated_at: recordString(value, 'updated_at'),
    identity_fingerprint: identityFingerprint,
    firewire_fingerprint: firewireFingerprint,
    source_profile_id: recordString(value, 'source_profile_id'),
    source_model_number: recordString(value, 'source_model_number'),
    source_generation: recordString(value, 'source_generation'),
    source_firmware_version: recordString(value, 'source_firmware_version'),
    target_firmware_version: recordString(value, 'target_firmware_version'),
    nor_committed: recordBoolean(value, 'nor_committed'),
    can_cancel: recordBoolean(value, 'can_cancel'),
    terminal: recordBoolean(value, 'terminal'),
    details: isRecord(value.details) ? { ...value.details } : {},
    history,
    recovery_resume_state: recoveryResumeState,
  };
}

function capacityUnlockArtifact(value: unknown): IPodCapacityUnlockArtifact | null {
  if (!isRecord(value)) return null;
  const artifactId = recordString(value, 'artifact_id');
  const sha256 = recordString(value, 'sha256');
  if (!artifactId || !sha256) return null;
  return {
    artifact_id: artifactId,
    filename: recordString(value, 'filename'),
    url: recordString(value, 'url'),
    expected_size: recordNumber(value, 'expected_size'),
    sha1: optionalString(value, 'sha1'),
    sha256,
    kind: recordString(value, 'kind'),
    license_expression: recordString(value, 'license_expression'),
    source_revision: recordString(value, 'source_revision'),
    redistributable: recordBoolean(value, 'redistributable'),
    executable: recordBoolean(value, 'executable'),
    metadata_sha256: recordString(value, 'metadata_sha256'),
  };
}

function capacityUnlockProfile(value: unknown): IPodCapacityUnlockProfile | null {
  if (!isRecord(value)) return null;
  const modelNumber = recordString(value, 'model_number');
  if (!modelNumber) return null;
  return {
    model_number: modelNumber,
    generation: recordString(value, 'generation'),
    nominal_capacity_gb: recordNumber(value, 'nominal_capacity_gb'),
    color: recordString(value, 'color'),
    expected_firmware: recordString(value, 'expected_firmware'),
  };
}

function capacityUnlockEvidence(value: unknown): IPodCapacityUnlockEvidence {
  const record = isRecord(value) ? value : {};
  return {
    platform: recordString(record, 'platform'),
    model_family: recordString(record, 'model_family'),
    generation: recordString(record, 'generation'),
    model_number: recordString(record, 'model_number'),
    firmware_version: recordString(record, 'firmware_version'),
    filesystem: recordString(record, 'filesystem'),
    serial_number: recordString(record, 'serial_number'),
    firewire_guid: recordString(record, 'firewire_guid'),
    serial_is_stable: recordBoolean(record, 'serial_is_stable'),
    firewire_is_stable: recordBoolean(record, 'firewire_is_stable'),
    identity_conflict_count: recordNumber(record, 'identity_conflict_count'),
    writable: recordBoolean(record, 'writable'),
    has_writable_evidence: recordBoolean(record, 'has_writable_evidence'),
    storage_healthy: recordBoolean(record, 'storage_healthy'),
    has_health_evidence: recordBoolean(record, 'has_health_evidence'),
    usb_vendor_id: recordNumber(record, 'usb_vendor_id'),
    usb_product_id: recordNumber(record, 'usb_product_id'),
    is_virtual: recordBoolean(record, 'is_virtual'),
    active_device_mutation: recordBoolean(record, 'active_device_mutation'),
  };
}

export function parseCapacityUnlockEligibility(raw: string): IPodCapacityUnlockEligibilityResponse {
  const response = parseIPodResponse(raw);
  const eligibilityValue = isRecord(response.eligibility) ? response.eligibility : {};
  const issues = Array.isArray(eligibilityValue.issues)
    ? eligibilityValue.issues.reduce<IPodCapacityUnlockIssue[]>((items, value) => {
        if (!isRecord(value)) return items;
        items.push({
          code: recordString(value, 'code', 'eligibility_failed'),
          message: recordString(value, 'message', 'A required eligibility check did not pass.'),
        });
        return items;
      }, [])
    : [];
  const artifacts = Array.isArray(response.artifacts)
    ? response.artifacts
        .map(capacityUnlockArtifact)
        .filter((item): item is IPodCapacityUnlockArtifact => item !== null)
    : [];
  const hasCurrentSession = Object.prototype.hasOwnProperty.call(response, 'current_session');
  let currentSession: IPodCapacityUnlockSession | null | undefined;
  if (hasCurrentSession) {
    if (response.current_session === null) {
      currentSession = null;
    } else {
      currentSession = capacityUnlockSession(response.current_session);
      if (!currentSession) {
        throw new Error('The iPod backend returned an invalid persisted capacity-unlock session.');
      }
    }
  }
  return {
    protocol_version: recordNumber(response, 'protocol_version', 1),
    experimental: recordBoolean(response, 'experimental'),
    eligibility: {
      eligible: recordBoolean(eligibilityValue, 'eligible'),
      issues,
      profile: capacityUnlockProfile(eligibilityValue.profile),
      identity_fingerprint: optionalString(eligibilityValue, 'identity_fingerprint'),
      firewire_fingerprint: optionalString(eligibilityValue, 'firewire_fingerprint'),
    },
    evidence: capacityUnlockEvidence(response.evidence),
    artifacts,
    acknowledgement_fields: recordStringArray(response, 'acknowledgement_fields'),
    actions: recordStringArray(response, 'actions'),
    current_session: currentSession,
  };
}

export function parseCapacityUnlockResult(value: unknown): IPodCapacityUnlockResult {
  if (!isRecord(value)) {
    throw new Error('The iPod backend returned an unexpected capacity-unlock result.');
  }
  const hasSession = Object.prototype.hasOwnProperty.call(value, 'session');
  const session = capacityUnlockSession(value.session);
  if (hasSession && value.session !== null && !session) {
    throw new Error('The iPod backend returned an invalid capacity-unlock session.');
  }
  let sessions: IPodCapacityUnlockSession[] | undefined;
  if (Object.prototype.hasOwnProperty.call(value, 'sessions')) {
    if (!Array.isArray(value.sessions)) {
      throw new Error('The iPod backend returned an invalid capacity-unlock session list.');
    }
    sessions = value.sessions.map((item) => {
      const parsed = capacityUnlockSession(item);
      if (!parsed) {
        throw new Error('The iPod backend returned an invalid capacity-unlock session list.');
      }
      return parsed;
    });
  }
  let candidate: IPodCapacityUnlockCandidate | undefined;
  if (isRecord(value.candidate)) {
    candidate = {
      preset_id: recordString(value.candidate, 'preset_id'),
      preset_sha256: recordString(value.candidate, 'preset_sha256'),
      source_model_number: recordString(value.candidate, 'source_model_number'),
      original_sha256: recordString(value.candidate, 'original_sha256'),
      candidate_sha256: recordString(value.candidate, 'candidate_sha256'),
      changed_tags: recordStringArray(value.candidate, 'changed_tags'),
      candidate_size: recordNumber(value.candidate, 'candidate_size'),
      path: recordString(value.candidate, 'path'),
      preset: isRecord(value.candidate.preset) ? { ...value.candidate.preset } : undefined,
    };
  }
  if (!session && !sessions && !candidate) {
    throw new Error('The iPod backend returned no capacity-unlock session data.');
  }
  return {
    protocol_version: recordNumber(value, 'protocol_version', 1),
    experimental: recordBoolean(value, 'experimental'),
    session: session || undefined,
    sessions,
    candidate,
  };
}

export function parseIPodEventPayload(value: unknown): IPodEventPayload | null {
  if (!isRecord(value) || typeof value.type !== 'string') return null;
  const recoveryValue = value.recovery;
  return {
    type: value.type,
    protocol_version: recordNumber(value, 'protocol_version', 1),
    data: value.data,
    details: value.details,
    operation: optionalString(value, 'operation'),
    operation_id: optionalString(value, 'operation_id'),
    bridge_operation_id: optionalString(value, 'bridge_operation_id'),
    backend_operation_id: optionalString(value, 'backend_operation_id'),
    backend_operation_kind: optionalString(value, 'backend_operation_kind'),
    operation_kind: optionalString(value, 'operation_kind'),
    kind: optionalString(value, 'kind'),
    phase: optionalString(value, 'phase'),
    stage: optionalString(value, 'stage'),
    can_cancel: typeof value.can_cancel === 'boolean' ? value.can_cancel : undefined,
    current: optionalNumber(value, 'current'),
    total: optionalNumber(value, 'total'),
    message: optionalString(value, 'message'),
    status: optionalString(value, 'status'),
    code: optionalString(value, 'code'),
    recovery: isRecord(recoveryValue) ? recoveryDetails(recoveryValue) : undefined,
  };
}

export function getIPodEventDevice(event: IPodEventPayload): IPodDevice | null {
  return normalizeIPodDevice(event.data);
}

export function getIPodEventData(event: IPodEventPayload): UnknownRecord {
  return isRecord(event.data) ? event.data : {};
}

export function mergeIPodOperation(
  current: IPodOperationEnvelope | null,
  event: IPodEventPayload,
): IPodOperationEnvelope | null {
  if (!event.operation_id || !event.kind || !event.operation) return current;
  const sameOperation = current?.operation_id === event.operation_id;
  const eventData = getIPodEventData(event);
  const nestedRecovery = isRecord(eventData.recovery)
    ? recoveryDetails(eventData.recovery)
    : undefined;
  const terminal = event.type === 'ipod_operation_ended';
  const status = event.status
    || (terminal ? 'completed' : sameOperation ? current?.status || 'running' : 'running');
  return {
    type: event.type,
    operation: event.operation,
    operation_id: event.operation_id,
    kind: event.kind,
    phase: event.phase || event.stage || (sameOperation ? current?.phase : '') || 'starting',
    can_cancel: terminal ? false : event.can_cancel ?? (sameOperation ? current?.can_cancel : false) ?? false,
    current: event.current ?? (sameOperation ? current?.current : 0) ?? 0,
    total: event.total ?? (sameOperation ? current?.total : 0) ?? 0,
    message: event.message || (sameOperation ? current?.message : '') || '',
    status,
    recovery: event.recovery || nestedRecovery || (sameOperation ? current?.recovery : undefined),
  };
}

export function isIPodOperationActive(operation: IPodOperationEnvelope | null): boolean {
  if (!operation) return false;
  if (['completed', 'failed', 'succeeded', 'cancelled'].includes(operation.status)) return false;
  return !['complete', 'failed', 'cancelled'].includes(operation.phase);
}

export function parseIPodBrowseResponse(raw: string): {
  items: IPodBrowseItem[];
  total: number;
} {
  const response = parseIPodResponse(raw);
  const items = Array.isArray(response.items)
    ? response.items.filter(isRecord).map(item => ({ ...item }))
    : [];
  return { items, total: recordNumber(response, 'total') };
}

export function parseDownloadedTracks(raw: string): IPodDownloadedTrack[] {
  const response = parseIPodResponse(raw);
  if (!Array.isArray(response.tracks)) return [];
  return response.tracks.reduce<IPodDownloadedTrack[]>((tracks, value) => {
    if (!isRecord(value)) return tracks;
    const filePath = recordString(value, 'file_path');
    if (!filePath) return tracks;
    tracks.push({
      file_path: filePath,
      title: optionalString(value, 'title'),
      file_name: optionalString(value, 'file_name'),
    });
    return tracks;
  }, []);
}

function planStorage(value: unknown): IPodPlanStorage {
  const record = isRecord(value) ? value : {};
  return {
    bytes_to_add: recordNumber(record, 'bytes_to_add'),
    bytes_to_remove: recordNumber(record, 'bytes_to_remove'),
    bytes_to_update: recordNumber(record, 'bytes_to_update'),
    net_change_bytes: recordNumber(record, 'net_change_bytes'),
    required_free_bytes: recordNumber(record, 'required_free_bytes'),
    free_before_bytes: recordNumber(record, 'free_before_bytes'),
    free_after_bytes: recordNumber(record, 'free_after_bytes'),
  };
}

const planGroups: IPodPlanGroup[] = [
  'additions',
  'removals',
  'metadata_updates',
  'artwork_updates',
  'conversions',
  'playlist_effects',
  'warnings',
  'unsupported',
];

function isPlanGroup(value: unknown): value is IPodPlanGroup {
  return typeof value === 'string' && planGroups.includes(value as IPodPlanGroup);
}

function groupDescriptors(value: unknown): IPodPlanGroupDescriptor[] {
  if (!Array.isArray(value)) return [];
  return value.reduce<IPodPlanGroupDescriptor[]>((groups, item) => {
    if (!isRecord(item) || !isPlanGroup(item.group)) return groups;
    groups.push({
      group: item.group,
      total: recordNumber(item, 'total'),
      page_size_max: recordNumber(item, 'page_size_max'),
    });
    return groups;
  }, []);
}

function planDetailItem(value: unknown): IPodPlanDetailItem | null {
  if (!isRecord(value) || !isPlanGroup(value.group)) return null;
  return {
    item_id: recordString(value, 'item_id'),
    group: value.group,
    action: recordString(value, 'action'),
    title: optionalString(value, 'title'),
    artist: optionalString(value, 'artist'),
    album: optionalString(value, 'album'),
    description: optionalString(value, 'description'),
    source_path: optionalString(value, 'source_path'),
    ipod_location: optionalString(value, 'ipod_location'),
    db_track_id: typeof value.db_track_id === 'number' || typeof value.db_track_id === 'string'
      ? value.db_track_id
      : undefined,
    estimated_bytes: optionalNumber(value, 'estimated_bytes'),
    removed_bytes: optionalNumber(value, 'removed_bytes'),
    metadata_fields: Array.isArray(value.metadata_fields)
      ? value.metadata_fields.filter((item): item is string => typeof item === 'string')
      : isRecord(value.metadata_fields) ? value.metadata_fields : undefined,
    conversion: typeof value.conversion === 'string' || isRecord(value.conversion)
      ? value.conversion
      : undefined,
    track_count: optionalNumber(value, 'track_count'),
    skipped_count: optionalNumber(value, 'skipped_count'),
    playlist_id: typeof value.playlist_id === 'number' || typeof value.playlist_id === 'string'
      ? value.playlist_id
      : undefined,
    code: optionalString(value, 'code'),
    message: optionalString(value, 'message'),
  };
}

function groupPreviews(value: unknown): Partial<Record<IPodPlanGroup, IPodPlanDetailItem[]>> {
  if (!isRecord(value)) return {};
  const previews: Partial<Record<IPodPlanGroup, IPodPlanDetailItem[]>> = {};
  planGroups.forEach(group => {
    const items = value[group];
    if (!Array.isArray(items)) return;
    previews[group] = items
      .map(planDetailItem)
      .filter((item): item is IPodPlanDetailItem => item !== null);
  });
  return previews;
}

export function parseIPodPlan(raw: string): IPodPlan {
  const response = parseIPodResponse(raw);
  const planId = recordString(response, 'plan_id');
  if (!planId) throw new Error('The iPod backend did not return a reviewable sync plan.');
  return {
    protocol_version: recordNumber(response, 'protocol_version', 1),
    plan_id: planId,
    additions: recordNumber(response, 'additions'),
    removals: recordNumber(response, 'removals'),
    updates: recordNumber(response, 'updates'),
    conversions: recordNumber(response, 'conversions'),
    playlist_changes: recordNumber(response, 'playlist_changes'),
    warnings: recordNumber(response, 'warnings'),
    unsupported: recordNumber(response, 'unsupported'),
    required_bytes: recordNumber(response, 'required_bytes'),
    source_count: recordNumber(response, 'source_count'),
    storage: planStorage(response.storage),
    groups: groupDescriptors(response.groups),
    group_previews: groupPreviews(response.group_previews),
    review_fingerprint: recordString(response, 'review_fingerprint'),
    expires_at: recordNumber(response, 'expires_at'),
  };
}

function backupSnapshot(value: unknown): IPodBackupSnapshot | null {
  if (!isRecord(value)) return null;
  const snapshotId = recordString(value, 'snapshot_id');
  if (!snapshotId) return null;
  return {
    snapshot_id: snapshotId,
    timestamp: recordString(value, 'timestamp'),
    archive_id: recordString(value, 'archive_id'),
    device_name: recordString(value, 'device_name', 'iPod'),
    file_count: recordNumber(value, 'file_count'),
    total_size_bytes: recordNumber(value, 'total_size_bytes'),
    reason: recordString(value, 'reason', 'manual'),
    note: recordString(value, 'note'),
    files_added: optionalNumber(value, 'files_added'),
    files_removed: optionalNumber(value, 'files_removed'),
    files_changed: optionalNumber(value, 'files_changed'),
    device_meta: deviceMeta(value.device_meta),
    is_valid: typeof value.is_valid === 'boolean' ? value.is_valid : undefined,
    validation_error: optionalString(value, 'validation_error'),
    identity_is_stable: typeof value.identity_is_stable === 'boolean'
      ? value.identity_is_stable
      : undefined,
    source_verification: optionalString(value, 'source_verification'),
    manifest_version: optionalNumber(value, 'manifest_version'),
    snapshot_fingerprint: optionalString(value, 'snapshot_fingerprint'),
  };
}

function backupScope(value: unknown): IPodBackupScope {
  const record = isRecord(value) ? value : {};
  return {
    kind: recordString(record, 'kind', 'full_regular_file_tree'),
    functional_backup: recordBoolean(record, 'functional_backup', true),
    raw_disk_image: recordBoolean(record, 'raw_disk_image'),
    included_file_count: recordNumber(record, 'included_file_count'),
    included_bytes: recordNumber(record, 'included_bytes'),
    content_verification: recordString(record, 'content_verification'),
    restores_included_tree_exactly: recordBoolean(record, 'restores_included_tree_exactly'),
  };
}

function backupExclusions(value: unknown): IPodBackupExclusion[] {
  if (!Array.isArray(value)) return [];
  return value.reduce<IPodBackupExclusion[]>((exclusions, item) => {
    if (!isRecord(item)) return exclusions;
    exclusions.push({
      category: recordString(item, 'category'),
      description: recordString(item, 'description'),
    });
    return exclusions;
  }, []);
}

export function parseBackupDevices(raw: string): IPodBackupDevicesResponse {
  const response = parseIPodResponse(raw);
  const devices = Array.isArray(response.devices)
    ? response.devices.reduce<IPodBackupDeviceArchive[]>((archives, value) => {
        if (!isRecord(value)) return archives;
        const archiveId = recordString(value, 'archive_id');
        if (!archiveId) return archives;
        archives.push({
          archive_id: archiveId,
          device_name: recordString(value, 'device_name', 'iPod'),
          snapshot_count: recordNumber(value, 'snapshot_count'),
          identity_is_stable: recordBoolean(value, 'identity_is_stable'),
          repository_size_bytes: recordNonNegativeSafeInteger(value, 'repository_size_bytes'),
          device_meta: deviceMeta(value.device_meta),
        });
        return archives;
      }, [])
    : [];
  return {
    protocol_version: recordNumber(response, 'protocol_version', 1),
    total: recordNumber(response, 'total', devices.length),
    truncated: recordBoolean(response, 'truncated'),
    repository_size_bytes: recordNonNegativeSafeInteger(response, 'repository_size_bytes'),
    devices,
  };
}

export function parseBackupSnapshots(raw: string): IPodBackupSnapshotsResponse {
  const response = parseIPodResponse(raw);
  const items = Array.isArray(response.items)
    ? response.items
        .map(backupSnapshot)
        .filter((item): item is IPodBackupSnapshot => item !== null)
    : [];
  return {
    protocol_version: recordNumber(response, 'protocol_version', 1),
    archive_id: recordString(response, 'archive_id'),
    page: recordNumber(response, 'page', 1),
    page_size: recordNumber(response, 'page_size', 50),
    total: recordNumber(response, 'total', items.length),
    repository_size_bytes: recordNumber(response, 'repository_size_bytes'),
    items,
  };
}

export function parseBackupSnapshotDetails(raw: string): IPodBackupSnapshotDetails {
  const response = parseIPodResponse(raw);
  const snapshot = backupSnapshot(response.snapshot);
  if (!snapshot) throw new Error('The iPod backend did not return backup snapshot details.');
  return {
    protocol_version: recordNumber(response, 'protocol_version', 1),
    archive_id: recordString(response, 'archive_id'),
    snapshot,
    scope: backupScope(response.scope),
    exclusions: backupExclusions(response.exclusions),
    repository_size_bytes: recordNumber(response, 'repository_size_bytes'),
  };
}

export function parseBackupVerification(raw: string): IPodBackupVerification {
  const response = parseIPodResponse(raw);
  return {
    protocol_version: recordNumber(response, 'protocol_version', 1),
    operation_id: recordString(response, 'operation_id'),
    archive_id: recordString(response, 'archive_id'),
    snapshot_id: recordString(response, 'snapshot_id'),
    file_count: recordNumber(response, 'file_count'),
    unique_blobs_verified: recordNumber(response, 'unique_blobs_verified'),
    verified_bytes: recordNumber(response, 'verified_bytes'),
    verification: recordString(response, 'verification'),
    ok: recordBoolean(response, 'ok'),
  };
}

function databaseGeneration(value: unknown): IPodDatabaseGeneration {
  return isRecord(value) ? { ...value } : {};
}

function restorePreflightVerification(
  value: unknown,
): IPodRestorePreflightVerification | null {
  if (!isRecord(value)) return null;
  const fileCount = recordNonNegativeSafeInteger(value, 'file_count', -1);
  const uniqueBlobsVerified = recordNonNegativeSafeInteger(
    value,
    'unique_blobs_verified',
    -1,
  );
  const verifiedBytes = recordNonNegativeSafeInteger(value, 'verified_bytes', -1);
  const method = recordString(value, 'method');
  if (
    !method
    || fileCount < 0
    || uniqueBlobsVerified < 0
    || uniqueBlobsVerified > fileCount
    || verifiedBytes < 0
    || typeof value.ok !== 'boolean'
    || typeof value.filesystem_names_valid !== 'boolean'
  ) return null;
  return {
    ok: value.ok,
    method,
    file_count: fileCount,
    unique_blobs_verified: uniqueBlobsVerified,
    verified_bytes: verifiedBytes,
    filesystem_names_valid: value.filesystem_names_valid,
  };
}

function restorePreflightStorage(value: unknown): IPodRestorePreflightStorage | null {
  if (!isRecord(value)) return null;
  const finalAllocatedBytes = recordNonNegativeSafeInteger(
    value,
    'final_allocated_bytes',
    -1,
  );
  const volumeTotalBytes = recordNonNegativeSafeInteger(value, 'volume_total_bytes', -1);
  const volumeFreeBytes = recordNonNegativeSafeInteger(value, 'volume_free_bytes', -1);
  if (
    finalAllocatedBytes < 0
    || volumeTotalBytes < 0
    || volumeFreeBytes < 0
    || volumeFreeBytes > volumeTotalBytes
    || typeof value.final_state_fits !== 'boolean'
    || typeof value.atomic_temp_capacity_rechecked_on_execute !== 'boolean'
  ) return null;
  return {
    final_allocated_bytes: finalAllocatedBytes,
    volume_total_bytes: volumeTotalBytes,
    volume_free_bytes: volumeFreeBytes,
    final_state_fits: value.final_state_fits,
    atomic_temp_capacity_rechecked_on_execute:
      value.atomic_temp_capacity_rechecked_on_execute,
  };
}

export function parseRestorePreflight(raw: string): IPodRestorePreflight {
  const response = parseIPodResponse(raw);
  const snapshot = backupSnapshot(response.snapshot);
  const targetValue = isRecord(response.target) ? response.target : {};
  const restorePlanId = recordString(response, 'restore_plan_id');
  const verification = restorePreflightVerification(response.verification);
  const storage = restorePreflightStorage(response.storage);
  const scope = backupScope(response.scope);
  if (
    !snapshot
    || !restorePlanId
    || !verification
    || !storage
    || !verification.ok
    || verification.method !== 'full_sha256'
    || verification.file_count !== snapshot.file_count
    || verification.file_count !== scope.included_file_count
    || !verification.filesystem_names_valid
    || !storage.final_state_fits
    || storage.final_allocated_bytes > storage.volume_total_bytes
    || !storage.atomic_temp_capacity_rechecked_on_execute
  ) {
    throw new Error('The iPod backend did not return a reviewable restore preflight.');
  }
  return {
    protocol_version: recordNumber(response, 'protocol_version', 1),
    restore_plan_id: restorePlanId,
    source_archive_id: recordString(response, 'source_archive_id'),
    source_snapshot_id: recordString(response, 'source_snapshot_id'),
    snapshot,
    scope,
    exclusions: backupExclusions(response.exclusions),
    target: {
      device_id: recordString(targetValue, 'device_id'),
      archive_id: recordString(targetValue, 'archive_id'),
      name: recordString(targetValue, 'name', 'iPod'),
      model_family: recordString(targetValue, 'model_family'),
      database_generation: databaseGeneration(targetValue.database_generation),
    },
    verification,
    storage,
    expires_at: recordNumber(response, 'expires_at'),
    confirmation_required: recordBoolean(response, 'confirmation_required'),
    raw_replacement_restore_allowed: recordBoolean(response, 'raw_replacement_restore_allowed'),
  };
}

function migrationEndpoint(value: unknown): IPodMigrationEndpoint {
  const record = isRecord(value) ? value : {};
  return {
    archive_id: recordString(record, 'archive_id'),
    snapshot_id: optionalString(record, 'snapshot_id'),
    device_id: recordString(record, 'device_id'),
    snapshot_fingerprint: optionalString(record, 'snapshot_fingerprint'),
    model_family: optionalString(record, 'model_family'),
    generation: optionalString(record, 'generation'),
    uses_sqlite_db: typeof record.uses_sqlite_db === 'boolean'
      ? record.uses_sqlite_db
      : undefined,
    database_generation: isRecord(record.database_generation)
      ? databaseGeneration(record.database_generation)
      : undefined,
  };
}

function migrationIssues(value: unknown): IPodMigrationIssue[] {
  if (!Array.isArray(value)) return [];
  return value.reduce<IPodMigrationIssue[]>((issues, item) => {
    if (!isRecord(item)) return issues;
    issues.push({
      field: recordString(item, 'field', 'compatibility'),
      message: optionalString(item, 'message'),
      source: item.source,
      target: item.target,
      count: optionalNumber(item, 'count'),
      required_bytes: optionalNumber(item, 'required_bytes'),
      available_bytes: optionalNumber(item, 'available_bytes'),
    });
    return issues;
  }, []);
}

function migrationMetadata(value: unknown): IPodMigrationMetadataLimitations | undefined {
  if (!isRecord(value)) return undefined;
  return {
    preserved: recordStringArray(value, 'preserved'),
    not_preserved: recordStringArray(value, 'not_preserved'),
    unresolved_source_tracks: recordNumber(value, 'unresolved_source_tracks'),
    skipped_playlists: recordNumber(value, 'skipped_playlists'),
    unresolved_playlist_items: recordNumber(value, 'unresolved_playlist_items'),
  };
}

function migrationBundle(value: unknown): IPodMigrationBundle | undefined {
  if (!isRecord(value)) return undefined;
  return {
    schema_version: recordNumber(value, 'schema_version'),
    path: recordString(value, 'path'),
    fingerprint: recordString(value, 'fingerprint'),
    media_file_count: recordNumber(value, 'media_file_count'),
    playlist_count: recordNumber(value, 'playlist_count'),
    total_media_bytes: recordNumber(value, 'total_media_bytes'),
  };
}

export function parseMigrationPreflight(raw: string): IPodMigrationPreflight {
  const response = parseIPodResponse(raw);
  const blocked = recordBoolean(response, 'blocked');
  const migrationPlanId = optionalString(response, 'migration_plan_id');
  if (!blocked && !migrationPlanId) {
    throw new Error('The iPod backend did not return a reviewable migration preflight.');
  }
  return {
    protocol_version: recordNumber(response, 'protocol_version', 1),
    blocked,
    compatible: recordBoolean(response, 'compatible'),
    code: recordString(response, 'code'),
    message: recordString(response, 'message'),
    raw_restore_allowed: recordBoolean(response, 'raw_restore_allowed'),
    safe_migration_available: recordBoolean(response, 'safe_migration_available'),
    same_device: recordBoolean(response, 'same_device'),
    issues: migrationIssues(response.issues),
    requirements: Array.isArray(response.requirements)
      ? response.requirements.filter((item): item is string => typeof item === 'string')
      : [],
    source: migrationEndpoint(response.source),
    target: migrationEndpoint(response.target),
    migration_plan_id: migrationPlanId,
    confirmation_required: typeof response.confirmation_required === 'boolean'
      ? response.confirmation_required
      : undefined,
    target_safety_backup_required: typeof response.target_safety_backup_required === 'boolean'
      ? response.target_safety_backup_required
      : undefined,
    staging_bundle: migrationBundle(response.staging_bundle),
    metadata: migrationMetadata(response.metadata),
    additions: optionalNumber(response, 'additions'),
    removals: optionalNumber(response, 'removals'),
    updates: optionalNumber(response, 'updates'),
    conversions: optionalNumber(response, 'conversions'),
    playlist_changes: optionalNumber(response, 'playlist_changes'),
    warnings: optionalNumber(response, 'warnings'),
    unsupported: optionalNumber(response, 'unsupported'),
    required_bytes: optionalNumber(response, 'required_bytes'),
    source_count: optionalNumber(response, 'source_count'),
    storage: isRecord(response.storage) ? planStorage(response.storage) : undefined,
    groups: Array.isArray(response.groups) ? groupDescriptors(response.groups) : undefined,
    group_previews: isRecord(response.group_previews)
      ? groupPreviews(response.group_previews)
      : undefined,
    review_fingerprint: optionalString(response, 'review_fingerprint'),
    expires_at: optionalNumber(response, 'expires_at'),
  };
}

function recoveryReconnect(value: unknown): IPodRecoveryReconnect {
  if (!isRecord(value)) return {};
  return {
    ...value,
    required_device_id: optionalString(value, 'required_device_id'),
    mount_path: optionalString(value, 'mount_path'),
  };
}

function recoveryDetails(value: UnknownRecord): IPodRecoveryDetails {
  return {
    ...value,
    required: typeof value.required === 'boolean' ? value.required : undefined,
    code: optionalString(value, 'code'),
    message: optionalString(value, 'message'),
    device_dirty: typeof value.device_dirty === 'boolean' ? value.device_dirty : undefined,
    content_verified: typeof value.content_verified === 'boolean'
      ? value.content_verified
      : undefined,
    requires_safe_eject: typeof value.requires_safe_eject === 'boolean'
      ? value.requires_safe_eject
      : undefined,
    source_archive_id: optionalString(value, 'source_archive_id'),
    source_snapshot_id: optionalString(value, 'source_snapshot_id'),
    safety_snapshot_id: optionalString(value, 'safety_snapshot_id'),
    next_action: optionalString(value, 'next_action'),
  };
}

function recoveryOperation(value: unknown): IPodRecoveryOperation | null {
  if (!isRecord(value) || !recordString(value, 'operation_id')) return null;
  const errorValue = isRecord(value.error) ? value.error : {};
  return {
    schema_version: recordNumber(value, 'schema_version'),
    revision: recordNumber(value, 'revision'),
    operation_id: recordString(value, 'operation_id'),
    kind: recordString(value, 'kind'),
    phase: recordString(value, 'phase'),
    can_cancel: recordBoolean(value, 'can_cancel'),
    target_id: recordString(value, 'target_id'),
    source_id: recordString(value, 'source_id'),
    target_archive_id: recordString(value, 'target_archive_id'),
    source_archive_id: recordString(value, 'source_archive_id'),
    snapshot_id: recordString(value, 'snapshot_id'),
    safety_snapshot_id: recordString(value, 'safety_snapshot_id'),
    reconnect: recoveryReconnect(value.reconnect),
    recovery: recoveryDetails(isRecord(value.recovery) ? value.recovery : {}),
    status: recordString(value, 'status'),
    error: {
      code: optionalString(errorValue, 'code'),
      message: optionalString(errorValue, 'message'),
    },
    metadata: isRecord(value.metadata) ? value.metadata : {},
    started_at: recordNumber(value, 'started_at'),
    updated_at: recordNumber(value, 'updated_at'),
    completed_at: typeof value.completed_at === 'number' ? value.completed_at : null,
  };
}

export function parseRecoveryState(raw: string): IPodRecoveryState {
  const response = parseIPodResponse(raw);
  return {
    protocol_version: recordNumber(response, 'protocol_version', 1),
    journal_version: recordNumber(response, 'journal_version'),
    operation: recoveryOperation(response.operation),
    incomplete: recordBoolean(response, 'incomplete'),
    requires_recovery: recordBoolean(response, 'requires_recovery'),
    reconnect: recoveryReconnect(response.reconnect),
    recovery: recoveryDetails(isRecord(response.recovery) ? response.recovery : {}),
  };
}
