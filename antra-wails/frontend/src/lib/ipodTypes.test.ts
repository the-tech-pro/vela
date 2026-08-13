import { describe, expect, it } from 'vitest';
import { IPodBackendError, parseIPodResponse } from './ipodTypes';

describe('parseIPodResponse', () => {
  it('preserves stable backend error codes and user-facing messages', () => {
    expect.assertions(3);
    try {
      parseIPodResponse(JSON.stringify({
        error: 'The iPod volume is mounted read-only.',
        code: 'volume_read_only',
        message: 'The iPod volume is mounted read-only.',
      }));
    } catch (error) {
      expect(error).toBeInstanceOf(IPodBackendError);
      expect((error as IPodBackendError).code).toBe('volume_read_only');
      expect((error as Error).message).toBe('The iPod volume is mounted read-only.');
    }
  });
});
