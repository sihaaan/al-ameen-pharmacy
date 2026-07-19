import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
      bulkUpdateLines: jest.fn(),
      bulkCreateProductsForLines: jest.fn(),
    },
    items: { list: jest.fn() },
    companies: { list: jest.fn(), create: jest.fn() },
    contacts: { list: jest.fn(), create: jest.fn() },
    auditLogs: { list: jest.fn() },
    lines: { createProduct: jest.fn(), rememberAlias: jest.fn() },
    lpos: { update: jest.fn() },
  },
  describeQuotationError: jest.fn(async (error, action, endpoint) => ({
    action,
    endpoint,
    status: error?.response?.status || 'Network error',
    detail: error?.message || 'Request failed',
  })),
  formatQuotationError: jest.fn(() => 'Request failed'),
}));

const products = [
  { id: 11, name: 'Gloves A', unit: 'box', primary_image_url: '' },
  { id: 12, name: 'Gloves B', unit: 'box', primary_image_url: '' },
];

const quote = {
  id: 21,
  quotation_number: 'Q-0021',
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
  subtotal: '0.00',
  total: '0.00',
  lines: [{
    id: 31,
    sort_order: 0,
    product: null,
    item_name_snapshot: 'Imported gloves',
    description: '',
    quantity: '1.000',
    unit: 'box',
    unit_price: '',
    vat_rate: '0.000',
    match_status: 'unresolved',
    notes: '',
  }],
};

const priceContext = (product, productName, price) => ({
  product,
  product_name: productName,
  unit_price: String(price),
  currency: 'AED',
  source: 'company_price_history',
  latest_quoted: {
    quotation: product,
    quotation_number: `Q-${product}`,
    quoted_at: '2026-06-01',
    quoted_unit_price: String(price),
    quantity: '1.000',
    unit: 'box',
    currency: 'AED',
    outcome_status: 'accepted',
    accepted_unit_price: String(Number(price) - 1),
    accepted_quantity: '1.000',
    accepted_at: '2026-06-02',
    lpo_number: `LPO-${product}`,
  },
  latest_accepted: {
    quotation: product,
    quotation_number: `Q-${product}`,
    quoted_at: '2026-06-01',
    quoted_unit_price: String(price),
    quantity: '1.000',
    unit: 'box',
    currency: 'AED',
    outcome_status: 'accepted',
    accepted_unit_price: String(Number(price) - 1),
    accepted_quantity: '1.000',
    accepted_at: '2026-06-02',
    lpo_number: `LPO-${product}`,
  },
  history: [],
});

const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};

