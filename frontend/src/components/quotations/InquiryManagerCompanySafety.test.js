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

  test('applies AI cleanup once and clears company-scoped matches after company changes', async () => {
    quotationAPI.inquiries.parseText.mockResolvedValue({ data: parsedPreview });
    render(<InquiryManager />);

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
    render(<InquiryManager />);

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
    render(<InquiryManager />);

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
    render(<InquiryManager />);

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
    render(<InquiryManager />);

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
    render(<InquiryManager />);

    fireEvent.change(screen.getByPlaceholderText("Paste the customer's requested items here..."), {
      target: { value: 'Old company item' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Extract Lines' }));
    await screen.findByLabelText('Requested item name row 1');

    fireEvent.click(screen.getByRole('button', { name: 'Upload File' }));
    expect(screen.queryByLabelText('Requested item name row 1')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save & Open Quotation' })).not.toBeInTheDocument();
  });

  test('blurs inquiry unit price on wheel so scrolling cannot change it', async () => {
    quotationAPI.inquiries.parseText.mockResolvedValue({
      data: {
        ...parsedPreview,
        ai_candidate: null,
        lines: [{ raw_name: 'Priced item', quantity: '1.000', unit_price: '12.50', parse_status: 'parsed' }],
      },
    });
    render(<InquiryManager />);

    fireEvent.change(screen.getByPlaceholderText("Paste the customer's requested items here..."), {
      target: { value: 'Priced item' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Extract Lines' }));
    const price = await screen.findByLabelText('Unit price row 1');
    price.focus();
    fireEvent.wheel(price, { deltaY: 100 });
    expect(document.activeElement).not.toBe(price);
    expect(price).toHaveValue(12.5);
  });

  test('saves, creates one quotation, and opens it in a single action', async () => {
    const onOpenQuote = jest.fn();
    quotationAPI.inquiries.parseText.mockResolvedValue({ data: { ...parsedPreview, ai_candidate: null } });
    quotationAPI.inquiries.createImported.mockResolvedValue({ data: { id: 501, company: 7 } });
    render(<InquiryManager onOpenQuote={onOpenQuote} />);

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
    render(<InquiryManager onOpenQuote={jest.fn()} />);

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
      data: { ...parsedPreview, source_type: 'image', source_filename: 'request.png', ai_candidate: null },
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
