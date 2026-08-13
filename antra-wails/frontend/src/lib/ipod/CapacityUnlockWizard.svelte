<script lang="ts">
  import { createEventDispatcher, onMount, tick } from 'svelte';
  import {
    AlertTriangle,
    Check,
    CheckCircle2,
    Circle,
    Download,
    FileCheck2,
    FileUp,
    HardDrive,
    Info,
    LoaderCircle,
    LockKeyhole,
    RefreshCw,
    RotateCcw,
    ShieldAlert,
    ShieldCheck,
    Smartphone,
    Usb,
    XCircle,
  } from 'lucide-svelte';
  import {
    AdvanceIPodCapacityUnlock,
    GetIPodCapacityUnlockEligibility,
    InspectIPodRecoveryUSB,
    PickIPodRecoveryFile,
    StartIPodCapacityUnlock,
  } from '../../../wailsjs/go/main/App.js';
  import ConfirmationDialog from './ConfirmationDialog.svelte';
  import RecoveryFileChoice from './RecoveryFileChoice.svelte';
  import type {
    IPodCapacityUnlockArtifact,
    IPodCapacityUnlockEligibilityResponse,
    IPodCapacityUnlockSession,
    IPodCapacityUnlockState,
    IPodDevice,
    IPodEventPayload,
    IPodRecoveryUSBDevice,
    IPodRecoveryUSBInspection,
    UnknownRecord,
  } from '../ipodTypes';
  import {
    getIPodEventData,
    isRecord,
    parseCapacityUnlockEligibility,
    parseCapacityUnlockResult,
    recordString,
    recordStringArray,
  } from '../ipodTypes';

  export let device: IPodDevice;
  export let connected = true;
  export let active = false;
  export let ipodEvent: IPodEventPayload | null = null;
  export let operationBusy = false;
  export let demoMode = false;

  const dispatch = createEventDispatcher<{
    announce: { tone: 'error' | 'warning' | 'success'; message: string };
    sessionstate: { active: boolean };
  }>();

  type EligibilityStatus = 'idle' | 'checking' | 'checking_sessions' | 'ready' | 'error';
  type ArtifactMode = 'download' | 'validate';

  const requiredAcknowledgements = [
    'destructive_restore_erases_device',
    'nor_flash_can_make_device_unbootable',
    'manual_rockbox_nor_dfu_steps_required',
    'hardware_recovery_may_be_required',
    'itunes_restore_is_user_controlled',
    'cancellation_ends_after_nor_commit',
  ];

  const requiredActions = [
    'status',
    'list',
    'backup',
    'artifacts',
    'bootloader-await',
    'bootloader-installed',
    'syscfg-original',
    'syscfg-candidate',
    'syscfg-stage',
    'nor-await',
    'nor-attested',
    'dfu-await',
    'dfu-detected',
    'itunes-handoff',
    'restore-finished',
    'postflight',
    'recovery',
    'resume',
    'cancel',
  ];

  const requiredArtifactIds = [
    'apple-ipod-classic-2.0.2-ipsw',
    'olsro-rockbox-syscfg-patch',
    'olsro-syscfg-editor-source',
  ];

  const normalSteps: Array<{ state: IPodCapacityUnlockState; label: string }> = [
    { state: 'environment_ready', label: 'Backup' },
    { state: 'filesystem_backup_verified', label: 'Artifacts' },
    { state: 'artifacts_verified', label: 'Rockbox prep' },
    { state: 'awaiting_bootloader_install', label: 'Install Rockbox' },
    { state: 'awaiting_syscfg_dump', label: 'Dump SysCfg' },
    { state: 'original_syscfg_verified', label: 'Build candidate' },
    { state: 'candidate_syscfg_verified', label: 'Stage candidate' },
    { state: 'candidate_staged', label: 'Review NOR' },
    { state: 'awaiting_manual_nor_flash', label: 'Flash and verify NOR' },
    { state: 'nor_flash_attested', label: 'DFU prep' },
    { state: 'awaiting_dfu', label: 'Inspect DFU' },
    { state: 'itunes_handoff', label: 'iTunes handoff' },
    { state: 'awaiting_restore', label: 'iTunes restore' },
    { state: 'postflight_verification', label: 'Postflight' },
    { state: 'complete', label: 'Complete' },
  ];

  const stateTitles: Record<IPodCapacityUnlockState, string> = {
    eligibility_checked: 'Eligibility recorded',
    environment_ready: 'Create and verify a fresh backup',
    filesystem_backup_verified: 'Obtain every pinned artifact',
    artifacts_verified: 'Rebuild the pinned Rockbox helper',
    awaiting_bootloader_install: 'Install Rockbox and the rebuilt helper',
    awaiting_syscfg_dump: 'Dump the original SysCfg and make two copies',
    original_syscfg_verified: 'Build a device-bound 2.0.2 candidate',
    candidate_syscfg_verified: 'Stage and reread the generated candidate',
    candidate_staged: 'Final review before manual NOR flash',
    awaiting_manual_nor_flash: 'Flash SysCfg in Rockbox, then read it back',
    nor_flash_attested: 'Prepare to enter DFU mode',
    awaiting_dfu: 'Enter DFU and inspect the USB identity',
    itunes_handoff: 'Hand off to iTunes manually',
    awaiting_restore: 'Wait for the user-controlled iTunes restore',
    postflight_verification: 'Reconnect and verify the restored iPod',
    complete: 'Capacity unlock completed',
    recovery_required: 'Recovery guidance is active',
    cancelled: 'Capacity unlock cancelled safely',
  };

  let eligibilityStatus: EligibilityStatus = 'idle';
  let eligibility: IPodCapacityUnlockEligibilityResponse | null = null;
  let eligibilityError = '';
  let sessionsLoaded = false;
  let session: IPodCapacityUnlockSession | null = null;
  let candidatePath = '';
  let candidateDetails: UnknownRecord | null = null;
  let actionError = '';
  let pendingAction = '';
  let mounted = false;
  let refreshGeneration = 0;
  let observedDeviceKey = '';
  let lastHandledEvent: IPodEventPayload | null = null;
  let stepHeading: HTMLHeadingElement | null = null;
  let reportedSessionActive = false;

  let showDisclosure = false;
  let showStartDialog = false;
  let showCancelDialog = false;
  let showRecoveryDialog = false;
  let showNorDialog = false;
  let showNorAttestationDialog = false;

  let acknowledgeErase = false;
  let acknowledgeNorRisk = false;
  let acknowledgeManualSteps = false;
  let acknowledgeHardwareRecovery = false;
  let acknowledgeItunesControl = false;
  let acknowledgeCancellationBoundary = false;
  let bootloaderAttested = false;
  let helperArchivePath = '';
  let helperSourcePath = '';
  let helperManifestPath = '';
  let itunesAttested = false;
  let restoreAttested = false;
  let recoveryReady = false;
  let norReadbackAttested = false;

  let artifactModes: Record<string, ArtifactMode> = {};
  let artifactPaths: Record<string, string> = {};
  let originalPath = '';
  let originalBackupOne = '';
  let originalBackupTwo = '';
  let stagedPath = '';
  let norReadbackPath = '';
  let usbInspection: IPodRecoveryUSBInspection | null = null;
  let usbInspectionBusy = false;
  let usbInspectionError = '';

  $: deviceKey = device.firewire_guid
    ? device.firewire_guid.toLowerCase()
    : `${device.device_id}|${device.path}`;
  $: artifacts = eligibility?.artifacts || [];
  $: profile = eligibility?.eligibility.profile || null;
  $: missingActions = requiredActions.filter((item) => !eligibility?.actions.includes(item));
  $: missingAcknowledgements = requiredAcknowledgements.filter(
    (item) => !eligibility?.acknowledgement_fields.includes(item),
  );
  $: missingArtifacts = requiredArtifactIds.filter(
    (item) => !eligibility?.artifacts.some((artifact) =>
      artifact.artifact_id === item
        && Boolean(artifact.filename)
        && Boolean(artifact.url)
        && /^[a-f0-9]{64}$/.test(artifact.sha256)
    ),
  );
  $: contractReady = Boolean(
    eligibility?.experimental
      && profile
      && eligibility?.eligibility.identity_fingerprint
      && eligibility?.eligibility.firewire_fingerprint
      && missingActions.length === 0
      && missingAcknowledgements.length === 0
      && missingArtifacts.length === 0,
  );
  $: eligibleToStart = Boolean(
    eligibilityStatus === 'ready'
      && sessionsLoaded
      && eligibility?.eligibility.eligible
      && contractReady
      && !session,
  );
  $: allStartAcknowledged = acknowledgeErase
    && acknowledgeNorRisk
    && acknowledgeManualSteps
    && acknowledgeHardwareRecovery
    && acknowledgeItunesControl
    && acknowledgeCancellationBoundary;
  $: allArtifactsSelected = artifacts.length > 0
    && artifacts.every((artifact) => artifactModes[artifact.artifact_id]);
  $: downloadCount = artifacts.filter(
    (artifact) => artifactModes[artifact.artifact_id] === 'download',
  ).length;
  $: helperOutputsReady = Boolean(
    helperArchivePath
      && helperSourcePath
      && helperManifestPath,
  );
  $: originalFilesReady = Boolean(
    originalPath
      && originalBackupOne
      && originalBackupTwo
      && originalPath !== originalBackupOne
      && originalPath !== originalBackupTwo
      && originalBackupOne !== originalBackupTwo
      && parentPath(originalBackupOne)
      && parentPath(originalBackupOne).toLowerCase() !== parentPath(originalBackupTwo).toLowerCase(),
  );
  $: appleFirmware = artifacts.find(
    (artifact) => artifact.artifact_id === 'apple-ipod-classic-2.0.2-ipsw',
  ) || null;
  $: displayedCandidate = candidateDetails
    || (
      session && isRecord(session.details.candidate_syscfg)
        ? session.details.candidate_syscfg
        : null
    );
  $: currentStepIndex = session ? stepIndex(session) : -1;
  $: capacityPlatform = eligibility?.evidence.platform || '';
  $: capacityActionsSupported = isWindowsPlatform(capacityPlatform);
  $: capacityActionsBlocked = eligibilityStatus === 'ready' && !capacityActionsSupported;
  $: capacityPlatformIssue = eligibility?.eligibility.issues.find((issue) =>
    /platform|windows|unsupported/i.test(`${issue.code} ${issue.message}`)
  ) || eligibility?.eligibility.issues[0] || null;
  $: usbInspectionUsable = Boolean(
    usbInspection?.supported
      && usbInspection.available
      && usbInspection.read_only,
  );
  $: cancellationLocked = Boolean(
    session?.nor_committed || session?.state === 'awaiting_manual_nor_flash',
  );
  $: canOfferCancel = Boolean(
    session
      && session.can_cancel
      && !session.terminal
      && !cancellationLocked
      && session.state !== 'recovery_required',
  );
  $: isBusy = operationBusy || Boolean(pendingAction);
  $: sessionActive = Boolean(capacityActionsSupported && session && !session.terminal);
  $: if (sessionActive !== reportedSessionActive) {
    reportedSessionActive = sessionActive;
    dispatch('sessionstate', { active: sessionActive });
  }

  $: if (mounted && deviceKey !== observedDeviceKey) {
    observedDeviceKey = deviceKey;
    resetForDevice();
    void refreshEligibility();
  }

  $: {
    if (
      ipodEvent
      && ipodEvent.kind === 'capacity_unlock'
      && ipodEvent !== lastHandledEvent
    ) {
      lastHandledEvent = ipodEvent;
      handleCapacityUnlockEvent(ipodEvent);
    }
  }

  onMount(() => {
    mounted = true;
    observedDeviceKey = deviceKey;
    void refreshEligibility();
    return () => {
      mounted = false;
      refreshGeneration += 1;
    };
  });

  function resetForDevice(): void {
    eligibilityStatus = 'idle';
    eligibility = null;
    eligibilityError = '';
    sessionsLoaded = false;
    session = null;
    candidatePath = '';
    candidateDetails = null;
    actionError = '';
    pendingAction = '';
    showDisclosure = false;
    resetStepInputs();
  }

  function resetStepInputs(): void {
    artifactModes = {};
    artifactPaths = {};
    originalPath = '';
    originalBackupOne = '';
    originalBackupTwo = '';
    stagedPath = '';
    norReadbackPath = '';
    helperArchivePath = '';
    helperSourcePath = '';
    helperManifestPath = '';
    bootloaderAttested = false;
    itunesAttested = false;
    restoreAttested = false;
    recoveryReady = false;
    norReadbackAttested = false;
    usbInspection = null;
    usbInspectionError = '';
  }

  async function refreshEligibility(): Promise<void> {
    const generation = ++refreshGeneration;
    actionError = '';
    eligibilityError = '';
    if (demoMode) {
      eligibilityStatus = 'error';
      eligibilityError = 'Capacity unlock is disabled in demo mode. Connect the physical iPod on Windows.';
      sessionsLoaded = false;
      return;
    }
    if (!connected) {
      if (session) return;
      eligibilityStatus = 'error';
      eligibilityError = 'Reconnect the mounted iPod before checking capacity-unlock eligibility.';
      return;
    }
    eligibilityStatus = 'checking';
    sessionsLoaded = false;
    try {
      const raw = await GetIPodCapacityUnlockEligibility({
        mount_path: device.path,
      });
      if (!mounted || generation !== refreshGeneration) return;
      const parsed = parseCapacityUnlockEligibility(raw);
      eligibility = parsed;
      if (parsed.current_session !== undefined) {
        session = parsed.current_session;
        sessionsLoaded = true;
        eligibilityStatus = 'ready';
      } else if (!isWindowsPlatform(parsed.evidence.platform)) {
        session = null;
        sessionsLoaded = true;
        eligibilityStatus = 'ready';
      } else {
        eligibilityStatus = 'checking_sessions';
        await listPersistedSessions();
      }
    } catch (error) {
      if (!mounted || generation !== refreshGeneration) return;
      eligibilityStatus = 'error';
      eligibilityError = errorMessage(error);
      sessionsLoaded = false;
    }
  }

  async function listPersistedSessions(): Promise<void> {
    pendingAction = 'list';
    try {
      await AdvanceIPodCapacityUnlock({
        session_id: '',
        action: 'list',
        confirmed: false,
        data: {},
      });
    } catch (error) {
      pendingAction = '';
      eligibilityStatus = 'error';
      eligibilityError = errorMessage(error);
      sessionsLoaded = false;
    }
  }

  function handleCapacityUnlockEvent(event: IPodEventPayload): void {
    if (
      event.type === 'ipod_error'
      || event.status === 'failed'
      || event.phase === 'failed'
      || event.phase === 'cancelled'
    ) {
      const message = event.message || 'The capacity-unlock step failed.';
      actionError = message;
      if (pendingAction === 'list') {
        eligibilityStatus = 'error';
        eligibilityError = message;
        sessionsLoaded = false;
      }
      pendingAction = '';
      announce('error', message);
      return;
    }
    if (
      event.type === 'ipod_operation_ended'
      && event.status === 'completed'
      && pendingAction
    ) {
      const completedWithoutResult = pendingAction;
      pendingAction = '';
      if (completedWithoutResult === 'list' || !session) {
        eligibilityStatus = 'error';
        eligibilityError = 'The backend step completed without a retained result event. Refresh eligibility to load the persisted session safely.';
        sessionsLoaded = false;
      } else {
        actionError = 'The step completed, but its result event was not retained. Refresh the persisted session before continuing.';
      }
      return;
    }
    if (
      event.type !== 'ipod_capacity_unlock_start'
      && event.type !== 'ipod_capacity_unlock_advance'
    ) return;
    const priorState = session?.state || '';
    try {
      const result = parseCapacityUnlockResult(getIPodEventData(event));
      if (result.sessions) {
        session = findMatchingActiveSession(result.sessions);
        sessionsLoaded = true;
        eligibilityStatus = 'ready';
      }
      if (result.session && resultMatchesSelectedDevice(result.session)) {
        session = result.session;
        sessionsLoaded = true;
        eligibilityStatus = 'ready';
      }
      if (result.candidate) {
        candidatePath = result.candidate.path;
        candidateDetails = { ...result.candidate };
      }
      actionError = '';
      const completedAction = pendingAction;
      pendingAction = '';
      if (completedAction && completedAction !== 'list' && completedAction !== 'status') {
        announce('success', `Capacity unlock advanced to ${stateTitle(session?.state)}.`);
      }
      if (active && session && session.state !== priorState) {
        void tick().then(() => stepHeading?.focus());
      }
    } catch (error) {
      actionError = errorMessage(error);
      pendingAction = '';
      announce('error', actionError);
    }
  }

  function resultMatchesSelectedDevice(value: IPodCapacityUnlockSession): boolean {
    const expected = eligibility?.eligibility.firewire_fingerprint;
    return Boolean(expected && value.firewire_fingerprint === expected);
  }

  function findMatchingActiveSession(
    sessions: IPodCapacityUnlockSession[],
  ): IPodCapacityUnlockSession | null {
    const fingerprint = eligibility?.eligibility.firewire_fingerprint;
    if (!fingerprint) return null;
    return sessions
      .filter((item) => item.firewire_fingerprint === fingerprint && !item.terminal)
      .sort((left, right) => right.updated_at.localeCompare(left.updated_at))[0] || null;
  }

  async function startUnlock(): Promise<void> {
    if (!capacityActionsSupported || !eligibleToStart || !allStartAcknowledged || !profile) return;
    showStartDialog = false;
    pendingAction = 'start';
    actionError = '';
    try {
      await StartIPodCapacityUnlock({
        mount_path: device.path,
        confirmed: true,
        acknowledgements: {
          destructive_restore_erases_device: acknowledgeErase,
          nor_flash_can_make_device_unbootable: acknowledgeNorRisk,
          manual_rockbox_nor_dfu_steps_required: acknowledgeManualSteps,
          hardware_recovery_may_be_required: acknowledgeHardwareRecovery,
          itunes_restore_is_user_controlled: acknowledgeItunesControl,
          cancellation_ends_after_nor_commit: acknowledgeCancellationBoundary,
        },
      });
    } catch (error) {
      pendingAction = '';
      actionError = errorMessage(error);
      announce('error', actionError);
    }
  }

  async function advance(
    action: string,
    data: UnknownRecord = {},
    confirmed = true,
  ): Promise<void> {
    if (!capacityActionsSupported || !session || isBusy) return;
    pendingAction = action;
    actionError = '';
    try {
      await AdvanceIPodCapacityUnlock({
        session_id: session.session_id,
        action,
        confirmed,
        data: {
          expected_revision: session.revision,
          ...data,
        },
      });
    } catch (error) {
      pendingAction = '';
      actionError = errorMessage(error);
      announce('error', actionError);
    }
  }

  function refreshSession(): void {
    void advance('status', {}, false);
  }

  function returnToEligibility(): void {
    session = null;
    candidatePath = '';
    candidateDetails = null;
    resetStepInputs();
    void refreshEligibility();
  }

  function createBackup(): void {
    if (!connected) return;
    void advance('backup', { mount_path: device.path });
  }

  function selectArtifactDownload(artifact: IPodCapacityUnlockArtifact): void {
    artifactModes = { ...artifactModes, [artifact.artifact_id]: 'download' };
    const nextPaths = { ...artifactPaths };
    delete nextPaths[artifact.artifact_id];
    artifactPaths = nextPaths;
  }

  async function selectArtifactFile(artifact: IPodCapacityUnlockArtifact): Promise<void> {
    const path = await pickRecoveryFile();
    if (!path) return;
    artifactModes = { ...artifactModes, [artifact.artifact_id]: 'validate' };
    artifactPaths = { ...artifactPaths, [artifact.artifact_id]: path };
  }

  function processArtifacts(): void {
    if (!allArtifactsSelected) return;
    const choices: UnknownRecord = {};
    const selectedPaths: string[] = [];
    for (const artifact of artifacts) {
      const mode = artifactModes[artifact.artifact_id];
      if (mode === 'download') {
        choices[artifact.artifact_id] = { mode: 'download' };
      } else {
        const path = artifactPaths[artifact.artifact_id];
        if (!path) return;
        choices[artifact.artifact_id] = { mode: 'validate', path };
        selectedPaths.push(path);
      }
    }
    void advance('artifacts', {
      artifacts: choices,
      selected_directories: selectedDirectories(selectedPaths),
    });
  }

  function confirmBootloaderInstalled(): void {
    if (!bootloaderAttested || !helperOutputsReady) return;
    const paths = [helperArchivePath, helperSourcePath, helperManifestPath];
    void advance('bootloader-installed', {
      user_attested: true,
      helper_path: helperArchivePath,
      source_path: helperSourcePath,
      manifest_path: helperManifestPath,
      selected_directories: selectedDirectories(paths),
    });
  }

  function verifyOriginalSysCfg(): void {
    if (!originalFilesReady) return;
    const paths = [originalPath, originalBackupOne, originalBackupTwo];
    void advance('syscfg-original', {
      source_path: originalPath,
      backup_paths: [originalBackupOne, originalBackupTwo],
      selected_directories: selectedDirectories(paths),
    });
  }

  function buildCandidate(): void {
    if (!originalPath) return;
    void advance('syscfg-candidate', {
      original_path: originalPath,
      selected_directories: selectedDirectories([originalPath]),
    });
  }

  function verifyStagedCandidate(): void {
    if (!stagedPath) return;
    void advance('syscfg-stage', {
      staged_path: stagedPath,
      selected_directories: selectedDirectories([stagedPath]),
    });
  }

  function beginManualNor(): void {
    showNorDialog = false;
    void advance('nor-await');
  }

  function attestNorReadback(): void {
    if (!norReadbackPath || !norReadbackAttested) return;
    showNorAttestationDialog = false;
    void advance('nor-attested', {
      user_attested: true,
      readback_path: norReadbackPath,
      selected_directories: selectedDirectories([norReadbackPath]),
    });
  }

  async function inspectRecoveryUSB(): Promise<void> {
    if (!capacityActionsSupported) return;
    usbInspectionBusy = true;
    usbInspectionError = '';
    try {
      usbInspection = await InspectIPodRecoveryUSB() as IPodRecoveryUSBInspection;
    } catch (error) {
      usbInspectionError = errorMessage(error);
    } finally {
      usbInspectionBusy = false;
    }
  }

  function useRecoveryUSBDevice(recoveryDevice: IPodRecoveryUSBDevice): void {
    if (!usbInspectionUsable || !usbInspection || usbInspection.devices.length !== 1) return;
    const vendor = parseUsbIdentifier(recoveryDevice.vendor_id);
    const product = parseUsbIdentifier(recoveryDevice.product_id);
    if (vendor === null || product === null) {
      usbInspectionError = 'The USB inspection returned an invalid vendor or product ID.';
      return;
    }
    void advance('dfu-detected', {
      usb_vendor_id: vendor,
      usb_product_id: product,
    });
  }

  function confirmItunesHandoff(): void {
    if (!itunesAttested || !appleFirmware) return;
    void advance('itunes-handoff', {
      user_attested: true,
      firmware_sha256: appleFirmware.sha256,
    });
  }

  function confirmRestoreFinished(): void {
    if (!restoreAttested) return;
    void advance('restore-finished', { user_attested: true });
  }

  function verifyPostflight(): void {
    if (!connected) return;
    void advance('postflight', { mount_path: device.path });
  }

  function requestRecovery(): void {
    showRecoveryDialog = false;
    void advance('recovery', { reason_code: 'user_requested_recovery' });
  }

  function resumeRecovery(): void {
    if (!recoveryReady) return;
    void advance('resume');
  }

  function cancelSession(): void {
    showCancelDialog = false;
    void advance('cancel');
  }

  async function chooseFile(
    target:
      | 'helper'
      | 'helper-source'
      | 'helper-manifest'
      | 'original'
      | 'copy-one'
      | 'copy-two'
      | 'staged'
      | 'readback',
  ) {
    const path = await pickRecoveryFile();
    if (!path) return;
    if (target === 'helper') helperArchivePath = path;
    if (target === 'helper-source') helperSourcePath = path;
    if (target === 'helper-manifest') helperManifestPath = path;
    if (target === 'original') originalPath = path;
    if (target === 'copy-one') originalBackupOne = path;
    if (target === 'copy-two') originalBackupTwo = path;
    if (target === 'staged') stagedPath = path;
    if (target === 'readback') norReadbackPath = path;
  }

  async function pickRecoveryFile(): Promise<string> {
    try {
      return (await PickIPodRecoveryFile()).trim();
    } catch (error) {
      actionError = errorMessage(error);
      announce('error', actionError);
      return '';
    }
  }

  function selectedDirectories(paths: string[]): string[] {
    return Array.from(new Set(paths.map(parentPath).filter(Boolean)));
  }

  function parentPath(path: string): string {
    const lastSeparator = Math.max(path.lastIndexOf('\\'), path.lastIndexOf('/'));
    return lastSeparator > 0 ? path.slice(0, lastSeparator) : '';
  }

  function parseUsbIdentifier(value: string): number | null {
    const normalized = value.trim().replace(/^0x/i, '');
    if (!/^[a-f0-9]{4}$/i.test(normalized)) return null;
    return Number.parseInt(normalized, 16);
  }

  function isWindowsPlatform(value: string): boolean {
    return ['windows', 'win32'].includes(value.trim().toLowerCase());
  }

  function platformLabel(value: string): string {
    const normalized = value.trim().toLowerCase();
    if (['darwin', 'mac', 'macos'].includes(normalized)) return 'macOS';
    if (isWindowsPlatform(normalized)) return 'Windows';
    if (normalized === 'linux') return 'Linux';
    return value.trim() || 'this platform';
  }

  function stepIndex(value: IPodCapacityUnlockSession): number {
    const state = value.state === 'recovery_required' && value.recovery_resume_state
      ? value.recovery_resume_state
      : value.state;
    return normalSteps.findIndex((step) => step.state === state);
  }

  function stateTitle(state?: IPodCapacityUnlockState): string {
    return state ? stateTitles[state] : 'the next step';
  }

  function shortFingerprint(value?: string): string {
    if (!value) return 'Unavailable';
    if (value.length <= 22) return value;
    return `${value.slice(0, 12)}…${value.slice(-8)}`;
  }

  function serialSuffix(value?: string): string {
    if (!value) return 'Unavailable';
    return `••••${value.slice(-4)}`;
  }

  function formatBytes(value: number): string {
    if (!Number.isFinite(value) || value <= 0) return 'Unknown size';
    const units = ['B', 'KB', 'MB', 'GB'];
    let amount = value;
    let unit = 0;
    while (amount >= 1024 && unit < units.length - 1) {
      amount /= 1024;
      unit += 1;
    }
    return `${amount >= 10 || unit === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`;
  }

  function artifactLabel(artifact: IPodCapacityUnlockArtifact): string {
    if (artifact.kind === 'apple-firmware') return 'Apple firmware restore image';
    if (artifact.kind === 'bootloader-tool') {
      return 'Official Rockbox Utility 1.5.1 for Windows';
    }
    if (artifact.kind === 'corresponding-source') {
      return 'Official Rockbox Utility 1.5.1 GPL source containing mks5lboot';
    }
    if (artifact.kind === 'source-patch-reference') return 'Audited Rockbox source patch';
    if (artifact.kind === 'source-only-reference') return 'SysCfg editor source reference';
    return artifact.filename;
  }

  function candidateString(key: string): string {
    return displayedCandidate ? recordString(displayedCandidate, key) : '';
  }

  function candidateTags(): string[] {
    return displayedCandidate ? recordStringArray(displayedCandidate, 'changed_tags') : [];
  }

  function announce(tone: 'error' | 'warning' | 'success', message: string): void {
    dispatch('announce', { tone, message });
  }

  function registerStepHeading(node: HTMLHeadingElement): { destroy: () => void } {
    stepHeading = node;
    return {
      destroy: () => {
        if (stepHeading === node) stepHeading = null;
      },
    };
  }

  function errorMessage(error: unknown): string {
    return error instanceof Error ? error.message : String(error || 'The operation failed.');
  }
