import { fireEvent, render, screen, within } from '@testing-library/react';
import ProductSelect, { buildProductCatalogue } from './ProductSelect';

const products = Array.from({ length: 5400 }, (_, index) => ({
  id: index + 1,
  name: `Product ${String(index + 1).padStart(4, '0')}`,
}));
products[5399] = { id: 5400, name: 'Nitrile gloves', brand_name: 'Medline', sku: 'GLV-5400', unit: 'box' };

const defaultProps = {
  catalogue: buildProductCatalogue(products, []),
  label: 'Product',
  searchLabel: 'Search products',
  value: '',
  onChange: jest.fn(),
};

test('bounds a full catalogue and finds products beyond the initial results by name, brand, SKU and unit', () => {
  const onChange = jest.fn();
  render(<ProductSelect {...defaultProps} onChange={onChange} />);
  const select = screen.getByRole('combobox', { name: 'Product' });
  const search = screen.getByRole('searchbox', { name: 'Search products' });
  expect(within(select).getAllByRole('option')).toHaveLength(21);
  expect(screen.getByText('Showing 20 of 5400 matches. Type to narrow the list.')).toBeInTheDocument();

  ['GLOVES', 'medline', 'GLV-5400', 'medline box'].forEach((query) => {
    fireEvent.change(search, { target: { value: query } });
    expect(within(select).getAllByRole('option')).toHaveLength(2);
    expect(within(select).getByRole('option', { name: 'Nitrile gloves' })).toBeInTheDocument();
  });
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.change(select, { target: { value: '5400' } });
  expect(onChange).toHaveBeenCalledWith('5400');
  expect(search).toHaveValue('');
});

test('keeps the selected product outside the short list and when a search has no matches', () => {
  const onChange = jest.fn();
  render(<ProductSelect {...defaultProps} value="5399" allowCreate onChange={onChange} />);
  const select = screen.getByRole('combobox');
  expect(select).toHaveValue('5399');
  expect(within(select).getAllByRole('option')).toHaveLength(23);
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'no such product' } });
  expect(select).toHaveValue('5399');
  expect(within(select).getAllByRole('option')).toHaveLength(3);
  expect(screen.getByText('No matching products. Try another search.')).toBeInTheDocument();
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.change(select, { target: { value: '__create__' } });
  expect(onChange).toHaveBeenLastCalledWith('__create__');
  fireEvent.change(select, { target: { value: '' } });
  expect(onChange).toHaveBeenLastCalledWith('');
});

test('prioritizes customer history and preserves a saved product missing from the catalogue during loading', () => {
  const catalogue = buildProductCatalogue(products, [products[5398]]);
  const { rerender } = render(<ProductSelect {...defaultProps} catalogue={catalogue} />);
  const group = screen.getByRole('group', { name: 'Previously quoted for this customer' });
  expect(within(group).getByRole('option', { name: 'Product 5399' })).toBeInTheDocument();
  expect(screen.getAllByRole('option')[1]).toHaveValue('5399');

  rerender(<ProductSelect {...defaultProps} catalogue={buildProductCatalogue([], [])} value="9999" fallbackName="Saved gloves" loading disabled />);
  expect(screen.getByRole('combobox')).toHaveValue('9999');
  expect(screen.getByRole('option', { name: 'Saved gloves' })).toBeInTheDocument();
  expect(screen.getByRole('combobox')).toBeDisabled();
  expect(screen.getByRole('searchbox')).toBeDisabled();
});

test('searching never submits the add-line form and Escape restores the short list', () => {
  const onSubmit = jest.fn((event) => event.preventDefault());
  render(<form onSubmit={onSubmit}><ProductSelect {...defaultProps} /></form>);
  const search = screen.getByRole('searchbox');
  fireEvent.change(search, { target: { value: 'gloves' } });
  expect(fireEvent.keyDown(search, { key: 'Enter' })).toBe(false);
  expect(onSubmit).not.toHaveBeenCalled();
  fireEvent.keyDown(search, { key: 'Escape' });
  expect(search).toHaveValue('');
  expect(screen.getAllByRole('option')).toHaveLength(21);
});
