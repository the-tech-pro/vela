<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import BackupInventory from './BackupInventory.svelte';
  import RecoveryPanel from './RecoveryPanel.svelte';
  import RestoreMigrationWizard from './RestoreMigrationWizard.svelte';
  import type {
    IPodBackupDeviceArchive, IPodBackupSnapshot, IPodBackupSnapshotDetails,
    IPodDevice, IPodEventPayload,
  } from '../ipodTypes';

  export let device: IPodDevice;
  export let ipodEvent: IPodEventPayload | null = null;
  export let operationBusy = false;
  export let demoMode = false;

  const dispatch = createEventDispatcher<{
    announce: { message: string };
  }>();
  let selectedArchive: IPodBackupDeviceArchive | null = null;
  let selectedSnapshot: IPodBackupSnapshot | null = null;
  let selectedDetails: IPodBackupSnapshotDetails | null = null;

  function handleSelection(event: CustomEvent<{
    archive: IPodBackupDeviceArchive | null;
    snapshot: IPodBackupSnapshot | null;
    details: IPodBackupSnapshotDetails | null;
  }>) {
    selectedArchive = event.detail.archive;
    selectedSnapshot = event.detail.snapshot;
    selectedDetails = event.detail.details;
  }

  function forwardAnnouncement(event: CustomEvent<{ message: string }>) {
    dispatch('announce', event.detail);
  }
</script>

<div class="backups-recovery">
  <BackupInventory
    {device}
    {ipodEvent}
    {operationBusy}
    {demoMode}
    on:select={handleSelection}
    on:announce={forwardAnnouncement}
  />

  <div class="recovery-layout">
    <RestoreMigrationWizard
      {device}
      archive={selectedArchive}
      snapshot={selectedSnapshot}
      details={selectedDetails}
      {operationBusy}
      {demoMode}
      on:announce={forwardAnnouncement}
    />
    <RecoveryPanel {ipodEvent} {demoMode} on:announce={forwardAnnouncement} />
  </div>
</div>

<style>
  .backups-recovery {
    display: grid;
    gap: 14px;
  }
  .recovery-layout {
    display: grid;
    grid-template-columns: minmax(420px, 1.25fr) minmax(300px, .75fr);
    align-items: start;
    gap: 14px;
  }
  @media (max-width: 980px) {
    .recovery-layout { grid-template-columns: 1fr; }
  }
  @media (max-width: 720px) {
    .backups-recovery,
    .recovery-layout {
      gap: 12px;
    }
  }
</style>
