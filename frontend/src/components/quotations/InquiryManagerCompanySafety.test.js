import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import InquiryManager from './InquiryManager';
import quotationAPI from '../../api/quotations';

jest.mock('./CompanySelectWithCreate', () => ({ onChange, onSearch, disabled, companies, loading }) => (
  <div>
    <span>{loading ? 'Companies loading' : `Companies ready: ${companies.map((company) => company.name).join(', ')}`}</span>
    <button type="button" onClick={() => onSearch?.('Narrow')}>Search companies remotely</button>
    <button type="button" disabled={disabled} onClick={() => onChange('7')}>Choose Company 7</button>
    <button type="button" disabled={disabled} onClick={() => onChange('8')}>Choose Company 8</button>
    <button type="button" onClick={() => onChange('8')}>Force Company 8</button>
  </div>
));

jest.mock('../../api/quotations', () => ({
  __esModule: true,
  default: {
    companies: { list: jest.fn() },
    contacts: { list: jest.fn() },
    items: { list: jest.fn() },
    inquiries: {
      list: jest.fn(),
      create: jest.fn(),
      parseText: jest.fn(),
      parseFile: jest.fn(),
      aiCleanParse: jest.fn(),
      createImported: jest.fn(),
      createQuote: jest.fn(),
    },
  },
  describeQuotationError: jest.fn(async (error, action, endpoint) => ({
    action,
    endpoint,
    status: error?.response?.status || 'Network error',
    detail: error?.message || 'Request failed',
  })),
  formatQuotationError: jest.fn(() => 'Request failed'),
}));

const parsedPreview = {
  result_source: 'deterministic_parse',
  lines: [{
    raw_name: 'Old company item',
    quantity: '1.000',
    matched_product: 11,
    match_status: 'confirmed',
    match_reason: 'Matched old company alias.',
    parse_status: 'parsed',
  }],
  summary: {},
  warnings: [],
  ai_candidate: {
    result_source: 'ai_text_cleanup',
    provider: 'test',
    model: 'test-model',
    lines: [{
      raw_name: 'Old company AI item',
      quantity: '1.000',
      matched_product: 11,
      match_status: 'confirmed',
      match_reason: 'Matched old company alias.',
    }],
  },
};

const renderPasteInquiryManager = (props = {}) => {
  const result = render(<InquiryManager {...props} />);
  fireEvent.click(screen.getByRole('button', { name: 'Paste Text' }));
  return result;
};