</script>

<section class="unlock-shell" aria-labelledby="capacity-unlock-title" aria-busy={isBusy}>
  <header class="unlock-header">
    <div>
      <div class="eyebrow-row">
        <span class="experimental-badge">
          <ShieldAlert size={14} aria-hidden="true" />
          Experimental
        </span>
        <span>Windows · iPod Classic 6G/6.5G only</span>
      </div>
      <h3 id="capacity-unlock-title">Unlock storage above 128 GB</h3>
      <p>
        A guided, fail-closed workflow for the audited Classic capacity conversion.
        It never flashes NOR, presses click-wheel buttons, or controls iTunes for you.
      </p>
    </div>
    <div class="header-actions">
      {#if session && capacityActionsSupported}
        <button
          class="button secondary"
          type="button"
          on:click={refreshSession}
          disabled={isBusy}
        >
          <span class:spin={pendingAction === 'status'}>
            <RefreshCw size={15} aria-hidden="true" />
          </span>
          Refresh session
        </button>
      {:else}
        <button
          class="button secondary"
          type="button"
          on:click={refreshEligibility}
          disabled={eligibilityStatus === 'checking' || eligibilityStatus === 'checking_sessions' || !connected}
        >
          <span class:spin={eligibilityStatus === 'checking' || eligibilityStatus === 'checking_sessions'}>
            <RefreshCw size={15} aria-hidden="true" />
          </span>
          Refresh eligibility
        </button>
      {/if}
    </div>
  </header>

  <div class:reconnect-warning={!connected} class="connection-strip">
    {#if connected}
      <Smartphone size={17} aria-hidden="true" />
      <span>
        Connected as <strong>{device.name}</strong> at <code>{device.path}</code>
      </span>
    {:else}
      <Usb size={17} aria-hidden="true" />
      <span>
        The mounted volume is disconnected. The persisted session remains available while
        DFU and iTunes temporarily remove the drive letter.
      </span>
    {/if}
  </div>

  {#if capacityActionsBlocked}
    <div class="state-panel blocked" role="status">
      <ShieldAlert size={22} aria-hidden="true" />
      <div>
        <h4>Advanced recovery is Windows-only</h4>
        <p>
          {capacityPlatformIssue?.message
            || `The backend reported ${platformLabel(capacityPlatform)} and did not enable the capacity-unlock workflow.`}
        </p>
        <p>
          Mounted-volume browsing, backup, sync, restore, and safe eject remain available.
          Capacity unlock and DFU/WTF recovery inspection must be continued on Windows.
        </p>
      </div>
    </div>
  {:else if !session}
    {#if eligibilityStatus === 'idle' || eligibilityStatus === 'checking'}
      <div class="state-panel centered">
        <LoaderCircle size={24} class="spin" aria-hidden="true" />
        <div>
          <h4>Checking every eligibility gate</h4>
          <p>No destructive control is available until the backend proves the exact device.</p>
        </div>
      </div>
    {:else if eligibilityStatus === 'checking_sessions'}
      <div class="state-panel centered">
        <LoaderCircle size={24} class="spin" aria-hidden="true" />
        <div>
          <h4>Looking for a persisted session</h4>
          <p>A new workflow stays locked until Vela confirms there is no active session for this iPod.</p>
        </div>
      </div>
    {:else if eligibilityStatus === 'error'}
      <div class="state-panel blocked" role="alert">
        <XCircle size={22} aria-hidden="true" />
        <div>
          <h4>Capacity unlock is unavailable</h4>
          <p>{eligibilityError}</p>
        </div>
      </div>
    {:else if eligibility}
      <div class="identity-card">
        <div class="identity-heading">
          <HardDrive size={20} aria-hidden="true" />
          <div>
            <strong>{profile?.generation || device.generation || 'Unknown generation'}</strong>
            <span>{profile?.model_number || device.model_number || 'Unknown model'} · firmware {device.firmware || 'unknown'}</span>
          </div>
        </div>
        <dl>
          <div>
            <dt>Device</dt>
            <dd>{device.name}</dd>
          </div>
          <div>
            <dt>Serial</dt>
            <dd>{serialSuffix(device.serial)}</dd>
          </div>
          <div>
            <dt>Identity proof</dt>
            <dd title={eligibility.eligibility.identity_fingerprint || ''}>
              {shortFingerprint(eligibility.eligibility.identity_fingerprint)}
            </dd>
          </div>
          <div>
            <dt>FireWire proof</dt>
            <dd title={eligibility.eligibility.firewire_fingerprint || ''}>
              {shortFingerprint(eligibility.eligibility.firewire_fingerprint)}
            </dd>
          </div>
        </dl>
      </div>

      {#if !eligibility.eligibility.eligible}
        <div class="state-panel blocked">
          <ShieldAlert size={22} aria-hidden="true" />
          <div>
            <h4>This iPod did not pass the required checks</h4>
            <p>The destructive unlock control is intentionally hidden.</p>
            {#if eligibility.eligibility.issues.length}
              <ul class="issue-list">
                {#each eligibility.eligibility.issues as issue (issue.code)}
                  <li>
                    <strong>{issue.code.replace(/_/g, ' ')}</strong>
                    <span>{issue.message}</span>
                  </li>
                {/each}
              </ul>
            {/if}
          </div>
        </div>
      {:else if !contractReady}
        <div class="state-panel blocked">
          <ShieldAlert size={22} aria-hidden="true" />
          <div>
            <h4>The backend safety contract is incomplete</h4>
            <p>
              Eligibility passed, but Vela will not expose the unlock control because required
              workflow metadata is missing.
            </p>
            {#if missingActions.length}
              <p class="technical-note">Missing actions: {missingActions.join(', ')}</p>
            {/if}
            {#if missingAcknowledgements.length}
              <p class="technical-note">
                Missing acknowledgements: {missingAcknowledgements.join(', ')}
              </p>
            {/if}
            {#if missingArtifacts.length}
              <p class="technical-note">Missing pinned artifacts: {missingArtifacts.join(', ')}</p>
            {/if}
          </div>
        </div>
      {:else}
        <div class="state-panel eligible">
          <ShieldCheck size={24} aria-hidden="true" />
          <div>
            <h4>All required eligibility checks passed</h4>
            <p>
              Exact profile: {profile?.generation}, model {profile?.model_number},
              {profile?.nominal_capacity_gb} GB {profile?.color}, firmware
              {profile?.expected_firmware}.
            </p>
            <ul class="compact-checks">
              <li><Check size={14} aria-hidden="true" /> Stable serial and FireWire identity</li>
              <li><Check size={14} aria-hidden="true" /> Windows FAT32 device profile</li>
              <li><Check size={14} aria-hidden="true" /> Writable and healthy storage evidence</li>
              <li><Check size={14} aria-hidden="true" /> No conflicting device mutation</li>
            </ul>
          </div>
        </div>

        {#if !showDisclosure}
          <div class="start-row">
            <div>
              <strong>Destructive expert workflow</strong>
              <span>Review every risk before any session is created.</span>
            </div>
            {#if eligibleToStart}
              <button class="button danger" type="button" on:click={() => showDisclosure = true}>
                <LockKeyhole size={16} aria-hidden="true" />
                Unlock storage above 128 GB
              </button>
            {/if}
          </div>
        {:else}
          <div class="disclosure-card">
            <div class="danger-heading">
              <AlertTriangle size={24} aria-hidden="true" />
              <div>
                <h4>Understand the irreversible boundary</h4>
                <p>
                  This conversion erases the iPod during an iTunes restore and a bad NOR write
                  can require hardware recovery. Vela cannot perform the physical steps.
                </p>
              </div>
            </div>
            <div class="acknowledgements">
              <label>
                <input type="checkbox" bind:checked={acknowledgeErase} />
                <span>I understand the iTunes restore erases this iPod.</span>
              </label>
              <label>
                <input type="checkbox" bind:checked={acknowledgeNorRisk} />
                <span>I understand a failed NOR flash can make the iPod unbootable.</span>
              </label>
              <label>
                <input type="checkbox" bind:checked={acknowledgeManualSteps} />
                <span>I will perform the Rockbox, click-wheel, NOR, and DFU steps myself.</span>
              </label>
              <label>
                <input type="checkbox" bind:checked={acknowledgeHardwareRecovery} />
                <span>I accept that hardware recovery may be required.</span>
              </label>
              <label>
                <input type="checkbox" bind:checked={acknowledgeItunesControl} />
                <span>I understand Vela never installs, configures, or controls iTunes.</span>
              </label>
              <label>
                <input type="checkbox" bind:checked={acknowledgeCancellationBoundary} />
                <span>I understand cancellation is locked before the manual NOR commit window.</span>
              </label>
            </div>
            <div class="button-row">
              <button class="button secondary" type="button" on:click={() => showDisclosure = false}>
                Close review
              </button>
              <button
                class="button danger"
                type="button"
                disabled={!allStartAcknowledged || isBusy}
                on:click={() => showStartDialog = true}
              >
                Review final confirmation
              </button>
            </div>
          </div>
        {/if}
      {/if}
    {/if}
  {:else}
    <div class="session-topline">
      <div>
        <span class="session-kicker">Persisted session</span>
        <strong>{session.session_id}</strong>
        <span>Revision {session.revision} · updated {new Date(session.updated_at).toLocaleString()}</span>
      </div>
      <div class:locked={cancellationLocked} class="boundary-badge">
        {#if cancellationLocked}
          <LockKeyhole size={14} aria-hidden="true" />
          Cancellation locked
        {:else}
          <ShieldCheck size={14} aria-hidden="true" />
          Pre-NOR cancellation available
        {/if}
      </div>
    </div>

    <div class="identity-card session-identity">
      <dl>
        <div>
          <dt>Source model</dt>
          <dd>{session.source_generation} · {session.source_model_number}</dd>
        </div>
        <div>
          <dt>Firmware conversion</dt>
          <dd>{session.source_firmware_version} → {session.target_firmware_version}</dd>
        </div>
        <div>
          <dt>Current mount</dt>
          <dd>{connected ? device.path : 'Waiting for reconnect'}</dd>
        </div>
        <div>
          <dt>FireWire proof</dt>
          <dd title={session.firewire_fingerprint}>{shortFingerprint(session.firewire_fingerprint)}</dd>
        </div>
      </dl>
    </div>

    <ol class="stepper" aria-label="Capacity unlock progress">
      {#each normalSteps as step, index (step.state)}
        <li
          class:complete={index < currentStepIndex || session.state === 'complete'}
          class:current={index === currentStepIndex && session.state !== 'complete'}
          aria-current={index === currentStepIndex ? 'step' : undefined}
        >
          <span class="step-marker">
            {#if index < currentStepIndex || session.state === 'complete'}
              <Check size={13} aria-hidden="true" />
            {:else}
              {index + 1}
            {/if}
          </span>
          <span>{step.label}</span>
        </li>
      {/each}
    </ol>

    {#if actionError}
      <div class="inline-alert error" role="alert">
        <XCircle size={18} aria-hidden="true" />
        <div>
          <strong>The step did not advance</strong>
          <span>{actionError}</span>
        </div>
      </div>
    {/if}

    {#if !connected && !session.terminal}
      <div class="inline-alert warning">
        <Usb size={18} aria-hidden="true" />
        <div>
          <strong>Mounted device disconnected</strong>
          <span>
            This is expected during DFU and iTunes restore. Do not connect a second iPod.
            The session is persisted and USB inspection is read-only.
          </span>
        </div>
      </div>
    {/if}

    <article class="step-card">
      <header class="step-card-header">
        <div>
          <span>Current step</span>
          <h4 use:registerStepHeading tabindex="-1">{stateTitle(session.state)}</h4>
        </div>
        <span class="state-code">{session.state.replace(/_/g, ' ')}</span>
      </header>

      {#if session.state === 'eligibility_checked'}
        <p>The session was persisted before environment acknowledgement. Refresh it before continuing.</p>
      {:else if session.state === 'environment_ready'}
        <div class="step-content">
          <p>
            Vela will create a new full regular-file backup and immediately deep-verify every
            unique blob with SHA-256. Existing backups do not satisfy this gate.
          </p>
          <div class="instruction-box">
            <Info size={18} aria-hidden="true" />
            <span>Keep the iPod mounted, close iTunes and Rockbox Utility, and do not eject it.</span>
          </div>
          <button
            class="button primary"
            type="button"
            disabled={isBusy || !connected}
            on:click={createBackup}
          >
            <HardDrive size={16} aria-hidden="true" />
            Create and deeply verify backup
          </button>
        </div>
      {:else if session.state === 'filesystem_backup_verified'}
        <div class="step-content">
          <p>
            Select an explicit action for every pinned artifact. Nothing is downloaded merely
            by opening this page. User-supplied files are read and hash-verified only after you continue.
          </p>
          <div class="artifact-list">
            {#each artifacts as artifact (artifact.artifact_id)}
              <section class="artifact-card">
                <div class="artifact-heading">
                  <FileCheck2 size={19} aria-hidden="true" />
                  <div>
                    <strong>{artifactLabel(artifact)}</strong>
                    <span>{artifact.filename} · {formatBytes(artifact.expected_size)}</span>
                  </div>
                </div>
                <dl class="artifact-meta">
                  <div>
                    <dt>Source revision</dt>
                    <dd>{artifact.source_revision}</dd>
                  </div>
                  <div>
                    <dt>License</dt>
                    <dd>{artifact.license_expression}</dd>
                  </div>
                  <div class="wide">
                    <dt>SHA-256</dt>
                    <dd><code>{artifact.sha256}</code></dd>
                  </div>
                </dl>
                <div class="artifact-actions">
                  <button
                    class:active={artifactModes[artifact.artifact_id] === 'download'}
                    class="choice-button"
                    type="button"
                    disabled={isBusy}
                    on:click={() => selectArtifactDownload(artifact)}
                  >
                    <Download size={15} aria-hidden="true" />
                    Select pinned download
                  </button>
                  <button
                    class:active={artifactModes[artifact.artifact_id] === 'validate'}
                    class="choice-button"
                    type="button"
                    disabled={isBusy}
                    on:click={() => selectArtifactFile(artifact)}
                  >
                    <FileUp size={15} aria-hidden="true" />
                    Choose local file
                  </button>
                </div>
                {#if artifactModes[artifact.artifact_id] === 'download'}
                  <p class="selection-note">
                    Selected for explicit download from <code>{artifact.url}</code>. No transfer has started.
                  </p>
                {:else if artifactPaths[artifact.artifact_id]}
                  <p class="path-value"><code>{artifactPaths[artifact.artifact_id]}</code></p>
                {/if}
              </section>
            {/each}
          </div>
          <button
            class="button primary"
            type="button"
            disabled={isBusy || !allArtifactsSelected}
            on:click={processArtifacts}
          >
            {#if downloadCount > 0}
              Download {downloadCount} and verify all artifacts
            {:else}
              Verify all selected artifacts
            {/if}
          </button>
        </div>
      {:else if session.state === 'artifacts_verified'}
        <div class="step-content">
          <p>
            Vela verified the pinned Apple firmware and source references. The next stage is
            manual: rebuild the Rockbox helper from the exact pinned source revision by following
            Vela's corresponding-source recipe.
          </p>
          <div class="instruction-box warning">
            <AlertTriangle size={18} aria-hidden="true" />
            <span>
              Do not run opaque community binaries. Use only the helper produced by that rebuild;
              keep the helper archive, corresponding-source archive, and generated
              <code>BUILD-MANIFEST.txt</code> together and unchanged for Vela to verify.
            </span>
          </div>
          <button
            class="button primary"
            type="button"
            disabled={isBusy}
            on:click={() => advance('bootloader-await')}
          >
            Continue to Rockbox instructions
          </button>
        </div>
      {:else if session.state === 'awaiting_bootloader_install'}
        <div class="step-content">
          <p>
            Use the official Rockbox Utility 1.5.1 package Vela already downloaded and
            SHA-256-verified for this session. The bootloader installation remains entirely manual.
          </p>
          <ol class="instructions">
            <li>
              Manually open that verified Rockbox Utility 1.5.1 package, target this exact iPod,
              and install the <strong>bootloader only</strong>. Untick unrelated themes, fonts,
              games, and voice packages.
            </li>
            <li>
              Use Vela's corresponding-source recipe to rebuild the SysCfg helper from the exact
              pinned Rockbox source revision. Keep the exact generated
              <code>vela-ipod6g-syscfg-helper.zip</code>,
              <code>vela-ipod6g-helper-corresponding-source.tar.gz</code>, and
              <code>BUILD-MANIFEST.txt</code> outputs.
            </li>
            <li>
              Extract the generated helper archive and copy its <code>.rockbox</code> folder to the
              iPod root, then select all three unchanged generated outputs below.
            </li>
            <li>
              Safely eject, boot Rockbox, and confirm the helper appears under
              <strong>System → Debug (Keep Out!)</strong>.
            </li>
          </ol>
          <div class="instruction-box warning">
            <AlertTriangle size={18} aria-hidden="true" />
            <span>
              Vela does not execute Rockbox Utility, install the bootloader, change Windows
              drivers, or launch or control iTunes. Review and perform every external action yourself.
            </span>
          </div>
          <div class="instruction-box">
            <ShieldCheck size={18} aria-hidden="true" />
            <span>
              Before recording the manual install, Vela hashes the helper and corresponding-source
              archives, requires both hashes to match <code>BUILD-MANIFEST.txt</code>, checks the
              manifest's Rockbox commit, Olsro patch, Vela patch, and license fields against the
              audited lock, and inspects both archive structures.
            </span>
          </div>
          <div class="file-grid">
            <RecoveryFileChoice
              label="vela-ipod6g-syscfg-helper.zip"
              path={helperArchivePath}
              disabled={isBusy}
              on:choose={() => chooseFile('helper')}
            />
            <RecoveryFileChoice
              label="vela-ipod6g-helper-corresponding-source.tar.gz"
              path={helperSourcePath}
              disabled={isBusy}
              on:choose={() => chooseFile('helper-source')}
            />
            <RecoveryFileChoice
              label="BUILD-MANIFEST.txt"
              path={helperManifestPath}
              disabled={isBusy}
              on:choose={() => chooseFile('helper-manifest')}
            />
          </div>
          <label class="attestation">
            <input type="checkbox" bind:checked={bootloaderAttested} />
            <span>
              I manually used the already downloaded and SHA-256-verified official Rockbox Utility
              1.5.1 package to install only the bootloader on this exact iPod, rebuilt the helper
              from the exact pinned source with Vela's corresponding-source recipe, and selected
              the three unchanged generated outputs for Vela's locked hash, manifest, license, and
              archive-structure checks. Vela did not execute Rockbox Utility or the helper, change
              drivers, or control iTunes.
            </span>
          </label>
          <button
            class="button primary"
            type="button"
            disabled={isBusy || !bootloaderAttested || !helperOutputsReady}
            on:click={confirmBootloaderInstalled}
          >
            Record Rockbox installation
          </button>
        </div>
      {:else if session.state === 'awaiting_syscfg_dump'}
        <div class="step-content">
          <ol class="instructions">
            <li>
              On the iPod, open
              <strong>System → Debug (Keep Out!) → View and save SysCfg (from NOR to file)</strong>.
            </li>
            <li>Wait until the final line says <strong>Syscfg file has been saved!</strong>.</li>
            <li>
              Reconnect the iPod. Copy the dump to two different host folders or drives.
              Do not select shortcuts, symlinks, or two files in the same folder.
            </li>
          </ol>
          <div class="file-grid">
            <RecoveryFileChoice
              label="Original SysCfg dump"
              path={originalPath}
              disabled={isBusy}
              on:choose={() => chooseFile('original')}
            />
            <RecoveryFileChoice
              label="Independent host copy 1"
              path={originalBackupOne}
              disabled={isBusy}
              on:choose={() => chooseFile('copy-one')}
            />
            <RecoveryFileChoice
              label="Independent host copy 2"
              path={originalBackupTwo}
              disabled={isBusy}
              on:choose={() => chooseFile('copy-two')}
            />
          </div>
          {#if originalBackupOne && originalBackupTwo && parentPath(originalBackupOne).toLowerCase() === parentPath(originalBackupTwo).toLowerCase()}
            <p class="field-error">The two backup copies must be in different host folders.</p>
          {/if}
          <button
            class="button primary"
            type="button"
            disabled={isBusy || !originalFilesReady}
            on:click={verifyOriginalSysCfg}
          >
            Verify original and both copies
          </button>
        </div>
      {:else if session.state === 'original_syscfg_verified'}
        <div class="step-content">
          <p>
            The original dump and both independent copies match. Vela will now apply only the
            audited device-bound 2.0.2 transformation and verify the exact changed tags.
          </p>
          {#if !originalPath}
            <div class="instruction-box">
              <Info size={18} aria-hidden="true" />
              <span>This session was resumed. Select the same verified original SysCfg again.</span>
            </div>
            <RecoveryFileChoice
              label="Verified original SysCfg"
              path={originalPath}
              disabled={isBusy}
              on:choose={() => chooseFile('original')}
            />
          {/if}
          <button
            class="button primary"
            type="button"
            disabled={isBusy || !originalPath}
            on:click={buildCandidate}
          >
            Build and verify candidate
          </button>
        </div>
      {:else if session.state === 'candidate_syscfg_verified'}
        <div class="step-content">
          <div class="digest-card">
            <dl>
              <div>
                <dt>Source model</dt>
                <dd>{candidateString('source_model_number') || session.source_model_number}</dd>
              </div>
              <div>
                <dt>Changed tags</dt>
                <dd>{candidateTags().join(', ') || 'Stored in session'}</dd>
              </div>
              <div class="wide">
                <dt>Original SHA-256</dt>
                <dd><code>{candidateString('original_sha256')}</code></dd>
              </div>
              <div class="wide">
                <dt>Candidate SHA-256</dt>
                <dd><code>{candidateString('candidate_sha256')}</code></dd>
              </div>
            </dl>
          </div>
          {#if candidatePath}
            <div class="instruction-box">
              <FileCheck2 size={18} aria-hidden="true" />
              <span>
                Generated candidate: <code>{candidatePath}</code>. Copy it to the iPod as the exact
                root filename <code>syscfg</code>, then select that staged copy below.
              </span>
            </div>
          {:else}
            <div class="instruction-box warning">
              <Info size={18} aria-hidden="true" />
              <span>
                This session was resumed. Select the candidate you previously copied to the iPod;
                Vela will accept it only if its SHA-256 matches the persisted candidate.
              </span>
            </div>
          {/if}
          <RecoveryFileChoice
            label="Candidate staged on the iPod"
            path={stagedPath}
            disabled={isBusy}
            on:choose={() => chooseFile('staged')}
          />
          <button
            class="button primary"
            type="button"
            disabled={isBusy || !stagedPath}
            on:click={verifyStagedCandidate}
          >
            Reread and verify staged candidate
          </button>
        </div>
      {:else if session.state === 'candidate_staged'}
        <div class="step-content">
          <div class="danger-heading">
            <AlertTriangle size={24} aria-hidden="true" />
            <div>
              <h5>NOR commit is the irreversible physical boundary</h5>
              <p>
                Vela has verified the staged bytes but cannot observe the click-wheel action.
                Starting the manual stage locks cancellation conservatively.
              </p>
            </div>
          </div>
          <button
            class="button danger"
            type="button"
            disabled={isBusy}
            on:click={() => showNorDialog = true}
          >
            Review manual NOR flash
          </button>
        </div>
      {:else if session.state === 'awaiting_manual_nor_flash'}
        <div class="step-content">
          <div class="inline-alert danger">
            <LockKeyhole size={18} aria-hidden="true" />
            <div>
              <strong>Do not cancel or power off after selecting Flash SysCfg</strong>
              <span>Vela intentionally offers no cancellation from this point.</span>
            </div>
          </div>
          <ol class="instructions">
            <li>Disconnect the iPod from the computer and connect it to stable wall power.</li>
            <li>
              Boot Rockbox and open
              <strong>System → Debug (Keep Out!) → Flash SysCfg (from file to NOR)</strong>.
            </li>
            <li>Read the warning on the iPod. Use the click wheel to select the verified staged file.</li>
            <li>
              Do not interrupt power. The patched helper may succeed after any bounded number of
              attempts; wait for the success line reporting that SysCfg was flashed to NOR,
              regardless of the reported attempt count.
            </li>
            <li>
              After that success line, use
              <strong>View and save SysCfg (from NOR to file)</strong> again to export a fresh
              readback. Reconnect to Windows and select it below. The backend requires a byte-exact match.
            </li>
          </ol>
          <RecoveryFileChoice
            label="Fresh post-flash NOR readback"
            path={norReadbackPath}
            disabled={isBusy}
            on:choose={() => chooseFile('readback')}
          />
          <label class="attestation">
            <input type="checkbox" bind:checked={norReadbackAttested} />
            <span>
              I personally completed the Rockbox NOR flash on this iPod and selected a fresh
              post-flash readback.
            </span>
          </label>
          <button
            class="button danger"
            type="button"
            disabled={isBusy || !norReadbackPath || !norReadbackAttested}
            on:click={() => showNorAttestationDialog = true}
          >
            Verify NOR readback
          </button>
        </div>
      {:else if session.state === 'nor_flash_attested'}
        <div class="step-content">
          <p>
            The NOR readback exactly matched the verified candidate. The device must now enter
            Apple DFU so iTunes can perform the final erase and firmware restore.
          </p>
          <button
            class="button primary"
            type="button"
            disabled={isBusy}
            on:click={() => advance('dfu-await')}
          >
            Continue to DFU instructions
          </button>
        </div>
      {:else if session.state === 'awaiting_dfu'}
        <div class="step-content">
          <ol class="instructions">
            <li>Close Rockbox Utility. Do not ask Vela to automate iTunes.</li>
            <li>Connect the iPod directly to the Windows computer with a known-good USB cable.</li>
            <li>
              Hold <strong>Menu + Select (center)</strong> together. Keep holding through the reboot
              and for at least 8 seconds; the display should remain black in DFU.
            </li>
            <li>Release the buttons, then inspect USB below. Disconnect every other iPod first.</li>
          </ol>
          <button
            class="button secondary"
            type="button"
            disabled={usbInspectionBusy || isBusy}
            on:click={inspectRecoveryUSB}
          >
            <Usb size={16} aria-hidden="true" />
            {usbInspectionBusy ? 'Inspecting USB…' : 'Inspect recovery USB'}
          </button>
          {#if usbInspectionError}
            <p class="field-error">{usbInspectionError}</p>
          {/if}
          {#if usbInspection}
            <div class="usb-results">
              <div class="usb-summary">
                <strong>
                  {usbInspection.supported
                    ? `${usbInspection.devices.length} supported recovery device${usbInspection.devices.length === 1 ? '' : 's'}`
                    : 'Recovery USB inspection unavailable'}
                </strong>
                <span>
                  Platform {usbInspection.platform} ·
                  {usbInspectionUsable ? 'supported read-only inspection' : 'not supported'}
                </span>
              </div>
              {#if usbInspection.message}
                <p>{usbInspection.message}</p>
              {/if}
              {#each usbInspection.devices as recoveryDevice (recoveryDevice.instance_id || `${recoveryDevice.vendor_id}:${recoveryDevice.product_id}`)}
                <div class="usb-device">
                  <div>
                    <strong>{recoveryDevice.name || recoveryDevice.model_hint || 'Apple recovery device'}</strong>
                    <span>
                      {recoveryDevice.mode.toUpperCase()} · VID {recoveryDevice.vendor_id} ·
                      PID {recoveryDevice.product_id}
                    </span>
                  </div>
                  {#if usbInspectionUsable && usbInspection.devices.length === 1}
                    <button
                      class="button primary compact"
                      type="button"
                      disabled={isBusy}
                      on:click={() => useRecoveryUSBDevice(recoveryDevice)}
                    >
                      Use this USB identity
                    </button>
                  {/if}
                </div>
              {/each}
              {#if usbInspection.devices.length > 1}
                <p class="field-error">Disconnect extra iPods, then inspect again. Vela will not guess.</p>
              {/if}
            </div>
          {/if}
        </div>
      {:else if session.state === 'itunes_handoff'}
        <div class="step-content">
          <div class="inline-alert warning">
            <AlertTriangle size={18} aria-hidden="true" />
            <div>
              <strong>Vela will not open, downgrade, configure, or control iTunes</strong>
              <span>
                The pinned guide reports success with Windows iTunes 12.10.11.2 and reports that
                12.13.7.1 cannot restore this DFU device. Verify your chosen installer independently.
              </span>
            </div>
          </div>
          <ol class="instructions">
            <li>Open a Windows iTunes version you independently trust for this Classic.</li>
            <li>
              Keep the iPod connected in recovery/DFU. If iTunes first asks to download its
              recovery image, review and accept that prompt manually, then wait for the iPod to reappear.
            </li>
            <li>
              Hold <strong>Shift</strong> while choosing <strong>Restore iPod</strong>, then select
              the exact verified <code>{appleFirmware?.filename || 'iPod_35.2.0.2.ipsw'}</code>.
            </li>
            <li>Confirm its pinned SHA-256 below, review the erase warning, and do not disconnect until restore completes.</li>
          </ol>
          {#if appleFirmware}
            <div class="digest-line">
              <span>Verified Apple IPSW SHA-256</span>
              <code>{appleFirmware.sha256}</code>
            </div>
          {/if}
          <label class="attestation">
            <input type="checkbox" bind:checked={itunesAttested} />
            <span>
              I will control iTunes myself and select the exact hash-verified Apple IPSW shown here.
            </span>
          </label>
          <button
            class="button primary"
            type="button"
            disabled={isBusy || !itunesAttested || !appleFirmware}
            on:click={confirmItunesHandoff}
          >
            Record manual iTunes handoff
          </button>
        </div>
      {:else if session.state === 'awaiting_restore'}
        <div class="step-content">
          <p>
            Vela is waiting. Complete the restore entirely in iTunes, keep USB power stable, and
            wait for the iPod to reboot. This button does not control iTunes.
          </p>
          <label class="attestation">
            <input type="checkbox" bind:checked={restoreAttested} />
            <span>
              iTunes reported a successful restore, the iPod rebooted, and I did not interrupt it.
            </span>
          </label>
          <button
            class="button primary"
            type="button"
            disabled={isBusy || !restoreAttested}
            on:click={confirmRestoreFinished}
          >
            Record restore completion
          </button>
        </div>
      {:else if session.state === 'postflight_verification'}
        <div class="step-content">
          <p>
            Wait for the restored iPod to mount in Windows. Vela will independently identify the
            device, require the same recovery identity, and verify the target model and firmware.
          </p>
          <div class:ok={connected} class="reconnect-card">
            {#if connected}
              <CheckCircle2 size={20} aria-hidden="true" />
              <div>
                <strong>{device.name} reconnected</strong>
                <span>{device.model_number} · firmware {device.firmware} · {device.path}</span>
              </div>
            {:else}
              <LoaderCircle size={20} class="spin" aria-hidden="true" />
              <div>
                <strong>Waiting for the mounted iPod</strong>
                <span>The session remains persisted. Do not select a different device.</span>
              </div>
            {/if}
          </div>
          <button
            class="button primary"
            type="button"
            disabled={isBusy || !connected}
            on:click={verifyPostflight}
          >
            Verify restored identity and firmware
          </button>
        </div>
      {:else if session.state === 'complete'}
        <div class="completion-panel">
          <CheckCircle2 size={34} aria-hidden="true" />
          <div>
            <h5>Unlock completed and postflight verified</h5>
            <p>
              The backend verified the restored target identity and firmware. Recreate your
              iPod library through the normal sync workflow. The iTunes restore removed the
              temporary Rockbox bootloader and helper.
            </p>
            <button class="button secondary" type="button" on:click={returnToEligibility}>
              Return to eligibility
            </button>
          </div>
        </div>
      {:else if session.state === 'recovery_required'}
        <div class="step-content">
          <div class="inline-alert danger">
            <RotateCcw size={18} aria-hidden="true" />
            <div>
              <strong>Stop the normal workflow</strong>
              <span>
                Do not flash again. Preserve all original SysCfg copies and use the state-specific
                recovery path below.
              </span>
            </div>
          </div>
          <dl class="recovery-details">
            <div>
              <dt>Resume point</dt>
              <dd>{session.recovery_resume_state?.replace(/_/g, ' ') || 'Unavailable'}</dd>
            </div>
            <div>
              <dt>NOR committed</dt>
              <dd>{session.nor_committed ? 'Yes — cancellation is forbidden' : 'No'}</dd>
            </div>
          </dl>
          <ol class="instructions">
            <li>Keep the two verified original SysCfg copies untouched.</li>
            <li>If the screen is black, inspect recovery USB before assuming the iPod is dead.</li>
            <li>
              If NOR was committed, use Apple DFU and the verified IPSW path; do not attempt to
              cancel or write another unverified SysCfg.
            </li>
            <li>Resume only after the physical device is stable and matches this session.</li>
          </ol>
          <button
            class="button secondary"
            type="button"
            disabled={usbInspectionBusy}
            on:click={inspectRecoveryUSB}
          >
            Inspect recovery USB
          </button>
          {#if usbInspection}
            <p class="technical-note">
              USB result: {usbInspection.devices.length} supported device(s);
              {usbInspection.message || 'inspection complete'}.
            </p>
          {/if}
          <label class="attestation">
            <input type="checkbox" bind:checked={recoveryReady} />
            <span>I resolved the reported condition and am ready to return to the persisted resume point.</span>
          </label>
          <button
            class="button primary"
            type="button"
            disabled={isBusy || !recoveryReady}
            on:click={resumeRecovery}
          >
            Resume persisted session
          </button>
        </div>
      {:else if session.state === 'cancelled'}
        <div class="completion-panel neutral">
          <Circle size={32} aria-hidden="true" />
          <div>
            <h5>Session cancelled before NOR commit</h5>
            <p>No further unlock action will be taken for this session.</p>
            <button class="button secondary" type="button" on:click={returnToEligibility}>
              Return to eligibility
            </button>
          </div>
        </div>
      {/if}
    </article>

    {#if session && !session.terminal}
      <footer class="session-actions">
        <div>
          {#if cancellationLocked}
            <LockKeyhole size={15} aria-hidden="true" />
            <span>Cancellation is unavailable at or beyond the possible NOR commit boundary.</span>
          {:else}
            <ShieldCheck size={15} aria-hidden="true" />
            <span>Session changes are revision-checked and persisted by the backend.</span>
          {/if}
        </div>
        <div class="button-row">
          {#if canOfferCancel}
            <button
              class="button secondary"
              type="button"
              disabled={isBusy}
              on:click={() => showCancelDialog = true}
            >
              Cancel before NOR
            </button>
          {/if}
          {#if session.state !== 'recovery_required'}
            <button
              class="button warning"
              type="button"
              disabled={isBusy}
              on:click={() => showRecoveryDialog = true}
            >
              I need recovery guidance
            </button>
          {/if}
        </div>
      </footer>
    {/if}
  {/if}
</section>

{#if showStartDialog && profile}
  <ConfirmationDialog
    title="Create the experimental unlock session?"
    description={`This session is bound to ${profile.generation} ${profile.model_number}. It will require a fresh backup, manual NOR flash, DFU, and a user-controlled iTunes erase and restore.`}
    confirmLabel="Create unlock session"
    cancelLabel="Go back"
    destructive={true}
    requiredPhrase={`UNLOCK ${profile.model_number}`}
    acknowledgement="I verified the model and saved my important media elsewhere."
    busy={isBusy}
    on:confirm={startUnlock}
    on:cancel={() => showStartDialog = false}
  />
{/if}

{#if showNorDialog}
  <ConfirmationDialog
    title="Enter the manual NOR commit stage?"
    description="After this confirmation, Vela hides cancellation because it cannot observe when you press Flash SysCfg on the iPod. Stable wall power and a fresh post-flash readback are required."
    confirmLabel="Lock cancellation and continue"
    cancelLabel="Not yet"
    destructive={true}
    requiredPhrase="FLASH NOR"
    acknowledgement="I have the verified original copies and stable wall power."
    busy={isBusy}
    on:confirm={beginManualNor}
    on:cancel={() => showNorDialog = false}
  />
{/if}

{#if showNorAttestationDialog}
  <ConfirmationDialog
    title="Attest the physical NOR flash?"
    description="The backend will read the selected post-flash dump twice and require an exact match to the device-bound candidate. This records NOR as committed and permanently disables cancellation."
    confirmLabel="Verify and record NOR commit"
    cancelLabel="Review again"
    destructive={true}
    requiredPhrase="NOR COMMITTED"
    acknowledgement="The selected file is a fresh readback made after the physical flash."
    busy={isBusy}
    on:confirm={attestNorReadback}
    on:cancel={() => showNorAttestationDialog = false}
  />
{/if}

{#if showCancelDialog}
  <ConfirmationDialog
    title="Cancel this pre-NOR session?"
    description="Cancellation is allowed only because this session has not entered the manual NOR commit stage. The existing backup and selected source files are not deleted."
    confirmLabel="Cancel unlock session"
    cancelLabel="Keep session"
    destructive={true}
    requiredPhrase="CANCEL UNLOCK"
    busy={isBusy}
    on:confirm={cancelSession}
    on:cancel={() => showCancelDialog = false}
  />
{/if}

{#if showRecoveryDialog}
  <ConfirmationDialog
    title="Pause and enter recovery guidance?"
    description="This persists the current resume point and stops the normal step sequence. It does not undo a NOR write or automate iTunes."
    confirmLabel="Enter recovery guidance"
    cancelLabel="Continue current step"
    destructive={false}
    acknowledgement="I will stop normal actions until the reported condition is resolved."
    busy={isBusy}
    on:confirm={requestRecovery}
    on:cancel={() => showRecoveryDialog = false}
  />
{/if}

<style>
  .unlock-shell {
    --surface-soft: var(--surface-2);
    --danger: var(--error-color);
    --danger-line: var(--error-border);
    --danger-soft: var(--error-soft);
    --warning: var(--warning-color);
    --warning-line: var(--warning-border);
    --warning-soft: var(--warning-soft);
    --success: var(--success-color);
    --success-line: var(--success-border);
    --success-soft: var(--success-soft);
    display: grid;
    gap: 16px;
    min-width: 0;
  }

  .unlock-header,
  .session-topline,
  .session-actions,
  .start-row {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 20px;
  }

  .unlock-header h3 {
    margin: 6px 0 4px;
    color: var(--text);
    font-size: 20px;
    letter-spacing: -0.02em;
  }

  .unlock-header p {
    max-width: 760px;
    margin: 0;
    color: var(--muted);
    font-size: 13px;
    line-height: 1.55;
  }

  .eyebrow-row,
  .experimental-badge,
  .header-actions,
  .connection-strip,
  .identity-heading,
  .boundary-badge,
  .artifact-heading,
  .artifact-actions,
  .inline-alert,
  .instruction-box,
  .completion-panel,
  .reconnect-card,
  .usb-device,
  .session-actions > div,
  .compact-checks li {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .eyebrow-row {
    color: var(--muted);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  .experimental-badge {
    border: 1px solid var(--warning-line, var(--line));
    border-radius: 999px;
    padding: 4px 8px;
    background: var(--warning-soft, var(--surface-soft));
    color: var(--warning, var(--text));
  }

  .button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    min-height: 36px;
    border: 1px solid transparent;
    border-radius: var(--radius-sm, 8px);
    padding: 8px 13px;
    font: inherit;
    font-size: 12px;
    font-weight: 700;
    cursor: pointer;
    transition: border-color 120ms ease, background 120ms ease, opacity 120ms ease;
  }

  .button:focus-visible,
  .choice-button:focus-visible,
  input:focus-visible,
  h4:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  .button:disabled,
  .choice-button:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }

  .button.primary {
    background: var(--accent);
    color: var(--accent-contrast, var(--bg));
  }

  .button.secondary {
    border-color: var(--line);
    background: var(--surface);
    color: var(--text);
  }

  .button.danger {
    border-color: var(--danger-line, var(--line));
    background: var(--danger, var(--accent));
    color: var(--danger-contrast, var(--bg));
  }

  .button.warning {
    border-color: var(--warning-line, var(--line));
    background: var(--warning-soft, var(--surface-soft));
    color: var(--warning, var(--text));
  }

  .button.compact {
    min-height: 30px;
    padding: 5px 9px;
  }

  .header-actions button > span {
    display: inline-grid;
  }

  .connection-strip {
    min-width: 0;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm, 8px);
    padding: 10px 12px;
    background: var(--surface-soft);
    color: var(--muted);
    font-size: 12px;
  }

  .connection-strip code {
    color: var(--text);
  }

  .connection-strip.reconnect-warning {
    border-color: var(--warning-line, var(--line));
    background: var(--warning-soft, var(--surface-soft));
    color: var(--warning, var(--text));
  }

  .state-panel,
  .identity-card,
  .disclosure-card,
  .start-row,
  .step-card,
  .session-topline,
  .session-actions {
    border: 1px solid var(--line);
    border-radius: var(--radius-md, 12px);
    background: var(--surface);
  }

  .state-panel {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    padding: 18px;
    color: var(--muted);
  }

  .state-panel.centered {
    align-items: center;
  }

  .state-panel.blocked {
    border-color: var(--danger-line, var(--line));
    background: var(--danger-soft, var(--surface-soft));
    color: var(--danger, var(--text));
  }

  .state-panel.eligible {
    border-color: var(--success-line, var(--line));
    background: var(--success-soft, var(--surface-soft));
    color: var(--success, var(--text));
  }

  .state-panel h4,
  .danger-heading h4,
  .danger-heading h5,
  .completion-panel h5 {
    margin: 0 0 5px;
    color: var(--text);
    font-size: 14px;
  }

  .state-panel p,
  .danger-heading p,
  .completion-panel p {
    margin: 0;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.55;
  }

  .identity-card {
    padding: 15px;
  }

  .identity-heading {
    margin-bottom: 14px;
  }

  .identity-heading div {
    display: grid;
    gap: 2px;
  }

  .identity-heading strong {
    color: var(--text);
    font-size: 13px;
  }

  .identity-heading span {
    color: var(--muted);
    font-size: 11px;
  }

  .identity-card dl,
  .artifact-meta,
  .digest-card dl,
  .recovery-details {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 10px;
    margin: 0;
  }

  .identity-card dl div,
  .artifact-meta div,
  .digest-card dl div,
  .recovery-details div {
    min-width: 0;
  }

  dt {
    margin-bottom: 3px;
    color: var(--muted);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }

  dd {
    min-width: 0;
    margin: 0;
    overflow-wrap: anywhere;
    color: var(--text);
    font-size: 12px;
  }

  .issue-list,
  .compact-checks,
  .instructions {
    margin: 12px 0 0;
    padding-left: 18px;
  }

  .issue-list li {
    margin-top: 8px;
  }

  .issue-list strong,
  .issue-list span {
    display: block;
  }

  .issue-list strong {
    color: var(--text);
    font-size: 11px;
    text-transform: capitalize;
  }

  .issue-list span,
  .technical-note {
    color: var(--muted);
    font-size: 11px;
    line-height: 1.45;
  }

  .compact-checks {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 7px 20px;
    padding-left: 0;
    list-style: none;
    color: var(--text);
    font-size: 11px;
  }

  .start-row,
  .session-topline,
  .session-actions {
    padding: 14px 15px;
  }

  .start-row > div,
  .session-topline > div:first-child {
    display: grid;
    gap: 3px;
  }

  .start-row strong,
  .session-topline strong {
    color: var(--text);
    font-size: 12px;
  }

  .start-row span,
  .session-topline span {
    color: var(--muted);
    font-size: 11px;
  }

  .disclosure-card {
    display: grid;
    gap: 16px;
    padding: 18px;
  }

  .danger-heading {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    color: var(--danger, var(--text));
  }

  .acknowledgements {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 9px 18px;
  }

  .acknowledgements label,
  .attestation {
    display: flex;
    align-items: flex-start;
    gap: 9px;
    color: var(--text);
    font-size: 12px;
    line-height: 1.45;
    cursor: pointer;
  }

  input[type='checkbox'] {
    width: 16px;
    height: 16px;
    margin: 1px 0 0;
    accent-color: var(--accent);
  }

  .button-row {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 8px;
  }

  .session-kicker {
    color: var(--accent) !important;
    font-weight: 800;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }

  .boundary-badge {
    flex: 0 0 auto;
    border: 1px solid var(--success-line, var(--line));
    border-radius: 999px;
    padding: 6px 9px;
    background: var(--success-soft, var(--surface-soft));
    color: var(--success, var(--text));
    font-size: 11px;
    font-weight: 700;
  }

  .boundary-badge.locked {
    border-color: var(--danger-line, var(--line));
    background: var(--danger-soft, var(--surface-soft));
    color: var(--danger, var(--text));
  }

  .stepper {
    display: flex;
    gap: 0;
    margin: 0;
    padding: 3px 2px 10px;
    overflow-x: auto;
    list-style: none;
  }

  .stepper li {
    position: relative;
    display: grid;
    flex: 0 0 92px;
    justify-items: center;
    gap: 6px;
    color: var(--muted);
    font-size: 10px;
    text-align: center;
  }

  .stepper li::before {
    position: absolute;
    top: 11px;
    right: 50%;
    left: -50%;
    z-index: 0;
    height: 1px;
    background: var(--line);
    content: '';
  }

  .stepper li:first-child::before {
    display: none;
  }

  .step-marker {
    position: relative;
    z-index: 1;
    display: grid;
    width: 22px;
    height: 22px;
    place-items: center;
    border: 1px solid var(--line);
    border-radius: 50%;
    background: var(--surface);
    color: var(--muted);
    font-size: 10px;
    font-weight: 800;
  }

  .stepper li.complete,
  .stepper li.current {
    color: var(--text);
  }

  .stepper li.complete::before,
  .stepper li.current::before {
    background: var(--accent);
  }

  .stepper li.complete .step-marker {
    border-color: var(--success, var(--accent));
    background: var(--success, var(--accent));
    color: var(--success-contrast, var(--bg));
  }

  .stepper li.current .step-marker {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-soft, var(--surface-soft));
    color: var(--accent);
  }

  .inline-alert {
    align-items: flex-start;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm, 8px);
    padding: 11px 12px;
    background: var(--surface-soft);
    color: var(--muted);
  }

  .inline-alert > div {
    display: grid;
    gap: 2px;
  }

  .inline-alert strong {
    color: var(--text);
    font-size: 12px;
  }

  .inline-alert span {
    font-size: 11px;
    line-height: 1.45;
  }

  .inline-alert.error,
  .inline-alert.danger {
    border-color: var(--danger-line, var(--line));
    background: var(--danger-soft, var(--surface-soft));
    color: var(--danger, var(--text));
  }

  .inline-alert.warning,
  .instruction-box.warning {
    border-color: var(--warning-line, var(--line));
    background: var(--warning-soft, var(--surface-soft));
    color: var(--warning, var(--text));
  }

  .step-card {
    overflow: hidden;
  }

  .step-card-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    border-bottom: 1px solid var(--line);
    padding: 14px 16px;
    background: var(--surface-soft);
  }

  .step-card-header span {
    color: var(--muted);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }

  .step-card-header h4 {
    margin: 3px 0 0;
    color: var(--text);
    font-size: 15px;
  }

  .state-code {
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 4px 7px;
    background: var(--surface);
  }

  .step-content {
    display: grid;
    justify-items: start;
    gap: 14px;
    padding: 18px;
  }

  .step-content > p {
    max-width: 820px;
    margin: 0;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.6;
  }

  .instruction-box {
    align-items: flex-start;
    width: 100%;
    box-sizing: border-box;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm, 8px);
    padding: 11px 12px;
    background: var(--surface-soft);
    color: var(--muted);
    font-size: 11px;
    line-height: 1.5;
  }

  .instructions {
    max-width: 820px;
    margin: 0;
    color: var(--text);
    font-size: 12px;
    line-height: 1.6;
  }

  .instructions li + li {
    margin-top: 7px;
  }

  .artifact-list,
  .file-grid {
    display: grid;
    width: 100%;
    gap: 10px;
  }

  .artifact-list {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .artifact-card,
  .digest-card,
  .usb-results,
  .reconnect-card {
    min-width: 0;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm, 8px);
    padding: 13px;
    background: var(--surface-soft);
  }

  .artifact-heading div {
    display: grid;
    gap: 2px;
  }

  .artifact-heading strong {
    color: var(--text);
    font-size: 12px;
  }

  .artifact-heading span {
    color: var(--muted);
    font-size: 10px;
  }

  .artifact-meta {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    margin-top: 12px;
  }

  .artifact-meta .wide,
  .digest-card .wide {
    grid-column: 1 / -1;
  }

  code {
    overflow-wrap: anywhere;
    color: var(--text);
    font-family: var(--font-mono, monospace);
    font-size: 0.95em;
  }

  .artifact-actions {
    flex-wrap: wrap;
    margin-top: 12px;
  }

  .choice-button {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    min-height: 31px;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm, 8px);
    padding: 6px 9px;
    background: var(--surface);
    color: var(--text);
    font: inherit;
    font-size: 10px;
    font-weight: 700;
    cursor: pointer;
  }

  .choice-button.active {
    border-color: var(--accent);
    background: var(--accent-soft, var(--surface-soft));
    color: var(--accent);
  }

  .selection-note,
  .path-value,
  .field-error {
    margin: 9px 0 0;
    color: var(--muted);
    font-size: 10px;
    line-height: 1.45;
  }

  .field-error {
    color: var(--danger, var(--text));
    font-weight: 700;
  }

  .attestation {
    max-width: 760px;
    border: 1px solid var(--line);
    border-radius: var(--radius-sm, 8px);
    padding: 11px 12px;
    background: var(--surface-soft);
  }

  .digest-card,
  .usb-results {
    width: 100%;
    box-sizing: border-box;
  }

  .digest-card dl {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .digest-line {
    display: grid;
    width: 100%;
    gap: 4px;
  }

  .digest-line span {
    color: var(--muted);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
  }

  .usb-results {
    display: grid;
    gap: 10px;
  }

  .usb-summary {
    display: grid;
    gap: 2px;
  }

  .usb-summary strong,
  .usb-device strong,
  .reconnect-card strong {
    color: var(--text);
    font-size: 12px;
  }

  .usb-summary span,
  .usb-device span,
  .reconnect-card span {
    color: var(--muted);
    font-size: 10px;
  }

  .usb-results > p {
    margin: 0;
    color: var(--muted);
    font-size: 11px;
  }

  .usb-device {
    justify-content: space-between;
    border-top: 1px solid var(--line);
    padding-top: 10px;
  }

  .usb-device > div,
  .reconnect-card > div {
    display: grid;
    gap: 2px;
  }

  .reconnect-card {
    width: 100%;
    box-sizing: border-box;
  }

  .reconnect-card.ok {
    border-color: var(--success-line, var(--line));
    background: var(--success-soft, var(--surface-soft));
    color: var(--success, var(--text));
  }

  .completion-panel {
    align-items: flex-start;
    padding: 24px;
    color: var(--success, var(--text));
  }

  .completion-panel.neutral {
    color: var(--muted);
  }

  .recovery-details {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    width: 100%;
  }

  .session-actions {
    align-items: center;
  }

  .session-actions > div:first-child {
    color: var(--muted);
    font-size: 11px;
  }

  .spin {
    animation: spin 900ms linear infinite;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  @media (max-width: 920px) {
    .unlock-header,
    .session-actions,
    .start-row {
      align-items: stretch;
      flex-direction: column;
    }

    .identity-card dl {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .artifact-list {
      grid-template-columns: 1fr;
    }
  }

  @media (max-width: 640px) {
    .acknowledgements,
    .compact-checks,
    .identity-card dl,
    .digest-card dl,
    .recovery-details {
      grid-template-columns: 1fr;
    }

    .session-topline,
    .usb-device {
      align-items: flex-start;
      flex-direction: column;
    }

    .state-code {
      display: none;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .button {
      transition: none;
    }

    .spin {
      animation: none;
    }
  }
</style>
