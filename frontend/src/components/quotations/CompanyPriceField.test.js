import { fireEvent, render, screen } from '@testing-library/react';
import CompanyPriceField, { manualPricePatch } from './CompanyPriceField';

const source = { kind: 'history', history_id: 44, product_id: 8, amount: '20', currency: 'AED', unit: 'box', basis: 'quoted' };
const filled = { product: 8, unit: 'box', unit_price: '20', price_provenance: source };

test('large corrections keep the entered price and expose a compact match check', () => {
  const onPatch = jest.fn(), onWrongProduct = jest.fn();
  const correction = { ...filled, ...manualPricePatch(filled, '5') };
  render(<CompanyPriceField draft={correction} aria-label="Unit price" onPatch={onPatch} onWrongProduct={onWrongProduct} />);
  expect(screen.getByLabelText('Unit price')).toHaveValue('5');
  fireEvent.click(screen.getByRole('button', { name: 'Large price change: check product match' }));
  expect(screen.getByText(/differs by 50% or more/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Same product, new price' }));
  expect(onPatch).toHaveBeenCalledWith(expect.objectContaining({ unit_price: '5', price_reviewed: true, price_review_required: false }));
  fireEvent.click(screen.getByRole('button', { name: 'Wrong product' }));
  expect(onWrongProduct).toHaveBeenCalledTimes(1);
});

test('small discounts and blank keystrokes do not persist a spurious large-change warning', () => {
  expect(manualPricePatch(filled, '18').price_review_required).toBe(false);
  const intermediate = { ...filled, ...manualPricePatch(filled, '1') };
  expect(intermediate.price_review_required).toBe(true);
  expect(manualPricePatch(intermediate, '18').price_review_required).toBe(false);
  expect(manualPricePatch(intermediate, '').price_review_required).toBe(false);
});

test('editing after a confirmation requires fresh review for another drastic change', () => {
  const reviewed = { ...filled, unit_price: '5', price_reviewed: true,
    price_provenance: { kind: 'manual', previous_source: source, match_check: { status: 'confirmed', entered_amount: '5' } } };
  expect(manualPricePatch(reviewed, '6').price_review_required).toBe(false);
  expect(manualPricePatch(reviewed, '100')).toEqual(expect.objectContaining({ price_review_required: true, price_reviewed: false }));
});

test('a pack-to-piece change retains unit review without declaring a price mismatch', () => {
  const changedUnit = { ...filled, ...manualPricePatch(filled, '5'), unit: 'piece', price_context_changed: true };
  const patch = manualPricePatch(changedUnit, '1');
  expect(patch.price_review_required).toBe(true);
  expect(patch.price_provenance.match_check.status).toBe('confirmed');
});
