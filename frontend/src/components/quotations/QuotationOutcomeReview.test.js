import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
    quotationAPI.quotes.updateOutcome.mockResolvedValue({ data: outcomePayload });
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
    expect(await screen.findByText(/email link approved and parsed.*no customer quantity\/price pair was safe enough/i)).toBeInTheDocument();
    expect(quotationAPI.quotes.updateOutcome).not.toHaveBeenCalled();
  });

  test('stages safe customer values inline and records import provenance only when staff saves', async () => {
    quotationAPI.quotes.outcome.mockResolvedValueOnce({
      data: {
        ...outcomePayload,
        quotation: {
          ...outcomePayload.quotation,
          lines: [{
            id: 501,
            item_name_snapshot: 'Bandage Pack',
            quantity: '2.000',
            unit: 'pack',
            unit_price: '12.00',
            line_total: '25.20',
            outcome_status: 'rejected',
            outcome_reason: 'not_available',
            outcome_notes: 'Previously rejected before the LPO arrived.',
          }],
        },
      },
    });
    quotationAPI.quotes.parsePOEvidence.mockResolvedValueOnce({
      data: {
        id: 77,
        suggestions: [{
          quotation_line_id: 501,
          requested_item_name: 'Bandage Pack',
          po_quantity: '2.000',
          po_unit_price: '10.00',
          suggested_outcome_status: 'accepted',
          suggested_accepted_quantity: '2.000',
          suggested_accepted_unit_price: '10.00',
          reason: 'Exact item and quantity match',
        }],
        unmatched_po_rows: [],
        missing_quote_line_ids: [],
        canonical_lpo: { id: 9, status: 'needs_review' },
      },
    });
    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

    expect(await screen.findByText('LPO for Q-0021')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /review evidence/i }));
    fireEvent.click(screen.getByRole('button', { name: /approve this email link & parse/i }));

    await waitFor(() => expect(screen.getByRole('spinbutton', { name: /accepted quantity for bandage pack/i })).toHaveValue(2));
    expect(screen.getByRole('spinbutton', { name: /accepted unit price for bandage pack/i })).toHaveValue(10);
    fireEvent.change(screen.getByLabelText('Notes'), { target: { value: 'Call purchasing tomorrow.' } });
    fireEvent.change(screen.getByRole('combobox', { name: /override status/i }), { target: { value: 'lost' } });
    expect(quotationAPI.quotes.updateOutcome).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole('button', { name: /save staged lpo decisions/i }));

    await waitFor(() => expect(quotationAPI.quotes.updateOutcome).toHaveBeenCalledWith(21, {
      line_updates: [{
        id: 501,
        outcome_status: 'accepted',
        accepted_quantity: '2.000',
        accepted_unit_price: '10.00',
        outcome_reason: '',
        outcome_notes: 'LPO review: Exact item and quantity match',
      }],
      po_import_id: 77,
      applied_po_line_ids: [501],
    }));
    expect(screen.getByLabelText('Notes')).toHaveValue('Call purchasing tomorrow.');
    expect(screen.getByRole('combobox', { name: /override status/i })).toHaveValue('lost');
  });

  test('keeps the existing staged import and provenance when a replacement PO parse fails', async () => {
    quotationAPI.quotes.outcome.mockResolvedValueOnce({
      data: {
        ...outcomePayload,
        quotation: {
          ...outcomePayload.quotation,
          lines: [{
            id: 501,
            item_name_snapshot: 'Bandage Pack',
            quantity: '2.000',
            unit: 'pack',
            unit_price: '12.00',
            line_total: '25.20',
          }],
        },
      },
    });
    quotationAPI.quotes.parsePOEvidence.mockResolvedValueOnce({
      data: {
        id: 77,
        suggestions: [{
          quotation_line_id: 501,
          po_item_name: 'Bandage Pack',
          po_quantity: '2.000',
          po_unit_price: '10.00',
          suggested_outcome_status: 'accepted',
          reason: 'Exact item and quantity match',
        }],
        unmatched_po_rows: [],
        missing_quote_line_ids: [],
      },
    });
    quotationAPI.quotes.parseOutcomePO.mockRejectedValueOnce(new Error('Replacement parse failed'));

    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

    expect(await screen.findByText('LPO for Q-0021')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /review evidence/i }));
    fireEvent.click(screen.getByRole('button', { name: /approve this email link & parse/i }));
    await waitFor(() => expect(screen.getByRole('spinbutton', { name: /accepted unit price for bandage pack/i })).toHaveValue(10));

    fireEvent.change(screen.getByRole('textbox', { name: /paste po text/i }), {
      target: { value: 'Replacement customer PO text' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^parse po$/i }));
    await waitFor(() => expect(quotationAPI.quotes.parseOutcomePO).toHaveBeenCalled());

    expect(screen.getByRole('spinbutton', { name: /accepted unit price for bandage pack/i })).toHaveValue(10);
    const saveStaged = screen.getByRole('button', { name: /save staged lpo decisions/i });
    await waitFor(() => expect(saveStaged).toBeEnabled());
    fireEvent.click(saveStaged);

    await waitFor(() => expect(quotationAPI.quotes.updateOutcome).toHaveBeenCalledWith(21, expect.objectContaining({
      po_import_id: 77,
      applied_po_line_ids: [501],
    })));
  });

  test('serializes evidence review while an earlier LPO parse is still pending', async () => {
    const quoteLine = {
      id: 501,
      item_name_snapshot: 'Bandage Pack',
      quantity: '2.000',
      unit: 'pack',
      unit_price: '12.00',
      line_total: '25.20',
    };
    const evidenceA = { ...evidence, id: 81, subject: 'Customer LPO A' };
    const evidenceB = { ...evidence, id: 82, subject: 'Customer LPO B' };
    let resolveParse;
    const pendingParse = new Promise((resolve) => {
      resolveParse = resolve;
    });
    quotationAPI.quotes.outcome.mockResolvedValueOnce({
      data: {
        ...outcomePayload,
        quotation: { ...outcomePayload.quotation, lines: [quoteLine] },
        po_evidence: [evidenceA, evidenceB],
      },
    });
    quotationAPI.quotes.parsePOEvidence.mockReturnValueOnce(pendingParse);
    quotationAPI.quotes.poEvidence.mockResolvedValueOnce({
      data: {
        results: [
          { ...evidenceA, status: 'parsed' },
          evidenceB,
        ],
      },
    });

    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

    const cardA = (await screen.findByText('Customer LPO A')).closest('article');
    const cardB = screen.getByText('Customer LPO B').closest('article');
    fireEvent.click(within(cardA).getByRole('button', { name: /review evidence/i }));
    fireEvent.click(screen.getByRole('button', { name: /approve this email link & parse/i }));

    await waitFor(() => expect(within(cardB).getByRole('button', { name: /review evidence/i })).toBeDisabled());
    fireEvent.click(within(cardB).getByRole('button', { name: /review evidence/i }));
    expect(screen.queryByRole('region', { name: /customer a - customer lpo b/i })).not.toBeInTheDocument();

    await act(async () => {
      resolveParse({
        data: {
          id: 77,
          suggestions: [{
            quotation_line_id: 501,
            po_item_name: 'Bandage Pack',
            po_quantity: '2.000',
            po_unit_price: '10.00',
            suggested_outcome_status: 'accepted',
            reason: 'Exact item and quantity match',
          }],
          unmatched_po_rows: [],
          missing_quote_line_ids: [],
        },
      });
      await pendingParse;
    });

    await waitFor(() => expect(screen.getByRole('spinbutton', { name: /accepted unit price for bandage pack/i })).toHaveValue(10));
    expect(screen.getByRole('region', { name: /customer a - customer lpo a/i })).toBeInTheDocument();
    await waitFor(() => expect(
      within(screen.getByText('Customer LPO B').closest('article')).getByRole('button', { name: /review evidence/i })
    ).toBeEnabled());
    expect(quotationAPI.quotes.updateOutcome).not.toHaveBeenCalled();
  });

  test('preserves staged decisions and prevents parsed evidence from being marked not relevant', async () => {
    quotationAPI.quotes.outcome.mockResolvedValueOnce({
      data: {
        ...outcomePayload,
        quotation: {
          ...outcomePayload.quotation,
          lines: [{
            id: 501,
            item_name_snapshot: 'Bandage Pack',
            quantity: '2.000',
            unit: 'pack',
            unit_price: '12.00',
            line_total: '25.20',
          }],
        },
      },
    });
    quotationAPI.quotes.parsePOEvidence.mockResolvedValueOnce({
      data: {
        id: 77,
        suggestions: [{
          quotation_line_id: 501,
          po_item_name: 'Bandage Pack',
          po_quantity: '2.000',
          po_unit_price: '10.00',
          suggested_outcome_status: 'accepted',
          reason: 'Exact item and quantity match',
        }],
        unmatched_po_rows: [],
        missing_quote_line_ids: [],
      },
    });
    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

    expect(await screen.findByText('LPO for Q-0021')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /review evidence/i }));
    fireEvent.click(screen.getByRole('button', { name: /approve this email link & parse/i }));
    await waitFor(() => expect(screen.getByRole('spinbutton', { name: /accepted unit price for bandage pack/i })).toHaveValue(10));

    const immutableButton = await screen.findByRole('button', { name: /approved evidence cannot be rejected/i });
    expect(immutableButton).toBeDisabled();
    fireEvent.click(immutableButton);

    expect(screen.getByRole('spinbutton', { name: /accepted unit price for bandage pack/i })).toHaveValue(10);
    expect(screen.getByRole('button', { name: /save staged lpo decisions/i })).toBeEnabled();
    expect(screen.getByRole('region', { name: /customer a - lpo for q-0021/i })).toBeInTheDocument();
    expect(quotationAPI.quotes.markPOEvidenceNotRelevant).not.toHaveBeenCalled();
    expect(quotationAPI.quotes.updateOutcome).not.toHaveBeenCalled();
  });

  test('prevents link-approved evidence from being rejected after a parse failure', async () => {
    const approvedEvidence = {
      ...evidence,
      status: 'parse_failed',
      link_approved_at: '2026-07-02T09:00:00Z',
      error: 'The approved attachment could not be parsed.',
    };
    quotationAPI.quotes.outcome.mockResolvedValueOnce({
      data: { ...outcomePayload, po_evidence: [approvedEvidence] },
    });

    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

    expect(await screen.findByRole('button', { name: /approved - cannot reject/i })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: /review evidence/i }));
    const immutableButton = screen.getByRole('button', { name: /approved evidence cannot be rejected/i });
    expect(immutableButton).toBeDisabled();
    fireEvent.click(immutableButton);

    expect(quotationAPI.quotes.markPOEvidenceNotRelevant).not.toHaveBeenCalled();
  });

  test('switches between previously parsed LPOs without leaking staged values and saves the selected import', async () => {
    const quoteLine = {
      id: 501,
      item_name_snapshot: 'Bandage Pack',
      quantity: '2.000',
      unit: 'pack',
      unit_price: '12.00',
      line_total: '25.20',
    };
    const comparisonFor = (lpoNumber, unitPrice, reason) => ({
      company_name: 'Customer A',
      quotation_number: 'Q-0021',
      lpo_number: lpoNumber,
      currency: 'AED',
      lines: [{
        quotation_line_id: 501,
        quote_item_name: 'Bandage Pack',
        lpo_item_name: 'Bandage Pack',
        quoted_quantity: '2.000',
        accepted_quantity: '2.000',
        quoted_unit_price: '12.00',
        accepted_unit_price: unitPrice,
        accepted_line_total: String(Number(unitPrice) * 2),
        status: 'repriced',
        confidence: 98,
        reason,
      }],
      unmatched_lpo_rows: [],
    });
    const evidenceA = {
      ...evidence,
      id: 81,
      status: 'parsed',
      subject: 'Customer LPO A',
      commercial_comparison: comparisonFor('PO-A', '9.50', 'Customer accepted PO-A pricing.'),
    };
    const evidenceB = {
      ...evidence,
      id: 82,
      status: 'parsed',
      subject: 'Customer LPO B',
      commercial_comparison: comparisonFor('PO-B', '8.75', 'Customer accepted PO-B pricing.'),
    };
    const importFor = (id, unitPrice, reason) => ({
      id,
      suggestions: [{
        quotation_line_id: 501,
        po_item_name: 'Bandage Pack',
        po_quantity: '2.000',
        po_unit_price: unitPrice,
        reason,
      }],
      missing_quote_line_ids: [],
      unmatched_po_rows: [],
    });
    quotationAPI.quotes.outcome.mockResolvedValueOnce({
      data: {
        ...outcomePayload,
        quotation: { ...outcomePayload.quotation, lines: [quoteLine] },
        po_evidence: [evidenceA, evidenceB],
      },
    });
    quotationAPI.quotes.poEvidenceSource.mockImplementation((evidenceId) => Promise.resolve({
      data: {
        id: evidenceId,
        commercial_comparison: evidenceId === 81
          ? evidenceA.commercial_comparison
          : evidenceB.commercial_comparison,
        latest_po_import: evidenceId === 81
          ? importFor(771, '9.50', 'Customer accepted PO-A pricing.')
          : importFor(772, '8.75', 'Customer accepted PO-B pricing.'),
      },
    }));

    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

    const cardA = (await screen.findByText('Customer LPO A')).closest('article');
    fireEvent.click(within(cardA).getByRole('button', { name: /review evidence/i }));
    expect((await screen.findAllByText('PO-A')).length).toBeGreaterThanOrEqual(1);
    fireEvent.click(await screen.findByRole('button', { name: /populate line outcomes from this lpo/i }));
    await waitFor(() => expect(screen.getByRole('spinbutton', { name: /accepted unit price for bandage pack/i })).toHaveValue(9.5));
    expect(quotationAPI.quotes.updateOutcome).not.toHaveBeenCalled();

    const cardB = screen.getByText('Customer LPO B').closest('article');
    fireEvent.click(within(cardB).getByRole('button', { name: /review evidence/i }));
    expect((await screen.findAllByText('PO-B')).length).toBeGreaterThanOrEqual(1);
    await waitFor(() => expect(screen.getByRole('spinbutton', { name: /accepted unit price for bandage pack/i })).toHaveValue(null));
    fireEvent.click(await screen.findByRole('button', { name: /populate line outcomes from this lpo/i }));
    expect(screen.getByRole('spinbutton', { name: /accepted unit price for bandage pack/i })).toHaveValue(8.75);

    fireEvent.click(screen.getByRole('button', { name: /save staged lpo decisions/i }));
    await waitFor(() => expect(quotationAPI.quotes.updateOutcome).toHaveBeenCalledWith(21, {
      line_updates: [{
        id: 501,
        outcome_status: 'accepted',
        accepted_quantity: '2.000',
        accepted_unit_price: '8.75',
        outcome_reason: '',
        outcome_notes: 'LPO review: Customer accepted PO-B pricing.',
      }],
      po_import_id: 772,
      applied_po_line_ids: [501],
    }));
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
      fireEvent.click(screen.getByText('Email text and matching diagnostics'));

      expect(screen.getByText('Email body')).toBeInTheDocument();
      expect(screen.getByText(/approved customer purchase order/i)).toBeInTheDocument();
      expect(screen.getAllByText('Quote reference present')).toHaveLength(1);
      expect(screen.getByText('Quote reference present · Q-0021')).toBeInTheDocument();
      expect(screen.getAllByText('Selected source').length).toBeGreaterThanOrEqual(2);
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

  test('shows company, totals, and every commercial decision inline beside Line Outcomes', async () => {
    const quoteLines = [
      { id: 501, item_name_snapshot: 'Bandage Pack', quantity: '10', unit: 'pack', unit_price: '10.00', line_total: '105.00' },
      { id: 502, item_name_snapshot: 'Surgical Mask', quantity: '5', unit: 'box', unit_price: '4.00', line_total: '21.00' },
      { id: 503, item_name_snapshot: 'Gauze Roll', quantity: '8', unit: 'roll', unit_price: '2.00', line_total: '16.80' },
      { id: 504, item_name_snapshot: 'Examination Gloves', quantity: '1', unit: 'box', unit_price: '20.00', line_total: '21.00' },
    ];
    quotationAPI.quotes.outcome.mockResolvedValueOnce({
      data: {
        ...outcomePayload,
        quotation: { ...outcomePayload.quotation, lines: quoteLines },
        summary: { ...outcomePayload.summary, quoted_value: '163.80', pending_lines: 4 },
      },
    });
    quotationAPI.quotes.poEvidenceSource.mockResolvedValueOnce({
      data: {
        id: 81,
        selected_source_kind: 'attachment',
        commercial_comparison: {
          company_name: 'Customer A',
          quotation_number: 'Q-0021',
          currency: 'AED',
          quotation_subtotal: '156.00',
          quotation_vat_total: '7.80',
          quotation_total: '163.80',
          lpo_number: 'PO-7781',
          lpo_total: '156.00',
          total_result: 'exact',
          total_basis: 'quotation_subtotal_ex_vat',
          complete_for_missing_lines: true,
          lines: [
            {
              quotation_line_id: 501,
              quote_item_name: 'Bandage Pack',
              lpo_item_name: 'Bandages',
              quoted_quantity: '10',
              accepted_quantity: '10',
              quoted_unit: 'pack',
              accepted_unit: 'pack',
              quoted_unit_price: '10.00',
              accepted_unit_price: '10.00',
              quoted_line_total: '105.00',
              accepted_line_total: '100.00',
              status: 'accepted',
              confidence: 98,
              reason: 'Exact item, quantity, and unit price.',
            },
            {
              quotation_line_id: 502,
              quote_item_name: 'Surgical Mask',
              lpo_item_name: 'Masks',
              quoted_quantity: '5',
              accepted_quantity: '5',
              quoted_unit: 'box',
              accepted_unit: 'box',
              quoted_unit_price: '4.00',
              accepted_unit_price: null,
              quoted_line_total: '21.00',
              accepted_line_total: null,
              status: 'accepted_price_not_stated',
              review_required: true,
              reason: 'The customer document did not state a unit price.',
            },
            {
              quotation_line_id: 503,
              quote_item_name: 'Gauze Roll',
              lpo_item_name: 'Gauze',
              quoted_quantity: '8',
              accepted_quantity: '4',
              quoted_unit_price: '2.00',
              accepted_unit_price: '2.00',
              quoted_line_total: '16.80',
              accepted_line_total: '8.00',
              status: 'reduced',
              reason: 'Customer ordered a lower quantity.',
            },
            {
              quotation_line_id: 504,
              quote_item_name: 'Examination Gloves',
              quoted_quantity: '1',
              quoted_unit_price: '20.00',
              quoted_line_total: '21.00',
              status: 'not_ordered',
              reason: 'Not present on this LPO; no explicit rejection statement was found.',
            },
          ],
          unmatched_lpo_rows: [{
            row_number: 5,
            item_name: 'Aspirin 100 mg',
            quantity: '2',
            unit: 'box',
            unit_price: '7.50',
            line_total: '15.00',
            reason: 'No confident quotation-line match.',
          }],
        },
      },
    });

    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

    expect(await screen.findByText('LPO for Q-0021')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /review evidence/i }));
    const reviewRegion = screen.getByRole('region', { name: /customer a - lpo for q-0021/i });
    const table = screen.getByRole('table', { name: /quotation lines compared with the selected customer lpo/i });

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(await within(reviewRegion).findByText('Customer A')).toBeInTheDocument();
    expect(within(reviewRegion).getByText('Q-0021')).toBeInTheDocument();
    expect(within(reviewRegion).getByText('PO-7781')).toBeInTheDocument();
    expect(within(reviewRegion).getByText('AED 163.80')).toBeInTheDocument();
    expect(within(reviewRegion).getByText('Matches quote subtotal before VAT')).toBeInTheDocument();
    expect(within(table).getByRole('columnheader', { name: 'Our quotation' })).toBeInTheDocument();
    expect(within(table).getByRole('columnheader', { name: 'Customer LPO' })).toBeInTheDocument();
    expect(within(table).getByText('Bandages')).toBeInTheDocument();
    expect(within(table).getByText('AED 100.00')).toBeInTheDocument();
    expect(within(table).getByText('Accepted as quoted')).toBeInTheDocument();
    expect(within(table).getByText('Accepted - price not stated')).toBeInTheDocument();
    expect(within(table).getByText('Reduced quantity')).toBeInTheDocument();
    expect(within(table).getByText('Not ordered / omitted')).toBeInTheDocument();
    expect(within(table).getAllByText('Not stated').length).toBeGreaterThanOrEqual(1);
    expect(within(table).getByText('Review required')).toBeInTheDocument();
    expect(screen.getByText(/1 unmatched customer lpo row/i)).toBeInTheDocument();
    expect(screen.getByText(/aspirin 100 mg/i)).toBeInTheDocument();
  });

  test('stages parsed decisions inline and applies an omitted line only after staff explicitly checks it', async () => {
    const quoteLines = [
      { id: 501, item_name_snapshot: 'Bandage Pack', quantity: '2', unit: 'pack', unit_price: '10.00', line_total: '21.00' },
      { id: 502, item_name_snapshot: 'Surgical Mask', quantity: '3', unit: 'box', unit_price: '20.00', line_total: '63.00' },
      { id: 503, item_name_snapshot: 'Gauze Roll', quantity: '4', unit: 'roll', unit_price: '5.00', line_total: '21.00' },
      { id: 504, item_name_snapshot: 'Examination Gloves', quantity: '1', unit: 'box', unit_price: '30.00', line_total: '31.50' },
    ];
    quotationAPI.quotes.outcome.mockResolvedValueOnce({
      data: {
        ...outcomePayload,
        quotation: { ...outcomePayload.quotation, lines: quoteLines },
        summary: { ...outcomePayload.summary, quoted_value: '136.50', pending_lines: 4 },
      },
    });
    quotationAPI.quotes.parsePOEvidence.mockResolvedValueOnce({
      data: {
        id: 77,
        suggestions: [{
          quotation_line_id: 501,
          po_row_index: 1,
          po_item_name: 'Bandages ordered',
          po_quantity: '2',
          po_unit_price: '9.50',
          suggested_outcome_status: 'accepted',
          suggested_accepted_quantity: '2',
          suggested_accepted_unit_price: '9.50',
          reason: 'Exact item and quantity match',
        }, {
          quotation_line_id: 502,
          po_row_index: 2,
          po_item_name: 'Masks ordered',
          po_quantity: '3',
          po_unit_price: '',
          suggested_outcome_status: 'accepted',
          suggested_accepted_quantity: '3',
          suggested_accepted_unit_price: '20.00',
          reason: 'Item and quantity matched; price was not stated',
        }],
        unmatched_po_rows: [{ po_row_number: 5, po_item_name: 'Aspirin 100 mg', quantity: '2' }],
        missing_quote_line_ids: [503, 504],
        commercial_comparison: {
          company_name: 'Customer A',
          quotation_number: 'Q-0021',
          currency: 'AED',
          quotation_total: '136.50',
          lpo_number: 'PO-7781',
          lines: [{
            quotation_line_id: 501,
            quote_item_name: 'Bandage Pack',
            lpo_item_name: 'Bandages ordered',
            quoted_quantity: '2',
            accepted_quantity: '2',
            quoted_unit_price: '10.00',
            accepted_unit_price: '9.50',
            quoted_line_total: '21.00',
            accepted_line_total: '19.00',
            status: 'repriced',
            confidence: 97,
            reason: 'Customer accepted a different unit price.',
          }, {
            quotation_line_id: 502,
            quote_item_name: 'Surgical Mask',
            lpo_item_name: 'Masks ordered',
            quoted_quantity: '3',
            accepted_quantity: '3',
            quoted_unit_price: '20.00',
            accepted_unit_price: null,
            quoted_line_total: '63.00',
            accepted_line_total: null,
            status: 'accepted_price_not_stated',
            review_required: true,
            reason: 'LPO price was not stated.',
          }, {
            quotation_line_id: 503,
            quote_item_name: 'Gauze Roll',
            quoted_quantity: '4',
            quoted_unit_price: '5.00',
            quoted_line_total: '21.00',
            status: 'not_ordered',
            reason: 'Not present on this complete LPO parse.',
          }, {
            quotation_line_id: 504,
            quote_item_name: 'Examination Gloves',
            quoted_quantity: '1',
            quoted_unit_price: '30.00',
            quoted_line_total: '31.50',
            status: 'uncertain',
            review_required: true,
            reason: 'An unmatched row may belong to this quotation line.',
          }],
          unmatched_lpo_rows: [{ item_name: 'Aspirin 100 mg', quantity: '2', reason: 'No confident quotation-line match.' }],
        },
      },
    });

    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

    expect(await screen.findByText('LPO for Q-0021')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /review evidence/i }));
    fireEvent.click(screen.getByRole('button', { name: /approve this email link & parse/i }));

    expect(await screen.findByText('Parsed LPO loaded into Line Outcomes')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /reparse approved email/i })).toBeInTheDocument();

    const table = screen.getByRole('table', { name: /quotation lines compared with the selected customer lpo/i });
    const acceptedRow = within(table).getByRole('row', { name: /bandage pack/i });
    const maskRow = within(table).getByRole('row', { name: /surgical mask/i });
    const omittedRow = within(table).getByRole('row', { name: /gauze roll/i });
    const uncertainRow = within(table).getByRole('row', { name: /examination gloves/i });
    const acceptedCheckbox = within(acceptedRow).getByRole('checkbox', { name: /use these lpo values/i });
    const unstatedPriceCheckbox = within(maskRow).queryByRole('checkbox', { name: /use these lpo values/i });
    const omittedCheckbox = within(omittedRow).getByRole('checkbox', { name: /mark rejected from this lpo/i });
    expect(acceptedCheckbox).toBeChecked();
    expect(unstatedPriceCheckbox).not.toBeInTheDocument();
    expect(omittedCheckbox).not.toBeChecked();
    expect(within(uncertainRow).queryByRole('checkbox', { name: /use these lpo values|mark rejected/i })).not.toBeInTheDocument();

    expect(within(maskRow).getAllByText('Not stated').length).toBeGreaterThanOrEqual(1);
    expect(within(maskRow).getByText('Accepted - price not stated')).toBeInTheDocument();
    expect(within(acceptedRow).getByRole('spinbutton', { name: /accepted unit price for bandage pack/i })).toHaveValue(9.5);
    expect(screen.getAllByText('PO-7781').length).toBeGreaterThanOrEqual(1);

    fireEvent.click(omittedCheckbox);
    fireEvent.click(screen.getByRole('button', { name: /save staged lpo decisions/i }));

    await waitFor(() => expect(quotationAPI.quotes.updateOutcome).toHaveBeenCalledWith(21, {
      line_updates: [{
        id: 501,
        outcome_status: 'accepted',
        accepted_quantity: '2',
        accepted_unit_price: '9.50',
        outcome_reason: '',
        outcome_notes: 'LPO review: Customer accepted a different unit price.',
      }, {
        id: 503,
        outcome_status: 'rejected',
        accepted_quantity: '',
        accepted_unit_price: '',
        outcome_reason: '',
        outcome_notes: 'LPO review: this quoted line was not ordered on the selected LPO.',
      }],
      po_import_id: 77,
      applied_po_line_ids: [501],
    }));
    expect(await screen.findByText(/selected lpo decisions and line outcomes saved/i)).toBeInTheDocument();
    await waitFor(() => expect(
      screen.getByRole('button', { name: /save staged lpo decisions/i })
    ).toBeDisabled());
  });

  test('does not apply quotation fallback values when a legacy parsed row omitted the customer price', async () => {
    quotationAPI.quotes.outcome.mockResolvedValueOnce({
      data: {
        ...outcomePayload,
        quotation: {
          ...outcomePayload.quotation,
          lines: [{
            id: 501,
            item_name_snapshot: 'Bandage Pack',
            quantity: '2',
            unit: 'pack',
            unit_price: '10.00',
            line_total: '21.00',
          }],
        },
      },
    });
    quotationAPI.quotes.parsePOEvidence.mockResolvedValueOnce({
      data: {
        id: 88,
        suggestions: [{
          quotation_line_id: 501,
          po_item_name: 'Bandage Pack',
          po_quantity: '2',
          po_unit_price: '',
          suggested_outcome_status: 'accepted',
          suggested_accepted_quantity: '2',
          suggested_accepted_unit_price: '10.00',
          reason: 'Legacy suggestion used the quotation fallback.',
        }],
        unmatched_po_rows: [],
        missing_quote_line_ids: [],
      },
    });

    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);
    expect(await screen.findByText('LPO for Q-0021')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /review evidence/i }));
    fireEvent.click(screen.getByRole('button', { name: /approve this email link & parse/i }));

    const table = screen.getByRole('table', { name: /quotation lines compared with the selected customer lpo/i });
    const bandageRow = within(table).getByRole('row', { name: /bandage pack/i });
    expect((await within(bandageRow).findAllByText('Accepted - price not stated')).length).toBeGreaterThanOrEqual(1);
    expect(within(bandageRow).queryByRole('checkbox', { name: /use these lpo values/i })).not.toBeInTheDocument();
    expect(screen.getByRole('spinbutton', { name: /accepted unit price for bandage pack/i })).toHaveValue(null);
    expect(screen.getByRole('button', { name: /save staged lpo decisions/i })).toBeDisabled();
    expect(quotationAPI.quotes.updateOutcome).not.toHaveBeenCalled();
  });

  test('renders evidence as a named inline region and lets staff hide and reopen it without a dialog', async () => {
    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);
    const reviewButton = await screen.findByRole('button', { name: /review evidence/i });
    fireEvent.click(reviewButton);

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: /customer a - lpo for q-0021/i })).toBeInTheDocument();
    expect(screen.getByRole('table', { name: /quotation lines compared with the selected customer lpo/i })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /hide details/i }));
    expect(screen.queryByRole('region', { name: /customer a - lpo for q-0021/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /show inline review/i }));
    expect(screen.getByRole('region', { name: /customer a - lpo for q-0021/i })).toBeInTheDocument();
  });

  test('blocks unrelated saves and bulk updates while line outcome edits are unsaved', async () => {
    quotationAPI.quotes.outcome.mockResolvedValueOnce({
      data: {
        ...outcomePayload,
        quotation: {
          ...outcomePayload.quotation,
          lines: [{
            id: 501,
            item_name_snapshot: 'Bandage Pack',
            quantity: '2.000',
            unit: 'pack',
            unit_price: '12.00',
            line_total: '25.20',
          }],
        },
      },
    });

    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

    const acceptedQuantity = await screen.findByRole('spinbutton', { name: /accepted quantity for bandage pack/i });
    fireEvent.change(acceptedQuantity, { target: { value: '1' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /select bandage pack for bulk outcome action/i }));

    expect(screen.getByRole('status')).toHaveTextContent('Unsaved line outcome changes');
    expect(screen.getByRole('status')).toHaveTextContent(/save line outcomes before running bulk actions/i);
    expect(screen.getByRole('button', { name: /save follow-up/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /save follow-up/i })).toHaveAttribute('aria-describedby', 'qm-unsaved-line-changes');
    expect(screen.getByRole('button', { name: /save final outcome/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /save final outcome/i })).toHaveAttribute('aria-describedby', 'qm-unsaved-line-changes');
    expect(screen.getByRole('button', { name: /mark accepted/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /mark accepted/i })).toHaveAttribute('aria-describedby', 'qm-unsaved-line-changes');
    expect(screen.getByRole('button', { name: /mark rejected/i })).toBeDisabled();
    expect(screen.getAllByRole('button', { name: /save line outcomes/i }).every((button) => !button.disabled)).toBe(true);
    expect(quotationAPI.quotes.updateOutcome).not.toHaveBeenCalled();
  });

  test('does not let the mouse wheel change an accepted quantity or unit price', async () => {
    quotationAPI.quotes.outcome.mockResolvedValueOnce({
      data: {
        ...outcomePayload,
        quotation: {
          ...outcomePayload.quotation,
          lines: [{
            id: 501,
            item_name_snapshot: 'Bandage Pack',
            quantity: '2.000',
            unit: 'pack',
            unit_price: '12.00',
            line_total: '25.20',
            outcome_status: 'accepted',
            accepted_quantity: '2.000',
            accepted_unit_price: '12.00',
            outcome_reason: '',
            outcome_notes: '',
          }],
        },
      },
    });

    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

    const acceptedQuantity = await screen.findByRole('spinbutton', { name: /accepted quantity for bandage pack/i });
    acceptedQuantity.focus();
    expect(document.activeElement).toBe(acceptedQuantity);
    fireEvent.wheel(acceptedQuantity, { deltaY: 100 });
    expect(document.activeElement).not.toBe(acceptedQuantity);
    expect(acceptedQuantity).toHaveValue(2);

    const acceptedPrice = await screen.findByRole('spinbutton', { name: /accepted unit price for bandage pack/i });
    acceptedPrice.focus();
    expect(document.activeElement).toBe(acceptedPrice);
    fireEvent.wheel(acceptedPrice, { deltaY: 100 });
    expect(document.activeElement).not.toBe(acceptedPrice);
    expect(acceptedPrice).toHaveValue(12);
  });

  test('locks all editable outcome controls until an in-flight line save completes', async () => {
    const quoteLine = {
      id: 501,
      item_name_snapshot: 'Bandage Pack',
      quantity: '2.000',
      unit: 'pack',
      unit_price: '12.00',
      line_total: '25.20',
      outcome_status: 'pending',
      accepted_quantity: null,
      accepted_unit_price: null,
      outcome_reason: '',
      outcome_notes: '',
    };
    const loadedWithLine = {
      ...outcomePayload,
      quotation: { ...outcomePayload.quotation, lines: [quoteLine] },
    };
    let resolveSave;
    const pendingSave = new Promise((resolve) => {
      resolveSave = resolve;
    });
    quotationAPI.quotes.outcome.mockResolvedValueOnce({ data: loadedWithLine });
    quotationAPI.quotes.updateOutcome.mockReturnValueOnce(pendingSave);

    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

    const acceptedQuantity = await screen.findByRole('spinbutton', { name: /accepted quantity for bandage pack/i });
    fireEvent.change(acceptedQuantity, { target: { value: '1' } });
    fireEvent.click(screen.getAllByRole('button', { name: /save line outcomes/i })[0]);

    await waitFor(() => expect(quotationAPI.quotes.updateOutcome).toHaveBeenCalledTimes(1));
    expect(acceptedQuantity).toBeDisabled();
    expect(screen.getByRole('combobox', { name: /outcome for bandage pack/i })).toBeDisabled();
    expect(screen.getByLabelText('Notes')).toBeDisabled();
    expect(screen.getByRole('combobox', { name: /override status/i })).toBeDisabled();
    expect(screen.getAllByRole('button', { name: /saving/i }).length).toBeGreaterThanOrEqual(1);

    await act(async () => {
      resolveSave({
        data: {
          ...loadedWithLine,
          quotation: {
            ...loadedWithLine.quotation,
            lines: [{
              ...quoteLine,
              accepted_quantity: '1.000',
            }],
          },
        },
      });
      await pendingSave;
    });

    await waitFor(() => expect(screen.getByRole('spinbutton', { name: /accepted quantity for bandage pack/i })).toBeEnabled());
    expect(screen.getByRole('spinbutton', { name: /accepted quantity for bandage pack/i })).toHaveValue(1);
  });

  test('labels an attachment source, separates its text from the email body, and expands both previews', async () => {
    const attachmentText = `${'A'.repeat(5010)} ATTACHMENT-END`;
    const emailBodyText = `${'B'.repeat(5010)} EMAIL-BODY-END`;
    const attachmentEvidence = {
      ...evidence,
      selected_attachment_id: 'gmail-attachment-1',
      selected_attachment_filename: 'LPO-7781.pdf',
      match_signals: { source: { kind: 'attachment', attachment_id: 'gmail-attachment-1' } },
      attachments: [{
        attachment_id: 'gmail-attachment-1',
        filename: 'LPO-7781.pdf',
        mime_type: 'application/pdf',
        size: 2048,
        status: 'parsed',
        is_selected: true,
      }, {
        attachment_id: 'gmail-attachment-duplicate-name',
        filename: 'LPO-7781.pdf',
        mime_type: 'application/pdf',
        size: 1024,
        status: 'parsed',
        is_selected: true,
      }],
    };
    quotationAPI.quotes.outcome.mockResolvedValueOnce({
      data: { ...outcomePayload, po_evidence: [attachmentEvidence] },
    });
    quotationAPI.quotes.poEvidenceSource.mockResolvedValueOnce({
      data: {
        id: 81,
        selected_source_kind: 'attachment',
        extracted_text: attachmentText,
        extracted_text_truncated: true,
        email_body_text: emailBodyText,
        email_body_text_truncated: false,
      },
    });

    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

    expect(await screen.findByText('LPO for Q-0021')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /review evidence/i }));
    fireEvent.click(screen.getByText('Email text and matching diagnostics'));

    expect(await screen.findByText('Attachment · LPO-7781.pdf')).toBeInTheDocument();
    expect(screen.getAllByText('Selected source').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('Extracted attachment text')).toBeInTheDocument();
    expect(screen.getByText('Email body')).toBeInTheDocument();
    expect(await screen.findByText(/backend returned only part of this text/i)).toBeInTheDocument();
    expect(screen.queryByText(/ATTACHMENT-END/)).not.toBeInTheDocument();
    expect(screen.queryByText(/EMAIL-BODY-END/)).not.toBeInTheDocument();
    expect(screen.getAllByText(/Showing the first 5,000/i)).toHaveLength(2);

    fireEvent.click(screen.getByRole('button', { name: /show full extracted attachment text/i }));
    expect(screen.getByText(/ATTACHMENT-END/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /show less extracted attachment text/i })).toHaveAttribute('aria-expanded', 'true');

    fireEvent.click(screen.getByRole('button', { name: /show full email body/i }));
    expect(screen.getByText(/EMAIL-BODY-END/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /show less email body/i })).toHaveAttribute('aria-expanded', 'true');
  });

  test('clearly identifies the email body when it is the selected source', async () => {
    const bodyEvidence = {
      ...evidence,
      attachment_count: 0,
      attachments: [],
      match_signals: { source: { kind: 'email_body' } },
    };
    quotationAPI.quotes.outcome.mockResolvedValueOnce({
      data: { ...outcomePayload, po_evidence: [bodyEvidence] },
    });
    quotationAPI.quotes.poEvidenceSource.mockResolvedValueOnce({
      data: {
        id: 81,
        selected_source_kind: 'email_body',
        extracted_text: 'Selected email body copy.',
        extracted_text_truncated: false,
        email_body_text: 'Newest email body with the confirmed quantities and total.',
        email_body_text_truncated: false,
      },
    });

    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);

    expect(await screen.findByText('LPO for Q-0021')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /review evidence/i }));

    expect(await screen.findByText('Email body (selected source)')).toBeInTheDocument();
    expect(screen.getByText('Newest email body with the confirmed quantities and total.')).toBeInTheDocument();
    expect(screen.queryByText('Extracted attachment text')).not.toBeInTheDocument();
    expect(screen.getAllByText('Email body')).toHaveLength(1);
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
