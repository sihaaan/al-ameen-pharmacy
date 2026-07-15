import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import QuotationList, { MAILBOX_AUDIT_REQUEST_BUDGET } from './QuotationList';
import quotationAPI from '../../api/quotations';

jest.mock('./CompanySelectWithCreate', () => () => <div data-testid="company-select" />);

jest.mock('../../api/quotations', () => ({
  __esModule: true,
  default: {
    quotes: {
      list: jest.fn(),
      create: jest.fn(),
    },
    mailboxPOAudits: {
      latest: jest.fn(),
      start: jest.fn(),
      scanPage: jest.fn(),
      reconcile: jest.fn(),
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
    quotationAPI.mailboxPOAudits.latest.mockResolvedValue({
      data: { run: null, match_run: null, inventory_done: false, done: false },
    });
  });

  test('separates unparsed candidates, parsed matches, and matches needing assignment', async () => {
    render(<QuotationList onOpenQuote={jest.fn()} onReviewOutcome={jest.fn()} />);

    expect(await screen.findByText('Q-0021')).toBeInTheDocument();
    expect(screen.getByText('2 candidates')).toBeInTheDocument();
    expect(screen.getByText('2 need assignment')).toBeInTheDocument();
    expect(screen.getByText('1 parsed')).toBeInTheDocument();
  });

  test('shows mailbox inventory and safe-match totals returned by the audit API', async () => {
    quotationAPI.mailboxPOAudits.start.mockResolvedValue({
      data: {
        run: {
          id: 9,
          status: 'completed',
          exhausted: true,
          messages_scanned: 1724,
          result_size_estimate: 1724,
          relevant_messages: 84,
          pages_scanned: 18,
          errors: [],
        },
        match_run: {
          status: 'completed',
          errors: [],
          summary: { active_evidence: 2, ambiguous_messages: 3, unmatched_messages: 61 },
        },
        inventory_done: true,
        done: true,
      },
    });
    render(<QuotationList onOpenQuote={jest.fn()} onReviewOutcome={jest.fn()} />);

    await screen.findByText('Q-0021');
    fireEvent.click(screen.getByRole('button', { name: 'Audit New Mailbox Run' }));

    await waitFor(() => expect(quotationAPI.mailboxPOAudits.start).toHaveBeenCalledWith({ restart: false }));
    expect(await screen.findByText('1724 / ~1724 emails inventoried')).toBeInTheDocument();
    expect(screen.getByText('84 possible PO/LPO emails')).toBeInTheDocument();
    expect(screen.getByText('2 active review matches')).toBeInTheDocument();
    expect(screen.getByText('3 emails need assignment')).toBeInTheDocument();
    expect(screen.getByText('61 possible emails had no safe quote match')).toBeInTheDocument();
  });

  test('reports an exhausted inventory as incomplete when messages were tombstoned', async () => {
    quotationAPI.mailboxPOAudits.start.mockResolvedValue({
      data: {
        run: {
          id: 10,
          status: 'completed',
          exhausted: true,
          messages_scanned: 100,
          result_size_estimate: 100,
          relevant_messages: 5,
          incomplete_messages: 2,
          pages_scanned: 2,
          errors: [],
        },
        match_run: { status: 'completed', errors: [], summary: {} },
        inventory_done: true,
        inventory_complete: false,
        done: true,
      },
    });
    render(<QuotationList onOpenQuote={jest.fn()} onReviewOutcome={jest.fn()} />);

    await screen.findByText('Q-0021');
    fireEvent.click(screen.getByRole('button', { name: 'Audit New Mailbox Run' }));

    expect(await screen.findByText('2 emails could not be read after three attempts')).toBeInTheDocument();
    expect(screen.queryByText('Mailbox inventory complete')).not.toBeInTheDocument();
  });

  test('pauses explicitly at the browser request budget and resumes the saved audit', async () => {
    const activePayload = {
      run: {
        id: 11,
        status: 'running',
        exhausted: false,
        messages_scanned: 25,
        result_size_estimate: 5000,
        relevant_messages: 1,
        pages_scanned: 1,
        errors: [],
      },
      match_run: null,
      inventory_done: false,
      done: false,
    };
    quotationAPI.mailboxPOAudits.start.mockResolvedValue({ data: activePayload });
    quotationAPI.mailboxPOAudits.scanPage.mockResolvedValue({ data: activePayload });

    render(<QuotationList onOpenQuote={jest.fn()} onReviewOutcome={jest.fn()} />);

    await screen.findByText('Q-0021');
    fireEvent.click(screen.getByRole('button', { name: 'Audit New Mailbox Run' }));

    expect(await screen.findByText(
      `Paused after ${MAILBOX_AUDIT_REQUEST_BUDGET} audit requests in this browser action; progress is saved. Select Resume Mailbox Audit to continue.`,
    )).toBeInTheDocument();
    expect(quotationAPI.mailboxPOAudits.scanPage).toHaveBeenCalledTimes(MAILBOX_AUDIT_REQUEST_BUDGET - 1);

    quotationAPI.mailboxPOAudits.start.mockResolvedValueOnce({
      data: {
        ...activePayload,
        run: { ...activePayload.run, status: 'completed', exhausted: true, messages_scanned: 5000 },
        match_run: { status: 'completed', errors: [], summary: {} },
        inventory_done: true,
        inventory_complete: true,
        done: true,
      },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Resume Mailbox Audit' }));

    await waitFor(() => expect(quotationAPI.mailboxPOAudits.start).toHaveBeenCalledTimes(2));
    expect(quotationAPI.mailboxPOAudits.start).toHaveBeenLastCalledWith({ restart: false });
    expect(await screen.findByText('Mailbox inventory complete')).toBeInTheDocument();
  });
});
