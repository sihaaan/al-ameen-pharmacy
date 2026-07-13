import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import QuotationOutcomeReview from './QuotationOutcomeReview';
import quotationAPI from '../../api/quotations';

jest.mock('../../api/quotations', () => ({
  __esModule: true,
  default: {
    quotes: {
      outcome: jest.fn(),
      poEvidence: jest.fn(),
      parsePOEvidence: jest.fn(),
      markPOEvidenceNotRelevant: jest.fn(),
      findPOEvidence: jest.fn(),
      updateOutcome: jest.fn(),
      parseOutcomePO: jest.fn(),
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

const evidence = {
  id: 81,
  status: 'candidate',
  subject: 'LPO for Q-0021',
  sender: 'buyer@customer.example',
  recipients: 'sales@pharmacy.example',
  mailbox_email: 'sales@pharmacy.example',
  sent_at: '2026-07-02T08:00:00Z',
  confidence: 91,
  matching_reason: 'Quotation number Q-0021 matched; customer sender domain matched',
  snippet: 'Please find our attached purchase order.',
  attachment_count: 1,
  attachments: [{ filename: 'LPO-7781.pdf', mime_type: 'application/pdf', size: 1000 }],
};

const outcomePayload = {
  quotation: {
    id: 21,
    quotation_number: 'Q-0021',
    company_name: 'Customer A',
    status: 'sent',
    status_display: 'Sent',
    outcome_status: 'pending',
    outcome_status_is_manual: false,
    outcome_notes: '',
    next_follow_up_date: '',
    follow_up_status: 'open',
    follow_up_contact_method: '',
    follow_up_notes: '',
    currency: 'AED',
    lines: [],
  },
  summary: {
    quoted_value: 0,
    accepted_value: 0,
    lost_value: 0,
    value_win_rate: 0,
    line_win_rate: 0,
    pending_lines: 0,
  },
  po_evidence: [evidence],
};

describe('QuotationOutcomeReview Gmail approval', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    quotationAPI.quotes.outcome.mockResolvedValue({ data: outcomePayload });
    quotationAPI.quotes.poEvidence.mockResolvedValue({ data: { results: [{ ...evidence, status: 'parsed' }] } });
    quotationAPI.quotes.parsePOEvidence.mockResolvedValue({
      data: {
        suggestions: [],
        unmatched_po_rows: [],
        missing_quote_line_ids: [],
        canonical_lpo: { id: 9, status: 'needs_review' },
      },
    });
  });

  test('requires review and sends explicit approval before parsing an email link', async () => {
    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

    expect(await screen.findByText('LPO for Q-0021')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /parse & suggest/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /review evidence/i }));

    expect(screen.getAllByText('sales@pharmacy.example')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: /approve this email link & parse/i }));

    await waitFor(() => expect(quotationAPI.quotes.parsePOEvidence).toHaveBeenCalledWith(21, {
      evidence_id: 81,
      approve_link: true,
      use_ai: true,
    }));
    expect(await screen.findByText(/email link approved.*no line outcome was applied/i)).toBeInTheDocument();
  });

  test('labels an ambiguous match as needing assignment and allows explicit assignment', async () => {
    const ambiguousEvidence = {
      ...evidence,
      id: 82,
      status: 'ambiguous',
      subject: 'Possible LPO for Customer A',
      error: 'Ambiguous: this Gmail message can match multiple quotations.',
    };
    quotationAPI.quotes.outcome.mockResolvedValueOnce({
      data: { ...outcomePayload, po_evidence: [ambiguousEvidence] },
    });

    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

    expect(await screen.findByText('Possible LPO for Customer A')).toBeInTheDocument();
    expect(screen.getByText('Needs assignment')).toBeInTheDocument();
    expect(screen.getByText(/this email may belong to more than one quotation/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /review evidence/i }));

    const assignButton = screen.getByRole('button', { name: /assign to this quotation & parse/i });
    expect(assignButton).toBeEnabled();
    fireEvent.click(assignButton);

    await waitFor(() => expect(quotationAPI.quotes.parsePOEvidence).toHaveBeenCalledWith(21, {
      evidence_id: 82,
      approve_link: true,
      use_ai: true,
    }));
  });

  test('keeps active evidence visible while separating archived scan history', async () => {
    quotationAPI.quotes.outcome.mockResolvedValueOnce({
      data: {
        ...outcomePayload,
        po_evidence: [
          evidence,
          { ...evidence, id: 85, status: 'superseded', subject: 'Old superseded match' },
        ],
      },
    });

    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

    expect(await screen.findByText('Active evidence')).toBeInTheDocument();
    expect(screen.getByText('LPO for Q-0021')).toBeInTheDocument();
    expect(screen.getByText('Archived evidence (1)')).toBeInTheDocument();
  });

  test('refetches complete evidence history after finding current Gmail matches', async () => {
    const archivedEvidence = {
      ...evidence,
      id: 86,
      status: 'superseded',
      subject: 'Archived earlier match',
    };
    const refreshedEvidence = {
      ...evidence,
      id: 87,
      subject: 'New current match',
    };
    quotationAPI.quotes.outcome.mockResolvedValueOnce({
      data: { ...outcomePayload, po_evidence: [evidence, archivedEvidence] },
    });
    quotationAPI.quotes.findPOEvidence.mockResolvedValueOnce({
      data: {
        count: 1,
        ambiguous_count: 0,
        results: [refreshedEvidence],
      },
    });
    quotationAPI.quotes.poEvidence.mockResolvedValueOnce({
      data: { results: [refreshedEvidence, archivedEvidence] },
    });

    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

    expect(await screen.findByText('Archived evidence (1)')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /find gmail evidence/i }));

    await waitFor(() => expect(quotationAPI.quotes.poEvidence).toHaveBeenCalledWith(21));
    expect(await screen.findByText('New current match')).toBeInTheDocument();
    expect(screen.getByText('Archived evidence (1)')).toBeInTheDocument();
    expect(screen.getByText(/found 1 candidate\/parsed gmail match.*0 need assignment/i)).toBeInTheDocument();
  });

  test.each([
    ['superseded', 'Superseded (archived)'],
    ['not_relevant', 'Not relevant (archived)'],
  ])('keeps %s evidence in audit history and disables parsing', async (status, statusLabel) => {
    const archivedEvidence = {
      ...evidence,
      id: status === 'superseded' ? 83 : 84,
      status,
      subject: `${statusLabel} email`,
    };
    quotationAPI.quotes.outcome.mockResolvedValueOnce({
      data: { ...outcomePayload, po_evidence: [archivedEvidence] },
    });

    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

    expect(await screen.findByText(/no active gmail evidence/i)).toBeInTheDocument();
    expect(screen.queryByText(/active evidence/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('Archived evidence (1)'));
    expect(screen.getByText(statusLabel)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /view archived evidence/i }));

    expect(screen.getByRole('button', { name: /archived - cannot parse/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /mark not relevant/i })).toBeDisabled();
    expect(quotationAPI.quotes.parsePOEvidence).not.toHaveBeenCalled();
  });
});