describe('InquiryManager company-scoped async safety', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    quotationAPI.companies.list.mockResolvedValue({ data: [] });
    quotationAPI.contacts.list.mockResolvedValue({ data: [] });
    quotationAPI.items.list.mockResolvedValue({ data: [{ id: 11, name: 'Matched Product' }] });
    quotationAPI.inquiries.list.mockResolvedValue({ data: [] });
    quotationAPI.inquiries.createQuote.mockResolvedValue({
      data: { id: 901, quotation_number: 'QT-TEST-1' },
      status: 201,
    });
  });

  test('opens on file upload by default and keeps paste text available', async () => {
    render(<InquiryManager />);

    await screen.findByText('Companies ready:');
    expect(screen.getByRole('button', { name: 'Upload File' })).toHaveClass('active');
    expect(screen.getByLabelText('Inquiry file')).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Paste the customer's requested items here...")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Paste Text' }));

    expect(screen.getByRole('button', { name: 'Paste Text' })).toHaveClass('active');
    expect(screen.getByPlaceholderText("Paste the customer's requested items here...")).toBeInTheDocument();
  });

  test('applies AI cleanup once and clears company-scoped matches after company changes', async () => {
    quotationAPI.inquiries.parseText.mockResolvedValue({ data: parsedPreview });
    renderPasteInquiryManager();

    fireEvent.click((await screen.findAllByRole('button', { name: 'Choose Company 7' }))[0]);
    fireEvent.change(screen.getByPlaceholderText("Paste the customer's requested items here..."), {
      target: { value: 'Old company item' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Extract Lines' }));

    expect(await screen.findByDisplayValue('Old company AI item')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Apply AI Cleaned Rows/i })).not.toBeInTheDocument();
    expect(screen.getByLabelText('Matched product row 1')).toHaveValue('11');
    fireEvent.click(screen.getAllByRole('button', { name: 'Choose Company 8' })[0]);

    await waitFor(() => expect(screen.getByLabelText('Matched product row 1')).toHaveValue(''));
  });

  test('disables the company picker while a company-scoped parse is in flight', async () => {
    let resolveParse;
    quotationAPI.inquiries.parseText.mockReturnValue(new Promise((resolve) => {
      resolveParse = resolve;
    }));
    renderPasteInquiryManager();

    const companyButton = (await screen.findAllByRole('button', { name: 'Choose Company 7' }))[0];
    fireEvent.click(companyButton);
    fireEvent.change(screen.getByPlaceholderText("Paste the customer's requested items here..."), {
      target: { value: 'Pending parse item' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Extract Lines' }));

    await waitFor(() => expect(companyButton).toBeDisabled());
    await act(async () => {
      resolveParse({ data: { ...parsedPreview, ai_candidate: null } });
    });
    await waitFor(() => expect(companyButton).not.toBeDisabled());
  });

  test('ignores a save response if the company generation changed in flight', async () => {
    let resolveSave;
    quotationAPI.inquiries.parseText.mockResolvedValue({ data: { ...parsedPreview, ai_candidate: null } });
    quotationAPI.inquiries.createImported.mockReturnValue(new Promise((resolve) => {
      resolveSave = resolve;
    }));
    renderPasteInquiryManager();

    fireEvent.click((await screen.findAllByRole('button', { name: 'Choose Company 7' }))[0]);
    fireEvent.change(screen.getByPlaceholderText("Paste the customer's requested items here..."), {
      target: { value: 'Old company item' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Extract Lines' }));
    await screen.findByText('Old company item');
    fireEvent.click(screen.getByRole('button', { name: 'Save & Open Quotation' }));
    await screen.findByRole('button', { name: 'Saving & opening…' });

    fireEvent.click(screen.getAllByRole('button', { name: 'Force Company 8' })[0]);
    await act(async () => {
      resolveSave({ data: { id: 501, company: 7 } });
    });

    await screen.findByRole('button', { name: 'Save & Open Quotation' });
    expect(screen.queryByText(/Imported inquiry saved/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Inquiry Saved' })).not.toBeInTheDocument();
  });

  test('keeps focus while typing more than one character into an added or parsed row', async () => {
    quotationAPI.inquiries.parseText.mockResolvedValue({ data: { ...parsedPreview, ai_candidate: null } });
    renderPasteInquiryManager();

    fireEvent.change(screen.getByPlaceholderText("Paste the customer's requested items here..."), {
      target: { value: 'A' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Extract Lines' }));

    const input = await screen.findByLabelText('Requested item name row 1');
    input.focus();
    fireEvent.change(input, { target: { value: 'N' } });
    expect(document.activeElement).toBe(input);
    fireEvent.change(input, { target: { value: 'New row name' } });
    expect(document.activeElement).toBe(input);
    expect(input).toHaveValue('New row name');
  });

  test('inserts a row at a chosen position and reorders rows', async () => {
    quotationAPI.inquiries.parseText.mockResolvedValue({
      data: {
        ...parsedPreview,
        ai_candidate: null,
        lines: [
          { raw_name: 'First item', quantity: '1.000', parse_status: 'parsed' },
          { raw_name: 'Second item', quantity: '2.000', parse_status: 'parsed' },
        ],
      },
    });
    renderPasteInquiryManager();

    fireEvent.change(screen.getByPlaceholderText("Paste the customer's requested items here..."), {
      target: { value: 'First item\nSecond item' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Extract Lines' }));
    await screen.findByDisplayValue('Second item');

    fireEvent.click(screen.getByRole('button', { name: 'Move row 2 up' }));
    expect(screen.getByLabelText('Requested item name row 1')).toHaveValue('Second item');
    expect(screen.getByLabelText('Requested item name row 2')).toHaveValue('First item');

    fireEvent.click(screen.getAllByRole('button', { name: '+ Above' })[1]);
    expect(screen.getByLabelText('Requested item name row 2')).toHaveValue('');
    expect(screen.getByLabelText('Requested item name row 3')).toHaveValue('First item');
  });

  test('changing the import source clears old preview rows so they cannot be saved for a new file', async () => {
    quotationAPI.inquiries.parseText.mockResolvedValue({ data: { ...parsedPreview, ai_candidate: null } });
    renderPasteInquiryManager();

    fireEvent.change(screen.getByPlaceholderText("Paste the customer's requested items here..."), {
      target: { value: 'Old company item' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Extract Lines' }));
    await screen.findByLabelText('Requested item name row 1');

    fireEvent.click(screen.getByRole('button', { name: 'Upload File' }));
    expect(screen.queryByLabelText('Requested item name row 1')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save & Open Quotation' })).not.toBeInTheDocument();
  });

  test('auto-fills extracted source price while protecting a manually typed replacement from wheel changes', async () => {
    quotationAPI.inquiries.parseText.mockResolvedValue({
      data: {
        ...parsedPreview,
        ai_candidate: null,
        lines: [{
          raw_name: 'Priced item',
          quantity: '1.000',
          unit_price: null,
          customer_unit_price: '12.50',
          parse_status: 'parsed',
        }],
      },
    });
    renderPasteInquiryManager();

    fireEvent.change(screen.getByPlaceholderText("Paste the customer's requested items here..."), {
      target: { value: 'Priced item' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Extract Lines' }));
    const quantity = await screen.findByLabelText('Quantity row 1');
    const price = screen.getByLabelText('Unit price row 1');
    expect(price).toHaveValue(12.5);
    fireEvent.click(screen.getByRole('button', { name: 'View Raw' }));
    expect(screen.getByText('Customer/source unit price: 12.50')).toBeInTheDocument();
    quantity.focus();
    fireEvent.wheel(quantity, { deltaY: 100 });
    expect(document.activeElement).not.toBe(quantity);
    expect(quantity).toHaveValue(1);

    fireEvent.change(price, { target: { value: '15.50' } });
    price.focus();
    fireEvent.wheel(price, { deltaY: 100 });
    expect(document.activeElement).not.toBe(price);
    expect(price).toHaveValue(15.5);
  });

  test('shows and auto-applies detected pricing, with one action to remove only those values', async () => {
    quotationAPI.inquiries.parseText.mockResolvedValue({
      data: {
        ...parsedPreview,
        ai_candidate: null,
        lines: [{
          raw_name: 'Priced item',
          quantity: '1.000',
          unit_price: null,
          vat_rate: null,
          customer_unit_price: '12.50',
          customer_vat_rate: '5',
          customer_vat: 'VAT rate 5%; VAT amount 0.63',
          parse_status: 'parsed',
        }],
      },
    });
    renderPasteInquiryManager();

    fireEvent.change(screen.getByPlaceholderText("Paste the customer's requested items here..."), {
      target: { value: 'Priced item' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Extract Lines' }));

    const price = await screen.findByLabelText('Unit price row 1');
    const vat = screen.getByLabelText('VAT row 1');
    expect(screen.getByText('Detected source: 12.50')).toBeInTheDocument();
    expect(screen.getByText('Detected source: 5%')).toBeInTheDocument();
    expect(price).toHaveValue(12.5);
    expect(vat).toHaveValue('5');

    fireEvent.click(screen.getByRole('button', { name: 'Remove detected prices & VAT' }));
    expect(price).toHaveValue(null);
    expect(vat).toHaveValue('0');
    expect(screen.getByText(/Removed 1 auto-detected prices and 1 VAT values/i)).toBeInTheDocument();
    expect(screen.getByText('Detected source: 12.50')).toBeInTheDocument();
  });

  test('does not remove a detected price after staff manually replaces it', async () => {
    quotationAPI.inquiries.parseText.mockResolvedValue({
      data: {
        ...parsedPreview,
        ai_candidate: null,
        lines: [{
          raw_name: 'Staff repriced item',
          quantity: '1.000',
          unit_price: null,
          vat_rate: null,
          customer_unit_price: '12.50',
          customer_vat_rate: '5',
          parse_status: 'parsed',
        }],
      },
    });
    renderPasteInquiryManager();

    fireEvent.change(screen.getByPlaceholderText("Paste the customer's requested items here..."), {
      target: { value: 'Staff repriced item' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Extract Lines' }));
    const price = await screen.findByLabelText('Unit price row 1');
    fireEvent.change(price, { target: { value: '19.75' } });
    fireEvent.click(screen.getByRole('button', { name: 'Remove detected prices & VAT' }));

    expect(price).toHaveValue(19.75);
    expect(screen.getByLabelText('VAT row 1')).toHaveValue('0');
  });

  test('saves automatically detected source price and VAT as quotation pricing', async () => {
    quotationAPI.inquiries.parseText.mockResolvedValue({
      data: {
        ...parsedPreview,
        ai_candidate: null,
        lines: [{
          raw_name: 'Priced item',
          quantity: '2.000',
          unit: 'BOX',
          unit_price: null,
          vat_rate: null,
          customer_unit_price: '18.25',
          customer_vat_rate: '5',
          parse_status: 'parsed',
        }],
      },
    });
    quotationAPI.inquiries.createImported.mockResolvedValue({ data: { id: 501, company: 7 } });
    renderPasteInquiryManager({ onOpenQuote: jest.fn() });

    fireEvent.click((await screen.findAllByRole('button', { name: 'Choose Company 7' }))[0]);
    fireEvent.change(screen.getByPlaceholderText("Paste the customer's requested items here..."), {
      target: { value: 'Priced item' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Extract Lines' }));
    await screen.findByLabelText('Unit price row 1');
    fireEvent.click(screen.getByRole('button', { name: 'Save & Open Quotation' }));

    await waitFor(() => expect(quotationAPI.inquiries.createImported).toHaveBeenCalledWith(expect.objectContaining({
      lines: [expect.objectContaining({ unit_price: '18.25', vat_rate: '5' })],
    })));
  });

  test('saves blank price and zero VAT after detected pricing is removed', async () => {
    quotationAPI.inquiries.parseText.mockResolvedValue({
      data: {
        ...parsedPreview,
        ai_candidate: null,
        lines: [{
          raw_name: 'Price removed item',
          quantity: '2.000',
          unit: 'BOX',
          unit_price: null,
          vat_rate: null,
          customer_unit_price: '18.25',
          customer_vat_rate: '5',
          parse_status: 'parsed',
        }],
      },
    });
    quotationAPI.inquiries.createImported.mockResolvedValue({ data: { id: 502, company: 7 } });
    renderPasteInquiryManager({ onOpenQuote: jest.fn() });

    fireEvent.click((await screen.findAllByRole('button', { name: 'Choose Company 7' }))[0]);
    fireEvent.change(screen.getByPlaceholderText("Paste the customer's requested items here..."), {
      target: { value: 'Price removed item' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Extract Lines' }));
    await screen.findByLabelText('Unit price row 1');
    fireEvent.click(screen.getByRole('button', { name: 'Remove detected prices & VAT' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save & Open Quotation' }));

    await waitFor(() => expect(quotationAPI.inquiries.createImported).toHaveBeenCalledWith(expect.objectContaining({
      lines: [expect.objectContaining({ unit_price: null, vat_rate: '0' })],
    })));
  });

  test('sanitizes auto pricing for AI cleanup and auto-fills the cleaned result', async () => {
    quotationAPI.inquiries.parseText.mockResolvedValue({
      data: {
        ...parsedPreview,
        ai_candidate: null,
        lines: [{
          raw_name: 'Initial priced item',
          quantity: '1.000',
          unit: 'PCS',
          unit_price: null,
          vat_rate: null,
          customer_unit_price: '12.50',
          customer_vat_rate: '5',
          notes: 'Customer/source unit price: 12.50',
          parse_status: 'parsed',
        }],
      },
    });
    quotationAPI.inquiries.aiCleanParse.mockResolvedValue({
      data: {
        result_source: 'ai_text_cleanup',
        lines: [{
          raw_name: 'AI cleaned priced item',
          quantity: '1.000',
          unit: 'PCS',
          unit_price: null,
          vat_rate: null,
          customer_unit_price: '13.25',
          customer_vat_rate: '5',
          parse_status: 'parsed',
        }],
      },
    });
    renderPasteInquiryManager();

    fireEvent.change(screen.getByPlaceholderText("Paste the customer's requested items here..."), {
      target: { value: 'Initial priced item' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Extract Lines' }));
    expect(await screen.findByLabelText('Unit price row 1')).toHaveValue(12.5);

    fireEvent.click(screen.getByRole('button', { name: /AI Clean/i }));
    await waitFor(() => expect(quotationAPI.inquiries.aiCleanParse).toHaveBeenCalledWith(expect.objectContaining({
      preview: expect.objectContaining({
        lines: [expect.objectContaining({
          unit_price: null,
          vat_rate: null,
          customer_unit_price: '12.50',
          customer_vat_rate: '5',
        })],
      }),
    })));
    expect(await screen.findByDisplayValue('AI cleaned priced item')).toBeInTheDocument();
    expect(screen.getByLabelText('Unit price row 1')).toHaveValue(13.25);
    expect(screen.getByLabelText('VAT row 1')).toHaveValue('5');
    expect(screen.getByRole('button', { name: 'Undo AI cleanup' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Remove detected prices & VAT' }));
    expect(screen.getByRole('button', { name: 'Undo AI cleanup' })).toBeInTheDocument();
  });

  test('keeps the remove-detected-pricing choice through AI cleanup', async () => {
    quotationAPI.inquiries.parseText.mockResolvedValue({
      data: {
        ...parsedPreview,
        ai_candidate: null,
        lines: [{
          raw_name: 'Source-priced item',
          quantity: '1.000',
          unit: 'PCS',
          unit_price: null,
          vat_rate: null,
          customer_unit_price: '12.50',
          customer_vat_rate: '5',
          parse_status: 'parsed',
        }],
      },
    });
    quotationAPI.inquiries.aiCleanParse.mockResolvedValue({
      data: {
        result_source: 'ai_text_cleanup',
        lines: [{
          raw_name: 'Cleaned source-priced item',
          quantity: '1.000',
          unit: 'PCS',
          unit_price: null,
          vat_rate: null,
          customer_unit_price: '13.25',
          customer_vat_rate: '5',
          parse_status: 'parsed',
        }],
      },
    });
    renderPasteInquiryManager();

    fireEvent.change(screen.getByPlaceholderText("Paste the customer's requested items here..."), {
      target: { value: 'Source-priced item' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Extract Lines' }));
    expect(await screen.findByLabelText('Unit price row 1')).toHaveValue(12.5);
    fireEvent.click(screen.getByRole('button', { name: 'Remove detected prices & VAT' }));
    expect(screen.getByLabelText('Unit price row 1')).toHaveValue(null);

    fireEvent.click(screen.getByRole('button', { name: /AI Clean/i }));
    await screen.findByDisplayValue('Cleaned source-priced item');

    expect(screen.getByLabelText('Unit price row 1')).toHaveValue(null);
    expect(screen.getByLabelText('VAT row 1')).toHaveValue('0');
    expect(screen.getByText('Detected source: 13.25')).toBeInTheDocument();
    expect(quotationAPI.inquiries.aiCleanParse).toHaveBeenCalledWith(expect.objectContaining({
      preview: expect.not.objectContaining({
        _source_pricing_suppressed_by_user: true,
      }),
    }));
  });

  test('does not run AI cleanup after employee pricing has begun', async () => {
    quotationAPI.inquiries.parseText.mockResolvedValue({
      data: {
        ...parsedPreview,
        ai_candidate: null,
        lines: [{ raw_name: 'Priced item', quantity: '1.000', unit_price: null, parse_status: 'parsed' }],
      },
    });
    renderPasteInquiryManager();

    fireEvent.change(screen.getByPlaceholderText("Paste the customer's requested items here..."), {
      target: { value: 'Priced item' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Extract Lines' }));
    const price = await screen.findByLabelText('Unit price row 1');
    fireEvent.change(price, { target: { value: '12.50' } });
    fireEvent.click(screen.getByRole('button', { name: /AI Clean/i }));

    expect(await screen.findByText(/AI cleanup is available before pricing/i)).toBeInTheDocument();
    expect(quotationAPI.inquiries.aiCleanParse).not.toHaveBeenCalled();
    expect(price).toHaveValue(12.5);
  });

  test('blurs manual inquiry quantity on wheel and retains the typed value', async () => {
    render(<InquiryManager />);

    fireEvent.click(screen.getByRole('button', { name: /Manual inquiry entry/i }));
    await screen.findByRole('option', { name: 'Matched Product' });
    const quantity = screen.getByRole('spinbutton', { name: 'Qty' });
    fireEvent.change(quantity, { target: { value: '7.5' } });
    quantity.focus();
    fireEvent.wheel(quantity, { deltaY: 100 });

    expect(document.activeElement).not.toBe(quantity);
    expect(quantity).toHaveValue(7.5);
  });

  test('saves, creates one quotation, and opens it in a single action', async () => {
    const onOpenQuote = jest.fn();
    quotationAPI.inquiries.parseText.mockResolvedValue({ data: { ...parsedPreview, ai_candidate: null } });
    quotationAPI.inquiries.createImported.mockResolvedValue({ data: { id: 501, company: 7 } });
    renderPasteInquiryManager({ onOpenQuote });

    fireEvent.click((await screen.findAllByRole('button', { name: 'Choose Company 7' }))[0]);
    fireEvent.change(screen.getByPlaceholderText("Paste the customer's requested items here..."), {
      target: { value: 'Old company item' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Extract Lines' }));
    await screen.findByLabelText('Requested item name row 1');
    fireEvent.click(screen.getByRole('button', { name: 'Save & Open Quotation' }));

    await waitFor(() => expect(quotationAPI.inquiries.createQuote).toHaveBeenCalledWith(501));
    expect(onOpenQuote).toHaveBeenCalledWith(901);
    expect(quotationAPI.inquiries.list).not.toHaveBeenCalled();
  });

  test('allows only one import operation even when two actions are clicked in the same render', async () => {
    let resolveSave;
    quotationAPI.inquiries.parseText.mockResolvedValue({ data: { ...parsedPreview, ai_candidate: null } });
    quotationAPI.inquiries.createImported.mockReturnValue(new Promise((resolve) => {
      resolveSave = resolve;
    }));
    renderPasteInquiryManager({ onOpenQuote: jest.fn() });

    fireEvent.click((await screen.findAllByRole('button', { name: 'Choose Company 7' }))[0]);
    fireEvent.change(screen.getByPlaceholderText("Paste the customer's requested items here..."), {
      target: { value: 'Old company item' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Extract Lines' }));
    await screen.findByLabelText('Requested item name row 1');

    const saveButton = screen.getByRole('button', { name: 'Save & Open Quotation' });
    const aiButton = screen.getByRole('button', { name: 'AI Clean & Apply' });
    act(() => {
      saveButton.click();
      aiButton.click();
    });

    await waitFor(() => expect(quotationAPI.inquiries.createImported).toHaveBeenCalledTimes(1));
    expect(quotationAPI.inquiries.aiCleanParse).not.toHaveBeenCalled();
    expect(aiButton).toBeDisabled();

    await act(async () => {
      resolveSave({ data: { id: 501, company: 7 } });
    });
    await waitFor(() => expect(quotationAPI.inquiries.createQuote).toHaveBeenCalledWith(501));
  });

  test('locks manual inquiry fields until its quotation has opened', async () => {
    let resolveQuote;
    const onOpenQuote = jest.fn();
    quotationAPI.inquiries.create.mockResolvedValue({ data: { id: 601, company: 7 } });
    quotationAPI.inquiries.createQuote.mockReturnValue(new Promise((resolve) => {
      resolveQuote = resolve;
    }));
    render(<InquiryManager onOpenQuote={onOpenQuote} />);

    fireEvent.click(screen.getByRole('button', { name: /Manual inquiry entry/i }));
    fireEvent.click(screen.getAllByRole('button', { name: 'Choose Company 7' })[1]);
    fireEvent.change(screen.getByLabelText('Requested item name'), { target: { value: 'Manual item' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save & Open Quotation' }));

    await waitFor(() => expect(quotationAPI.inquiries.createQuote).toHaveBeenCalledWith(601));
    expect(screen.getByLabelText('Requested item name')).toBeDisabled();

    await act(async () => {
      resolveQuote({ data: { id: 902, quotation_number: 'QT-MANUAL' }, status: 201 });
    });
    await waitFor(() => expect(onOpenQuote).toHaveBeenCalledWith(902));
  });

  test('loads inquiry history only when the collapsed history panel is opened', async () => {
    render(<InquiryManager />);

    await waitFor(() => expect(quotationAPI.companies.list).toHaveBeenCalled());
    expect(quotationAPI.inquiries.list).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /Inquiry history/i }));
    await waitFor(() => expect(quotationAPI.inquiries.list).toHaveBeenCalledWith({ limit: 100 }));
  });

  test('makes companies selectable without waiting for products or inquiry history', async () => {
    let resolveItems;
    quotationAPI.companies.list.mockResolvedValue({ data: [{ id: 7, name: 'Fast Company' }] });
    quotationAPI.items.list.mockReturnValue(new Promise((resolve) => {
      resolveItems = resolve;
    }));
    render(<InquiryManager />);

    expect(await screen.findByText('Companies ready: Fast Company')).toBeInTheDocument();
    expect(quotationAPI.inquiries.list).not.toHaveBeenCalled();

    await act(async () => {
      resolveItems({ data: [] });
    });
  });

  test('makes the first companies usable before hydrating Trojan from the complete directory', async () => {
    const initialCompanies = Array.from({ length: 100 }, (_, index) => ({
      id: index + 1,
      name: `Alpha Company ${String(index + 1).padStart(3, '0')}`,
    }));
    const completeCompanies = [
      ...initialCompanies,
      { id: 501, name: 'Trojan General Contracting' },
    ];
    let resolveCompleteDirectory;
    quotationAPI.companies.list
      .mockResolvedValueOnce({ data: initialCompanies })
      .mockReturnValueOnce(new Promise((resolve) => {
        resolveCompleteDirectory = resolve;
      }));

    render(<InquiryManager />);

    expect(await screen.findByText(/Alpha Company 100/)).toBeInTheDocument();
    expect(screen.queryByText(/Trojan General Contracting/)).not.toBeInTheDocument();
    expect(quotationAPI.companies.list).toHaveBeenNthCalledWith(1, { active: 'true', limit: 100 });
    await waitFor(() => expect(quotationAPI.companies.list).toHaveBeenNthCalledWith(2, { active: 'true' }));

    await act(async () => {
      resolveCompleteDirectory({ data: completeCompanies });
    });

    expect(await screen.findByText(/Trojan General Contracting/)).toBeInTheDocument();
  });

  test('merges a slow initial company response after a faster search response', async () => {
    let resolveInitialCompanies;
    quotationAPI.companies.list
      .mockReturnValueOnce(new Promise((resolve) => {
        resolveInitialCompanies = resolve;
      }))
      .mockResolvedValueOnce({ data: [{ id: 8, name: 'Narrow Result' }] });
    render(<InquiryManager />);

    fireEvent.click(screen.getByRole('button', { name: 'Search companies remotely' }));
    expect(await screen.findByText('Companies ready: Narrow Result')).toBeInTheDocument();

    await act(async () => {
      resolveInitialCompanies({ data: [{ id: 7, name: 'Baseline Company' }] });
    });
    expect(await screen.findByText('Companies ready: Baseline Company, Narrow Result')).toBeInTheDocument();
  });

  test('accepts a dropped screenshot and sends it to the file parser', async () => {
    quotationAPI.inquiries.parseFile.mockResolvedValue({
      data: {
        ...parsedPreview,
        source_type: 'image',
        source_filename: 'request.png',
        ai_candidate: null,
        lines: [{
          raw_name: 'Screenshot priced item',
          quantity: '2.000',
          unit: 'PCS',
          unit_price: null,
          vat_rate: null,
          customer_unit_price: '6.00',
          customer_vat_rate: '5',
          parse_status: 'parsed',
        }],
      },
    });
    render(<InquiryManager />);

    fireEvent.click(screen.getByRole('button', { name: 'Upload File' }));
    const file = new File(['image-bytes'], 'request.png', { type: 'image/png' });
    const dropzone = screen.getByText('Drag a file here, or choose from your computer').closest('.qm-file-dropzone');
    fireEvent.drop(dropzone, { dataTransfer: { files: [file] } });
    fireEvent.click(screen.getByRole('button', { name: 'Parse File' }));

    await waitFor(() => expect(quotationAPI.inquiries.parseFile).toHaveBeenCalledTimes(1));
    const formData = quotationAPI.inquiries.parseFile.mock.calls[0][0];
    expect(formData.get('file')).toBe(file);
    expect(await screen.findByLabelText('Unit price row 1')).toHaveValue(6);
    expect(screen.getByLabelText('VAT row 1')).toHaveValue('5');
  });

  test('uses one upload picker and identifies every supported inquiry file automatically', async () => {
    render(<InquiryManager />);
    await screen.findByText('Companies ready:');

    fireEvent.click(screen.getByRole('button', { name: 'Upload File' }));
    const input = screen.getByLabelText('Inquiry file');
    expect(input).toHaveAttribute(
      'accept',
      '.xlsx,.xlsb,.xls,.pdf,.png,.jpg,.jpeg,.webp,application/pdf,image/png,image/jpeg,image/webp'
    );
    expect(screen.queryByRole('button', { name: 'Upload Excel' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Upload PDF' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Upload Image' })).not.toBeInTheDocument();

    fireEvent.change(input, {
      target: { files: [new File(['pdf-bytes'], 'request.pdf', { type: 'application/pdf' })] },
    });

    expect(screen.getByText('Detected: PDF')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Parse File' })).toBeEnabled();
  });

  test('rejects unsupported inquiry files before calling the parser', async () => {
    render(<InquiryManager />);
    await screen.findByText('Companies ready:');

    fireEvent.click(screen.getByRole('button', { name: 'Upload File' }));
    fireEvent.change(screen.getByLabelText('Inquiry file'), {
      target: { files: [new File(['notes'], 'request.txt', { type: 'text/plain' })] },
    });

    expect(screen.getByText('Use an Excel, PDF, PNG, JPEG, or WebP inquiry file.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Parse File' })).toBeDisabled();
    expect(quotationAPI.inquiries.parseFile).not.toHaveBeenCalled();
  });
});
