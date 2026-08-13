import { render } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import IPodManager from './IPodManager.svelte';
import type { IPodDevice } from './ipodTypes';

function device(overrides: Partial<IPodDevice> = {}): IPodDevice {
  return {
    device_id: 'device-1',
    path: 'E:\\',
    name: 'Test iPod',
    model_family: 'iPod Classic',
    generation: '6th Generation',
    model_number: 'MB145',
    capacity: '80 GB',
    serial: 'TEST',
    firewire_guid: '0011223344556677',
    firmware: '1.1.2',
    filesystem_type: 'FAT32',
    filesystem_accessible: true,
    raw_read_only: false,
    access_state: 'mounted',
    access_message: '',
    raw_device_path: '',
    volume_identity_key: 'volume-1',
    disk_size_gb: 80,
    free_space_gb: 30,
    uses_sqlite_db: false,
    checksum_type: 2,
    audio_codecs: ['AAC', 'MP3'],
    podcasts_supported: true,
    voice_memos_supported: true,
    supports_sparse_artwork: false,
    browse_only: false,
    needs_preparation: false,
    write_ready: true,
    filesystem_read_only: false,
    write_block_code: '',
    write_block_reason: '',
    ...overrides,
  };
}

describe('IPodManager write safety', () => {
  it('keeps mounted read-only devices browsable while disabling mutations', () => {
    const { getByRole, getByText } = render(IPodManager, {
      device: device({
        write_ready: false,
        filesystem_read_only: true,
        write_block_code: 'filesystem_read_only',
        write_block_reason: 'Remount this volume with write access.',
      }),
      demoMode: true,
    });

    expect((getByRole('tab', { name: /tracks/i }) as HTMLButtonElement).disabled).toBe(false);
    expect((getByRole('button', { name: /safe eject/i }) as HTMLButtonElement).disabled).toBe(true);
    expect(getByText('Remount this volume with write access.')).toBeTruthy();
  });

  it('enables reviewed write entry points for write-ready devices', () => {
    const { getByRole, getByText } = render(IPodManager, {
      device: device(),
      demoMode: true,
    });

    expect((getByRole('button', { name: /safe eject/i }) as HTMLButtonElement).disabled).toBe(false);
    expect((getByRole('tab', { name: /^sync$/i }) as HTMLButtonElement).disabled).toBe(false);
    expect(getByText('Verified for reviewed sync')).toBeTruthy();
  });
});
