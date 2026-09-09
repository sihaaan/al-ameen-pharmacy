import { fireEvent, render, screen, within } from '@testing-library/react';
import OrderLpoDetails from './OrderLpoDetails';

const record = { id: 9, lpo_number: 'LPO-123', lpo_date: '2026-09-09', notes: '', status: 'confirmed', warnings: [
  'Verify formula cells.', 'Verify hidden rows.', 'Verify merged cells.',
  'Stopped reading after 500 rows.', 'Additional detail.',
] };

test('reviews an existing LPO without an upload and preserves all warnings', () => {
  const onReview = jest.fn();
  render(<OrderLpoDetails records={[record]} selectedId={9} onReview={onReview} onDirtyChange={jest.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Review saved LPO items' }));
  expect(onReview).toHaveBeenCalledWith(9);
  const warnings = screen.getByRole('alert', { name: 'LPO attachment warnings' });
  expect(within(warnings).getByText('Stopped reading after 500 rows.')).toBeVisible();
  fireEvent.click(within(warnings).getByText('Show 1 more warning'));
  expect(within(warnings).getByText('Additional detail.')).toBeVisible();
});

test('saves only reference metadata and blocks switching sources while it is dirty', () => {
  const onSave = jest.fn();
  render(<OrderLpoDetails records={[record]} selectedId={9} onSave={onSave} onDirtyChange={jest.fn()} />);
  fireEvent.click(screen.getByText('LPO reference & notes'));
  fireEvent.change(screen.getByLabelText('LPO number'), { target: { value: 'LPO-CORRECTED' } });
  expect(screen.getByLabelText('Saved customer LPO')).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Review saved LPO items' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Save LPO details' }));
  expect(onSave).toHaveBeenCalledWith(9, { lpo_number: 'LPO-CORRECTED', lpo_date: '2026-09-09', notes: '' });
});
