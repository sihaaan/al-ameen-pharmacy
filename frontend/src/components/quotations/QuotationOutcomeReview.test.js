import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
    expect(await screen.findByText(/email link approved.*no line outcome was applied/i)).toBeInTheDocument();
  });

  test('records the PO import and only the suggestions staff selected when applying outcomes', async () => {
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
    const dialog = screen.getByRole('dialog');
    fireEvent.click(await within(dialog).findByRole('button', { name: /apply selected line decisions/i }));

    await waitFor(() => expect(quotationAPI.quotes.updateOutcome).toHaveBeenCalledWith(21, {
      line_updates: [{
        id: 501,
        outcome_status: 'accepted',
        accepted_quantity: '2.000',
        accepted_unit_price: '10.00',
        outcome_notes: 'PO suggestion applied: Exact item and quantity match',
      }],
      po_import_id: 77,
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

      expect(screen.getByText('Email body')).toBeInTheDocument();
      expect(screen.getByText(/approved customer purchase order/i)).toBeInTheDocument();
      expect(screen.getAllByText('Quote reference present')).toHaveLength(1);
      expect(screen.getByText('Quote reference present · Q-0021')).toBeInTheDocument();
      expect(screen.getAllByText('Selected source')).toHaveLength(2);
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

  test('makes evidence review self-contained with company, document totals, and every commercial line decision', async () => {
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
    const dialog = screen.getByRole('dialog');

    expect(await within(dialog).findByText('Customer A')).toBeInTheDocument();
    expect(within(dialog).getByText('Q-0021')).toBeInTheDocument();
    expect(within(dialog).getByText('PO-7781')).toBeInTheDocument();
    expect(within(dialog).getByText('AED 163.80')).toBeInTheDocument();
    expect(within(dialog).getByText('Matches quote subtotal before VAT')).toBeInTheDocument();
    expect(within(dialog).getByRole('columnheader', { name: 'Total incl. VAT' })).toBeInTheDocument();
    expect(within(dialog).getByRole('columnheader', { name: 'LPO line total' })).toBeInTheDocument();
    expect(within(dialog).getByText('Accepted as quoted')).toBeInTheDocument();
    expect(within(dialog).getByText('Accepted - price not stated')).toBeInTheDocument();
    expect(within(dialog).getByText('Reduced quantity')).toBeInTheDocument();
    expect(within(dialog).getByText('Not ordered / omitted')).toBeInTheDocument();
    expect(within(dialog).getByText('Unmatched LPO item')).toBeInTheDocument();
    expect(within(dialog).getAllByText('Not stated').length).toBeGreaterThanOrEqual(2);
    expect(within(dialog).getByText('Review required')).toBeInTheDocument();
  });

  test('keeps parsed decisions in the modal and applies an omitted line only after staff explicitly checks it', async () => {
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

    const dialog = screen.getByRole('dialog');
    expect(await within(dialog).findByText('Confirmed link - parsed line decisions')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: /reparse approved email/i })).toBeInTheDocument();

    const acceptedCheckbox = within(dialog).getByRole('checkbox', { name: /apply decision for bandage pack/i });
    const unstatedPriceCheckbox = within(dialog).queryByRole('checkbox', { name: /apply decision for surgical mask/i });
    const omittedCheckbox = within(dialog).getByRole('checkbox', { name: /apply decision for gauze roll/i });
    expect(acceptedCheckbox).toBeChecked();
    expect(unstatedPriceCheckbox).not.toBeInTheDocument();
    expect(omittedCheckbox).not.toBeChecked();
    expect(within(dialog).queryByRole('checkbox', { name: /apply decision for examination gloves/i })).not.toBeInTheDocument();

    const maskRow = within(dialog).getAllByText('Masks ordered')[0].closest('tr');
    expect(within(maskRow).getAllByText('Not stated').length).toBeGreaterThanOrEqual(2);
    expect(within(maskRow).getByText('Accepted - price not stated')).toBeInTheDocument();
    expect(within(dialog).getAllByText('AED 9.50').length).toBeGreaterThanOrEqual(1);
    expect(within(dialog).getByText('PO-7781')).toBeInTheDocument();

    fireEvent.click(omittedCheckbox);
    fireEvent.click(within(dialog).getByRole('button', { name: /apply selected line decisions/i }));

    await waitFor(() => expect(quotationAPI.quotes.updateOutcome).toHaveBeenCalledWith(21, {
      line_updates: [{
        id: 501,
        outcome_status: 'accepted',
        accepted_quantity: '2',
        accepted_unit_price: '9.50',
        outcome_notes: 'PO suggestion applied: Exact item and quantity match',
      }, {
        id: 503,
        outcome_status: 'rejected',
        outcome_notes: 'PO suggestion applied: this quoted line was not ordered on the reviewed LPO.',
      }],
      po_import_id: 77,
      applied_po_line_ids: [501],
    }));
    expect(await within(dialog).findByText(/selected po line decisions applied/i)).toBeInTheDocument();
    await waitFor(() => expect(
      within(dialog).getByRole('button', { name: /apply selected line decisions/i })
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

    const dialog = screen.getByRole('dialog');
    expect((await within(dialog).findAllByText('Accepted - price not stated')).length).toBeGreaterThanOrEqual(1);
    expect(within(dialog).queryByRole('checkbox', { name: /apply decision for bandage pack/i })).not.toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: /apply selected line decisions/i })).toBeDisabled();
    expect(quotationAPI.quotes.updateOutcome).not.toHaveBeenCalled();
  });

  test('names the evidence dialog, closes it with Escape, and restores review-button focus', async () => {
    render(<QuotationOutcomeReview quoteId={21} onBack={jest.fn()} />);
    const reviewButton = await screen.findByRole('button', { name: /review evidence/i });
    reviewButton.focus();
    fireEvent.click(reviewButton);

    const dialog = screen.getByRole('dialog', { name: /customer a - lpo for q-0021/i });
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Escape' });

    expect(dialog).not.toBeInTheDocument();
    expect(reviewButton).toHaveFocus();
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

    expect(await screen.findByText('Attachment · LPO-7781.pdf')).toBeInTheDocument();
    expect(screen.getAllByText('Selected source')).toHaveLength(2);
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
