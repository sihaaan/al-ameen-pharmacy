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
import quotationAPI from '../../api/quotations';

jest.mock('../../api/quotations', () => ({
  __esModule: true,
  default: {
    companies: {
      list: jest.fn(),
    },
    contacts: {
      list: jest.fn(),
    },
    gmailInquiryImports: {
      claim: jest.fn(),
      retrieve: jest.fn(),
      update: jest.fn(),
      analyze: jest.fn(),
      approveCompany: jest.fn(),
      confirm: jest.fn(),
      attachment: jest.fn(),
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

describe('GmailInquiryReview', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    quotationAPI.companies.list.mockResolvedValue({
      data: [{ id: 7, name: 'Example Medical' }],
    });
    quotationAPI.contacts.list.mockResolvedValue({
      data: [{ id: 8, name: 'Celine', email: 'buyer@example.com', company: 7 }],
    });
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValue({ data: baseRecord });
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
    quotationAPI.gmailInquiryImports.attachment.mockResolvedValue({
      data: new Blob(['pdf'], { type: 'application/pdf' }),
      headers: { 'content-type': 'application/pdf' },
    });
  });

  test('adapts wrapped records and scalar quotation IDs', () => {
    expect(gmailImportRecordFromPayload({ data: { gmail_import: baseRecord } })).toBe(baseRecord);
    expect(quotationIdFromGmailImportPayload({ data: { ...baseRecord, quotation: 99 } })).toBe(99);
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
});
