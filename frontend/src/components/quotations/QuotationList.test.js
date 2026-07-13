import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import QuotationList from './QuotationList';
import quotationAPI from '../../api/quotations';

jest.mock('./CompanySelectWithCreate', () => () => <div data-testid="company-select" />);

jest.mock('../../api/quotations', () => ({
  __esModule: true,
  default: {
    quotes: {
      list: jest.fn(),
      create: jest.fn(),
      scanPOEvidence: jest.fn(),
    },
    companies: {
      list: jest.fn(),
    },
    contacts: {
      list: jest.fn(),
      create: jest.fn(),
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

const quotation = {
  id: 21,
  quotation_number: 'Q-0021',
  company_name: 'Customer A',
  created_by_username: 'sihan',
  status: 'sent',
  outcome_status: 'pending',
  version: 1,
  currency: 'AED',
  total: '125.00',
  updated_at: '2026-07-02T08:00:00Z',
  po_evidence_candidate_count: 3,
  po_evidence_parsed_count: 1,
  po_evidence_ambiguous_count: 2,
};

describe('QuotationList PO/LPO evidence summaries', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    quotationAPI.quotes.list.mockResolvedValue({ data: [quotation] });
    quotationAPI.companies.list.mockResolvedValue({ data: [] });
  });

  test('separates unparsed candidates, parsed matches, and matches needing assignment', async () => {
    render(<QuotationList onOpenQuote={jest.fn()} onReviewOutcome={jest.fn()} />);

    expect(await screen.findByText('Q-0021')).toBeInTheDocument();
    expect(screen.getByText('2 candidates')).toBeInTheDocument();
    expect(screen.getByText('2 need assignment')).toBeInTheDocument();
    expect(screen.getByText('1 parsed')).toBeInTheDocument();
  });

  test('shows separate candidate and ambiguous totals returned by the scan API', async () => {
    quotationAPI.quotes.scanPOEvidence.mockResolvedValue({
      data: {
        processed: 1,
        candidates_found: 2,
        ambiguous_found: 3,
        incomplete_scans: 1,
        remaining: 0,
        done: true,
        errors: [],
        quotes: [],
      },
    });
    render(<QuotationList onOpenQuote={jest.fn()} onReviewOutcome={jest.fn()} />);

    await screen.findByText('Q-0021');
    fireEvent.click(screen.getByRole('button', { name: 'Scan Sent Quotes' }));

    await waitFor(() => expect(quotationAPI.quotes.scanPOEvidence).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('2 candidate/parsed email matches')).toBeInTheDocument();
    expect(screen.getByText('3 need assignment')).toBeInTheDocument();
    expect(screen.getByText('1 partial scan (older evidence preserved)')).toBeInTheDocument();
    expect(screen.getByText('1 quotation(s) checked')).toBeInTheDocument();
  });
});
