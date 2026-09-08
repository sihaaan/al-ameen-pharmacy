import { fireEvent, render, screen, within } from '@testing-library/react';
import QuoteItemManager from './QuoteItemManager';
import quotationAPI from '../../api/quotations';

jest.mock('../../api/quotations', () => ({
  __esModule: true,
  default: { items: { list: jest.fn() } },
}));
jest.mock('../ProductFormModal', () => () => null);
jest.mock('./CatalogueIdentityReview', () => () => null);

const products = Array.from({ length: 105 }, (_, index) => ({
  id: index + 1,
  name: `Product ${String(index + 1).padStart(3, '0')}`,
  sku: `SKU-${index + 1}`,
  status: 'draft',
}));

beforeEach(() => {
  jest.clearAllMocks();
  quotationAPI.items.list.mockResolvedValue({ data: products });
});

test('bounds catalogue rendering while keeping every product reachable', async () => {
  render(<QuoteItemManager />);
  expect(await screen.findByText('1–50 of 105 products')).toBeInTheDocument();
  const table = screen.getByRole('table');
  expect(within(table).getAllByRole('row')).toHaveLength(51);
  expect(screen.queryByText('Product 051')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Next', exact: true }));
  expect(screen.getByText('51–100 of 105 products')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Next', exact: true }));
  expect(screen.getByText('101–105 of 105 products')).toBeInTheDocument();
  expect(within(table).getAllByRole('row')).toHaveLength(6);
  expect(screen.getByRole('button', { name: 'Next', exact: true })).toBeDisabled();
  fireEvent.click(screen.getByText('Product 105'));
  expect(screen.getByRole('heading', { name: 'Product 105' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Previous', exact: true }));
  expect(screen.getByText('51–100 of 105 products')).toBeInTheDocument();
});

test('search includes off-page products and resets navigation without losing the catalogue', async () => {
  render(<QuoteItemManager />);
  await screen.findByText('1–50 of 105 products');
  fireEvent.click(screen.getByRole('button', { name: 'Next', exact: true }));
  const search = screen.getByRole('textbox', { name: 'Search product items' });
  fireEvent.change(search, { target: { value: ' sku-105 ' } });
  expect(screen.getByText('Product 105')).toBeInTheDocument();
  expect(screen.getByText('1–1 of 1 products')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Previous', exact: true })).toBeDisabled();
  fireEvent.change(search, { target: { value: 'no matching product' } });
  expect(screen.getByText('No products match your search')).toBeInTheDocument();
  expect(within(screen.getByRole('table')).getAllByRole('row')).toHaveLength(1);
  fireEvent.change(search, { target: { value: '' } });
  expect(screen.getByText('1–50 of 105 products')).toBeInTheDocument();
  expect(quotationAPI.items.list).toHaveBeenCalledTimes(1);
});
