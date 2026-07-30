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
        evidence: [{ source_key: 'attachment:opaque-1' }],
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
    expect(screen.getByText('Used')).toBeInTheDocument();
    expect(screen.getByText('Context')).toBeInTheDocument();
    expect(screen.getByText('Excluded')).toBeInTheDocument();
    expect(screen.getByText('Open email / Anchor')).toBeInTheDocument();
    expect(screen.getByText(/inquiry \| Contains the current item request\. \| 96% confidence/i)).toBeInTheDocument();
    expect(screen.getByText('buyer@example.com')).toBeInTheDocument();
    expect(screen.getByText('request.pdf | page 2')).toBeInTheDocument();
    expect(screen.queryByText('AED 5.50')).not.toBeInTheDocument();
    expect(screen.getByText('5.50 (currency not stated)')).toBeInTheDocument();
    expect(screen.getByText(/Confirming as sara/i)).toBeInTheDocument();

    const confirmButton = screen.getByRole('button', { name: /confirm & open quotation/i });
    expect(confirmButton).toBeDisabled();

    fireEvent.change(screen.getByLabelText('Requested item row 1'), {
      target: { value: 'Sterile Bandage' },
    });
    fireEvent.click(screen.getByRole('button', { name: /save reviewed rows/i }));

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

  test('reanalyzes the chosen messages against a deduplicated import ID', async () => {
    const onClaimed = jest.fn();
    let resolveAnalysis;
    quotationAPI.gmailInquiryImports.update.mockResolvedValueOnce({
      data: {
        ...baseRecord,
        id: 44,
        selected_message_ids: ['m-1'],
      },
    });
    quotationAPI.gmailInquiryImports.analyze.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveAnalysis = resolve;
      })
    );

    render(<GmailInquiryReview importId="31" onClaimed={onClaimed} />);
    const contextMessage = (await screen.findByText('Context reply')).closest('article');
    fireEvent.click(within(contextMessage).getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: /reanalyze selection/i }));

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.update).toHaveBeenCalledWith(31, {
      mode: 'selected_messages',
      selected_message_ids: ['m-1'],
    }));
    expect(quotationAPI.gmailInquiryImports.analyze).toHaveBeenCalledWith(44, {
      force: true,
    });
    // The replacement ID must be durable in the URL before the long analysis
    // response returns.
    expect(onClaimed).toHaveBeenCalledWith(44);

    await act(async () => {
      resolveAnalysis({
        data: {
          ...reviewedRecord,
          id: 44,
          selected_message_ids: ['m-1'],
        },
      });
    });
  });

  test('recovers an analysis timeout by checking server status and continuing to poll', async () => {
    quotationAPI.gmailInquiryImports.retrieve
      .mockResolvedValueOnce({ data: baseRecord })
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
    fireEvent.click(screen.getByRole('button', { name: /reanalyze selection/i }));

    await waitFor(() => expect(quotationAPI.gmailInquiryImports.analyze).toHaveBeenCalledWith(31, {
      force: true,
    }));
    expect(await screen.findByText(
      /browser stopped waiting, but gmail analysis is still processing/i
    )).toBeInTheDocument();
    expect(quotationAPI.gmailInquiryImports.retrieve).toHaveBeenCalledTimes(2);
    expect(screen.queryByText('Request failed')).not.toBeInTheDocument();
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

  test('blocks confirmation when an included row cites unchecked evidence', async () => {
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

    render(<GmailInquiryReview importId="31" />);

    expect(await screen.findByText(/confirmation is blocked: 1 included row/i)).toBeInTheDocument();
    expect(screen.getByText(/#1 Sterile Bandage/i)).toBeInTheDocument();
    expect(screen.getByText(/blocked: re-select this row's evidence/i)).toBeInTheDocument();
    const confirmButton = screen.getByRole('button', { name: /confirm & open quotation/i });
    fireEvent.click(screen.getByLabelText(/confirm that this inquiry belongs/i));
    expect(confirmButton).toBeDisabled();
    expect(quotationAPI.gmailInquiryImports.confirm).not.toHaveBeenCalled();

    fireEvent.click(screen.getByLabelText(/Email body/i));
    expect(screen.queryByText(/confirmation is blocked: 1 included row/i)).not.toBeInTheDocument();
    // Changing evidence invalidates the prior identity acknowledgement.
    expect(screen.getByLabelText(/confirm that this inquiry belongs/i)).not.toBeChecked();
  });

  test('requires a fresh identity acknowledgement after reanalysis', async () => {
    render(<GmailInquiryReview importId="31" />);

    await screen.findByDisplayValue('Bandage');
    const identityCheckbox = screen.getByLabelText(/confirm that this inquiry belongs/i);
    fireEvent.click(identityCheckbox);
    expect(identityCheckbox).toBeChecked();

    fireEvent.click(screen.getByRole('button', { name: /reanalyze selection/i }));
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

  test('opens an attachment using only its opaque source key', async () => {
    const unsupportedRecord = {
      ...baseRecord,
      attachment_manifest: [
        ...baseRecord.attachment_manifest,
        {
          source_key: 'attachment:opaque-unsupported',
          filename: 'scan-failed.pdf',
          mime_type: 'application/pdf',
          size: 1000000,
          status: 'failed',
          reason: 'Automatic parsing failed.',
        },
      ],
    };
    quotationAPI.gmailInquiryImports.retrieve.mockResolvedValueOnce({ data: unsupportedRecord });
    const originalOpen = window.open;
    const originalCreateObjectURL = window.URL.createObjectURL;
    const originalRevokeObjectURL = window.URL.revokeObjectURL;
    const previewWindow = { location: { href: '' }, close: jest.fn(), opener: {} };
    window.open = jest.fn(() => previewWindow);
    window.URL.createObjectURL = jest.fn(() => 'blob:gmail-inquiry');
    window.URL.revokeObjectURL = jest.fn();

    try {
      const { container } = render(<GmailInquiryReview importId="31" />);
      const unsupportedCard = (await screen.findByText('scan-failed.pdf')).closest('article');
      expect(within(unsupportedCard).getByText(/automatic parsing failed/i)).toBeInTheDocument();
      const sourceOptions = container.querySelector('.qm-gmail-source-options');
      expect(within(sourceOptions).queryByText('scan-failed.pdf')).not.toBeInTheDocument();
      fireEvent.click(within(unsupportedCard).getByRole('button', { name: /view \/ open/i }));

      await waitFor(() => expect(quotationAPI.gmailInquiryImports.attachment).toHaveBeenCalledWith(
        31,
        'attachment:opaque-unsupported'
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
});
