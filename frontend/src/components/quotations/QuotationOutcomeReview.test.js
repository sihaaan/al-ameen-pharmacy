import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import QuotationOutcomeReview from './QuotationOutcomeReview';
import quotationAPI from '../../api/quotations';

jest.mock('../../api/quotations', () => ({
  __esModule: true,
  default: {
    quotes: {
      outcome: jest.fn(),
      poEvidence: jest.fn(),
      poEvidenceSource: jest.fn(),
      poEvidenceAttachment: jest.fn(),
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
    quotationAPI.quotes.poEvidenceSource.mockImplementation(() => new Promise(() => {}));
    quotationAPI.quotes.poEvidenceAttachment.mockResolvedValue({
      data: new Blob(['attachment'], { type: 'application/pdf' }),
      headers: { 'content-type': 'application/pdf' },
    });
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
    expect(screen.getByText('Gmail snippet')).toBeInTheDocument();
    expect(screen.getByText('Please find our attached purchase order.')).toBeInTheDocument();
    expect(screen.getByText('LPO-7781.pdf')).toBeInTheDocument();
    expect(screen.getByText('1000 B')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /view unavailable/i })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: /approve this email link & parse/i }));

    await waitFor(() => expect(quotationAPI.quotes.parsePOEvidence).toHaveBeenCalledWith(21, {
      evidence_id: 81,
      approve_link: true,
      use_ai: true,
    }));
    expect(await screen.findByText(/email link approved.*no line outcome was applied/i)).toBeInTheDocument();
  });

  test('shows enriched evidence signals and opens the selected attachment through the authenticated API', async () => {
    const enrichedEvidence = {
      ...evidence,
      email_body_preview: 'Attached is the approved customer purchase order for delivery.',
      quote_reference_present: true,
      matched_quote_reference: 'Q-0021',
      selected_attachment_id: 'gmail-attachment-1',
      attachments: [{
        attachment_id: 'gmail-attachment-1',
        filename: 'LPO-7781.pdf',
        mime_type: 'application/pdf',
        size: 2048,
        status: 'parsed',
        line_count: 2,
      }],
      match_signals: [{ label: 'Customer domain', description: 'Exact sender domain', matched: true }],
      item_match_signals: [{ label: 'Matched items', value: '2 of 2', matched: true }],
      quantity_match_signals: { label: 'Quantities', detail: '2 exact quantities', matched: true },
      time_match_signals: { label: 'Email timing', detail: 'After quotation was sent', matched: true },
    };
    quotationAPI.quotes.outcome.mockResolvedValueOnce({
      data: { ...outcomePayload, po_evidence: [enrichedEvidence] },
    });

    const originalCreateObjectURL = window.URL.createObjectURL;
    const originalRevokeObjectURL = window.URL.revokeObjectURL;
    window.URL.createObjectURL = jest.fn(() => 'blob:evidence-attachment');
    window.URL.revokeObjectURL = jest.fn();
    const attachmentWindow = { opener: window, location: { href: '' }, close: jest.fn() };
    const openSpy = jest.spyOn(window, 'open').mockReturnValue(attachmentWindow);

    try {
      render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

      expect(await screen.findByText('LPO for Q-0021')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: /review evidence/i }));

      expect(screen.getByText('Email body')).toBeInTheDocument();
      expect(screen.getByText(/approved customer purchase order/i)).toBeInTheDocument();
      expect(screen.getAllByText('Quote reference present')).toHaveLength(1);
      expect(screen.getByText('Quote reference present · Q-0021')).toBeInTheDocument();
      expect(screen.getByText('Selected source')).toBeInTheDocument();
      expect(screen.getByText('PDF · application/pdf')).toBeInTheDocument();
      expect(screen.getByText('2.0 KB')).toBeInTheDocument();
      expect(screen.getByText('2 parsed row(s)')).toBeInTheDocument();
      expect(screen.getByText('Exact sender domain')).toBeInTheDocument();
      expect(screen.getByText('2 of 2')).toBeInTheDocument();
      expect(screen.getByText('2 exact quantities')).toBeInTheDocument();
      expect(screen.getByText('After quotation was sent')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: /^view attachment$/i }));

      await waitFor(() => expect(quotationAPI.quotes.poEvidenceAttachment).toHaveBeenCalledWith(81, 'gmail-attachment-1'));
      await waitFor(() => expect(attachmentWindow.location.href).toBe('blob:evidence-attachment'));
      expect(attachmentWindow.opener).toBeNull();
    } finally {
      openSpy.mockRestore();
      window.URL.createObjectURL = originalCreateObjectURL;
      window.URL.revokeObjectURL = originalRevokeObjectURL;
    }
  });

  test('loads source text lazily only after evidence is opened', async () => {
    quotationAPI.quotes.poEvidenceSource.mockResolvedValueOnce({
      data: {
        id: 81,
        extracted_text: 'Full selected LPO source loaded on demand.',
        extracted_text_truncated: false,
        email_body_text: '',
      },
    });

    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

    expect(await screen.findByText('LPO for Q-0021')).toBeInTheDocument();
    expect(quotationAPI.quotes.poEvidenceSource).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /review evidence/i }));

    await waitFor(() => expect(quotationAPI.quotes.poEvidenceSource).toHaveBeenCalledWith(81));
    expect(await screen.findByText('Full selected LPO source loaded on demand.')).toBeInTheDocument();
  });

  test('downloads active attachment types without navigating a same-origin blob', async () => {
    const activeAttachmentEvidence = {
      ...evidence,
      attachments: [{
        attachment_id: 'active-html-1',
        filename: 'customer-message.html',
        mime_type: 'text/html',
        size: 512,
        status: 'available',
      }],
    };
    quotationAPI.quotes.outcome.mockResolvedValueOnce({
      data: { ...outcomePayload, po_evidence: [activeAttachmentEvidence] },
    });
    quotationAPI.quotes.poEvidenceAttachment.mockResolvedValueOnce({
      data: new Blob(['<script>window.opener.hacked = true</script>'], { type: 'text/html' }),
      headers: { 'content-type': 'text/html; charset=utf-8' },
    });

    const originalCreateObjectURL = window.URL.createObjectURL;
    const originalRevokeObjectURL = window.URL.revokeObjectURL;
    window.URL.createObjectURL = jest.fn(() => 'blob:active-attachment');
    window.URL.revokeObjectURL = jest.fn();
    const openSpy = jest.spyOn(window, 'open').mockReturnValue(null);
    let clickedDownload = null;
    const clickSpy = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function click() {
      clickedDownload = {
        href: this.href,
        download: this.download,
        target: this.target,
      };
    });

    try {
      render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

      expect(await screen.findByText('LPO for Q-0021')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: /review evidence/i }));
      fireEvent.click(screen.getByRole('button', { name: /^download attachment$/i }));

      await waitFor(() => expect(quotationAPI.quotes.poEvidenceAttachment).toHaveBeenCalledWith(81, 'active-html-1'));
      await waitFor(() => expect(clickedDownload).not.toBeNull());
      expect(openSpy).not.toHaveBeenCalled();
      expect(window.URL.createObjectURL.mock.calls[0][0].type).toBe('application/octet-stream');
      expect(clickedDownload).toEqual({
        href: 'blob:active-attachment',
        download: 'customer-message.html',
        target: '',
      });
    } finally {
      clickSpy.mockRestore();
      openSpy.mockRestore();
      window.URL.createObjectURL = originalCreateObjectURL;
      window.URL.revokeObjectURL = originalRevokeObjectURL;
    }
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

  test('loads additional archived evidence pages without replacing active evidence', async () => {
    const firstArchived = { ...evidence, id: 85, status: 'superseded', subject: 'First archived match' };
    const nextArchived = { ...evidence, id: 86, status: 'not_relevant', subject: 'Next archived match' };
    quotationAPI.quotes.outcome.mockResolvedValueOnce({
      data: {
        ...outcomePayload,
        po_evidence: [evidence, firstArchived],
        po_evidence_pagination: {
          active_count: 1,
          archived_count: 21,
          archived_has_more: true,
          archived_next_offset: 20,
        },
      },
    });
    quotationAPI.quotes.poEvidence.mockResolvedValueOnce({
      data: {
        results: [evidence, nextArchived],
        pagination: {
          active_count: 1,
          archived_count: 21,
          archived_has_more: false,
          archived_next_offset: null,
        },
      },
    });

    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

    expect(await screen.findByText('Archived evidence (21)')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Archived evidence (21)'));
    fireEvent.click(screen.getByRole('button', { name: /load more archived evidence/i }));

    await waitFor(() => expect(quotationAPI.quotes.poEvidence).toHaveBeenCalledWith(21, { archived_offset: 20 }));
    expect(screen.getByText('LPO for Q-0021')).toBeInTheDocument();
    expect(await screen.findByText('Next archived match')).toBeInTheDocument();
  });

  test('refetches complete mailbox-wide evidence without a capped per-quote Gmail search', async () => {
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
    quotationAPI.quotes.poEvidence.mockResolvedValueOnce({
      data: { results: [refreshedEvidence, archivedEvidence] },
    });

    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

    expect(await screen.findByText('Archived evidence (1)')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /refresh evidence/i }));

    await waitFor(() => expect(quotationAPI.quotes.poEvidence).toHaveBeenCalledWith(21));
    expect(quotationAPI.quotes.findPOEvidence).not.toHaveBeenCalled();
    expect(await screen.findByText('New current match')).toBeInTheDocument();
    expect(screen.getByText('Archived evidence (1)')).toBeInTheDocument();
    expect(screen.getByText(/mailbox-wide evidence refreshed/i)).toBeInTheDocument();
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