describe('QuotationEditor Product price context', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    describeQuotationError.mockImplementation(async (error, action, endpoint) => ({
      action,
      endpoint,
      status: error?.response?.status || 'Network error',
      detail: error?.response?.data?.detail || error?.message || 'Request failed',
    }));
    formatQuotationError.mockImplementation(() => 'Request failed');
    quotationAPI.quotes.retrieve.mockResolvedValue({ data: quote });
    quotationAPI.quotes.productPrices.mockResolvedValue({ data: { results: {} } });
    quotationAPI.quotes.lpos.mockResolvedValue({ data: [] });
    quotationAPI.items.list.mockImplementation((params) => Promise.resolve({
      data: params?.company_used ? [products[0]] : products,
    }));
    quotationAPI.companies.list.mockResolvedValue({ data: [{ id: 7, name: 'Customer A' }] });
    quotationAPI.contacts.list.mockResolvedValue({ data: [] });
    quotationAPI.auditLogs.list.mockResolvedValue({ data: [] });
    quotationAPI.lines.rememberAlias.mockResolvedValue({ data: {} });
  });

  test('shows the full catalog and ignores an older Product lookup that resolves last', async () => {
    const first = deferred();
    const second = deferred();
    quotationAPI.quotes.productPrice
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    const productSelect = await screen.findByLabelText('Product for Imported gloves');
    expect(within(productSelect).getByRole('option', { name: 'Gloves B' })).toBeInTheDocument();

    fireEvent.change(productSelect, { target: { value: '11' } });
    fireEvent.change(productSelect, { target: { value: '12' } });
    expect(screen.getByDisplayValue('Imported gloves')).toBeInTheDocument();

    await act(async () => second.resolve({ data: priceContext(12, 'Gloves B', 22) }));
    const priceInput = await screen.findByLabelText('Unit price for Imported gloves');
    await waitFor(() => expect(priceInput).toHaveValue(22));
    expect(within(screen.getByRole('dialog', { name: /price history/i })).getByText(/Gloves B/)).toBeInTheDocument();

    await act(async () => first.resolve({ data: priceContext(11, 'Gloves A', 10) }));
    expect(priceInput).toHaveValue(22);
    expect(within(screen.getByRole('dialog', { name: /price history/i })).getByText(/Gloves B/)).toBeInTheDocument();
  });

  test('reviews and saves exact ordered line mappings when confirming a manual LPO', async () => {
    const sentQuote = {
      ...quote,
      status: 'sent',
      status_display: 'Sent',
      lines: [{ ...quote.lines[0], match_status: 'confirmed' }],
    };
    const parsedLpo = {
      id: 91,
      lpo_number: 'LPO-MANUAL-77',
      lpo_date: '2026-07-15',
      notes: '',
      status: 'parsed',
      status_display: 'Parsed',
      source_filename: 'LPO-MANUAL-77.pdf',
      source_type_display: 'File',
      parsed_row_count: 1,
      received_at: '2026-07-15T08:00:00Z',
      warnings: [],
      parsed_meta: {
        outcome_suggestions: [{ quotation_line_id: 31 }],
      },
    };
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({ data: sentQuote });
    quotationAPI.quotes.lpos.mockResolvedValueOnce({ data: [parsedLpo] });
    quotationAPI.lpos.update.mockResolvedValueOnce({
      data: {
        ...parsedLpo,
        status: 'confirmed',
        status_display: 'Confirmed',
        parsed_meta: {
          ...parsedLpo.parsed_meta,
          applied_outcome_line_ids: [31],
        },
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    const mappingPanel = (await screen.findByText('Ordered quotation lines')).closest('.qm-lpo-warning');
    expect(within(mappingPanel).getByRole('checkbox')).toBeChecked();
    const detailsCard = screen.getByText('Review detected details').closest('.qm-lpo-card');
    fireEvent.change(within(detailsCard).getByLabelText('Status'), { target: { value: 'confirmed' } });
    fireEvent.click(within(detailsCard).getByRole('button', { name: /save lpo details/i }));

    await waitFor(() => expect(quotationAPI.lpos.update).toHaveBeenCalledWith(91, {
      lpo_number: 'LPO-MANUAL-77',
      lpo_date: '2026-07-15',
      notes: '',
      status: 'confirmed',
      applied_outcome_line_ids: [31],
    }));
  });

  test('never overwrites a price typed while history is loading', async () => {
    const request = deferred();
    quotationAPI.quotes.productPrice.mockImplementationOnce(() => request.promise);

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.change(await screen.findByLabelText('Product for Imported gloves'), { target: { value: '11' } });
    expect(screen.getByDisplayValue('Imported gloves')).toBeInTheDocument();
    const priceInput = await screen.findByLabelText('Unit price for Imported gloves');
    fireEvent.change(priceInput, { target: { value: '73' } });

    await act(async () => request.resolve({ data: priceContext(11, 'Gloves A', 10) }));
    await waitFor(() => expect(priceInput).toHaveValue(73));
    expect(screen.getByText(/current price kept/i)).toBeInTheDocument();
    expect(screen.getByRole('dialog', { name: /price history/i })).toBeInTheDocument();
  });

  test('does not change a quotation unit price when the mouse wheel scrolls', async () => {
    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    const priceInput = await screen.findByLabelText('Unit price for Imported gloves');
    fireEvent.change(priceInput, { target: { value: '12.5' } });
    priceInput.focus();
    fireEvent.wheel(priceInput, { deltaY: 100 });

    expect(document.activeElement).not.toBe(priceInput);
    expect(priceInput).toHaveValue(12.5);
  });

  test('warns about a similar Product and only creates after an explicit override', async () => {
    quotationAPI.lines.createProduct.mockRejectedValue({
      response: {
        status: 409,
        data: {
          detail: 'A similar Product exists.',
          warning: 'A similar Product exists.',
          requires_confirmation: true,
          creation_blocked: false,
          candidates: [{
            product_id: 11,
            product_name: 'Gloves A',
            confidence: 0.92,
            pack_size: 'box',
          }],
        },
      },
    });
    quotationAPI.quotes.bulkCreateProductsForLines.mockResolvedValue({
      data: {
        updated_lines: [{ ...quote.lines[0], product: 13, product_name: 'Imported gloves', match_status: 'confirmed' }],
        confirmation_required: [],
        message: 'Created and linked one Product.',
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.change(await screen.findByLabelText('Product for Imported gloves'), { target: { value: '__create__' } });

    expect(await screen.findByText('Likely existing Product found')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Use Gloves A/i })).toBeInTheDocument();
    const override = screen.getByRole('button', { name: /Create new Product anyway/i });
    await waitFor(() => expect(override).toBeEnabled());
    fireEvent.click(override);

    await waitFor(() => expect(quotationAPI.quotes.bulkCreateProductsForLines).toHaveBeenCalledWith(21, {
      line_ids: [31],
      names: { 31: 'Imported gloves' },
      confirm_create_line_ids: [31],
    }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /create products/i })).not.toBeInTheDocument());
  });

  test('links a suggested Product without replacing the source snapshot or writing the alias separately', async () => {
    quotationAPI.lines.createProduct.mockRejectedValue({
      response: {
        status: 409,
        data: {
          detail: 'A similar Product exists.',
          warning: 'A similar Product exists.',
          requires_confirmation: true,
          creation_blocked: false,
          candidates: [{
            product_id: 11,
            product_name: 'Gloves A',
            confidence: 0.92,
            pack_size: 'box',
          }],
        },
      },
    });
    quotationAPI.quotes.bulkUpdateLines.mockResolvedValue({
      data: {
        quotation: {
          ...quote,
          lines: [{
            ...quote.lines[0],
            product: 11,
            product_name: 'Gloves A',
            item_name_snapshot: 'Imported gloves',
            match_status: 'confirmed',
          }],
        },
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.change(await screen.findByLabelText('Product for Imported gloves'), { target: { value: '__create__' } });
    fireEvent.click(await screen.findByRole('button', { name: /Use Gloves A/i }));

    await waitFor(() => expect(quotationAPI.quotes.bulkUpdateLines).toHaveBeenCalledWith(21, {
      lines: [expect.objectContaining({
        id: 31,
        product: '11',
        item_name_snapshot: 'Imported gloves',
        match_status: 'confirmed',
      })],
    }));
    expect(quotationAPI.lines.rememberAlias).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /create products/i })).not.toBeInTheDocument());
    expect(screen.getByDisplayValue('Imported gloves')).toBeInTheDocument();
  });

  test('shows bulk Product creation errors inside the open modal', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    quotationAPI.quotes.bulkCreateProductsForLines.mockRejectedValue({
      message: 'A retired alias blocked this Product name.',
      response: {
        status: 400,
        data: { detail: 'A retired alias blocked this Product name.' },
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Select visible unmatched' }));
    fireEvent.click(screen.getByRole('button', { name: 'Create Products for Selected Unmatched Rows' }));
    const dialog = await screen.findByRole('dialog', { name: 'Create Products from quotation lines' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Check catalog and continue' }));

    expect(await within(dialog).findByText('A retired alias blocked this Product name.')).toBeInTheDocument();
    expect(within(dialog).getByRole('alert')).toBeInTheDocument();
    expect(screen.getAllByRole('alert')).toHaveLength(1);
    expect(dialog).toBeInTheDocument();
    expect(consoleError).toHaveBeenCalled();
    consoleError.mockRestore();
  });

  test('keeps the create modal stable while a catalog check is pending', async () => {
    const request = deferred();
    quotationAPI.quotes.bulkCreateProductsForLines.mockImplementationOnce(() => request.promise);

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Select visible unmatched' }));
    fireEvent.click(screen.getByRole('button', { name: 'Create Products for Selected Unmatched Rows' }));
    const dialog = await screen.findByRole('dialog', { name: 'Create Products from quotation lines' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Check catalog and continue' }));

    expect(within(dialog).getByRole('button', { name: 'Close' })).toBeDisabled();
    expect(within(dialog).getByDisplayValue('Imported gloves')).toBeDisabled();

    await act(async () => request.resolve({
      data: {
        updated_lines: [],
        confirmation_required: [{
          line_id: 31,
          warning: 'A similar Product exists.',
          creation_blocked: false,
          candidates: [],
        }],
      },
    }));

    expect(await within(dialog).findByText('Likely existing Product found')).toBeInTheDocument();
    expect(within(dialog).getByDisplayValue('Imported gloves')).toBeEnabled();
  });
});
