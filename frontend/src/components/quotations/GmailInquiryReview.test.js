import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import GmailInquiryReview, {
  gmailImportRecordFromPayload,
  quotationIdFromGmailImportPayload,
} from './GmailInquiryReview';
import quotationAPI, { describeQuotationError } from '../../api/quotations';

jest.mock('../../api/quotations', () => ({
  __esModule: true,
  default: {
    companies: {
      list: jest.fn(),
      priceHistory: jest.fn(),
    },
    contacts: {
      list: jest.fn(),
    },
    gmailInquiryImports: {
      claim: jest.fn(),
      retrieve: jest.fn(),
      analysisProgress: jest.fn(),
      update: jest.fn(),
      analyze: jest.fn(),
      approveCompany: jest.fn(),
      confirm: jest.fn(),
      confirmAndPrepareQuotation: jest.fn(),
      attachment: jest.fn(),
    },
    items: {
      list: jest.fn(),
    },
    quotes: {
      finalize: jest.fn(),
      sendEmail: jest.fn(),
      emailPreview: jest.fn(),
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

jest.mock('./CompanySelectWithCreate', () => ({
  __esModule: true,
  default: ({ companies, value, onChange, disabled }) => (
    <label>
      Company
      <select
        aria-label="Company"
        value={value || ''}
        disabled={disabled}
        onChange={(event) => {
          const company = companies.find((candidate) => String(candidate.id) === event.target.value);
          onChange(event.target.value, company);
        }}
      >
        <option value="">Select company</option>
        {companies.map((company) => (
          <option key={company.id} value={company.id}>{company.name}</option>
        ))}
      </select>
    </label>
  ),
}));

jest.mock('./QuotationEditor', () => ({
  __esModule: true,
  default: ({ quoteId, onOpenQuote, onOpenGmailImport, onReviewOutcome, gmailEvidenceVisible }) => (
    <section aria-label="Embedded standard quotation editor">
      <h3>Standard quotation editor #{quoteId}</h3>
      <button type="button" onClick={() => onOpenQuote?.(100)}>Open revision draft</button>
      <button type="button" onClick={() => onOpenGmailImport?.(31)}>
        {gmailEvidenceVisible ? 'Hide Gmail evidence' : 'View Gmail evidence'}
      </button>
      <button type="button" onClick={() => onReviewOutcome?.(quoteId)}>Review outcome</button>
    </section>
  ),
}));

jest.mock('./QuotationOutcomeReview', () => ({
  __esModule: true,
  default: ({ quoteId, onBack }) => (
    <section aria-label="Embedded quotation outcome review">
      <h3>Quotation outcome #{quoteId}</h3>
      <button type="button" onClick={onBack}>Back to quotation editor</button>
    </section>
  ),
}));

const baseRecord = {
  id: 31,
  status: 'review_required',
  claimed_by_username: 'sara',
  anchor_message_id: 'm-1',
  mode: 'selected_messages',
  selected_message_ids: ['m-1', 'm-2'],
  company: 7,
  contact: 8,
  subject: 'Request for medical supplies',
  message_manifest: [
    {
      gmail_message_id: 'm-1',
      subject: 'Request for medical supplies',
      sender: 'Buyer <buyer@example.com>',
      sent_at: '2026-07-29T08:00:00Z',
      snippet: 'Please quote the attached list.',
      usage: 'used',
      classification: 'inquiry',
      analysis_reason: 'Contains the current item request.',
      analysis_confidence: 0.96,
    },
    {
      gmail_message_id: 'm-2',
      subject: 'Context reply',
      sender: 'Purchasing <purchasing@example.com>',
      sent_at: '2026-07-29T09:00:00Z',
      snippet: 'The quantities are confirmed.',
      usage: 'context',
    },
    {
      gmail_message_id: 'm-3',
      subject: 'Old unrelated request',
      sender: 'Buyer <buyer@example.com>',
      sent_at: '2026-06-01T08:00:00Z',
      snippet: 'An older request.',
      usage: 'excluded',
    },
  ],
  attachment_manifest: [{
    gmail_message_id: 'm-1',
    attachment_id: 'raw-gmail-attachment-id',
    filename: 'request.pdf',
    mime_type: 'application/pdf',
    size: 2048,
  }],
  evidence: [{
    source_key: 'attachment:opaque-1',
    gmail_message_id: 'm-1',
    kind: 'attachment',
    filename: 'request.pdf',
    line_count: 1,
    parse_method: 'pdf_text',
  }, {
    source_key: 'body:opaque-2',
    gmail_message_id: 'm-2',
    kind: 'email_body',
    line_count: 0,
  }],
  candidates: {
    sender_emails: ['buyer@example.com'],
    companies: [{
      company_id: 7,
      company_name: 'Example Medical',
      confidence: 1,
      match_method: 'exact_contact_email',
    }],
    contacts: [{
      contact_id: 8,
      contact_name: 'Celine',
      company_id: 7,
      email: 'buyer@example.com',
    }],
    recommended_company_id: 7,
    recommended_contact_id: 8,
  },
  analysis: {
    recommended_source_keys: ['attachment:opaque-1'],
    warnings: ['One row needs staff review.'],
    preview: {
      meta: {
        multiple_distinct_sources: true,
      },
      lines: [{
        row_key: 'row-key-000000000001',
        raw_name: 'Bandage',
        raw_line: 'Bandage | 10 | Pcs | 5.50',
        quantity: '10.000',
        unit: 'Pcs',
        operation: 'uncertain',
        parse_status: 'needs_review',
        parse_confidence: 0.72,
        matched_quote_item_name: 'Bandage Roll',
        match_reason: 'Suggested only; staff must confirm.',
        customer_unit_price: '5.50',
        customer_line_total: '55.00',
        _source_keys: ['attachment:opaque-1'],
        evidence: [{
          source_key: 'attachment:opaque-1',
          sheet_name: 'Items',
          cell_range: 'B4:D4',
        }],
        source_page: 2,
      }],
    },
  },
};

const reviewedRecord = {
  ...baseRecord,
  analysis: {
    ...baseRecord.analysis,
    preview: {
      ...baseRecord.analysis.preview,
      lines: [{
        ...baseRecord.analysis.preview.lines[0],
        raw_name: 'Sterile Bandage',
        operation: 'changed',
        parse_status: 'parsed',
        reviewed_by_user: true,
      }],
    },
  },
};

const CHAIN_SOURCE_BEFORE = 'a'.repeat(64);
const CHAIN_SOURCE_AFTER = 'b'.repeat(64);
const CHAIN_IDENTITY_BEFORE = 'c'.repeat(64);
const CHAIN_IDENTITY_AFTER = 'd'.repeat(64);
const CHAIN_ROWS_BEFORE = 'e'.repeat(64);
const CHAIN_ROWS_AFTER = 'f'.repeat(64);

const chainedRecord = (source = reviewedRecord, overrides = {}) => ({
  ...source,
  company: 7,
  contact: null,
  source_fingerprint: CHAIN_SOURCE_BEFORE,
  analysis_attempts: 2,
  review_rows_fingerprint: CHAIN_ROWS_BEFORE,
  workflow_features: {
    ...(source.workflow_features || {}),
    gmail_review_ui_v2: true,
    gmail_chained_actions: true,
  },
  identity_review_approved: true,
  identity_review_fingerprint: CHAIN_IDENTITY_BEFORE,
  ...overrides,
});

const UNIFIED_ANALYSIS_GENERATION = '1'.repeat(64);
const UNIFIED_QUOTATION_FINGERPRINT = '2'.repeat(64);
const unifiedRecord = (overrides = {}) => ({
  ...baseRecord,
  company: 7,
  contact: 8,
  source_fingerprint: CHAIN_SOURCE_BEFORE,
  analysis_attempts: 2,
  analysis_generation: UNIFIED_ANALYSIS_GENERATION,
  review_rows_fingerprint: CHAIN_ROWS_BEFORE,
  identity_review_approved: true,
  identity_review_fingerprint: CHAIN_IDENTITY_BEFORE,
  workflow_features: {
    gmail_review_ui_v2: true,
    gmail_chained_actions: true,
    gmail_unified_workspace: true,
  },
  analysis: {
    ...baseRecord.analysis,
    preview: {
      ...baseRecord.analysis.preview,
      lines: [{
        ...baseRecord.analysis.preview.lines[0],
        matched_product: 11,
        matched_product_id: 11,
        matched_product_name: 'Bandage Roll',
        matched_quote_item_name: '',
      }],
    },
  },
  ...overrides,
});

const standardEditorRecord = (source = reviewedRecord, overrides = {}) => {
  const record = chainedRecord(source, overrides);
  return {
    ...record,
    workflow_features: {
      ...(record.workflow_features || {}),
      gmail_unified_workspace: true,
      gmail_standard_editor_intake: true,
    },
  };
};

const analysisProgress = (state = 'running', overrides = {}) => ({
  version: 'gmail_analysis_progress_v1',
  state,
  stage: state === 'completed' ? 'completed' : state === 'failed' ? 'failed' : 'preparing',
  attempt: 1,
  source_generation: 'progress-generation-1',
  safe_error_category: '',
  started_at: null,
  updated_at: null,
  completed_at: null,
  retryable: false,
  ...overrides,
});

const withAnalysisProgress = (source = baseRecord, progress = analysisProgress()) => ({
  ...source,
  workflow_features: {
    ...(source.workflow_features || {}),
    gmail_analysis_progress: true,
  },
  analysis_progress: progress,
});

const hexGeneration = (digit) => String(digit).repeat(32);

const backgroundAnalysisJob = (state = 'queued', overrides = {}) => ({
  id: 901,
  state,
  analysis_attempt: 1,
  source_generation: hexGeneration('1'),
  progress_stage: state === 'completed' ? 'completed' : state === 'failed' ? 'failed' : 'queued',
  attempt_count: state === 'queued' ? 0 : 1,
  safe_error_category: '',
  queued_at: '2026-08-02T08:00:00Z',
  started_at: null,
  heartbeat_at: null,
  completed_at: null,
  updated_at: '2026-08-02T08:00:00Z',
  terminal: ['completed', 'failed', 'superseded', 'cancelled'].includes(state),
  retryable: state === 'failed',
  ...overrides,
});

const withBackgroundAnalysis = (
  source = baseRecord,
  job = backgroundAnalysisJob(),
  progress = analysisProgress('running', {
    stage: 'queued',
    source_generation: job.source_generation,
    attempt: job.analysis_attempt,
  })
) => ({
  ...withAnalysisProgress(source, progress),
  workflow_features: {
    ...(source.workflow_features || {}),
    gmail_analysis_progress: true,
    gmail_background_analysis: true,
  },
  analysis_job: job,
});

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
};

describe('GmailInquiryReview', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    quotationAPI.companies.list.mockResolvedValue({
      data: [{ id: 7, name: 'Example Medical' }],
    });
    quotationAPI.companies.priceHistory.mockResolvedValue({ data: [] });
    quotationAPI.items.list.mockResolvedValue({
      data: [
        { id: 11, name: 'Bandage Roll', unit: 'Pcs' },
        { id: 12, name: 'Sterile Bandage Product', unit: 'Pcs' },
      ],
    });
    quotationAPI.contacts.list.mockResolvedValue({
      data: [{ id: 8, name: 'Celine', email: 'buyer@example.com', company: 7 }],
    });
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValue({ data: baseRecord });
    quotationAPI.gmailInquiryImports.analysisProgress.mockResolvedValue({
      data: {
        version: 'gmail_analysis_progress_v1',
        state: 'running',
        stage: 'preparing',
        attempt: 1,
        source_generation: 'default-progress-generation',
        safe_error_category: '',
        started_at: null,
        updated_at: null,
        completed_at: null,
        retryable: false,
      },
    });
    quotationAPI.gmailInquiryImports.update.mockResolvedValue({ data: reviewedRecord });
    quotationAPI.gmailInquiryImports.analyze.mockResolvedValue({ data: reviewedRecord });
    quotationAPI.gmailInquiryImports.approveCompany.mockResolvedValue({
      data: {
        ...baseRecord,
        workflow_features: { gmail_review_ui_v2: true },
        identity_review_approved: true,
        identity_review_fingerprint: 'identity-fingerprint-v2',
      },
    });
    quotationAPI.gmailInquiryImports.confirm.mockResolvedValue({
      data: {
        ...reviewedRecord,
        status: 'confirmed',
        quotation: 99,
        quotation_id: 99,
      },
    });
    quotationAPI.gmailInquiryImports.confirmAndPrepareQuotation.mockResolvedValue({
      data: {
        gmail_import: {
          ...unifiedRecord(),
          status: 'confirmed',
          quotation: 99,
        },
        quotation: {
          id: 99,
          quotation_review_fingerprint: UNIFIED_QUOTATION_FINGERPRINT,
        },
        quotation_review_fingerprint: UNIFIED_QUOTATION_FINGERPRINT,
        created: true,
        prepared: true,
        prepared_for_preview: true,
        preparation_reused: false,
        reused_reason: '',
      },
    });
    quotationAPI.gmailInquiryImports.attachment.mockResolvedValue({
      data: new Blob(['pdf'], { type: 'application/pdf' }),
      headers: { 'content-type': 'application/pdf' },
    });
  });

  test('adapts wrapped records and scalar quotation IDs', () => {
    expect(gmailImportRecordFromPayload({ data: { gmail_import: baseRecord } })).toBe(baseRecord);
    expect(quotationIdFromGmailImportPayload({ data: { ...baseRecord, quotation: 99 } })).toBe(99);
  });

  test('keeps customer budget evidence separate from the blank employee selling price and loads history only on request', async () => {
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: unifiedRecord() });
    quotationAPI.companies.priceHistory.mockResolvedValueOnce({
      data: [{
        id: 5,
        unit_price: '4.75',
        currency: 'AED',
        quotation_number: 'QT-OLD',
        quoted_at: '2026-01-01T00:00:00Z',
      }],
    });

    render(<GmailInquiryReview importId="31" onOpenQuote={jest.fn()} />);

    expect(await screen.findByRole('heading', { name: '2. Review request and price quotation' })).toBeInTheDocument();
    expect(screen.getByText('5.50 (currency not stated)')).toBeInTheDocument();
    expect(screen.getByLabelText('Our unit price row 1')).toHaveValue(null);
    expect(quotationAPI.companies.priceHistory).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Approve suggestion' }));
    expect(screen.getByLabelText('Our unit price row 1')).toHaveValue(null);
    fireEvent.click(screen.getByRole('button', { name: 'View price history row 1' }));

    await waitFor(() => expect(quotationAPI.companies.priceHistory).toHaveBeenCalledWith('7', {
      product: '11',
    }));
    expect(await screen.findByText('AED 4.75')).toBeInTheDocument();
    expect(screen.getByLabelText('Our unit price row 1')).toHaveValue(null);
  });

  test('fails closed before preparation when the analysis-attempt binding is absent', async () => {
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({
      data: unifiedRecord({ analysis_attempts: null }),
    });
    render(<GmailInquiryReview importId="31" onOpenQuote={jest.fn()} />);

    await screen.findByRole('heading', { name: '2. Review request and price quotation' });
    expect(screen.getByRole('button', { name: 'Review Email' })).toBeDisabled();
    expect(screen.getByText(/refresh this review to obtain current safety fingerprints/i)).toBeInTheDocument();
    expect(quotationAPI.gmailInquiryImports.confirmAndPrepareQuotation).not.toHaveBeenCalled();
  });

  test('clears Product approval and employee price when customer identity context changes', async () => {
    quotationAPI.companies.list.mockResolvedValueOnce({
      data: [
        { id: 7, name: 'Example Medical' },
        { id: 9, name: 'Different Customer' },
      ],
    });
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: unifiedRecord() });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({
      data: unifiedRecord({
        company: 9,
        contact: null,
        identity_review_approved: false,
      }),
    });
    render(<GmailInquiryReview importId="31" onOpenQuote={jest.fn()} />);

    await screen.findByRole('heading', { name: '2. Review request and price quotation' });
    fireEvent.click(screen.getByRole('button', { name: 'Approve suggestion' }));
    fireEvent.change(screen.getByLabelText('Our unit price row 1'), {
      target: { value: '6.25' },
    });
    expect(screen.getByLabelText('Product decision row 1')).toHaveValue('11');
    expect(screen.getByLabelText('Our unit price row 1')).toHaveValue(6.25);

    fireEvent.change(screen.getByLabelText('Company'), { target: { value: '9' } });
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.update).toHaveBeenCalledWith(31, {
      company: '9',
      contact: null,
    }));
    expect(screen.getByLabelText('Product decision row 1')).toHaveValue('');
    expect(screen.getByLabelText('Our unit price row 1')).toHaveValue(null);
    expect(screen.getByText('Suggestion from a different company context')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve suggestion' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Product decision row 1'), {
      target: { value: '11' },
    });
    expect(screen.getByText('Existing Product selected')).toBeInTheDocument();
    expect(screen.getByLabelText('Our unit price row 1')).toHaveValue(null);
    expect(screen.getByRole('button', { name: 'Review Email' })).toBeDisabled();
  });

  test('atomically prepares approved suggestions and hands one exact fingerprint to the existing editor without sending', async () => {
    const onOpenQuote = jest.fn();
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: unifiedRecord() });
    render(<GmailInquiryReview importId="31" onOpenQuote={onOpenQuote} />);

    await screen.findByRole('heading', { name: '2. Review request and price quotation' });
    fireEvent.click(screen.getByRole('button', { name: 'Approve suggestion' }));
    fireEvent.click(screen.getByRole('button', { name: 'Approve as extracted' }));
    fireEvent.change(screen.getByLabelText('Our unit price row 1'), {
      target: { value: '6.250' },
    });
    fireEvent.change(screen.getByLabelText('VAT row 1'), { target: { value: '5' } });

    const reviewEmailButton = screen.getByRole('button', { name: 'Review Email' });
    await waitFor(() => expect(reviewEmailButton).toBeEnabled());
    fireEvent.click(reviewEmailButton);

    await waitFor(() => expect(
      quotationAPI.gmailInquiryImports.confirmAndPrepareQuotation
    ).toHaveBeenCalledWith(31, {
      expected_source_fingerprint: CHAIN_SOURCE_BEFORE,
      expected_analysis_attempt: 2,
      expected_analysis_generation: UNIFIED_ANALYSIS_GENERATION,
      expected_review_rows_fingerprint: CHAIN_ROWS_BEFORE,
      identity_review_fingerprint: CHAIN_IDENTITY_BEFORE,
      rows: [{
        row_key: 'row-key-000000000001',
        raw_name: 'Bandage',
        quantity: '10.000',
        unit: 'Pcs',
        included: true,
        product_decision: 'approve',
        uncertainty_decision: 'approve',
        product: '11',
        quote_item: null,
        match_status: 'confirmed',
        unit_price: '6.250',
        vat_rate: '5.00',
      }],
    }));
    const sentPayload = quotationAPI.gmailInquiryImports.confirmAndPrepareQuotation.mock.calls[0][1];
    expect(JSON.stringify(sentPayload)).not.toMatch(/customer_(unit_)?price|budget/i);
    expect(onOpenQuote).toHaveBeenCalledWith(99, {
      reviewEmail: true,
      quotationReviewFingerprint: UNIFIED_QUOTATION_FINGERPRINT,
    });
    expect(quotationAPI.quotes.finalize).not.toHaveBeenCalled();
    expect(quotationAPI.quotes.sendEmail).not.toHaveBeenCalled();
    expect(quotationAPI.quotes.emailPreview).not.toHaveBeenCalled();
  });

  test('requires an explicit different existing Product and substantive uncertainty correction', async () => {
    const onOpenQuote = jest.fn();
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: unifiedRecord() });
    render(<GmailInquiryReview importId="31" onOpenQuote={onOpenQuote} />);

    await screen.findByRole('heading', { name: '2. Review request and price quotation' });
    await waitFor(() => expect(screen.getByLabelText('Product decision row 1')).toBeEnabled());
    fireEvent.change(screen.getByLabelText('Product decision row 1'), { target: { value: '12' } });
    fireEvent.change(screen.getByLabelText('Requested item row 1'), {
      target: { value: 'Sterile Bandage' },
    });
    fireEvent.change(screen.getByLabelText('Our unit price row 1'), {
      target: { value: '7.00' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Review Email' }));

    await waitFor(() => expect(
      quotationAPI.gmailInquiryImports.confirmAndPrepareQuotation
    ).toHaveBeenCalled());
    const row = quotationAPI.gmailInquiryImports.confirmAndPrepareQuotation.mock.calls[0][1].rows[0];
    expect(row).toEqual(expect.objectContaining({
      raw_name: 'Sterile Bandage',
      product: '12',
      quote_item: null,
      product_decision: 'correct',
      uncertainty_decision: 'correct',
      unit_price: '7.00',
    }));
  });

  test('requires a fresh explicit uncertainty approval after an edit is reverted', async () => {
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: unifiedRecord() });
    render(<GmailInquiryReview importId="31" onOpenQuote={jest.fn()} />);

    await screen.findByRole('heading', { name: '2. Review request and price quotation' });
    fireEvent.click(screen.getByRole('button', { name: 'Approve suggestion' }));
    fireEvent.change(screen.getByLabelText('Our unit price row 1'), {
      target: { value: '7.00' },
    });
    fireEvent.change(screen.getByLabelText('Requested item row 1'), {
      target: { value: 'Sterile Bandage' },
    });
    expect(screen.getByText('Corrected by staff')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Requested item row 1'), {
      target: { value: 'Bandage' },
    });
    expect(screen.getByRole('button', { name: 'Approve as extracted' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Review Email' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Approve as extracted' }));
    await waitFor(() => expect(
      screen.getByRole('button', { name: 'Review Email' })
    ).toBeEnabled());
  });

  test('locks synchronous double clicks and never auto-previews an inconsistent reused response', async () => {
    const onOpenQuote = jest.fn();
    const preparation = deferred();
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: unifiedRecord() });
    quotationAPI.gmailInquiryImports.confirmAndPrepareQuotation.mockReturnValueOnce(preparation.promise);
    render(<GmailInquiryReview importId="31" onOpenQuote={onOpenQuote} />);

    await screen.findByRole('heading', { name: '2. Review request and price quotation' });
    fireEvent.click(screen.getByRole('button', { name: 'Approve suggestion' }));
    fireEvent.click(screen.getByRole('button', { name: 'Approve as extracted' }));
    fireEvent.change(screen.getByLabelText('Our unit price row 1'), { target: { value: '6.25' } });
    const reviewEmailButton = screen.getByRole('button', { name: 'Review Email' });
    fireEvent.click(reviewEmailButton);
    fireEvent.click(reviewEmailButton);
    expect(quotationAPI.gmailInquiryImports.confirmAndPrepareQuotation).toHaveBeenCalledTimes(1);

    preparation.resolve({
      data: {
        gmail_import: { ...unifiedRecord(), status: 'confirmed', quotation: 99 },
        quotation: { id: 99, quotation_review_fingerprint: UNIFIED_QUOTATION_FINGERPRINT },
        quotation_review_fingerprint: UNIFIED_QUOTATION_FINGERPRINT,
        created: false,
        prepared: false,
        // Even a contradictory preview hint cannot bypass the complete
        // one-request grant required by the frontend.
        prepared_for_preview: true,
        preparation_reused: true,
        reused_reason: 'same_preparation',
      },
    });

    await waitFor(() => expect(onOpenQuote).toHaveBeenCalledWith(99));
    expect(onOpenQuote.mock.calls[0]).toHaveLength(1);
  });

  test('fails closed on a stale unified preparation and preserves the old workflow unless the flag is boolean true', async () => {
    const onOpenQuote = jest.fn();
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: unifiedRecord() });
    quotationAPI.gmailInquiryImports.confirmAndPrepareQuotation.mockRejectedValueOnce(Object.assign(
      new Error('The Gmail review changed. Refresh and try again.'),
      { response: { status: 409 } }
    ));
    const { unmount } = render(<GmailInquiryReview importId="31" onOpenQuote={onOpenQuote} />);
    await screen.findByRole('heading', { name: '2. Review request and price quotation' });
    fireEvent.click(screen.getByRole('button', { name: 'Approve suggestion' }));
    fireEvent.click(screen.getByRole('button', { name: 'Approve as extracted' }));
    fireEvent.change(screen.getByLabelText('Our unit price row 1'), { target: { value: '6.25' } });
    fireEvent.click(screen.getByRole('button', { name: 'Review Email' }));
    await waitFor(() => expect(describeQuotationError).toHaveBeenCalledWith(
      expect.any(Error),
      'Prepare Gmail quotation and review email',
      'POST /quotations/gmail-inquiry-imports/31/confirm_and_prepare_quotation/'
    ));
    expect(onOpenQuote).not.toHaveBeenCalled();
    unmount();

    jest.clearAllMocks();
    quotationAPI.companies.list.mockResolvedValue({ data: [{ id: 7, name: 'Example Medical' }] });
    quotationAPI.contacts.list.mockResolvedValue({ data: [] });
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({
      data: {
        ...reviewedRecord,
        workflow_features: { gmail_unified_workspace: 'true' },
      },
    });
    render(<GmailInquiryReview importId="31" onOpenQuote={jest.fn()} />);
    expect(await screen.findByRole('heading', { name: '2. Review extracted request lines' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Review Email' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirm & Open Quotation' })).toBeInTheDocument();
    expect(quotationAPI.items.list).not.toHaveBeenCalled();
    expect(quotationAPI.gmailInquiryImports.confirmAndPrepareQuotation).not.toHaveBeenCalled();
  });

  test('confirms the company once and unlocks the standard quotation editor on the same page', async () => {
    const onOpenQuote = jest.fn();
    const record = standardEditorRecord(reviewedRecord, {
      identity_review_approved: false,
    });
    const approvedRecord = {
      ...record,
      identity_review_approved: true,
    };
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: record });
    quotationAPI.gmailInquiryImports.approveCompany.mockResolvedValueOnce({ data: approvedRecord });
    quotationAPI.gmailInquiryImports.confirm.mockResolvedValueOnce({
      data: { ...approvedRecord, status: 'confirmed', quotation_id: 99 },
    });

    render(<GmailInquiryReview importId="31" onOpenQuote={onOpenQuote} />);

    expect(await screen.findByRole('heading', {
      name: '2. Quotation lines',
    })).toBeInTheDocument();
    expect(screen.getByText(/1 request row loaded/i)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '1. Confirm customer identity' })).toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: 'Product Decision' })).not.toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /Unit Price/i })).toBeInTheDocument();
    expect(screen.queryByLabelText('Requested item row 1')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save reviewed rows' })).not.toBeInTheDocument();
    const lockedEditor = screen.getByRole('group', {
      name: 'Quotation editor locked until company confirmation',
    });
    expect(lockedEditor).toBeDisabled();
    within(lockedEditor).getAllByRole('button').forEach((button) => expect(button).toBeDisabled());
    expect(within(lockedEditor).getByRole('button', { name: 'Finalize' })).toBeDisabled();
    expect(within(lockedEditor).queryByRole('button', { name: 'Review Email' })).not.toBeInTheDocument();
    expect(quotationAPI.items.list).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Confirm company & unlock quotation' }));

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.approveCompany).toHaveBeenCalledWith(31, {
      company: '7',
      contact: null,
      suggested: false,
      identity_review_fingerprint: CHAIN_IDENTITY_BEFORE,
    }));

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.confirm).toHaveBeenCalledWith(31, {
      company: '7',
      contact: null,
      expected_source_fingerprint: CHAIN_SOURCE_BEFORE,
      expected_analysis_attempt: 2,
      identity_review_fingerprint: CHAIN_IDENTITY_BEFORE,
      expected_review_rows_fingerprint: CHAIN_ROWS_BEFORE,
      selected_source_keys: ['attachment:opaque-1'],
    }));
    expect(quotationAPI.gmailInquiryImports.confirmAndPrepareQuotation).not.toHaveBeenCalled();
    expect(onOpenQuote).not.toHaveBeenCalled();
    expect(await screen.findByRole('heading', { name: 'Standard quotation editor #99' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '1. Confirmed customer identity' })).toBeInTheDocument();
  });

  test('prevents a double company-confirm click from creating duplicate quotations', async () => {
    const record = standardEditorRecord(reviewedRecord, {
      identity_review_approved: false,
    });
    const approvedRecord = { ...record, identity_review_approved: true };
    const approval = deferred();
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: record });
    quotationAPI.gmailInquiryImports.approveCompany.mockReturnValueOnce(approval.promise);
    quotationAPI.gmailInquiryImports.confirm.mockResolvedValueOnce({
      data: { ...approvedRecord, status: 'confirmed', quotation_id: 99 },
    });

    render(<GmailInquiryReview importId="31" />);

    const button = await screen.findByRole('button', {
      name: 'Confirm company & unlock quotation',
    });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(quotationAPI.gmailInquiryImports.approveCompany).toHaveBeenCalledTimes(1);

    await act(async () => {
      approval.resolve({ data: approvedRecord });
      await approval.promise;
    });

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.confirm).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole('heading', { name: 'Standard quotation editor #99' })).toBeInTheDocument();
  });

  test('keeps a successful company approval and safely retries quotation unlock after a failure', async () => {
    const record = standardEditorRecord(reviewedRecord, {
      identity_review_approved: false,
    });
    const approvedRecord = { ...record, identity_review_approved: true };
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: record });
    quotationAPI.gmailInquiryImports.approveCompany.mockResolvedValueOnce({ data: approvedRecord });
    quotationAPI.gmailInquiryImports.confirm
      .mockRejectedValueOnce(new Error('temporary network failure'))
      .mockResolvedValueOnce({
        data: { ...approvedRecord, status: 'confirmed', quotation_id: 99 },
      });

    render(<GmailInquiryReview importId="31" />);

    fireEvent.click(await screen.findByRole('button', {
      name: 'Confirm company & unlock quotation',
    }));

    expect(await screen.findByText(/company is confirmed, but the quotation did not unlock/i)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '2. Quotation lines' })).toBeInTheDocument();
    expect(quotationAPI.gmailInquiryImports.approveCompany).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: 'Retry unlocking quotation' }));

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.confirm).toHaveBeenCalledTimes(2));
    expect(quotationAPI.gmailInquiryImports.approveCompany).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole('heading', { name: 'Standard quotation editor #99' })).toBeInTheDocument();
  });

  test('keeps the quotation locked when the approval response is not actually approved', async () => {
    const record = standardEditorRecord(reviewedRecord, {
      identity_review_approved: false,
    });
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: record });
    quotationAPI.gmailInquiryImports.approveCompany.mockResolvedValueOnce({
      data: { ...record, identity_review_approved: false },
    });

    render(<GmailInquiryReview importId="31" />);

    fireEvent.click(await screen.findByRole('button', {
      name: 'Confirm company & unlock quotation',
    }));

    expect(await screen.findByText(/server did not confirm the company approval/i)).toBeInTheDocument();
    expect(quotationAPI.gmailInquiryImports.confirm).not.toHaveBeenCalled();
    expect(screen.getByRole('group', {
      name: 'Quotation editor locked until company confirmation',
    })).toBeDisabled();
    expect(screen.queryByRole('heading', { name: /standard quotation editor/i })).not.toBeInTheDocument();
  });

  test('keeps the quotation locked when the approved response lacks a current safety binding', async () => {
    const record = standardEditorRecord(reviewedRecord, {
      identity_review_approved: false,
    });
    const approvedWithoutBinding = {
      ...record,
      identity_review_approved: true,
      review_rows_fingerprint: '',
    };
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: record });
    quotationAPI.gmailInquiryImports.approveCompany.mockResolvedValueOnce({
      data: approvedWithoutBinding,
    });

    render(<GmailInquiryReview importId="31" />);

    fireEvent.click(await screen.findByRole('button', {
      name: 'Confirm company & unlock quotation',
    }));

    expect(await screen.findByText(/safety binding is incomplete/i)).toBeInTheDocument();
    expect(quotationAPI.gmailInquiryImports.confirm).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Unlock quotation' })).toBeDisabled();
  });

  test('does not attempt to create a quotation when analysis returned no usable rows', async () => {
    const emptySource = {
      ...reviewedRecord,
      analysis: {
        ...reviewedRecord.analysis,
        preview: { ...reviewedRecord.analysis.preview, lines: [] },
      },
    };
    const record = standardEditorRecord(emptySource, {
      identity_review_approved: false,
    });
    const approvedRecord = { ...record, identity_review_approved: true };
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: record });
    quotationAPI.gmailInquiryImports.approveCompany.mockResolvedValueOnce({ data: approvedRecord });

    render(<GmailInquiryReview importId="31" />);

    fireEvent.click(await screen.findByRole('button', {
      name: 'Confirm company & unlock quotation',
    }));

    expect(await screen.findByText(/no usable request rows are available/i)).toBeInTheDocument();
    expect(quotationAPI.gmailInquiryImports.confirm).not.toHaveBeenCalled();
    expect(screen.getByText(/no usable request rows are ready yet/i)).toBeInTheDocument();
  });

  test('shows only exceptional rows before the standard quotation can be created', async () => {
    const validLine = {
      ...reviewedRecord.analysis.preview.lines[0],
      row_key: 'row-key-valid-0000001',
      raw_name: 'Sterile Gauze',
      operation: 'added',
      parse_status: 'parsed',
      reviewed_by_user: true,
    };
    const uncertainLine = {
      ...baseRecord.analysis.preview.lines[0],
      row_key: 'row-key-uncertain-001',
      raw_name: 'Possible Dressing',
    };
    const source = {
      ...reviewedRecord,
      analysis: {
        ...reviewedRecord.analysis,
        preview: {
          ...reviewedRecord.analysis.preview,
          lines: [validLine, uncertainLine],
        },
      },
    };
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({
      data: standardEditorRecord(source),
    });

    render(<GmailInquiryReview importId="31" />);

    expect(await screen.findByRole('heading', {
      name: 'Source exceptions to resolve before unlocking',
    })).toBeInTheDocument();
    expect(screen.queryByLabelText('Requested item row 1')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Requested item row 2')).toHaveValue('Possible Dressing');
    expect(screen.queryByRole('button', { name: 'Save reviewed rows' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Unlock quotation' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Mark reviewed' }));
    const saveAndUnlockButton = screen.getByRole('button', {
      name: 'Save exceptions & unlock quotation',
    });
    expect(saveAndUnlockButton).toBeEnabled();
    fireEvent.click(saveAndUnlockButton);

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.update).toHaveBeenCalledWith(31, {
      review_lines: [{
        row_key: 'row-key-uncertain-001',
        raw_name: 'Possible Dressing',
        quantity: '10.000',
        unit: 'Pcs',
        included: true,
        reviewed: true,
      }],
      expected_source_fingerprint: CHAIN_SOURCE_BEFORE,
      expected_analysis_attempt: 2,
      identity_review_fingerprint: CHAIN_IDENTITY_BEFORE,
      expected_review_rows_fingerprint: CHAIN_ROWS_BEFORE,
    }));
  });

  test('keeps source-exception controls locked until the employee confirms the company', async () => {
    const source = standardEditorRecord(baseRecord, {
      identity_review_approved: false,
    });
    const approvedSource = {
      ...source,
      identity_review_approved: true,
    };
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: source });
    quotationAPI.gmailInquiryImports.approveCompany.mockResolvedValueOnce({ data: approvedSource });

    render(<GmailInquiryReview importId="31" />);

    const requestedItem = await screen.findByLabelText('Requested item row 1');
    expect(requestedItem).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Mark reviewed' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', {
      name: 'Confirm company & review exceptions',
    }));

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.approveCompany).toHaveBeenCalledTimes(1));
    expect(quotationAPI.gmailInquiryImports.confirm).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Requested item row 1')).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Mark reviewed' })).toBeEnabled();
  });

  test('keeps a sole excluded exception visible so it can be saved or restored', async () => {
    const source = standardEditorRecord(baseRecord);
    const excludedLine = {
      ...baseRecord.analysis.preview.lines[0],
      included: false,
    };
    const savedRecord = standardEditorRecord({
      ...baseRecord,
      analysis: {
        ...baseRecord.analysis,
        preview: {
          ...baseRecord.analysis.preview,
          lines: [excludedLine],
        },
      },
    });
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: source });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({ data: savedRecord });

    render(<GmailInquiryReview importId="31" />);

    const requestedItem = await screen.findByLabelText('Requested item row 1');
    const row = requestedItem.closest('tr');
    const includeCheckbox = within(row).getByRole('checkbox');
    fireEvent.click(includeCheckbox);

    await waitFor(() => expect(screen.getByText('Excluded')).toBeInTheDocument());
    expect(screen.getByLabelText('Requested item row 1')).toBeInTheDocument();
    const saveButton = await screen.findByRole('button', { name: 'Save reviewed rows' });
    expect(saveButton).toBeEnabled();
    fireEvent.click(saveButton);

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.update).toHaveBeenCalledWith(31, {
      review_lines: [{
        row_key: 'row-key-000000000001',
        raw_name: 'Bandage',
        quantity: '10.000',
        unit: 'Pcs',
        included: false,
        reviewed: false,
      }],
      expected_source_fingerprint: CHAIN_SOURCE_BEFORE,
      expected_analysis_attempt: 2,
      identity_review_fingerprint: CHAIN_IDENTITY_BEFORE,
      expected_review_rows_fingerprint: CHAIN_ROWS_BEFORE,
    }));

    const savedRow = screen.getByText('Excluded').closest('tr');
    const restoreCheckbox = within(savedRow).getByRole('checkbox');
    expect(restoreCheckbox).not.toBeChecked();
    fireEvent.click(restoreCheckbox);
    expect(restoreCheckbox).toBeChecked();
    expect(screen.queryByRole('button', { name: 'Save reviewed rows' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Mark reviewed' })).toBeEnabled();
  });

  test('shows every retained source row when a confirmed quotation reopens Gmail evidence', async () => {
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({
      data: standardEditorRecord(reviewedRecord, {
        status: 'confirmed',
        quotation: 99,
        quotation_id: 99,
      }),
    });

    render(<GmailInquiryReview importId="31" />);

    expect(await screen.findByRole('heading', { name: 'Standard quotation editor #99' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '1. Confirmed customer identity' })).toBeInTheDocument();
    expect(screen.getByText(/quotation is unlocked below.*evidence remains available/i)).toBeInTheDocument();
    expect(screen.queryByText(/nothing is created automatically/i)).not.toBeInTheDocument();
    const embeddedEditor = screen.getByRole('region', { name: 'Embedded standard quotation editor' });
    fireEvent.click(within(embeddedEditor).getByRole('button', { name: 'View Gmail evidence' }));
    expect(await screen.findByRole('heading', { name: '2. Gmail source evidence' })).toBeInTheDocument();
    expect(within(embeddedEditor).getByRole('button', { name: 'Hide Gmail evidence' })).toBeInTheDocument();
    expect(screen.getByLabelText('Requested item row 1')).toHaveValue('Sterile Bandage');
    expect(screen.getByLabelText('Requested item row 1')).toBeDisabled();
    expect(screen.getByRole('button', { name: /open source request\.pdf/i })).toBeInTheDocument();
    expect(screen.queryByText(/confirm below to create the draft/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save reviewed rows' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /create.*quotation/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Review outcome' }));
    expect(screen.getByRole('heading', { name: 'Quotation outcome #99' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Back to quotation editor' }));
    expect(screen.getByRole('heading', { name: 'Standard quotation editor #99' })).toBeInTheDocument();
  });

  test('shows confirmed Gmail evidence immediately when opened from its quotation', async () => {
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({
      data: standardEditorRecord(reviewedRecord, {
        status: 'confirmed',
        quotation: 99,
        quotation_id: 99,
      }),
    });

    render(<GmailInquiryReview importId="31" initialShowEvidence />);

    expect(await screen.findByRole('heading', { name: '2. Gmail source evidence' })).toBeInTheDocument();
    expect(screen.getByLabelText('Requested item row 1')).toHaveValue('Sterile Bandage');
    expect(screen.getByRole('heading', { name: 'Standard quotation editor #99' })).toBeInTheDocument();
    expect(within(
      screen.getByRole('region', { name: 'Embedded standard quotation editor' })
    ).getByRole('button', { name: 'Hide Gmail evidence' })).toBeInTheDocument();
  });

  test('routes an embedded editor revision directly to the returned draft quotation', async () => {
    const onOpenQuote = jest.fn();
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({
      data: standardEditorRecord(reviewedRecord, {
        status: 'confirmed',
        quotation: 99,
        quotation_id: 99,
      }),
    });

    render(<GmailInquiryReview importId="31" onOpenQuote={onOpenQuote} />);

    const embeddedEditor = await screen.findByRole('region', {
      name: 'Embedded standard quotation editor',
    });
    fireEvent.click(within(embeddedEditor).getByRole('button', {
      name: 'Open revision draft',
    }));

    expect(onOpenQuote).toHaveBeenCalledWith(100);
    expect(onOpenQuote).toHaveBeenCalledTimes(1);
  });

  test('keeps backend-rejected quantity boundaries visible as standard-intake exceptions', async () => {
    const source = {
      ...reviewedRecord,
      analysis: {
        ...reviewedRecord.analysis,
        preview: {
          ...reviewedRecord.analysis.preview,
          lines: [{
            ...reviewedRecord.analysis.preview.lines[0],
            row_key: 'row-key-too-large-001',
            raw_name: 'Large quantity item',
            quantity: '1000000000',
          }, {
            ...reviewedRecord.analysis.preview.lines[0],
            row_key: 'row-key-too-precise-1',
            raw_name: 'Over-precise item',
            quantity: '1.0000',
          }],
        },
      },
    };
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({
      data: standardEditorRecord(source),
    });

    render(<GmailInquiryReview importId="31" />);

    expect(await screen.findByLabelText('Requested item row 1')).toHaveValue('Large quantity item');
    expect(screen.getByLabelText('Requested item row 2')).toHaveValue('Over-precise item');
    expect(screen.getByRole('button', { name: 'Unlock quotation' })).toBeDisabled();
  });

  test('reviews evidence, saves exact row edits, then confirms and opens the returned quotation', async () => {
    const onOpenQuote = jest.fn();
    render(<GmailInquiryReview importId="31" onOpenQuote={onOpenQuote} />);

    expect(await screen.findByDisplayValue('Bandage')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '1. Confirm customer identity' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '2. Review extracted request lines' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /thread and analysis/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /reanalyze selection/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('radio')).not.toBeInTheDocument();
    expect(screen.queryByText('Context reply')).not.toBeInTheDocument();
    expect(screen.queryByText('Old unrelated request')).not.toBeInTheDocument();
    expect(screen.getByText('buyer@example.com')).toBeInTheDocument();
    expect(screen.getByText('request.pdf | page 2 | sheet Items | cells B4:D4')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /attachments and source evidence/i })).not.toBeInTheDocument();
    expect(screen.queryByText('AED 5.50')).not.toBeInTheDocument();
    expect(screen.getByText('5.50 (currency not stated)')).toBeInTheDocument();
    expect(screen.getByText(/Confirming as sara/i)).toBeInTheDocument();
    const contactSelect = screen.getByLabelText('Contact / Purchaser');
    expect(contactSelect.closest('label')).toHaveClass('qm-gmail-contact-picker');

    const confirmButton = screen.getByRole('button', { name: /confirm & open quotation/i });
    expect(confirmButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText('Requested item row 1'), {
      target: { value: 'Sterile Bandage' },
    });
    const saveButton = screen.getByRole('button', { name: /save reviewed rows/i });
    expect(saveButton).toHaveClass('qm-primary');
    expect(screen.getByText(/unsaved row changes.*unlock confirmation/i)).toBeInTheDocument();
    expect(confirmButton).toBeDisabled();
    fireEvent.click(saveButton);

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.update).toHaveBeenCalledWith(31, {
      review_lines: [{
        row_key: 'row-key-000000000001',
        raw_name: 'Sterile Bandage',
        quantity: '10.000',
        unit: 'Pcs',
        included: true,
      }],
    }));

    await screen.findByText('staff reviewed');
    expect(screen.queryByText(/unsaved row changes.*unlock confirmation/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/confirm that this inquiry belongs/i));
    await waitFor(() => expect(confirmButton).toBeEnabled());
    fireEvent.click(confirmButton);

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.confirm).toHaveBeenCalledWith(31, {
      company: '7',
      contact: '8',
      selected_source_keys: ['attachment:opaque-1'],
    }));
    expect(onOpenQuote).toHaveBeenCalledWith(99);
  });

  test('automatically analyzes the existing Gmail selection against a deduplicated import ID', async () => {
    const onClaimed = jest.fn();
    let resolveAnalysis;
    const claimedRecord = {
      ...baseRecord,
      status: 'claimed',
      analysis: {
        ...baseRecord.analysis,
        preview: {
          ...baseRecord.analysis.preview,
          lines: [],
        },
      },
    };
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({
      data: claimedRecord,
    });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({
      data: {
        ...claimedRecord,
        id: 44,
      },
    });
    quotationAPI.gmailInquiryImports.analyze.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveAnalysis = resolve;
      })
    );

    render(<GmailInquiryReview importId="31" onClaimed={onClaimed} />);

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.update).toHaveBeenCalledWith(31, {
      mode: 'selected_messages',
      selected_message_ids: ['m-1', 'm-2'],
    }));
    expect(quotationAPI.gmailInquiryImports.analyze).toHaveBeenCalledWith(44, {
      force: false,
    });
    // The replacement ID must be durable in the URL before the long analysis
    // response returns.
    expect(onClaimed).toHaveBeenCalledWith(44);
    expect(screen.queryByDisplayValue('Bandage')).not.toBeInTheDocument();
    expect(screen.getByText(/usually takes 15.*30 seconds/i)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /thread and analysis/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('radio')).not.toBeInTheDocument();
    expect(await screen.findByText('Analyzing the request...')).toBeInTheDocument();
    expect(screen.queryByText(/no inquiry rows were extracted/i)).not.toBeInTheDocument();

    await act(async () => {
      resolveAnalysis({
        data: {
          ...reviewedRecord,
          id: 44,
          selected_message_ids: ['m-1', 'm-2'],
        },
      });
    });
  });

  test('shows analysis immediately while the initial long request is still pending', async () => {
    let resolveAnalysis;
    const claimedRecord = {
      ...baseRecord,
      status: 'claimed',
      mode: 'ai_thread',
      selected_message_ids: [],
      message_manifest: [],
      attachment_manifest: [],
      evidence: [],
      analysis: {
        preview: {
          warnings: [],
          meta: {},
          lines: [],
        },
      },
    };
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({
      data: claimedRecord,
    });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({
      data: claimedRecord,
    });
    quotationAPI.gmailInquiryImports.analyze.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveAnalysis = resolve;
      })
    );

    render(<GmailInquiryReview importId="31" />);

    expect(await screen.findByText(
      /analyzing the gmail inquiry and supported documents/i
    )).toBeInTheDocument();
    expect(screen.getByText(/usually takes 15.*30 seconds/i)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /thread and analysis/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /reanalyze selection/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('radio')).not.toBeInTheDocument();
    expect(screen.getByText('Analyzing the request...')).toBeInTheDocument();
    expect(screen.queryByText(/no inquiry rows were extracted/i)).not.toBeInTheDocument();

    await act(async () => {
      resolveAnalysis({
        data: {
          ...reviewedRecord,
          mode: 'ai_thread',
          selected_message_ids: ['m-1'],
        },
      });
    });
    expect(await screen.findByDisplayValue('Sterile Bandage')).toBeInTheDocument();
  });

  test('clears stale rows when retry returns a replacement source fingerprint', async () => {
    let resolveAnalysis;
    const failedSelectedRecord = {
      ...baseRecord,
      status: 'failed',
      source_fingerprint: 'selected-source-fingerprint',
    };
    const replacementClaimedRecord = {
      ...failedSelectedRecord,
      status: 'claimed',
      source_fingerprint: 'replacement-source-fingerprint',
      analysis: {
        preview: {
          warnings: [],
          meta: {},
          lines: [],
        },
      },
    };
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({
      data: failedSelectedRecord,
    });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({
      data: replacementClaimedRecord,
    });
    quotationAPI.gmailInquiryImports.analyze.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveAnalysis = resolve;
      })
    );

    render(<GmailInquiryReview importId="31" />);
    await screen.findByDisplayValue('Bandage');
    fireEvent.click(screen.getByRole('button', { name: /retry analysis/i }));

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.update).toHaveBeenCalledWith(31, {
      mode: 'selected_messages',
      selected_message_ids: ['m-1', 'm-2'],
    }));
    expect(screen.queryByDisplayValue('Bandage')).not.toBeInTheDocument();
    expect(await screen.findByText('Analyzing the request...')).toBeInTheDocument();

    await act(async () => {
      resolveAnalysis({
        data: {
          ...reviewedRecord,
          source_fingerprint: 'replacement-source-fingerprint',
        },
      });
    });
    expect(await screen.findByDisplayValue('Sterile Bandage')).toBeInTheDocument();
  });

  test('recovers an analysis timeout by checking server status and continuing to poll', async () => {
    const failedRecord = {
      ...baseRecord,
      status: 'failed',
    };
    quotationAPI.gmailInquiryImports.retrieve
      .mockResolvedValueOnce({ data: failedRecord })
      .mockResolvedValueOnce({
        data: {
          ...baseRecord,
          status: 'analyzing',
        },
      });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({ data: baseRecord });
    quotationAPI.gmailInquiryImports.analyze.mockRejectedValueOnce(
      Object.assign(new Error('timeout'), { code: 'ECONNABORTED' })
    );

    render(<GmailInquiryReview importId="31" />);
    await screen.findByDisplayValue('Bandage');
    fireEvent.click(screen.getByRole('button', { name: /retry analysis/i }));

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.analyze).toHaveBeenCalledWith(31, {
      force: true,
    }));
    expect(await screen.findByText(
      /gmail analysis is still processing/i
    )).toBeInTheDocument();
    expect(quotationAPI.gmailInquiryImports.retrieve).toHaveBeenCalledTimes(2);
    expect(screen.queryByText('Request failed')).not.toBeInTheDocument();
  });

  test('resumes polling when a remounted replacement import is already analyzing', async () => {
    const claimedRecord = {
      ...baseRecord,
      status: 'claimed',
      mode: 'ai_thread',
      selected_message_ids: [],
      message_manifest: [],
      attachment_manifest: [],
      evidence: [],
      analysis: {
        preview: {
          warnings: [],
          meta: {},
          lines: [],
        },
      },
    };
    const analyzingRecord = {
      ...claimedRecord,
      status: 'analyzing',
    };
    quotationAPI.gmailInquiryImports.retrieve
      .mockResolvedValueOnce({ data: claimedRecord })
      .mockResolvedValueOnce({ data: analyzingRecord });
    quotationAPI.gmailInquiryImports.update.mockRejectedValueOnce(
      Object.assign(new Error('Already analyzing.'), {
        response: { status: 409 },
      })
    );

    render(<GmailInquiryReview importId="31" />);

    expect(await screen.findByText(/gmail analysis is still processing/i)).toBeInTheDocument();
    expect(quotationAPI.gmailInquiryImports.retrieve).toHaveBeenCalledTimes(2);
    expect(quotationAPI.gmailInquiryImports.analyze).not.toHaveBeenCalled();
    expect(screen.getByText(/analyzing the gmail inquiry and supported documents/i)).toBeInTheDocument();
    expect(screen.getByText(/usually takes 15.*30 seconds/i)).toBeInTheDocument();
    expect(screen.queryByText(/no inquiry rows were extracted/i)).not.toBeInTheDocument();
  });

  test('refreshes failed server state after a nonrecoverable analysis error without auto-retrying', async () => {
    const retryableFailedRecord = {
      ...baseRecord,
      status: 'failed',
    };
    const failedRecord = {
      ...baseRecord,
      status: 'failed',
      analysis: {
        preview: {
          warnings: [],
          meta: {},
          lines: [],
        },
      },
      errors: [{ message: 'Invalid analysis request.' }],
    };
    quotationAPI.gmailInquiryImports.retrieve
      .mockResolvedValueOnce({ data: retryableFailedRecord })
      .mockResolvedValueOnce({ data: failedRecord });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({
      data: {
        ...baseRecord,
        status: 'claimed',
      },
    });
    quotationAPI.gmailInquiryImports.analyze.mockRejectedValueOnce(
      Object.assign(new Error('Invalid analysis request.'), {
        response: { status: 400 },
      })
    );

    render(<GmailInquiryReview importId="31" />);
    await screen.findByDisplayValue('Bandage');
    fireEvent.click(screen.getByRole('button', { name: /retry analysis/i }));

    expect(await screen.findByText('failed')).toBeInTheDocument();
    expect(quotationAPI.gmailInquiryImports.retrieve).toHaveBeenCalledTimes(2);
    expect(quotationAPI.gmailInquiryImports.analyze).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/no inquiry rows were extracted/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /retry analysis/i })).toBeInTheDocument();
  });

  test('keeps polling after a transient refresh failure', async () => {
    jest.useFakeTimers();
    const analyzingRecord = {
      ...baseRecord,
      status: 'analyzing',
      analysis: {
        ...baseRecord.analysis,
        preview: {
          ...baseRecord.analysis.preview,
          lines: [],
        },
      },
    };
    quotationAPI.gmailInquiryImports.retrieve
      .mockResolvedValueOnce({ data: analyzingRecord })
      .mockRejectedValueOnce(new Error('temporary network failure'))
      .mockResolvedValueOnce({ data: reviewedRecord });

    try {
      render(<GmailInquiryReview importId="31" />);
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(quotationAPI.gmailInquiryImports.retrieve).toHaveBeenCalledTimes(1);

      await act(async () => {
        jest.advanceTimersByTime(1800);
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(quotationAPI.gmailInquiryImports.retrieve).toHaveBeenCalledTimes(2);

      await act(async () => {
        jest.advanceTimersByTime(1800);
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(quotationAPI.gmailInquiryImports.retrieve).toHaveBeenCalledTimes(3);
      expect(screen.getByDisplayValue('Sterile Bandage')).toBeInTheDocument();
    } finally {
      jest.useRealTimers();
    }
  });

  test('shows a compact hero analysis state before results are persisted', async () => {
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({
      data: {
        ...baseRecord,
        status: 'analyzing',
        message_manifest: [],
        attachment_manifest: [],
        evidence: [],
        analysis: {
          ...baseRecord.analysis,
          preview: {
            ...baseRecord.analysis.preview,
            lines: [],
          },
        },
      },
    });

    render(<GmailInquiryReview importId="31" />);

    expect(await screen.findByText(/analyzing the gmail inquiry and supported documents/i)).toBeInTheDocument();
    expect(screen.getByText(/usually takes 15.*30 seconds/i)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /thread and analysis/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/no gmail thread messages are available/i)).not.toBeInTheDocument();
  });

  test('offers a compact continue action when completed analysis has no rows', async () => {
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({
      data: {
        ...baseRecord,
        status: 'review_required',
        message_manifest: [],
        analysis: {
          ...baseRecord.analysis,
          preview: {
            ...baseRecord.analysis.preview,
            lines: [],
          },
        },
      },
    });

    render(<GmailInquiryReview importId="31" />);

    expect(await screen.findByRole('button', { name: /continue analysis/i })).toBeInTheDocument();
    expect(screen.getByText(/no inquiry rows were extracted/i)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /thread and analysis/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/no gmail thread messages are available/i)).not.toBeInTheDocument();
  });

  test.each([
    ['exact_contact_email', 'Suggested from an exact sender email match'],
    ['verified_email_domain', 'Suggested from a verified company email domain'],
    ['company_name_domain_inference', 'Suggested from company-name and email-domain inference'],
    ['exact_company_name_signature', 'Suggested from the company name in the email signature'],
    ['domain_signature_inference', 'Suggested from email domain and signature inference'],
  ])('labels a %s company suggestion and requires staff confirmation', async (matchMethod, label) => {
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({
      data: {
        ...baseRecord,
        company: null,
        contact: null,
        candidates: {
          ...baseRecord.candidates,
          companies: [{
            ...baseRecord.candidates.companies[0],
            match_method: matchMethod,
          }],
        },
      },
    });

    render(<GmailInquiryReview importId="31" />);

    const suggestionButton = await screen.findByRole('button', { name: 'Use suggested company' });
    const suggestion = suggestionButton.closest('.qm-gmail-company-suggestion');
    expect(within(suggestion).getByText(label)).toBeInTheDocument();
    expect(within(suggestion).getByText('Example Medical')).toBeInTheDocument();
    expect(within(suggestion).getByText(
      'Staff confirmation is required before creating the quotation.'
    )).toBeInTheDocument();
  });

  test('does not promote a sole candidate when the backend deliberately recommends none', async () => {
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({
      data: {
        ...baseRecord,
        company: null,
        contact: null,
        candidates: {
          ...baseRecord.candidates,
          recommended_company_id: null,
          recommended_contact_id: null,
        },
      },
    });

    render(<GmailInquiryReview importId="31" />);

    expect(await screen.findByRole('option', { name: 'Example Medical' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Use suggested company' })).not.toBeInTheDocument();
  });

  test('shows the AI-read signature identity separately from the selected saved records', async () => {
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({
      data: {
        ...reviewedRecord,
        candidates: {
          ...reviewedRecord.candidates,
          ai_identity: {
            company_name: 'HILTON DUBAI PALM JUMEIRAH',
            contact_name: 'Faiza Ahmad',
            contact_email: 'faiza.ahmad@hilton.com',
            confidence: 0.94,
            reason: 'The company and purchaser appear in the email signature.',
          },
        },
      },
    });

    render(<GmailInquiryReview importId="31" />);

    const aiIdentityPanel = await screen.findByLabelText('AI-detected customer identity');
    expect(within(aiIdentityPanel).getByText('HILTON DUBAI PALM JUMEIRAH')).toBeInTheDocument();
    expect(within(aiIdentityPanel).getByText('Faiza Ahmad')).toBeInTheDocument();
    expect(within(aiIdentityPanel).getByText('faiza.ahmad@hilton.com')).toBeInTheDocument();
    expect(within(aiIdentityPanel).getByText('94% confidence')).toBeInTheDocument();
    expect(within(aiIdentityPanel).getByText(
      /evidence only.*compare this with the selected saved company and purchaser/i
    )).toBeInTheDocument();
  });

  test('labels identity read from forwarded content as unverified', async () => {
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({
      data: {
        ...reviewedRecord,
        candidates: {
          ...reviewedRecord.candidates,
          ai_identity_unverified_forwarded: true,
          ai_identity: {
            company_name: 'Forwarded Customer LLC',
            contact_name: 'Original Buyer',
            contact_email: 'buyer@forwarded.example',
            confidence: 0.91,
            reason: 'Read from the forwarded request.',
          },
        },
      },
    });

    render(<GmailInquiryReview importId="31" />);

    const aiIdentityPanel = await screen.findByLabelText('AI-detected customer identity');
    expect(within(aiIdentityPanel).getByText(
      'Read from unverified forwarded content'
    )).toBeInTheDocument();
    expect(screen.getByText(
      'Suggested from unverified forwarded content; confirm manually'
    )).toBeInTheDocument();
  });

  test('prompts for and starts reanalysis of stale identity suggestions', async () => {
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({
      data: {
        ...reviewedRecord,
        candidates: {
          ...reviewedRecord.candidates,
          identity_reanalysis_required: true,
          recommended_company_id: null,
          recommended_contact_id: null,
        },
      },
    });

    render(<GmailInquiryReview importId="31" />);

    expect(await screen.findByText(
      /customer identity matching was upgraded/i
    )).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {
      name: 'Reanalyze Gmail inquiry',
    }));

    await waitFor(() => expect(
      quotationAPI.gmailInquiryImports.analyze
    ).toHaveBeenCalledWith(31, { force: true }));
  });

  test('uses included rows as the source decision without a separate evidence selector', async () => {
    const conflictRecord = {
      ...reviewedRecord,
      analysis: {
        ...reviewedRecord.analysis,
        recommended_source_keys: ['attachment:opaque-1'],
        preview: {
          ...reviewedRecord.analysis.preview,
          lines: [{
            ...reviewedRecord.analysis.preview.lines[0],
            _source_keys: ['attachment:opaque-1', 'body:opaque-2'],
            evidence: [
              { source_key: 'attachment:opaque-1' },
              { source_key: 'body:opaque-2' },
            ],
          }],
        },
      },
    };
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: conflictRecord });

    const { container } = render(<GmailInquiryReview importId="31" />);

    expect(await screen.findByDisplayValue('Sterile Bandage')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /attachments and source evidence/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/confirmation is blocked/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open source request.pdf | page 2' })).toBeInTheDocument();
    expect(container.querySelectorAll('.qm-gmail-row-evidence > *')).toHaveLength(2);
    const confirmButton = screen.getByRole('button', { name: /confirm & open quotation/i });
    fireEvent.click(screen.getByLabelText(/confirm that this inquiry belongs/i));
    await waitFor(() => expect(confirmButton).toBeEnabled());
    fireEvent.click(confirmButton);
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.confirm).toHaveBeenCalledWith(31, {
      company: '7',
      contact: '8',
      selected_source_keys: ['attachment:opaque-1', 'body:opaque-2'],
    }));
  });

  test('blocks an included row that has no source provenance', async () => {
    const missingEvidenceRecord = {
      ...reviewedRecord,
      analysis: {
        ...reviewedRecord.analysis,
        preview: {
          ...reviewedRecord.analysis.preview,
          lines: [{
            ...reviewedRecord.analysis.preview.lines[0],
            _source_keys: [],
            evidence: [],
            source_page: null,
          }],
        },
      },
    };
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({
      data: missingEvidenceRecord,
    });

    render(<GmailInquiryReview importId="31" />);

    expect(await screen.findByText(
      /1 included row\(s\) have no source evidence/i
    )).toBeInTheDocument();
    expect(screen.getByText('Evidence link unavailable')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/confirm that this inquiry belongs/i));
    const confirmButton = screen.getByRole('button', { name: /confirm & open quotation/i });
    expect(confirmButton).toBeDisabled();
    expect(screen.getByText(/retry analysis above or exclude every row without source evidence/i)).toBeInTheDocument();
    expect(quotationAPI.gmailInquiryImports.confirm).not.toHaveBeenCalled();
  });

  test('requires a fresh identity acknowledgement after retrying failed analysis', async () => {
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({
      data: {
        ...baseRecord,
        status: 'failed',
      },
    });
    render(<GmailInquiryReview importId="31" />);

    await screen.findByDisplayValue('Bandage');
    const identityCheckbox = screen.getByLabelText(/confirm that this inquiry belongs/i);
    fireEvent.click(identityCheckbox);
    expect(identityCheckbox).toBeChecked();

    fireEvent.click(screen.getByRole('button', { name: /retry analysis/i }));
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.analyze).toHaveBeenCalled());
    expect(identityCheckbox).not.toBeChecked();
    expect(screen.getByRole('button', { name: /confirm & open quotation/i })).toBeDisabled();
  });

  test('ignores a confirmation response after the review is unmounted', async () => {
    let resolveConfirmation;
    const onOpenQuote = jest.fn();
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: reviewedRecord });
    quotationAPI.gmailInquiryImports.confirm.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveConfirmation = resolve;
      })
    );
    const { unmount } = render(
      <GmailInquiryReview importId="31" onOpenQuote={onOpenQuote} />
    );

    await screen.findByDisplayValue('Sterile Bandage');
    fireEvent.click(screen.getByLabelText(/confirm that this inquiry belongs/i));
    fireEvent.click(screen.getByRole('button', { name: /confirm & open quotation/i }));
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.confirm).toHaveBeenCalled());
    unmount();

    await act(async () => {
      resolveConfirmation({
        data: {
          ...reviewedRecord,
          status: 'confirmed',
          quotation_id: 99,
        },
      });
    });
    expect(onOpenQuote).not.toHaveBeenCalled();
  });

  test('allows an invalid row to be explicitly excluded and sends null quantity', async () => {
    const invalidRecord = {
      ...baseRecord,
      analysis: {
        ...baseRecord.analysis,
        preview: {
          ...baseRecord.analysis.preview,
          lines: [{
            ...baseRecord.analysis.preview.lines[0],
            quantity: null,
            unit: '',
          }],
        },
      },
    };
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: invalidRecord });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({
      data: {
        ...invalidRecord,
        analysis: {
          ...invalidRecord.analysis,
          preview: {
            ...invalidRecord.analysis.preview,
            lines: [{
              ...invalidRecord.analysis.preview.lines[0],
              included: false,
              parse_status: 'ignored',
              reviewed_by_user: true,
            }],
          },
        },
      },
    });

    render(<GmailInquiryReview importId="31" />);
    const includeLabel = (await screen.findByText('Included')).closest('label');
    fireEvent.click(within(includeLabel).getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: /save reviewed rows/i }));

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.update).toHaveBeenCalledWith(31, {
      review_lines: [{
        row_key: 'row-key-000000000001',
        raw_name: 'Bandage',
        quantity: null,
        unit: '',
        included: false,
      }],
    }));
  });

  test('opens cited source evidence using only its opaque source key', async () => {
    const originalOpen = window.open;
    const originalCreateObjectURL = window.URL.createObjectURL;
    const originalRevokeObjectURL = window.URL.revokeObjectURL;
    const previewWindow = { location: { href: '' }, close: jest.fn(), opener: {} };
    window.open = jest.fn(() => previewWindow);
    window.URL.createObjectURL = jest.fn(() => 'blob:gmail-inquiry');
    window.URL.revokeObjectURL = jest.fn();

    try {
      render(<GmailInquiryReview importId="31" />);
      const attachmentButton = await screen.findByRole('button', {
        name: /open source request\.pdf \| page 2/i,
      });
      fireEvent.click(attachmentButton);

      await waitFor(() => expect(quotationAPI.gmailInquiryImports.attachment).toHaveBeenCalledWith(
        31,
        'attachment:opaque-1'
      ));
      expect(quotationAPI.gmailInquiryImports.attachment.mock.calls[0]).not.toContain(
        'raw-gmail-attachment-id'
      );
      expect(previewWindow.location.href).toBe('blob:gmail-inquiry');
    } finally {
      window.open = originalOpen;
      window.URL.createObjectURL = originalCreateObjectURL;
      window.URL.revokeObjectURL = originalRevokeObjectURL;
    }
  });

  test('keeps a confirmed import read-only and directs later changes to manual revision', async () => {
    const onOpenQuote = jest.fn();
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({
      data: {
        ...reviewedRecord,
        status: 'confirmed',
        quotation: 99,
        quotation_id: 99,
      },
    });

    render(<GmailInquiryReview importId="31" onOpenQuote={onOpenQuote} />);

    expect(await screen.findByText(/use the quotation's manual revision action/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Requested item row 1')).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: /open linked quotation/i }));
    expect(onOpenQuote).toHaveBeenCalledWith(99);
    expect(screen.queryByRole('button', { name: /confirm & open quotation/i })).not.toBeInTheDocument();
  });

  test('uses one server-verified action to approve a suggested company without selecting a purchaser', async () => {
    const unapprovedRecord = {
      ...baseRecord,
      company: null,
      contact: null,
      workflow_features: { gmail_review_ui_v2: true },
      identity_review_approved: false,
      identity_review_fingerprint: 'identity-fingerprint-v2',
      identity_review: { suggestion_approvable: true },
    };
    const approvedRecord = {
      ...unapprovedRecord,
      company: 7,
      identity_review_approved: true,
    };
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: unapprovedRecord });
    quotationAPI.gmailInquiryImports.approveCompany.mockResolvedValueOnce({ data: approvedRecord });

    render(<GmailInquiryReview importId="31" />);

    const approveSuggestion = await screen.findByRole('button', {
      name: /approve suggested company/i,
    });
    expect(screen.queryByLabelText(/confirm that this inquiry belongs/i)).not.toBeInTheDocument();
    fireEvent.click(approveSuggestion);

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.approveCompany).toHaveBeenCalledWith(31, {
      company: '7',
      contact: null,
      suggested: true,
      identity_review_fingerprint: 'identity-fingerprint-v2',
    }));
    expect(await screen.findByText(/company approved for this evidence/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Contact / Purchaser')).toHaveValue('');
  });

  test('can approve an already-selected safe suggestion', async () => {
    const selectedSuggestion = {
      ...baseRecord,
      company: 7,
      contact: null,
      workflow_features: { gmail_review_ui_v2: true },
      identity_review_approved: false,
      identity_review_fingerprint: 'identity-fingerprint-selected',
      identity_review: { suggestion_approvable: true },
    };
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: selectedSuggestion });
    quotationAPI.gmailInquiryImports.approveCompany.mockResolvedValueOnce({
      data: { ...selectedSuggestion, identity_review_approved: true },
    });

    render(<GmailInquiryReview importId="31" />);

    fireEvent.click(await screen.findByRole('button', {
      name: /approve suggested company/i,
    }));
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.approveCompany).toHaveBeenCalledWith(31, {
      company: '7',
      contact: null,
      suggested: true,
      identity_review_fingerprint: 'identity-fingerprint-selected',
    }));
    expect(await screen.findByText(/company approved for this evidence/i)).toBeInTheDocument();
  });

  test('keeps the approval action available after a transient suggested-approval failure', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    const unapprovedRecord = {
      ...baseRecord,
      company: null,
      contact: null,
      workflow_features: { gmail_review_ui_v2: true },
      identity_review_approved: false,
      identity_review_fingerprint: 'identity-fingerprint-retry',
      identity_review: { suggestion_approvable: true },
    };
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: unapprovedRecord });
    quotationAPI.gmailInquiryImports.approveCompany
      .mockRejectedValueOnce(new Error('temporary network failure'))
      .mockResolvedValueOnce({
        data: {
          ...unapprovedRecord,
          company: 7,
          identity_review_approved: true,
        },
      });

    render(<GmailInquiryReview importId="31" />);

    fireEvent.click(await screen.findByRole('button', {
      name: /approve suggested company/i,
    }));
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.approveCompany).toHaveBeenCalledTimes(1));
    const retryButton = await screen.findByRole('button', {
      name: /approve suggested company/i,
    });
    await waitFor(() => expect(retryButton).toBeEnabled());
    fireEvent.click(retryButton);

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.approveCompany).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(/company approved for this evidence/i)).toBeInTheDocument();
    consoleError.mockRestore();
  });

  test('marks one uncertain row reviewed and saves only that dirty row while preserving company approval', async () => {
    const approvedRecord = {
      ...baseRecord,
      contact: null,
      workflow_features: { gmail_review_ui_v2: true },
      identity_review_approved: true,
      identity_review_fingerprint: 'identity-fingerprint-v2',
    };
    const savedRecord = {
      ...approvedRecord,
      analysis: {
        ...approvedRecord.analysis,
        preview: {
          ...approvedRecord.analysis.preview,
          lines: [{
            ...approvedRecord.analysis.preview.lines[0],
            reviewed_by_user: true,
          }],
        },
      },
    };
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: approvedRecord });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({ data: savedRecord });

    render(<GmailInquiryReview importId="31" />);

    expect(await screen.findByText(/company approved for this evidence/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /mark reviewed/i }));
    expect(screen.getByText('staff reviewed')).toBeInTheDocument();
    expect(screen.getByText(/company approved for this evidence/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /save reviewed rows/i }));

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.update).toHaveBeenCalledWith(31, {
      review_lines: [{
        row_key: 'row-key-000000000001',
        raw_name: 'Bandage',
        quantity: '10.000',
        unit: 'Pcs',
        included: true,
        reviewed: true,
      }],
    }));
    expect(screen.getByText(/company approved for this evidence/i)).toBeInTheDocument();

    const confirmButton = screen.getByRole('button', { name: /confirm & open quotation/i });
    await waitFor(() => expect(confirmButton).toBeEnabled());
    fireEvent.click(confirmButton);
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.confirm).toHaveBeenCalledWith(31, {
      company: '7',
      contact: null,
      identity_review_fingerprint: 'identity-fingerprint-v2',
      selected_source_keys: ['attachment:opaque-1'],
    }));
  });

  test('treats a substantive row correction as explicit review in the v2 payload', async () => {
    const approvedRecord = {
      ...baseRecord,
      contact: null,
      workflow_features: { gmail_review_ui_v2: true },
      identity_review_approved: true,
      identity_review_fingerprint: 'identity-fingerprint-v2',
    };
    const correctedRecord = {
      ...approvedRecord,
      analysis: {
        ...approvedRecord.analysis,
        preview: {
          ...approvedRecord.analysis.preview,
          lines: [{
            ...approvedRecord.analysis.preview.lines[0],
            raw_name: 'Sterile Bandage',
            reviewed_by_user: true,
          }],
        },
      },
    };
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: approvedRecord });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({ data: correctedRecord });

    render(<GmailInquiryReview importId="31" />);

    fireEvent.change(await screen.findByLabelText('Requested item row 1'), {
      target: { value: 'Sterile Bandage' },
    });
    expect(screen.getByText('staff reviewed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /save reviewed rows/i }));

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.update).toHaveBeenCalledWith(31, {
      review_lines: [{
        row_key: 'row-key-000000000001',
        raw_name: 'Sterile Bandage',
        quantity: '10.000',
        unit: 'Pcs',
        included: true,
        reviewed: true,
      }],
    }));
    expect(screen.getByText(/company approved for this evidence/i)).toBeInTheDocument();
  });

  test('requires fresh server approval after a company or purchaser selection changes', async () => {
    const approvedRecord = {
      ...reviewedRecord,
      contact: null,
      workflow_features: { gmail_review_ui_v2: true },
      identity_review_approved: true,
      identity_review_fingerprint: 'identity-fingerprint-v2',
    };
    const changedRecord = {
      ...approvedRecord,
      contact: 8,
      identity_review_approved: false,
      identity_review_fingerprint: 'identity-fingerprint-v2-contact',
    };
    const reapprovedRecord = {
      ...changedRecord,
      identity_review_approved: true,
    };
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: approvedRecord });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({ data: changedRecord });
    quotationAPI.gmailInquiryImports.approveCompany.mockResolvedValueOnce({ data: reapprovedRecord });

    render(<GmailInquiryReview importId="31" />);

    expect(await screen.findByText(/company approved for this evidence/i)).toBeInTheDocument();
    const contactSelect = screen.getByLabelText('Contact / Purchaser');
    await waitFor(() => expect(contactSelect).toBeEnabled());
    fireEvent.change(contactSelect, { target: { value: '8' } });
    expect(screen.getByText(/company approval required/i)).toBeInTheDocument();
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.update).toHaveBeenCalledWith(31, {
      company: '7',
      contact: '8',
    }));

    fireEvent.click(await screen.findByRole('button', { name: /approve selected company/i }));
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.approveCompany).toHaveBeenCalledWith(31, {
      company: '7',
      contact: '8',
      suggested: false,
      identity_review_fingerprint: 'identity-fingerprint-v2-contact',
    }));
    expect(await screen.findByText(/company approved for this evidence/i)).toBeInTheDocument();
  });

  test('reconciles the server identity after an ambiguous selection response', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    const approvedRecord = {
      ...reviewedRecord,
      contact: null,
      workflow_features: { gmail_review_ui_v2: true },
      identity_review_approved: true,
      identity_review_fingerprint: 'identity-fingerprint-before-patch',
    };
    const recoveredRecord = {
      ...approvedRecord,
      contact: 8,
      identity_review_approved: false,
      identity_review_fingerprint: 'identity-fingerprint-after-patch',
    };
    quotationAPI.gmailInquiryImports.retrieve
      .mockResolvedValueOnce({ data: approvedRecord })
      .mockResolvedValueOnce({ data: recoveredRecord });
    quotationAPI.gmailInquiryImports.update.mockRejectedValueOnce(
      new Error('response was not received')
    );
    quotationAPI.gmailInquiryImports.approveCompany.mockResolvedValueOnce({
      data: { ...recoveredRecord, identity_review_approved: true },
    });

    render(<GmailInquiryReview importId="31" />);

    const contactSelect = await screen.findByLabelText('Contact / Purchaser');
    await waitFor(() => expect(contactSelect).toBeEnabled());
    fireEvent.change(contactSelect, { target: { value: '8' } });
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.retrieve).toHaveBeenCalledTimes(2));
    const approveButton = await screen.findByRole('button', {
      name: /approve selected company/i,
    });
    await waitFor(() => expect(approveButton).toBeEnabled());
    fireEvent.click(approveButton);

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.approveCompany).toHaveBeenCalledWith(31, {
      company: '7',
      contact: '8',
      suggested: false,
      identity_review_fingerprint: 'identity-fingerprint-after-patch',
    }));
    consoleError.mockRestore();
  });

  test('requires manual company approval when the server marks a suggestion as conflicting', async () => {
    const conflictingRecord = {
      ...baseRecord,
      company: null,
      contact: null,
      workflow_features: { gmail_review_ui_v2: true },
      identity_review_approved: false,
      identity_review_fingerprint: 'identity-fingerprint-conflict',
      identity_review: { suggestion_approvable: false },
    };
    const selectedRecord = {
      ...conflictingRecord,
      company: 7,
    };
    const approvedRecord = {
      ...selectedRecord,
      identity_review_approved: true,
    };
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: conflictingRecord });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({ data: selectedRecord });
    quotationAPI.gmailInquiryImports.approveCompany.mockResolvedValueOnce({ data: approvedRecord });

    render(<GmailInquiryReview importId="31" />);

    expect(await screen.findByRole('button', {
      name: /select company for manual approval/i,
    })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /approve suggested company/i })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /select company for manual approval/i }));
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.update).toHaveBeenCalledWith(31, {
      company: '7',
      contact: null,
    }));

    fireEvent.click(await screen.findByRole('button', { name: /approve selected company/i }));
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.approveCompany).toHaveBeenCalledWith(31, {
      company: '7',
      contact: null,
      suggested: false,
      identity_review_fingerprint: 'identity-fingerprint-conflict',
    }));
  });

  test('saves dirty review rows then creates from the authoritative returned fingerprints', async () => {
    const onOpenQuote = jest.fn();
    const initialRecord = chainedRecord(baseRecord);
    const savedRecord = chainedRecord(initialRecord, {
      source_fingerprint: CHAIN_SOURCE_AFTER,
      analysis_attempts: 3,
      identity_review_fingerprint: CHAIN_IDENTITY_AFTER,
      review_rows_fingerprint: CHAIN_ROWS_AFTER,
      analysis: {
        ...initialRecord.analysis,
        preview: {
          ...initialRecord.analysis.preview,
          lines: [{
            ...initialRecord.analysis.preview.lines[0],
            raw_name: 'Sterile Bandage',
            reviewed_by_user: true,
          }],
        },
      },
    });
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: initialRecord });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({ data: savedRecord });
    quotationAPI.gmailInquiryImports.confirm.mockResolvedValueOnce({
      data: {
        ...savedRecord,
        status: 'confirmed',
        quotation_id: 99,
      },
    });

    render(<GmailInquiryReview importId="31" onOpenQuote={onOpenQuote} />);

    fireEvent.change(await screen.findByLabelText('Requested item row 1'), {
      target: { value: 'Sterile Bandage' },
    });
    const chainedButton = screen.getByRole('button', {
      name: 'Save Review & Create Quotation',
    });
    expect(chainedButton).toBeEnabled();
    fireEvent.click(chainedButton);

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.update).toHaveBeenCalledWith(31, {
      review_lines: [{
        row_key: 'row-key-000000000001',
        raw_name: 'Sterile Bandage',
        quantity: '10.000',
        unit: 'Pcs',
        included: true,
        reviewed: true,
      }],
      expected_source_fingerprint: CHAIN_SOURCE_BEFORE,
      expected_analysis_attempt: 2,
      identity_review_fingerprint: CHAIN_IDENTITY_BEFORE,
      expected_review_rows_fingerprint: CHAIN_ROWS_BEFORE,
    }));
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.confirm).toHaveBeenCalledWith(31, {
      company: '7',
      contact: null,
      expected_source_fingerprint: CHAIN_SOURCE_AFTER,
      expected_analysis_attempt: 3,
      identity_review_fingerprint: CHAIN_IDENTITY_AFTER,
      expected_review_rows_fingerprint: CHAIN_ROWS_AFTER,
      selected_source_keys: ['attachment:opaque-1'],
    }));
    expect(onOpenQuote).toHaveBeenCalledWith(99);
  });

  test('skips the review PATCH when the chained review is already clean', async () => {
    const onOpenQuote = jest.fn();
    const cleanRecord = chainedRecord(reviewedRecord);
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: cleanRecord });
    quotationAPI.gmailInquiryImports.confirm.mockResolvedValueOnce({
      data: { ...cleanRecord, status: 'confirmed', quotation_id: 99 },
    });

    render(<GmailInquiryReview importId="31" onOpenQuote={onOpenQuote} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Create Quotation' }));

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.confirm).toHaveBeenCalledWith(31, {
      company: '7',
      contact: null,
      expected_source_fingerprint: CHAIN_SOURCE_BEFORE,
      expected_analysis_attempt: 2,
      identity_review_fingerprint: CHAIN_IDENTITY_BEFORE,
      expected_review_rows_fingerprint: CHAIN_ROWS_BEFORE,
      selected_source_keys: ['attachment:opaque-1'],
    }));
    expect(quotationAPI.gmailInquiryImports.update).not.toHaveBeenCalled();
    expect(onOpenQuote).toHaveBeenCalledWith(99);
  });

  test('fails closed before saving when the current review-row fingerprint is missing', async () => {
    const initialRecord = chainedRecord(baseRecord, {
      review_rows_fingerprint: '',
    });
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: initialRecord });

    render(<GmailInquiryReview importId="31" />);

    fireEvent.change(await screen.findByLabelText('Requested item row 1'), {
      target: { value: 'Sterile Bandage' },
    });
    const saveAndCreateButton = screen.getByRole('button', {
      name: 'Save Review & Create Quotation',
    });

    expect(saveAndCreateButton).toBeDisabled();
    expect(screen.getByText(/refresh this Gmail review to restore its current safety binding/i)).toBeInTheDocument();
    expect(quotationAPI.gmailInquiryImports.update).not.toHaveBeenCalled();
    expect(quotationAPI.gmailInquiryImports.confirm).not.toHaveBeenCalled();
  });

  test('does not confirm when an authoritative save response omits its new review-row fingerprint', async () => {
    const initialRecord = chainedRecord(baseRecord);
    const incompleteSavedRecord = chainedRecord(reviewedRecord, {
      source_fingerprint: CHAIN_SOURCE_AFTER,
      analysis_attempts: 3,
      identity_review_fingerprint: CHAIN_IDENTITY_AFTER,
      review_rows_fingerprint: '',
    });
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: initialRecord });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({ data: incompleteSavedRecord });

    render(<GmailInquiryReview importId="31" />);

    fireEvent.change(await screen.findByLabelText('Requested item row 1'), {
      target: { value: 'Sterile Bandage' },
    });
    fireEvent.click(screen.getByRole('button', {
      name: 'Save Review & Create Quotation',
    }));

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.update).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/saved review is no longer current/i)).toBeInTheDocument();
    expect(quotationAPI.gmailInquiryImports.confirm).not.toHaveBeenCalled();
  });

  test('does not confirm a clean review when its review-row fingerprint is missing', async () => {
    const cleanRecord = chainedRecord(reviewedRecord, {
      review_rows_fingerprint: null,
    });
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: cleanRecord });

    render(<GmailInquiryReview importId="31" />);

    const createButton = await screen.findByRole('button', { name: 'Create Quotation' });

    expect(createButton).toBeDisabled();
    expect(screen.getByText(/refresh this Gmail review to restore its current safety binding/i)).toBeInTheDocument();
    expect(quotationAPI.gmailInquiryImports.update).not.toHaveBeenCalled();
    expect(quotationAPI.gmailInquiryImports.confirm).not.toHaveBeenCalled();
  });

  test('does not confirm or navigate when the chained review save is stale', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    const onOpenQuote = jest.fn();
    const initialRecord = chainedRecord(baseRecord);
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: initialRecord });
    quotationAPI.gmailInquiryImports.update.mockRejectedValueOnce(
      Object.assign(new Error('The Gmail review changed.'), {
        response: { status: 409 },
      })
    );

    render(<GmailInquiryReview importId="31" onOpenQuote={onOpenQuote} />);

    fireEvent.change(await screen.findByLabelText('Requested item row 1'), {
      target: { value: 'Sterile Bandage' },
    });
    fireEvent.click(screen.getByRole('button', {
      name: 'Save Review & Create Quotation',
    }));

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.update).toHaveBeenCalledTimes(1));
    expect(quotationAPI.gmailInquiryImports.update).toHaveBeenCalledWith(31, expect.objectContaining({
      expected_review_rows_fingerprint: CHAIN_ROWS_BEFORE,
    }));
    await waitFor(() => expect(describeQuotationError).toHaveBeenCalledWith(
      expect.objectContaining({ response: { status: 409 } }),
      'Save Gmail review and create quotation',
      'PATCH /quotations/gmail-inquiry-imports/31/'
    ));
    expect(quotationAPI.gmailInquiryImports.confirm).not.toHaveBeenCalled();
    expect(onOpenQuote).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });

  test('does not navigate when confirmation rejects a stale review-row fingerprint', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    const onOpenQuote = jest.fn();
    const cleanRecord = chainedRecord(reviewedRecord);
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: cleanRecord });
    quotationAPI.gmailInquiryImports.confirm.mockRejectedValueOnce(
      Object.assign(new Error('The reviewed rows changed.'), {
        response: {
          status: 409,
          data: { code: 'stale_review_rows' },
        },
      })
    );

    render(<GmailInquiryReview importId="31" onOpenQuote={onOpenQuote} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Create Quotation' }));
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.confirm).toHaveBeenCalledWith(
      31,
      expect.objectContaining({
        expected_review_rows_fingerprint: CHAIN_ROWS_BEFORE,
      })
    ));
    await waitFor(() => expect(describeQuotationError).toHaveBeenCalledWith(
      expect.objectContaining({ response: expect.objectContaining({ status: 409 }) }),
      'Create quotation from Gmail review',
      'POST /quotations/gmail-inquiry-imports/31/confirm/'
    ));
    expect(quotationAPI.gmailInquiryImports.update).not.toHaveBeenCalled();
    expect(onOpenQuote).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });

  test('a synchronous double click starts only one chained save and one confirmation', async () => {
    const initialRecord = chainedRecord(baseRecord);
    const savedRecord = chainedRecord(reviewedRecord);
    let resolveSave;
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: initialRecord });
    quotationAPI.gmailInquiryImports.update.mockReturnValueOnce(new Promise((resolve) => {
      resolveSave = resolve;
    }));
    quotationAPI.gmailInquiryImports.confirm.mockResolvedValueOnce({
      data: { ...savedRecord, status: 'confirmed', quotation_id: 99 },
    });

    render(<GmailInquiryReview importId="31" />);

    fireEvent.change(await screen.findByLabelText('Requested item row 1'), {
      target: { value: 'Sterile Bandage' },
    });
    const button = screen.getByRole('button', { name: 'Save Review & Create Quotation' });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(quotationAPI.gmailInquiryImports.update).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveSave({ data: savedRecord });
    });
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.confirm).toHaveBeenCalledTimes(1));
  });

  test('does not continue a chained action after the review unmounts', async () => {
    const initialRecord = chainedRecord(baseRecord);
    let resolveSave;
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: initialRecord });
    quotationAPI.gmailInquiryImports.update.mockReturnValueOnce(new Promise((resolve) => {
      resolveSave = resolve;
    }));

    const { unmount } = render(<GmailInquiryReview importId="31" />);

    fireEvent.change(await screen.findByLabelText('Requested item row 1'), {
      target: { value: 'Sterile Bandage' },
    });
    fireEvent.click(screen.getByRole('button', {
      name: 'Save Review & Create Quotation',
    }));
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.update).toHaveBeenCalledTimes(1));
    unmount();
    await act(async () => {
      resolveSave({ data: chainedRecord(reviewedRecord) });
    });

    expect(quotationAPI.gmailInquiryImports.confirm).not.toHaveBeenCalled();
  });

  test('polls a first strict analysis immediately and binds the first new attempt and generation atomically', async () => {
    jest.useFakeTimers();
    const analysisRequest = deferred();
    const claimedRecord = withAnalysisProgress({
      ...baseRecord,
      status: 'claimed',
      mode: 'ai_thread',
      selected_message_ids: [],
      analysis: { preview: { warnings: [], meta: {}, lines: [] } },
    }, analysisProgress('idle', {
      stage: '',
      attempt: 0,
      source_generation: '',
    }));
    const completedRecord = withAnalysisProgress(reviewedRecord, analysisProgress('completed', {
      attempt: 1,
      source_generation: 'first-live-generation',
    }));
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: claimedRecord });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({ data: claimedRecord });
    quotationAPI.gmailInquiryImports.analyze.mockReturnValueOnce(analysisRequest.promise);
    quotationAPI.gmailInquiryImports.analysisProgress
      .mockResolvedValueOnce({
        data: analysisProgress('completed', {
          attempt: 0,
          source_generation: 'old-generation',
        }),
      })
      .mockResolvedValueOnce({
        data: analysisProgress('running', {
          stage: 'fetching_messages',
          attempt: 1,
          source_generation: 'first-live-generation',
        }),
      });

    try {
      render(<GmailInquiryReview importId="31" />);
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(quotationAPI.gmailInquiryImports.analyze).toHaveBeenCalledTimes(1);
      expect(quotationAPI.gmailInquiryImports.analysisProgress).toHaveBeenCalledWith('31');
      expect(screen.getByText('Waiting for live analysis status')).toBeInTheDocument();
      expect(screen.queryByText('Analysis ready')).not.toBeInTheDocument();
      expect(screen.queryByText(/usually takes 15.*30 seconds/i)).not.toBeInTheDocument();

      await act(async () => {
        jest.advanceTimersByTime(700);
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(screen.getByText('Reading selected messages')).toBeInTheDocument();

      await act(async () => analysisRequest.resolve({ data: completedRecord }));
      expect(await screen.findByDisplayValue('Sterile Bandage')).toBeInTheDocument();
      expect(screen.queryByText('Old generation result')).not.toBeInTheDocument();
    } finally {
      jest.useRealTimers();
    }
  });

  test('ignores the preceding generation during strict reanalysis and binds only the next attempt', async () => {
    jest.useFakeTimers();
    const analysisRequest = deferred();
    const failedRecord = withAnalysisProgress(baseRecord, analysisProgress('failed', {
      attempt: 2,
      source_generation: 'previous-generation',
      safe_error_category: 'ai_analysis_failed',
      retryable: true,
    }));
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: failedRecord });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({ data: failedRecord });
    quotationAPI.gmailInquiryImports.analyze.mockReturnValueOnce(analysisRequest.promise);
    quotationAPI.gmailInquiryImports.analysisProgress
      .mockResolvedValueOnce({
        data: analysisProgress('completed', {
          attempt: 2,
          source_generation: 'previous-generation',
        }),
      })
      .mockResolvedValueOnce({
        data: analysisProgress('running', {
          stage: 'analyzing_with_ai',
          attempt: 3,
          source_generation: 'next-generation',
        }),
      });

    try {
      render(<GmailInquiryReview importId="31" />);
      fireEvent.click(await screen.findByRole('button', { name: 'Retry analysis' }));
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(quotationAPI.gmailInquiryImports.analysisProgress).toHaveBeenCalledTimes(1);
      expect(screen.queryByText('Analysis ready')).not.toBeInTheDocument();

      await act(async () => {
        jest.advanceTimersByTime(700);
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(screen.getByText('Analyzing request')).toBeInTheDocument();

      await act(async () => analysisRequest.resolve({
        data: withAnalysisProgress(reviewedRecord, analysisProgress('completed', {
          attempt: 3,
          source_generation: 'next-generation',
        })),
      }));
      expect(await screen.findByDisplayValue('Sterile Bandage')).toBeInTheDocument();
    } finally {
      jest.useRealTimers();
    }
  });

  test('ignores an older analyze response after import replacement without stopping the newer poll', async () => {
    const oldAnalysisRequest = deferred();
    const oldProgressRequest = deferred();
    const newProgressRequest = deferred();
    const oldClaimedRecord = withAnalysisProgress({
      ...baseRecord,
      id: 31,
      status: 'claimed',
      mode: 'ai_thread',
      selected_message_ids: [],
      analysis: { preview: { warnings: [], meta: {}, lines: [] } },
    }, analysisProgress('idle', {
      stage: '',
      attempt: 0,
      source_generation: '',
    }));
    const newRunningRecord = withAnalysisProgress({
      ...baseRecord,
      id: 32,
      status: 'analyzing',
      analysis: { preview: { warnings: [], meta: {}, lines: [] } },
    }, analysisProgress('running', {
      stage: 'fetching_messages',
      attempt: 4,
      source_generation: 'new-import-generation',
    }));
    const oldCompletedRecord = withAnalysisProgress({
      ...reviewedRecord,
      id: 31,
      analysis: {
        ...reviewedRecord.analysis,
        preview: {
          ...reviewedRecord.analysis.preview,
          lines: [{
            ...reviewedRecord.analysis.preview.lines[0],
            raw_name: 'OLD IMPORT RESULT',
          }],
        },
      },
    }, analysisProgress('completed', {
      attempt: 1,
      source_generation: 'old-import-generation',
    }));
    quotationAPI.gmailInquiryImports.retrieve
      .mockResolvedValueOnce({ data: oldClaimedRecord })
      .mockResolvedValueOnce({ data: newRunningRecord });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({ data: oldClaimedRecord });
    quotationAPI.gmailInquiryImports.analyze.mockReturnValueOnce(oldAnalysisRequest.promise);
    quotationAPI.gmailInquiryImports.analysisProgress
      .mockReturnValueOnce(oldProgressRequest.promise)
      .mockReturnValueOnce(newProgressRequest.promise);

    const { rerender } = render(<GmailInquiryReview importId="31" />);
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.analyze).toHaveBeenCalledWith(31, {
      force: false,
    }));
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.analysisProgress).toHaveBeenCalledWith('31'));

    rerender(<GmailInquiryReview importId="32" />);
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.retrieve).toHaveBeenCalledWith('32'));
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.analysisProgress).toHaveBeenCalledWith('32'));

    await act(async () => {
      oldAnalysisRequest.resolve({ data: oldCompletedRecord });
      oldProgressRequest.resolve({ data: oldCompletedRecord.analysis_progress });
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.queryByDisplayValue('OLD IMPORT RESULT')).not.toBeInTheDocument();

    await act(async () => {
      newProgressRequest.resolve({
        data: analysisProgress('running', {
          stage: 'fetching_attachments',
          attempt: 4,
          source_generation: 'new-import-generation',
        }),
      });
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByText('Retrieving attachments')).toBeInTheDocument();
    expect(screen.queryByDisplayValue('OLD IMPORT RESULT')).not.toBeInTheDocument();
  });

  test('resumes strict progress after reload and retrieves the full record once on completion', async () => {
    const runningRecord = withAnalysisProgress({
      ...baseRecord,
      status: 'analyzing',
      analysis: { preview: { warnings: [], meta: {}, lines: [] } },
    }, analysisProgress('running', {
      stage: 'validating_evidence',
      attempt: 4,
      source_generation: 'reload-generation',
    }));
    const completedRecord = withAnalysisProgress(reviewedRecord, analysisProgress('completed', {
      attempt: 4,
      source_generation: 'reload-generation',
    }));
    quotationAPI.gmailInquiryImports.retrieve
      .mockResolvedValueOnce({ data: runningRecord })
      .mockResolvedValueOnce({ data: completedRecord });
    quotationAPI.gmailInquiryImports.analysisProgress.mockResolvedValueOnce({
      data: completedRecord.analysis_progress,
    });

    render(<GmailInquiryReview importId="31" />);

    expect(await screen.findByDisplayValue('Sterile Bandage')).toBeInTheDocument();
    expect(quotationAPI.gmailInquiryImports.analysisProgress).toHaveBeenCalledWith('31');
    expect(quotationAPI.gmailInquiryImports.retrieve).toHaveBeenCalledTimes(2);
    expect(quotationAPI.gmailInquiryImports.analyze).not.toHaveBeenCalled();
  });

  test('keeps polling when a completed strict status is followed by a mismatched full record', async () => {
    jest.useFakeTimers();
    const runningRecord = withAnalysisProgress({
      ...baseRecord,
      status: 'analyzing',
      analysis: { preview: { warnings: [], meta: {}, lines: [] } },
    }, analysisProgress('running', {
      attempt: 5,
      source_generation: 'bound-generation',
    }));
    const mismatchedRecord = withAnalysisProgress(reviewedRecord, analysisProgress('completed', {
      attempt: 5,
      source_generation: 'other-generation',
    }));
    const matchingRecord = withAnalysisProgress(reviewedRecord, analysisProgress('completed', {
      attempt: 5,
      source_generation: 'bound-generation',
    }));
    quotationAPI.gmailInquiryImports.retrieve
      .mockResolvedValueOnce({ data: runningRecord })
      .mockResolvedValueOnce({ data: mismatchedRecord })
      .mockResolvedValueOnce({ data: matchingRecord });
    quotationAPI.gmailInquiryImports.analysisProgress.mockResolvedValue({
      data: matchingRecord.analysis_progress,
    });

    try {
      render(<GmailInquiryReview importId="31" />);
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(screen.queryByDisplayValue('Sterile Bandage')).not.toBeInTheDocument();
      expect(quotationAPI.gmailInquiryImports.retrieve).toHaveBeenCalledTimes(2);

      await act(async () => {
        jest.advanceTimersByTime(700);
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(screen.getByDisplayValue('Sterile Bandage')).toBeInTheDocument();
      expect(quotationAPI.gmailInquiryImports.retrieve).toHaveBeenCalledTimes(3);
    } finally {
      jest.useRealTimers();
    }
  });

  test('maps strict failure categories without exposing backend content and preserves existing rows', async () => {
    const runningRecord = withAnalysisProgress({
      ...reviewedRecord,
      status: 'analyzing',
    }, analysisProgress('running', {
      attempt: 6,
      source_generation: 'failure-generation',
    }));
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: runningRecord });
    quotationAPI.gmailInquiryImports.analysisProgress.mockResolvedValueOnce({
      data: analysisProgress('failed', {
        attempt: 6,
        source_generation: 'failure-generation',
        safe_error_category: 'gmail_fetch_failed',
        retryable: true,
        detail: 'buyer@example.com secret subject raw backend trace',
      }),
    });

    render(<GmailInquiryReview importId="31" />);

    expect(await screen.findByText(/selected Gmail messages could not be retrieved/i)).toBeInTheDocument();
    expect(screen.getByDisplayValue('Sterile Bandage')).toBeDisabled();
    expect(screen.queryByText(/buyer@example.com secret subject raw backend trace/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry analysis' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirm & Open Quotation' })).toBeDisabled();
  });

  test('retries transient strict progress polls without clearing current review content', async () => {
    jest.useFakeTimers();
    const runningRecord = withAnalysisProgress({
      ...reviewedRecord,
      status: 'analyzing',
    }, analysisProgress('running', {
      attempt: 7,
      source_generation: 'transient-generation',
    }));
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: runningRecord });
    quotationAPI.gmailInquiryImports.analysisProgress
      .mockRejectedValueOnce(new Error('buyer@example.com network detail'))
      .mockRejectedValueOnce(Object.assign(new Error('Rate limited'), {
        response: { status: 429 },
      }))
      .mockRejectedValueOnce(Object.assign(new Error('Temporary server error'), {
        response: { status: 503 },
      }))
      .mockResolvedValueOnce({
        data: analysisProgress('running', {
          stage: 'inspecting_documents',
          attempt: 7,
          source_generation: 'transient-generation',
        }),
      });

    try {
      render(<GmailInquiryReview importId="31" />);
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(quotationAPI.gmailInquiryImports.analysisProgress).toHaveBeenCalledTimes(1);
      expect(screen.getByDisplayValue('Sterile Bandage')).toBeInTheDocument();
      expect(screen.queryByText(/buyer@example.com network detail/i)).not.toBeInTheDocument();

      await act(async () => {
        jest.advanceTimersByTime(700);
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(quotationAPI.gmailInquiryImports.analysisProgress).toHaveBeenCalledTimes(2);
      expect(screen.getByDisplayValue('Sterile Bandage')).toBeInTheDocument();

      await act(async () => {
        jest.advanceTimersByTime(700);
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(quotationAPI.gmailInquiryImports.analysisProgress).toHaveBeenCalledTimes(3);
      expect(screen.getByDisplayValue('Sterile Bandage')).toBeInTheDocument();

      await act(async () => {
        jest.advanceTimersByTime(700);
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(quotationAPI.gmailInquiryImports.analysisProgress).toHaveBeenCalledTimes(4);
      expect(screen.getByText('Inspecting documents')).toBeInTheDocument();
      expect(screen.getByDisplayValue('Sterile Bandage')).toBeInTheDocument();
    } finally {
      jest.useRealTimers();
    }
  });

  test('stops strict polling and surfaces a nonrecoverable analyze request error', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    const analysisRequest = deferred();
    const progressRequest = deferred();
    const failedRecord = withAnalysisProgress(baseRecord, analysisProgress('failed', {
      attempt: 9,
      source_generation: 'request-error-generation',
      safe_error_category: 'ai_analysis_failed',
      retryable: true,
    }));
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: failedRecord });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({ data: failedRecord });
    quotationAPI.gmailInquiryImports.analyze.mockReturnValueOnce(analysisRequest.promise);
    quotationAPI.gmailInquiryImports.analysisProgress.mockReturnValueOnce(progressRequest.promise);

    render(<GmailInquiryReview importId="31" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Retry analysis' }));
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.analysisProgress).toHaveBeenCalledTimes(1));
    await act(async () => analysisRequest.reject(
      Object.assign(new Error('The selected source is no longer valid.'), {
        response: { status: 403 },
      })
    ));

    await waitFor(() => expect(describeQuotationError).toHaveBeenCalledWith(
      expect.objectContaining({ response: { status: 403 } }),
      'Reanalyze Gmail inquiry',
      'POST /quotations/gmail-inquiry-imports/31/analyze/'
    ));
    expect(quotationAPI.gmailInquiryImports.analysisProgress).toHaveBeenCalledTimes(1);
    await act(async () => progressRequest.resolve({
      data: analysisProgress('running', {
        stage: 'saving_results',
        attempt: 10,
        source_generation: 'request-error-generation-next',
      }),
    }));
    expect(screen.queryByText('Saving review results')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry analysis' })).toBeInTheDocument();
    consoleError.mockRestore();
  });

  test('stops a nonretryable strict progress authorization failure without clearing rows', async () => {
    jest.useFakeTimers();
    const runningRecord = withAnalysisProgress({
      ...reviewedRecord,
      status: 'analyzing',
    }, analysisProgress('running', {
      attempt: 10,
      source_generation: 'authorization-generation',
    }));
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: runningRecord });
    quotationAPI.gmailInquiryImports.analysisProgress.mockRejectedValueOnce(
      Object.assign(new Error('buyer@example.com must never be rendered'), {
        response: { status: 403 },
      })
    );

    try {
      render(<GmailInquiryReview importId="31" />);
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(screen.getByText(/live analysis status could not be verified/i)).toBeInTheDocument();
      expect(screen.queryByText(/buyer@example.com must never be rendered/i)).not.toBeInTheDocument();
      expect(screen.getByDisplayValue('Sterile Bandage')).toBeDisabled();
      expect(quotationAPI.gmailInquiryImports.analysisProgress).toHaveBeenCalledTimes(1);

      await act(async () => {
        jest.advanceTimersByTime(2100);
        await Promise.resolve();
      });
      expect(quotationAPI.gmailInquiryImports.analysisProgress).toHaveBeenCalledTimes(1);
      expect(screen.getByRole('button', { name: 'Retry analysis' })).toBeInTheDocument();
    } finally {
      jest.useRealTimers();
    }
  });

  test('falls back to legacy full-record polling when the progress endpoint is disabled mid-flight', async () => {
    jest.useFakeTimers();
    const strictRunningRecord = withAnalysisProgress({
      ...baseRecord,
      status: 'analyzing',
      analysis: { preview: { warnings: [], meta: {}, lines: [] } },
    }, analysisProgress('running', {
      attempt: 11,
      source_generation: 'rollback-generation',
    }));
    const flagOffRunningRecord = {
      ...strictRunningRecord,
      workflow_features: { gmail_analysis_progress: false },
      analysis_progress: null,
    };
    quotationAPI.gmailInquiryImports.retrieve
      .mockResolvedValueOnce({ data: strictRunningRecord })
      .mockResolvedValueOnce({ data: flagOffRunningRecord })
      .mockResolvedValueOnce({ data: reviewedRecord });
    quotationAPI.gmailInquiryImports.analysisProgress.mockRejectedValueOnce(
      Object.assign(new Error('Not found'), { response: { status: 404 } })
    );

    try {
      render(<GmailInquiryReview importId="31" />);
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(quotationAPI.gmailInquiryImports.analysisProgress).toHaveBeenCalledTimes(1);
      expect(quotationAPI.gmailInquiryImports.retrieve).toHaveBeenCalledTimes(2);
      expect(screen.getByText(/standard status checks will continue safely/i)).toBeInTheDocument();
      expect(screen.getByText(/usually takes 15.*30 seconds/i)).toBeInTheDocument();

      await act(async () => {
        jest.advanceTimersByTime(1799);
        await Promise.resolve();
      });
      expect(quotationAPI.gmailInquiryImports.retrieve).toHaveBeenCalledTimes(2);

      await act(async () => {
        jest.advanceTimersByTime(1);
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(quotationAPI.gmailInquiryImports.retrieve).toHaveBeenCalledTimes(3);
      expect(screen.getByDisplayValue('Sterile Bandage')).toBeInTheDocument();
    } finally {
      jest.useRealTimers();
    }
  });

  test('accepts an old worker full analyze response when the progress flag is omitted mid-request', async () => {
    const progressRequest = deferred();
    const strictClaimedRecord = withAnalysisProgress({
      ...baseRecord,
      status: 'claimed',
      mode: 'ai_thread',
      selected_message_ids: [],
      analysis: { preview: { warnings: [], meta: {}, lines: [] } },
    }, analysisProgress('idle', {
      stage: '',
      attempt: 0,
      source_generation: '',
    }));
    const flagOffCompletedRecord = {
      ...reviewedRecord,
      workflow_features: {},
      analysis_progress: null,
    };
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: strictClaimedRecord });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({ data: strictClaimedRecord });
    const analysisRequest = deferred();
    quotationAPI.gmailInquiryImports.analyze.mockReturnValueOnce(analysisRequest.promise);
    quotationAPI.gmailInquiryImports.analysisProgress.mockReturnValueOnce(progressRequest.promise);

    render(<GmailInquiryReview importId="31" />);

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.analysisProgress).toHaveBeenCalledTimes(1));
    await act(async () => analysisRequest.resolve({ data: flagOffCompletedRecord }));
    expect(await screen.findByDisplayValue('Sterile Bandage')).toBeInTheDocument();
    expect(screen.queryByText('Waiting for live analysis status')).not.toBeInTheDocument();
    await act(async () => progressRequest.resolve({
      data: analysisProgress('running', {
        stage: 'saving_results',
        attempt: 1,
        source_generation: 'obsolete-progress-generation',
      }),
    }));
    expect(screen.queryByText('Saving review results')).not.toBeInTheDocument();
    expect(screen.getByDisplayValue('Sterile Bandage')).toBeInTheDocument();
  });

  test('rejects an unbound flag-off analyze response without an exact import ID', async () => {
    const analysisRequest = deferred();
    const progressRequest = deferred();
    const strictClaimedRecord = withAnalysisProgress({
      ...baseRecord,
      status: 'claimed',
      mode: 'ai_thread',
      selected_message_ids: [],
      analysis: { preview: { warnings: [], meta: {}, lines: [] } },
    }, analysisProgress('idle', {
      stage: '',
      attempt: 0,
      source_generation: '',
    }));
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: strictClaimedRecord });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({ data: strictClaimedRecord });
    quotationAPI.gmailInquiryImports.analyze.mockReturnValueOnce(analysisRequest.promise);
    quotationAPI.gmailInquiryImports.analysisProgress.mockReturnValueOnce(progressRequest.promise);

    render(<GmailInquiryReview importId="31" />);
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.analysisProgress).toHaveBeenCalledWith('31'));
    await act(async () => analysisRequest.resolve({
      data: {
        workflow_features: { gmail_analysis_progress: false },
        analysis: { preview: { lines: [{ raw_name: 'UNBOUND RESULT' }] } },
      },
    }));
    expect(screen.queryByDisplayValue('UNBOUND RESULT')).not.toBeInTheDocument();

    await act(async () => progressRequest.resolve({
      data: analysisProgress('running', {
        stage: 'fetching_messages',
        attempt: 1,
        source_generation: 'bound-after-untrusted-response',
      }),
    }));
    expect(screen.getByText('Reading selected messages')).toBeInTheDocument();
    expect(screen.queryByDisplayValue('UNBOUND RESULT')).not.toBeInTheDocument();
  });

  test('ignores a late strict poll after unmount', async () => {
    const progressRequest = deferred();
    const runningRecord = withAnalysisProgress({
      ...baseRecord,
      status: 'analyzing',
    }, analysisProgress('running', {
      attempt: 8,
      source_generation: 'unmount-generation',
    }));
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: runningRecord });
    quotationAPI.gmailInquiryImports.analysisProgress.mockReturnValueOnce(progressRequest.promise);

    const { unmount } = render(<GmailInquiryReview importId="31" />);
    await waitFor(() => expect(quotationAPI.gmailInquiryImports.analysisProgress).toHaveBeenCalledTimes(1));
    unmount();
    await act(async () => progressRequest.resolve({
      data: analysisProgress('completed', {
        attempt: 8,
        source_generation: 'unmount-generation',
      }),
    }));

    expect(quotationAPI.gmailInquiryImports.retrieve).toHaveBeenCalledTimes(1);
  });

  test('uses the exact legacy 1800ms full-record poll unless the progress flag is boolean true', async () => {
    jest.useFakeTimers();
    const legacyRecord = {
      ...baseRecord,
      status: 'analyzing',
      workflow_features: { gmail_analysis_progress: 'true' },
      analysis: { preview: { warnings: [], meta: {}, lines: [] } },
    };
    quotationAPI.gmailInquiryImports.retrieve
      .mockResolvedValueOnce({ data: legacyRecord })
      .mockResolvedValueOnce({ data: reviewedRecord });

    try {
      render(<GmailInquiryReview importId="31" />);
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(screen.getByText(/usually takes 15.*30 seconds/i)).toBeInTheDocument();
      expect(quotationAPI.gmailInquiryImports.analysisProgress).not.toHaveBeenCalled();

      await act(async () => {
        jest.advanceTimersByTime(1799);
        await Promise.resolve();
      });
      expect(quotationAPI.gmailInquiryImports.retrieve).toHaveBeenCalledTimes(1);

      await act(async () => {
        jest.advanceTimersByTime(1);
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(quotationAPI.gmailInquiryImports.retrieve).toHaveBeenCalledTimes(2);
      expect(screen.getByDisplayValue('Sterile Bandage')).toBeInTheDocument();
    } finally {
      jest.useRealTimers();
    }
  });

  test('applies an exact completed cache hit after selection deduplicates to another import', async () => {
    const onClaimed = jest.fn();
    const analysisRequest = deferred();
    const progressRequest = deferred();
    const generation = hexGeneration('c');
    const claimedImportA = {
      ...withAnalysisProgress({
        ...baseRecord,
        id: 31,
        status: 'claimed',
        source_fingerprint: 'source-import-a',
        analysis: { preview: { warnings: [], meta: {}, lines: [] } },
      }, analysisProgress('idle', {
        stage: '',
        attempt: 0,
        source_generation: '',
      })),
      workflow_features: {
        gmail_analysis_progress: true,
        gmail_background_analysis: true,
      },
      analysis_job: null,
    };
    const completedJob = backgroundAnalysisJob('completed', {
      id: 905,
      analysis_attempt: 4,
      source_generation: generation,
      progress_stage: 'completed',
      attempt_count: 1,
      terminal: true,
    });
    const completedImportB = withBackgroundAnalysis({
      ...reviewedRecord,
      id: 44,
      status: 'review_required',
      source_fingerprint: 'source-import-b',
      analysis_attempts: 4,
    }, completedJob, analysisProgress('completed', {
      attempt: 4,
      source_generation: generation,
    }));
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: claimedImportA });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({ data: completedImportB });
    quotationAPI.gmailInquiryImports.analyze.mockReturnValueOnce(analysisRequest.promise);
    quotationAPI.gmailInquiryImports.analysisProgress.mockReturnValueOnce(progressRequest.promise);

    render(<GmailInquiryReview importId="31" onClaimed={onClaimed} />);

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.analyze).toHaveBeenCalledWith(44, {
      force: false,
    }));
    expect(onClaimed).toHaveBeenCalledWith(44);
    expect(screen.queryByDisplayValue('Sterile Bandage')).not.toBeInTheDocument();

    await act(async () => analysisRequest.resolve({
      status: 200,
      data: completedImportB,
    }));

    expect(await screen.findByDisplayValue('Sterile Bandage')).toBeInTheDocument();
    expect(screen.getByText(/analysis is ready for review/i)).toBeInTheDocument();
    expect(screen.queryByText(/completed analysis could not be verified/i)).not.toBeInTheDocument();
  });

  test('accepts one durable 202 enqueue, blocks a synchronous double click, and refreshes only at completion', async () => {
    const progressRequest = deferred();
    const failedJob = backgroundAnalysisJob('failed', {
      id: 910,
      analysis_attempt: 2,
      source_generation: hexGeneration('2'),
      progress_stage: 'failed',
      attempt_count: 1,
      safe_error_category: 'ai_analysis_failed',
      retryable: true,
    });
    const failedRecord = withBackgroundAnalysis(
      { ...reviewedRecord, status: 'failed' },
      failedJob,
      analysisProgress('failed', {
        attempt: 2,
        source_generation: hexGeneration('2'),
        safe_error_category: 'ai_analysis_failed',
        retryable: true,
      })
    );
    const queuedJob = backgroundAnalysisJob('queued', {
      id: 911,
      analysis_attempt: 3,
      source_generation: hexGeneration('3'),
    });
    const queuedProgress = analysisProgress('running', {
      stage: 'queued',
      attempt: 3,
      source_generation: hexGeneration('3'),
    });
    const queuedRecord = withBackgroundAnalysis(
      { ...failedRecord, status: 'analyzing' },
      queuedJob,
      queuedProgress
    );
    const completedJob = backgroundAnalysisJob('completed', {
      ...queuedJob,
      progress_stage: 'completed',
      attempt_count: 1,
      completed_at: '2026-08-02T08:00:10Z',
      terminal: true,
    });
    const completedRecord = withBackgroundAnalysis(
      reviewedRecord,
      completedJob,
      analysisProgress('completed', {
        attempt: 3,
        source_generation: hexGeneration('3'),
      })
    );
    quotationAPI.gmailInquiryImports.retrieve
      .mockResolvedValueOnce({ data: failedRecord })
      .mockResolvedValueOnce({ data: completedRecord });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({ data: failedRecord });
    quotationAPI.gmailInquiryImports.analyze.mockResolvedValueOnce({
      status: 202,
      data: queuedRecord,
    });
    quotationAPI.gmailInquiryImports.analysisProgress.mockReturnValueOnce(
      progressRequest.promise
    );

    render(<GmailInquiryReview importId="31" />);
    const retryButton = await screen.findByRole('button', { name: 'Retry analysis' });
    fireEvent.click(retryButton);
    fireEvent.click(retryButton);

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.analyze).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/analysis is queued.*leave this page/i)).toBeInTheDocument();
    expect(screen.getByDisplayValue('Sterile Bandage')).toBeDisabled();
    expect(quotationAPI.gmailInquiryImports.retrieve).toHaveBeenCalledTimes(1);

    await act(async () => progressRequest.resolve({
      data: completedRecord.analysis_progress,
    }));
    expect(await screen.findByText(/analysis is ready for review/i)).toBeInTheDocument();
    expect(quotationAPI.gmailInquiryImports.retrieve).toHaveBeenCalledTimes(2);
    expect(quotationAPI.gmailInquiryImports.analyze).toHaveBeenCalledTimes(1);
  });

  test('resumes an active durable job after reload without enqueueing another job', async () => {
    const progressRequest = deferred();
    const runningJob = backgroundAnalysisJob('running', {
      id: 920,
      analysis_attempt: 4,
      source_generation: hexGeneration('4'),
      progress_stage: 'inspecting_documents',
      attempt_count: 1,
    });
    const runningRecord = withBackgroundAnalysis(
      { ...reviewedRecord, status: 'analyzing' },
      runningJob,
      analysisProgress('running', {
        stage: 'inspecting_documents',
        attempt: 4,
        source_generation: hexGeneration('4'),
      })
    );
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: runningRecord });
    quotationAPI.gmailInquiryImports.analysisProgress.mockReturnValueOnce(
      progressRequest.promise
    );

    const { unmount } = render(<GmailInquiryReview importId="31" />);

    expect(await screen.findByText('Inspecting documents')).toBeInTheDocument();
    await waitFor(() => expect(
      quotationAPI.gmailInquiryImports.analysisProgress
    ).toHaveBeenCalledWith('31'));
    expect(quotationAPI.gmailInquiryImports.analyze).not.toHaveBeenCalled();
    expect(screen.getByDisplayValue('Sterile Bandage')).toBeDisabled();
    unmount();
  });

  test.each([
    ['superseded', /A newer source or analysis replaced this attempt/i],
    ['cancelled', /This attempt was cancelled safely/i],
  ])('shows a safe %s durable terminal state without exposing arbitrary job content', async (jobState, expectedCopy) => {
    const stoppedJob = backgroundAnalysisJob(jobState, {
      id: jobState === 'superseded' ? 930 : 931,
      analysis_attempt: 5,
      source_generation: hexGeneration(jobState === 'superseded' ? '5' : '6'),
      progress_stage: 'failed',
      safe_error_category: '',
      detail: 'buyer@example.com private subject and raw worker trace',
    });
    const stoppedRecord = withBackgroundAnalysis(
      { ...reviewedRecord, status: 'failed' },
      stoppedJob,
      analysisProgress('failed', {
        attempt: 5,
        source_generation: stoppedJob.source_generation,
        safe_error_category: '',
        retryable: true,
      })
    );
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: stoppedRecord });

    render(<GmailInquiryReview importId="31" />);

    expect(await screen.findByText(expectedCopy)).toBeInTheDocument();
    expect(screen.getByText('Analysis stopped safely.')).toBeInTheDocument();
    expect(screen.queryByText(/buyer@example.com private subject/i)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry analysis' })).toBeInTheDocument();
    expect(quotationAPI.gmailInquiryImports.analyze).not.toHaveBeenCalled();
  });

  test('rejects a durable 202 response whose job is not bound to its safe progress projection', async () => {
    const progressRequest = deferred();
    const failedRecord = withBackgroundAnalysis(
      { ...reviewedRecord, status: 'failed' },
      backgroundAnalysisJob('failed', {
        analysis_attempt: 6,
        source_generation: hexGeneration('7'),
        progress_stage: 'failed',
        safe_error_category: 'ai_analysis_failed',
      }),
      analysisProgress('failed', {
        attempt: 6,
        source_generation: hexGeneration('7'),
        safe_error_category: 'ai_analysis_failed',
      })
    );
    const mismatchedRecord = withBackgroundAnalysis(
      { ...failedRecord, status: 'analyzing' },
      backgroundAnalysisJob('queued', {
        id: 941,
        analysis_attempt: 7,
        source_generation: hexGeneration('8'),
      }),
      analysisProgress('running', {
        stage: 'queued',
        attempt: 7,
        source_generation: hexGeneration('9'),
      })
    );
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: failedRecord });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({ data: failedRecord });
    quotationAPI.gmailInquiryImports.analyze.mockResolvedValueOnce({
      status: 202,
      data: mismatchedRecord,
    });
    quotationAPI.gmailInquiryImports.analysisProgress.mockReturnValueOnce(
      progressRequest.promise
    );

    render(<GmailInquiryReview importId="31" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Retry analysis' }));

    expect(await screen.findByText(/queued analysis could not be verified/i)).toBeInTheDocument();
    expect(screen.queryByText(/analysis is queued.*leave this page/i)).not.toBeInTheDocument();
    expect(quotationAPI.gmailInquiryImports.analyze).toHaveBeenCalledTimes(1);
  });

  test('moves a durable poll to a newer retry attempt instead of polling the obsolete binding forever', async () => {
    jest.useFakeTimers();
    const firstJob = backgroundAnalysisJob('running', {
      id: 950,
      analysis_attempt: 8,
      source_generation: hexGeneration('a'),
      progress_stage: 'fetching_messages',
      attempt_count: 1,
    });
    const runningRecord = withBackgroundAnalysis(
      { ...reviewedRecord, status: 'analyzing' },
      firstJob,
      analysisProgress('running', {
        stage: 'fetching_messages',
        attempt: 8,
        source_generation: hexGeneration('a'),
      })
    );
    const completedRecord = withBackgroundAnalysis(
      reviewedRecord,
      backgroundAnalysisJob('completed', {
        id: 951,
        analysis_attempt: 9,
        source_generation: hexGeneration('b'),
        progress_stage: 'completed',
        attempt_count: 1,
        terminal: true,
      }),
      analysisProgress('completed', {
        attempt: 9,
        source_generation: hexGeneration('b'),
      })
    );
    quotationAPI.gmailInquiryImports.retrieve
      .mockResolvedValueOnce({ data: runningRecord })
      .mockResolvedValueOnce({ data: completedRecord });
    quotationAPI.gmailInquiryImports.analysisProgress
      .mockResolvedValueOnce({
        data: analysisProgress('running', {
          stage: 'analyzing_with_ai',
          attempt: 9,
          source_generation: hexGeneration('b'),
        }),
      })
      .mockResolvedValueOnce({ data: completedRecord.analysis_progress });

    try {
      render(<GmailInquiryReview importId="31" />);
      expect(await screen.findByText(/newer Gmail analysis attempt is now being tracked/i)).toBeInTheDocument();
      expect(quotationAPI.gmailInquiryImports.analysisProgress).toHaveBeenCalledTimes(1);

      await act(async () => {
        jest.advanceTimersByTime(700);
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(quotationAPI.gmailInquiryImports.analysisProgress).toHaveBeenCalledTimes(2);
      expect(quotationAPI.gmailInquiryImports.retrieve).toHaveBeenCalledTimes(2);
      expect(screen.getByDisplayValue('Sterile Bandage')).toBeInTheDocument();
      expect(quotationAPI.gmailInquiryImports.analyze).not.toHaveBeenCalled();
    } finally {
      jest.useRealTimers();
    }
  });

  test('keeps the exact synchronous completed-response path when the background flag is off', async () => {
    const progressRequest = deferred();
    const failedRecord = withAnalysisProgress(
      { ...baseRecord, status: 'failed' },
      analysisProgress('failed', {
        attempt: 10,
        source_generation: 'synchronous-failed-generation',
        safe_error_category: 'ai_analysis_failed',
        retryable: true,
      })
    );
    const completedRecord = withAnalysisProgress(
      reviewedRecord,
      analysisProgress('completed', {
        attempt: 11,
        source_generation: 'synchronous-completed-generation',
      })
    );
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: failedRecord });
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({ data: failedRecord });
    quotationAPI.gmailInquiryImports.analyze.mockResolvedValueOnce({
      status: 200,
      data: completedRecord,
    });
    quotationAPI.gmailInquiryImports.analysisProgress.mockReturnValueOnce(
      progressRequest.promise
    );

    render(<GmailInquiryReview importId="31" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Retry analysis' }));

    expect(await screen.findByDisplayValue('Sterile Bandage')).toBeInTheDocument();
    expect(screen.queryByText(/leave this page and return/i)).not.toBeInTheDocument();
    expect(quotationAPI.gmailInquiryImports.analyze).toHaveBeenCalledTimes(1);
  });
});
