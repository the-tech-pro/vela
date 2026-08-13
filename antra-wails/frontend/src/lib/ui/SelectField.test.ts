import { fireEvent, render } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import SelectField from './SelectField.svelte';

const options = [
  { value: 'albums', label: 'Albums' },
  { value: 'artists', label: 'Artists' },
  { value: 'downloads', label: 'Downloads' },
];

describe('SelectField', () => {
  it('supports keyboard selection and dispatches change', async () => {
    const { component, getByRole, queryByRole } = render(SelectField, {
      id: 'destination',
      label: 'Destination',
      value: 'albums',
      options,
    });
    const changed = vi.fn();
    component.$on('change', changed);
    const trigger = getByRole('combobox');

    await fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    await fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    await fireEvent.keyDown(trigger, { key: 'Enter' });

    expect(changed).toHaveBeenCalledTimes(1);
    expect(changed.mock.calls[0][0].detail).toBe('artists');
    expect(queryByRole('listbox')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('supports typeahead, escape, and disabled state', async () => {
    const { getByRole, rerender } = render(SelectField, {
      id: 'filter',
      ariaLabel: 'Filter',
      value: '',
      options,
    });
    const trigger = getByRole('combobox');
    await fireEvent.click(trigger);
    await fireEvent.keyDown(trigger, { key: 'd' });
    expect(trigger.getAttribute('aria-activedescendant')).toContain('option-2');
    await fireEvent.keyDown(trigger, { key: 'Escape' });
    expect(trigger.getAttribute('aria-expanded')).toBe('false');

    await rerender({ id: 'filter', ariaLabel: 'Filter', value: '', options, disabled: true });
    expect((getByRole('combobox') as HTMLButtonElement).disabled).toBe(true);
  });
});
