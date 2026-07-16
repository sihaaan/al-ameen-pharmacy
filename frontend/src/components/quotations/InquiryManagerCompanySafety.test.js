import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import InquiryManager from './InquiryManager';
import quotationAPI from '../../api/quotations';

jest.mock('./CompanySelectWithCreate', () => ({ onChange, disabled }) => (
  <div>
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
      parseText: jest.fn(),
      createImported: jest.fn(),
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
    lines: [{ raw_name: 'Old company AI item', quantity: '1.000' }],
  },
};

describe('InquiryManager company-scoped async safety', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    quotationAPI.companies.list.mockResolvedValue({ data: [] });
    quotationAPI.contacts.list.mockResolvedValue({ data: [] });
    quotationAPI.items.list.mockResolvedValue({ data: [{ id: 11, name: 'Matched Product' }] });
    quotationAPI.inquiries.list.mockResolvedValue({ data: [] });
  });

  test('clears an AI candidate produced for the previous company', async () => {
    quotationAPI.inquiries.parseText.mockResolvedValue({ data: parsedPreview });
    render(<InquiryManager />);

    fireEvent.click((await screen.findAllByRole('button', { name: 'Choose Company 7' }))[0]);
    fireEvent.change(screen.getByPlaceholderText("Paste the customer's requested items here..."), {
      target: { value: 'Old company item' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Extract Lines' }));

    expect(await screen.findByText('1 candidate rows')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Choose Company 8' })[0]);

    await waitFor(() => expect(screen.queryByText('1 candidate rows')).not.toBeInTheDocument());
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
    fireEvent.click(screen.getByRole('button', { name: 'Save Inquiry' }));
    await screen.findByRole('button', { name: 'Saving...' });

    fireEvent.click(screen.getAllByRole('button', { name: 'Force Company 8' })[0]);
    await act(async () => {
      resolveSave({ data: { id: 501, company: 7 } });
    });

    await screen.findByRole('button', { name: 'Save Inquiry' });
    expect(screen.queryByText('Imported inquiry saved. You can now create a quotation from it.')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Inquiry Saved' })).not.toBeInTheDocument();
  });
});
