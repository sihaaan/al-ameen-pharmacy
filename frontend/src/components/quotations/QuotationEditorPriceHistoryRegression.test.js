import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import QuotationEditor from './QuotationEditor';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';

jest.mock('../../api/quotations', () => ({
  __esModule: true,
  default: {
    quotes: {
      retrieve: jest.fn(),
      productPrices: jest.fn(),
      productPrice: jest.fn(),
      lpos: jest.fn(),
    },
    items: { list: jest.fn() },
    companies: { list: jest.fn() },
    contacts: { list: jest.fn() },
    auditLogs: { list: jest.fn() },
    lines: { rememberAlias: jest.fn() },
  },
  describeQuotationError: jest.fn(async (error, action, endpoint) => ({
    action,
    endpoint,
    status: error?.response?.status || 'Network error',
    detail: error?.message || 'Request failed',
  })),
  formatQuotationError: jest.fn(() => 'Request failed'),
}));

const product = {
  id: 11,
  name: 'Powder-free Gloves',
  unit: 'box',
  primary_image_url: '',
};

const quotation = {
  id: 21,
  quotation_number: 'QT-20260701-0021',
  company: 7,
  company_name: 'Customer A',
  contact: null,
  contact_name: '',
  status: 'draft',
  status_display: 'Draft',
  version: 1,
  currency: 'AED',
  payment_terms: 'as_per_agreement',
  valid_until: '2026-08-01',
  subtotal: '1000.00',
  vat_total: '0.00',
  total: '1000.00',
  lines: [{
    id: 31,
    sort_order: 0,
    product: 11,
    item_name_snapshot: 'Powder-free Gloves',
    description: '',
    quantity: '10.000',
    unit: 'box',
    unit_price: '100.000',
    vat_rate: '0.000',
    match_status: 'confirmed',
    include_product_image: false,
    product_image: null,
    product_image_url: '',
    has_product_image: false,
    notes: '',
  }],
};

const historyRow = {
  quotation: 18,
  quotation_number: 'QT-20260601-0018',
  quoted_at: '2026-06-01',
  quoted_unit_price: '98.000',
  quantity: '12.000',
  unit: 'box',
  currency: 'AED',
  outcome_status: 'accepted',
  accepted_unit_price: '95.000',
  accepted_quantity: '12.000',
  accepted_at: '2026-06-04',
  lpo_number: 'LPO-7781',
};

const historyContext = {
  product: 11,
  product_name: product.name,
  unit_price: '98.00',
  unit: 'box',
  currency: 'AED',
  source: 'company_price_history',
  source_label: 'Latest Customer A price',
  quoted_at: '2026-06-01',
  latest_quoted: historyRow,
  latest_accepted: historyRow,
  history: [historyRow],
};

describe('QuotationEditor existing-line price history regression', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    describeQuotationError.mockImplementation(async (error, action, endpoint) => ({
      action,
      endpoint,
      status: error?.response?.status || 'Network error',
      detail: error?.message || 'Request failed',
    }));
    formatQuotationError.mockReturnValue('Request failed');
    quotationAPI.quotes.retrieve.mockResolvedValue({ data: quotation });
    quotationAPI.quotes.productPrices.mockResolvedValue({
      data: { results: { 11: historyContext } },
    });
    quotationAPI.quotes.productPrice.mockResolvedValue({ data: historyContext });
    quotationAPI.quotes.lpos.mockResolvedValue({ data: [] });
    quotationAPI.items.list.mockResolvedValue({ data: [product] });
    quotationAPI.companies.list.mockResolvedValue({ data: [{ id: 7, name: 'Customer A' }] });
    quotationAPI.contacts.list.mockResolvedValue({ data: [] });
    quotationAPI.auditLogs.list.mockResolvedValue({ data: [] });
  });

  test('opens the prefetched history for an already matched quotation line', async () => {
    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    await waitFor(() => expect(quotationAPI.quotes.productPrices).toHaveBeenCalledWith(21, {
      products: '11',
      history_limit: 10,
    }));
    const viewButton = await screen.findByRole('button', { name: 'View price history' });
    fireEvent.click(viewButton);

    const dialog = screen.getByRole('dialog', { name: /price history/i });
    expect(within(dialog).getByText(/Powder-free Gloves/)).toBeInTheDocument();
    expect(within(dialog).getByText('QT-20260601-0018')).toBeInTheDocument();
    expect(within(dialog).getAllByText('AED 98.00').length).toBeGreaterThan(0);
    expect(within(dialog).getAllByText('AED 95.00').length).toBeGreaterThan(0);
    expect(within(dialog).getAllByText(/LPO-7781/).length).toBeGreaterThan(0);
    expect(quotationAPI.quotes.productPrice).not.toHaveBeenCalled();
  });

  test('still opens a clear empty-history dialog when the Product has no earlier quote', async () => {
    quotationAPI.quotes.productPrices.mockResolvedValueOnce({
      data: {
        results: {
          11: {
            product: 11,
            product_name: product.name,
            unit_price: '',
            unit: '',
            currency: 'AED',
            source: 'no_company_price_history',
            source_label: 'No previous Customer A price',
            quoted_at: '',
            latest_quoted: null,
            latest_accepted: null,
            history: [],
          },
        },
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    fireEvent.click(await screen.findByRole('button', { name: 'View price history' }));
    const dialog = screen.getByRole('dialog', { name: /price history/i });
    expect(within(dialog).getAllByText('No history')).toHaveLength(2);
    expect(within(dialog).getByText(/No earlier prices exist/i)).toBeInTheDocument();
  });

  test('renders the editor after a batch history failure and retries the selected Product on demand', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    quotationAPI.quotes.productPrices.mockRejectedValue(new Error('Temporary history service failure'));

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    expect(await screen.findByText('QT-20260701-0021')).toBeInTheDocument();
    expect(await screen.findByText(/Price history previews are temporarily unavailable/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Unit price for Powder-free Gloves')).toHaveValue(100);

    fireEvent.click(screen.getByRole('button', { name: 'View price history' }));

    await waitFor(() => expect(quotationAPI.quotes.productPrice).toHaveBeenCalledWith(21, {
      product: 11,
      history_limit: 50,
    }));
    const dialog = await screen.findByRole('dialog', { name: /price history/i });
    expect(within(dialog).getByText('QT-20260601-0018')).toBeInTheDocument();
    expect(within(dialog).getAllByText(/LPO-7781/).length).toBeGreaterThan(0);
    expect(consoleError).toHaveBeenCalled();
    consoleError.mockRestore();
  });
});
