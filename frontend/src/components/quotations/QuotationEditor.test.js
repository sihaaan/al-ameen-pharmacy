import { act, createEvent, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import QuotationEditor from './QuotationEditor';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';

jest.mock('../../api/quotations', () => ({
  __esModule: true,
  default: {
    quotes: {
      retrieve: jest.fn(),
      update: jest.fn(),
      productPrices: jest.fn(),
      productPrice: jest.fn(),
      lpos: jest.fn(),
      bulkUpdateLines: jest.fn(),
      bulkCreateProductsForLines: jest.fn(),
      emailPreview: jest.fn(),
      emailThreadCandidates: jest.fn(),
      finalizeAndSend: jest.fn(),
      sendEmail: jest.fn(),
      reconcileEmail: jest.fn(),
      finalize: jest.fn(),
      pdf: jest.fn(),
    },
    items: { list: jest.fn() },
    companies: { list: jest.fn(), create: jest.fn() },
    contacts: { list: jest.fn(), create: jest.fn() },
    auditLogs: { list: jest.fn() },
    lines: { create: jest.fn(), createProduct: jest.fn(), rememberAlias: jest.fn() },
    lpos: { update: jest.fn() },
    gmail: { connectUrl: jest.fn() },
  },
  describeQuotationError: jest.fn(async (error, action, endpoint) => ({
    action,
    endpoint,
    status: error?.response?.status || 'Network error',
    detail: error?.message || 'Request failed',
  })),
  formatQuotationError: jest.fn(() => 'Request failed'),
}));

const products = [
  { id: 11, name: 'Gloves A', brand_name: 'Medline', unit: 'box', primary_image_url: '' },
  { id: 12, name: 'Gloves B', brand_name: 'Ansell', unit: 'box', primary_image_url: '' },
];

const quote = {
  id: 21,
  quotation_number: 'Q-0021',
  company: 7,
  company_name: 'Customer A',
  contact: null,
  contact_name: '',
  status: 'draft',
  status_display: 'Draft',
  version: 1,
  quotation_review_fingerprint: 'quotation-review-fingerprint-1',
  currency: 'AED',
  payment_terms: 'as_per_agreement',
  valid_until: '2026-08-01',
  show_brand_column: false,
  subtotal: '0.00',
  total: '0.00',
  lines: [{
    id: 31,
    sort_order: 0,
    product: null,
    item_name_snapshot: 'Imported gloves',
    brand_name_snapshot: '',
    description: '',
    quantity: '1.000',
    unit: 'box',
    unit_price: '',
    vat_rate: '0.000',
    match_status: 'unresolved',
    notes: '',
  }],
};

const readyQuote = {
  ...quote,
  subtotal: '10.00',
  total: '10.00',
  lines: [{
    ...quote.lines[0],
    product: 11,
    product_name: 'Gloves A',
    unit_price: '10.00',
    match_status: 'confirmed',
  }],
};

const INITIAL_EMAIL_REVIEW_FINGERPRINT = 'f'.repeat(64);
const preparedReadyQuote = {
  ...readyQuote,
  quotation_review_fingerprint: INITIAL_EMAIL_REVIEW_FINGERPRINT,
};

const withGmailChainedActions = (sourceQuote = readyQuote) => ({
  ...sourceQuote,
  workflow_features: {
    ...(sourceQuote.workflow_features || {}),
    gmail_chained_actions: true,
  },
});

const withProgressiveLoad = (sourceQuote = quote) => ({
  ...sourceQuote,
  workflow_features: {
    ...(sourceQuote.workflow_features || {}),
    quotation_editor_progressive_load: true,
  },
});

const priceContext = (product, productName, price) => ({
  product,
  product_name: productName,
  unit_price: String(price),
  currency: 'AED',
  source: 'company_price_history',
  latest_quoted: {
    quotation: product,
    quotation_number: `Q-${product}`,
    quoted_at: '2026-06-01',
    quoted_unit_price: String(price),
    quantity: '1.000',
    unit: 'box',
    currency: 'AED',
    outcome_status: 'accepted',
    accepted_unit_price: String(Number(price) - 1),
    accepted_quantity: '1.000',
    accepted_at: '2026-06-02',
    lpo_number: `LPO-${product}`,
  },
  latest_accepted: {
    quotation: product,
    quotation_number: `Q-${product}`,
    quoted_at: '2026-06-01',
    quoted_unit_price: String(price),
    quantity: '1.000',
    unit: 'box',
    currency: 'AED',
    outcome_status: 'accepted',
    accepted_unit_price: String(Number(price) - 1),
    accepted_quantity: '1.000',
    accepted_at: '2026-06-02',
    lpo_number: `LPO-${product}`,
  },
  history: [],
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

describe('QuotationEditor Product price context', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    describeQuotationError.mockImplementation(async (error, action, endpoint) => ({
      action,
      endpoint,
      status: error?.response?.status || 'Network error',
      detail: error?.response?.data?.detail || error?.message || 'Request failed',
    }));
    formatQuotationError.mockImplementation(() => 'Request failed');
    quotationAPI.quotes.retrieve.mockResolvedValue({ data: quote });
    quotationAPI.quotes.update.mockResolvedValue({ data: quote });
    quotationAPI.quotes.productPrices.mockResolvedValue({ data: { results: {} } });
    quotationAPI.quotes.lpos.mockResolvedValue({ data: [] });
    quotationAPI.quotes.emailPreview.mockResolvedValue({
      data: {
        delivery_mode: 'gmail_reply',
        to: ['buyer@example.com'],
        cc: [],
        subject: 'Re: RFQ',
        body: 'Please find attached our quotation.',
        attachment_filename: 'CUSTOMER-Q-0021.pdf',
        preview_fingerprint: 'preview-fingerprint-1',
        trusted_source: { sender_email: 'buyer@example.com', subject: 'RFQ' },
      },
    });
    quotationAPI.quotes.emailThreadCandidates.mockResolvedValue({ data: { recipient: 'buyer@example.com', candidates: [] } });
    quotationAPI.quotes.finalizeAndSend.mockResolvedValue({ data: { message: 'Quotation emailed.' } });
    quotationAPI.quotes.sendEmail.mockResolvedValue({ data: { message: 'Quotation emailed.' } });
    quotationAPI.quotes.reconcileEmail.mockResolvedValue({
      data: {
        reconciled: false,
        detail: 'No matching sent message was found yet.',
        delivery: { status: 'unknown' },
      },
    });
    quotationAPI.quotes.finalize.mockResolvedValue({ data: { ...quote, status: 'finalized', status_display: 'Finalized' } });
    quotationAPI.quotes.pdf.mockResolvedValue({ data: new Blob(['pdf']) });
    quotationAPI.gmail.connectUrl.mockResolvedValue({ data: {} });
    quotationAPI.items.list.mockImplementation((params) => Promise.resolve({
      data: params?.company_used ? [products[0]] : products,
    }));
    quotationAPI.companies.list.mockResolvedValue({ data: [{ id: 7, name: 'Customer A' }] });
    quotationAPI.contacts.list.mockResolvedValue({ data: [] });
    quotationAPI.auditLogs.list.mockResolvedValue({ data: [] });
    quotationAPI.lines.create.mockResolvedValue({ data: {} });
    quotationAPI.lines.rememberAlias.mockResolvedValue({ data: {} });
  });

  test('keeps the optional Brand column off by default', async () => {
    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    const toggle = await screen.findByRole('checkbox', { name: 'Show Brand column' });
    const termsPanel = screen.getByRole('heading', { name: 'Quotation Terms & Layout' }).closest('.qm-terms-panel');
    expect(toggle).not.toBeChecked();
    expect(termsPanel.querySelector('.qm-terms-heading')).toBeInTheDocument();
    expect(termsPanel.querySelector('.qm-terms-fields')).toBeInTheDocument();
    expect(toggle.closest('.qm-terms-toggle-control')).toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: 'Brand' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Brand for Imported gloves')).not.toBeInTheDocument();
  });

  test('consumes an exact one-shot Gmail handoff and opens only the existing hardened preview', async () => {
    const onInitialEmailReviewHandled = jest.fn();
    quotationAPI.quotes.retrieve.mockResolvedValue({ data: preparedReadyQuote });

    render(
      <QuotationEditor
        quoteId={21}
        onClose={jest.fn()}
        initialEmailReviewFingerprint={INITIAL_EMAIL_REVIEW_FINGERPRINT}
        onInitialEmailReviewHandled={onInitialEmailReviewHandled}
      />
    );

    const dialog = await screen.findByRole('dialog', { name: 'Finalize and send quotation' });
    expect(dialog).toBeInTheDocument();
    expect(onInitialEmailReviewHandled).toHaveBeenCalledTimes(1);
    expect(quotationAPI.quotes.emailPreview).toHaveBeenCalledTimes(1);
    expect(quotationAPI.quotes.emailPreview).toHaveBeenCalledWith(21, {
      quotation_review_fingerprint: INITIAL_EMAIL_REVIEW_FINGERPRINT,
    });
    expect(quotationAPI.quotes.finalize).not.toHaveBeenCalled();
    expect(quotationAPI.quotes.finalizeAndSend).not.toHaveBeenCalled();
    expect(quotationAPI.quotes.sendEmail).not.toHaveBeenCalled();
  });

  test('consumes but rejects a one-shot Gmail handoff when the loaded quotation fingerprint differs', async () => {
    const onInitialEmailReviewHandled = jest.fn();
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({
      data: {
        ...preparedReadyQuote,
        quotation_review_fingerprint: 'e'.repeat(64),
      },
    });

    render(
      <QuotationEditor
        quoteId={21}
        onClose={jest.fn()}
        initialEmailReviewFingerprint={INITIAL_EMAIL_REVIEW_FINGERPRINT}
        onInitialEmailReviewHandled={onInitialEmailReviewHandled}
      />
    );

    expect(await screen.findByText(/quotation changed before the email review opened/i)).toBeInTheDocument();
    expect(onInitialEmailReviewHandled).toHaveBeenCalledTimes(1);
    expect(quotationAPI.quotes.emailPreview).not.toHaveBeenCalled();
    expect(quotationAPI.quotes.finalizeAndSend).not.toHaveBeenCalled();
  });

  test('suppresses the one-shot preview when a local price changes during its authoritative refresh', async () => {
    const refreshRequest = deferred();
    quotationAPI.quotes.retrieve
      .mockResolvedValueOnce({ data: preparedReadyQuote })
      .mockReturnValueOnce(refreshRequest.promise);

    render(
      <QuotationEditor
        quoteId={21}
        onClose={jest.fn()}
        initialEmailReviewFingerprint={INITIAL_EMAIL_REVIEW_FINGERPRINT}
        onInitialEmailReviewHandled={jest.fn()}
      />
    );

    const priceInput = await screen.findByLabelText('Unit price for Imported gloves');
    await waitFor(() => expect(quotationAPI.quotes.retrieve).toHaveBeenCalledTimes(2));
    fireEvent.change(priceInput, { target: { value: '11.00' } });
    await act(async () => {
      refreshRequest.resolve({ data: preparedReadyQuote });
      await refreshRequest.promise;
    });

    expect(await screen.findByText(/quotation lines changed while the email review was being prepared/i)).toBeInTheDocument();
    expect(quotationAPI.quotes.emailPreview).not.toHaveBeenCalled();
    expect(quotationAPI.quotes.finalizeAndSend).not.toHaveBeenCalled();
  });

  test('shows the saved Brand column and line snapshot when enabled', async () => {
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({
      data: {
        ...quote,
        show_brand_column: true,
        lines: [{ ...quote.lines[0], brand_name_snapshot: 'Customer Brand' }],
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    expect(await screen.findByRole('checkbox', { name: 'Show Brand column' })).toBeChecked();
    expect(screen.getByRole('columnheader', { name: 'Brand' })).toBeInTheDocument();
    expect(screen.getByLabelText('Brand for Imported gloves')).toHaveValue('Customer Brand');
    expect(screen.getByRole('textbox', { name: 'Brand' })).toBeInTheDocument();
  });

  test('saves the Brand toggle with quotation terms and layout', async () => {
    quotationAPI.quotes.update.mockResolvedValueOnce({
      data: { ...quote, show_brand_column: true },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    fireEvent.click(await screen.findByRole('checkbox', { name: 'Show Brand column' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save Terms & Layout' }));

    await waitFor(() => expect(quotationAPI.quotes.update).toHaveBeenCalledWith(21, {
      payment_terms: 'as_per_agreement',
      valid_until: '2026-08-01',
      show_brand_column: true,
    }));
    expect(await screen.findByRole('button', { name: 'Terms & Layout Saved' })).toBeDisabled();
  });

  test('preserves an unsaved Brand layout choice when adding a line refreshes the quote', async () => {
    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    const toggle = await screen.findByRole('checkbox', { name: 'Show Brand column' });
    fireEvent.click(toggle);
    fireEvent.change(screen.getByPlaceholderText('Snapshot name'), { target: { value: 'New customer item' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add Line' }));

    await waitFor(() => expect(quotationAPI.lines.create).toHaveBeenCalled());
    await waitFor(() => expect(quotationAPI.quotes.retrieve).toHaveBeenCalledTimes(2));
    expect(toggle).toBeChecked();
    expect(screen.getByRole('columnheader', { name: 'Brand' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save Terms & Layout' })).toBeEnabled();
  });

  test('disables customer document downloads while the Brand layout is unsaved', async () => {
    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    const toggle = await screen.findByRole('checkbox', { name: 'Show Brand column' });
    const pdfButton = screen.getByRole('button', { name: 'Download Draft PDF' });
    const excelButton = screen.getByRole('button', { name: 'Download Excel' });
    expect(pdfButton).toBeEnabled();
    expect(excelButton).toBeEnabled();

    fireEvent.click(toggle);

    expect(pdfButton).toBeDisabled();
    expect(excelButton).toBeDisabled();
    expect(pdfButton).toHaveAttribute('title', expect.stringMatching(/save customer, terms and layout, and line changes/i));
  });

  test('autofills Brand from the selected Product without changing the source snapshot', async () => {
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({
      data: { ...quote, show_brand_column: true },
    });
    quotationAPI.quotes.productPrice.mockResolvedValueOnce({
      data: {
        product: 11,
        product_name: 'Gloves A',
        source: 'no_history',
        unit_price: null,
        history: [],
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    fireEvent.change(await screen.findByLabelText('Product for Imported gloves'), { target: { value: '11' } });

    await waitFor(() => expect(screen.getByLabelText('Brand for Imported gloves')).toHaveValue('Medline'));
    expect(screen.getByDisplayValue('Imported gloves')).toBeInTheDocument();
  });

  test('includes a manually edited Brand snapshot when saving a quotation line', async () => {
    const quoteWithBrand = {
      ...quote,
      show_brand_column: true,
      lines: [{
        ...quote.lines[0],
        product: 11,
        product_name: 'Gloves A',
        brand_name_snapshot: 'Medline',
        match_status: 'confirmed',
      }],
    };
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({ data: quoteWithBrand });
    quotationAPI.quotes.bulkUpdateLines.mockResolvedValueOnce({
      data: {
        quotation: {
          ...quoteWithBrand,
          lines: [{ ...quoteWithBrand.lines[0], brand_name_snapshot: 'Medline Gulf' }],
        },
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    const brandInput = await screen.findByLabelText('Brand for Imported gloves');
    fireEvent.change(brandInput, { target: { value: 'Medline Gulf' } });
    const row = brandInput.closest('tr');
    fireEvent.click(within(row).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(quotationAPI.quotes.bulkUpdateLines).toHaveBeenCalledWith(21, {
      lines: [expect.objectContaining({
        id: 31,
        item_name_snapshot: 'Imported gloves',
        brand_name_snapshot: 'Medline Gulf',
      })],
    }));
  });

  test('keeps an unsaved Brand override after bulk Product creation updates the row', async () => {
    const quoteWithBrandColumn = {
      ...quote,
      show_brand_column: true,
    };
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({ data: quoteWithBrandColumn });
    quotationAPI.quotes.bulkCreateProductsForLines.mockResolvedValueOnce({
      data: {
        updated_lines: [{
          ...quote.lines[0],
          product: 13,
          product_name: 'Imported gloves',
          brand_name_snapshot: '',
          match_status: 'confirmed',
        }],
        confirmation_required: [],
        message: 'Created and linked one Product.',
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    const brandInput = await screen.findByLabelText('Brand for Imported gloves');
    fireEvent.change(brandInput, { target: { value: 'Customer Contract Brand' } });
    fireEvent.click(screen.getByRole('button', { name: 'Select visible unmatched' }));
    fireEvent.click(screen.getByRole('button', { name: 'Create Products for Selected Unmatched Rows' }));
    const dialog = await screen.findByRole('dialog', { name: 'Create Products from quotation lines' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Check catalog and continue' }));

    await waitFor(() => expect(screen.queryByRole('dialog', { name: /create products/i })).not.toBeInTheDocument());
    expect(screen.getByLabelText('Brand for Imported gloves')).toHaveValue('Customer Contract Brand');
    expect(within(brandInput.closest('tr')).getByText('Unsaved')).toBeInTheDocument();
  });

  test('disables Brand layout and line editing on a finalized quotation', async () => {
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({
      data: {
        ...quote,
        status: 'finalized',
        status_display: 'Finalized',
        show_brand_column: true,
        lines: [{ ...quote.lines[0], brand_name_snapshot: 'Medline' }],
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    expect(await screen.findByRole('checkbox', { name: 'Show Brand column' })).toBeDisabled();
    expect(screen.getByLabelText('Brand for Imported gloves')).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Save Terms & Layout' })).not.toBeInTheDocument();
  });

  test('opens a verified Gmail preview before finalizing and sends only after explicit confirmation', async () => {
    quotationAPI.quotes.retrieve.mockResolvedValue({ data: readyQuote });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    const finalizeButtons = await screen.findAllByRole('button', { name: 'Finalize' });
    fireEvent.click(finalizeButtons[0]);

    const dialog = await screen.findByRole('dialog', { name: 'Finalize and send quotation' });
    expect(quotationAPI.quotes.emailPreview).toHaveBeenCalledWith(21, {
      quotation_review_fingerprint: 'quotation-review-fingerprint-1',
    });
    expect(quotationAPI.quotes.finalize).not.toHaveBeenCalled();
    expect(within(dialog).getByDisplayValue('buyer@example.com')).toHaveAttribute('readonly');
    fireEvent.change(within(dialog).getByLabelText(/Message/), {
      target: { value: 'Dear Buyer,\n\nPlease find attached quotation Q-0021.' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Finalize & Send Quotation' }));

    await waitFor(() => expect(quotationAPI.quotes.finalizeAndSend).toHaveBeenCalledWith(21, {
      to: ['buyer@example.com'],
      cc: [],
      subject: 'Re: RFQ',
      body: 'Dear Buyer,\n\nPlease find attached quotation Q-0021.',
      confirm_recipient: true,
      delivery_mode: 'gmail_reply',
      preview_fingerprint: 'preview-fingerprint-1',
    }));
    expect(quotationAPI.quotes.finalize).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /quotation/i })).not.toBeInTheDocument());
  });

  test('keeps the existing Finalize action unless the strict server flag is true', async () => {
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({
      data: {
        ...readyQuote,
        workflow_features: { gmail_chained_actions: 'true' },
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    expect((await screen.findAllByRole('button', { name: 'Finalize' })).length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: 'Review Email' })).not.toBeInTheDocument();
  });

  test('saves only changed lines once and previews the authoritative returned fingerprint', async () => {
    const initialQuote = withGmailChainedActions({
      ...readyQuote,
      subtotal: '30.00',
      total: '30.00',
      lines: [
        readyQuote.lines[0],
        {
          ...readyQuote.lines[0],
          id: 32,
          sort_order: 1,
          product: 12,
          product_name: 'Gloves B',
          item_name_snapshot: 'Imported masks',
          unit_price: '20.00',
        },
      ],
    });
    const savedQuote = {
      ...initialQuote,
      quotation_review_fingerprint: 'quotation-review-fingerprint-2',
      subtotal: '32.00',
      total: '32.00',
      lines: [
        { ...initialQuote.lines[0], unit_price: '12.00' },
        initialQuote.lines[1],
      ],
    };
    const saveRequest = deferred();
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({ data: initialQuote });
    quotationAPI.quotes.bulkUpdateLines.mockReturnValueOnce(saveRequest.promise);

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    fireEvent.change(await screen.findByLabelText('Unit price for Imported gloves'), {
      target: { value: '12' },
    });
    const reviewButton = (await screen.findAllByRole('button', { name: 'Review Email' }))[0];
    fireEvent.click(reviewButton);
    fireEvent.click(reviewButton);

    await waitFor(() => expect(quotationAPI.quotes.bulkUpdateLines).toHaveBeenCalledWith(21, {
      quotation_review_fingerprint: 'quotation-review-fingerprint-1',
      lines: [expect.objectContaining({
        id: 31,
        product: 11,
        item_name_snapshot: 'Imported gloves',
        quantity: '1.000',
        unit: 'box',
        unit_price: '12',
        vat_rate: '0',
        match_status: 'confirmed',
      })],
    }));
    expect(quotationAPI.quotes.bulkUpdateLines).toHaveBeenCalledTimes(1);
    expect(quotationAPI.quotes.emailPreview).not.toHaveBeenCalled();

    await act(async () => {
      saveRequest.resolve({ data: { quotation: savedQuote } });
      await saveRequest.promise;
    });

    await screen.findByRole('dialog', { name: 'Finalize and send quotation' });
    expect(quotationAPI.quotes.emailPreview).toHaveBeenCalledTimes(1);
    expect(quotationAPI.quotes.emailPreview).toHaveBeenCalledWith(21, {
      quotation_review_fingerprint: 'quotation-review-fingerprint-2',
    });
    expect(quotationAPI.quotes.retrieve).toHaveBeenCalledTimes(1);
    expect(quotationAPI.quotes.finalize).not.toHaveBeenCalled();
    expect(quotationAPI.quotes.finalizeAndSend).not.toHaveBeenCalled();
    expect(quotationAPI.quotes.sendEmail).not.toHaveBeenCalled();
  });

  test('preserves edits made during a chained save and requires a fresh review action', async () => {
    const initialQuote = withGmailChainedActions();
    const savedQuote = {
      ...initialQuote,
      quotation_review_fingerprint: 'quotation-review-fingerprint-2',
      subtotal: '12.00',
      total: '12.00',
      lines: [{ ...initialQuote.lines[0], unit_price: '12.00' }],
    };
    const saveRequest = deferred();
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({ data: initialQuote });
    quotationAPI.quotes.bulkUpdateLines.mockReturnValueOnce(saveRequest.promise);

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    const priceInput = await screen.findByLabelText('Unit price for Imported gloves');
    fireEvent.change(priceInput, { target: { value: '12' } });
    fireEvent.click((await screen.findAllByRole('button', { name: 'Review Email' }))[0]);
    await waitFor(() => expect(quotationAPI.quotes.bulkUpdateLines).toHaveBeenCalledTimes(1));

    fireEvent.change(priceInput, { target: { value: '13' } });
    await act(async () => {
      saveRequest.resolve({ data: { quotation: savedQuote } });
      await saveRequest.promise;
    });

    expect(await screen.findByText(/saved response was applied without discarding your newer edits/i)).toBeInTheDocument();
    expect(screen.getByLabelText('Unit price for Imported gloves')).toHaveValue(13);
    expect(screen.getByText('1 unsaved line change(s)')).toBeInTheDocument();
    expect(quotationAPI.quotes.emailPreview).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog', { name: /quotation/i })).not.toBeInTheDocument();
  });

  test('discards a chained preview response when lines change while it is loading', async () => {
    const initialQuote = withGmailChainedActions();
    const previewRequest = deferred();
    quotationAPI.quotes.retrieve.mockResolvedValue({ data: initialQuote });
    quotationAPI.quotes.emailPreview.mockReturnValueOnce(previewRequest.promise);

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.click((await screen.findAllByRole('button', { name: 'Review Email' }))[0]);
    await waitFor(() => expect(quotationAPI.quotes.emailPreview).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText('Unit price for Imported gloves'), {
      target: { value: '13' },
    });
    await act(async () => {
      previewRequest.resolve({ data: {} });
      await previewRequest.promise;
    });

    expect(await screen.findByText(/lines changed while the email review was being prepared/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /quotation/i })).not.toBeInTheDocument());
    expect(quotationAPI.quotes.finalizeAndSend).not.toHaveBeenCalled();
    expect(quotationAPI.quotes.sendEmail).not.toHaveBeenCalled();
  });

  test('does not open an email preview when the chained line save fails', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({ data: withGmailChainedActions() });
    quotationAPI.quotes.bulkUpdateLines.mockRejectedValueOnce({
      response: {
        status: 400,
        data: { detail: 'The quotation line could not be saved.' },
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.change(await screen.findByLabelText('Unit price for Imported gloves'), {
      target: { value: '12' },
    });
    fireEvent.click((await screen.findAllByRole('button', { name: 'Review Email' }))[0]);

    await waitFor(() => expect(screen.getAllByText('The quotation line could not be saved.').length).toBeGreaterThan(0));
    expect(quotationAPI.quotes.emailPreview).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog', { name: /quotation/i })).not.toBeInTheDocument();
    expect(quotationAPI.quotes.finalizeAndSend).not.toHaveBeenCalled();
    expect(quotationAPI.quotes.sendEmail).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });

  test('refreshes stale chained saves without opening an email preview', async () => {
    const staleQuote = {
      ...withGmailChainedActions(),
      quotation_review_fingerprint: 'quotation-review-fingerprint-2',
      lines: [{
        ...readyQuote.lines[0],
        item_name_snapshot: 'Changed by another employee',
      }],
    };
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({ data: withGmailChainedActions() });
    quotationAPI.quotes.bulkUpdateLines.mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          code: 'stale_quotation_review',
          detail: 'The quotation changed in another session.',
          quote: staleQuote,
        },
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.change(await screen.findByLabelText('Unit price for Imported gloves'), {
      target: { value: '12' },
    });
    fireEvent.click((await screen.findAllByRole('button', { name: 'Review Email' }))[0]);

    expect(await screen.findByText('The quotation changed in another session.')).toBeInTheDocument();
    expect(quotationAPI.quotes.emailPreview).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog', { name: /quotation/i })).not.toBeInTheDocument();
  });

  test('does not preview a chained action that finishes saving after unmount', async () => {
    const saveRequest = deferred();
    const initialQuote = withGmailChainedActions();
    const savedQuote = {
      ...initialQuote,
      quotation_review_fingerprint: 'quotation-review-fingerprint-2',
      lines: [{ ...initialQuote.lines[0], unit_price: '12.00' }],
    };
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({ data: initialQuote });
    quotationAPI.quotes.bulkUpdateLines.mockReturnValueOnce(saveRequest.promise);

    const { unmount } = render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.change(await screen.findByLabelText('Unit price for Imported gloves'), {
      target: { value: '12' },
    });
    fireEvent.click((await screen.findAllByRole('button', { name: 'Review Email' }))[0]);
    await waitFor(() => expect(quotationAPI.quotes.bulkUpdateLines).toHaveBeenCalledTimes(1));
    unmount();

    await act(async () => {
      saveRequest.resolve({ data: { quotation: savedQuote } });
      await saveRequest.promise;
    });

    expect(quotationAPI.quotes.emailPreview).not.toHaveBeenCalled();
    expect(quotationAPI.quotes.finalizeAndSend).not.toHaveBeenCalled();
    expect(quotationAPI.quotes.sendEmail).not.toHaveBeenCalled();
  });

  test('refreshes a remotely changed quotation and requires a second explicit preview action', async () => {
    const remotelyChangedQuote = {
      ...readyQuote,
      quotation_review_fingerprint: 'quotation-review-fingerprint-2',
      lines: [{
        ...readyQuote.lines[0],
        item_name_snapshot: 'Gloves changed by another employee',
      }],
    };
    quotationAPI.quotes.retrieve
      .mockReset()
      .mockResolvedValueOnce({ data: readyQuote })
      .mockResolvedValue({ data: remotelyChangedQuote });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.click((await screen.findAllByRole('button', { name: 'Finalize' }))[0]);

    expect(await screen.findByText(/changed since this editor loaded/i)).toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: /quotation/i })).not.toBeInTheDocument();
    expect(screen.getByDisplayValue('Gloves changed by another employee')).toBeInTheDocument();
    expect(quotationAPI.quotes.emailPreview).not.toHaveBeenCalled();

    fireEvent.click((await screen.findAllByRole('button', { name: 'Finalize' }))[0]);
    await screen.findByRole('dialog', { name: 'Finalize and send quotation' });
    expect(quotationAPI.quotes.emailPreview).toHaveBeenCalledTimes(1);
    expect(quotationAPI.quotes.emailPreview).toHaveBeenCalledWith(21, {
      quotation_review_fingerprint: 'quotation-review-fingerprint-2',
    });
    expect(quotationAPI.quotes.finalizeAndSend).not.toHaveBeenCalled();
  });

  test('rejects a hybrid displayed payload even when its review fingerprint is current', async () => {
    const hybridQuote = {
      ...readyQuote,
      quotation_review_fingerprint: 'quotation-review-fingerprint-2',
    };
    const atomicCurrentQuote = {
      ...hybridQuote,
      lines: [{
        ...hybridQuote.lines[0],
        item_name_snapshot: 'Current locked quotation line',
      }],
    };
    quotationAPI.quotes.retrieve
      .mockReset()
      .mockResolvedValueOnce({ data: hybridQuote })
      .mockResolvedValue({ data: atomicCurrentQuote });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.click((await screen.findAllByRole('button', { name: 'Finalize' }))[0]);

    expect(await screen.findByText(/changed since this editor loaded/i)).toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: /quotation/i })).not.toBeInTheDocument();
    expect(screen.getByDisplayValue('Current locked quotation line')).toBeInTheDocument();
    expect(quotationAPI.quotes.emailPreview).not.toHaveBeenCalled();

    fireEvent.click((await screen.findAllByRole('button', { name: 'Finalize' }))[0]);
    await screen.findByRole('dialog', { name: 'Finalize and send quotation' });
    expect(quotationAPI.quotes.emailPreview).toHaveBeenCalledWith(21, {
      quotation_review_fingerprint: 'quotation-review-fingerprint-2',
    });
  });

  test('runs stale-preview refresh through the same atomic quotation gate', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    const hybridErrorQuote = {
      ...readyQuote,
      quotation_review_fingerprint: 'quotation-review-fingerprint-2',
    };
    const atomicCurrentQuote = {
      ...hybridErrorQuote,
      lines: [{
        ...hybridErrorQuote.lines[0],
        item_name_snapshot: 'Changed before stale preview refresh',
      }],
    };
    quotationAPI.quotes.retrieve
      .mockReset()
      .mockResolvedValueOnce({ data: readyQuote })
      .mockResolvedValueOnce({ data: readyQuote })
      .mockResolvedValue({ data: atomicCurrentQuote });
    quotationAPI.quotes.finalizeAndSend.mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          code: 'stale_email_preview',
          detail: 'The quotation changed after this preview was prepared.',
          quote_finalized: false,
          retryable: false,
          delivery_status: 'not_sent',
          quote: hybridErrorQuote,
        },
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.click((await screen.findAllByRole('button', { name: 'Finalize' }))[0]);
    let dialog = await screen.findByRole('dialog', { name: 'Finalize and send quotation' });
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Finalize & Send Quotation' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Refresh preview' }));

    expect(await screen.findByText(/changed since this editor loaded/i)).toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: /quotation/i })).not.toBeInTheDocument();
    expect(screen.getByDisplayValue('Changed before stale preview refresh')).toBeInTheDocument();
    expect(quotationAPI.quotes.emailPreview).toHaveBeenCalledTimes(1);

    fireEvent.click((await screen.findAllByRole('button', { name: 'Finalize' }))[0]);
    dialog = await screen.findByRole('dialog', { name: 'Finalize and send quotation' });
    await within(dialog).findByRole('button', { name: 'Finalize & Send Quotation' });
    expect(quotationAPI.quotes.emailPreview).toHaveBeenCalledTimes(2);
    expect(quotationAPI.quotes.emailPreview).toHaveBeenLastCalledWith(21, {
      quotation_review_fingerprint: 'quotation-review-fingerprint-2',
    });
    consoleError.mockRestore();
  });

  test('requires staff to enter and confirm a recipient for a manual quotation', async () => {
    quotationAPI.quotes.retrieve.mockResolvedValue({ data: readyQuote });
    quotationAPI.quotes.emailPreview.mockResolvedValueOnce({
      data: {
        delivery_mode: 'new_email',
        to: [],
        cc: [],
        subject: 'Quotation Q-0021',
        body: 'Please find attached our quotation.',
        attachment_filename: 'CUSTOMER-Q-0021.pdf',
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.click((await screen.findAllByRole('button', { name: 'Finalize' }))[0]);

    const dialog = await screen.findByRole('dialog', { name: 'Finalize and send quotation' });
    expect(within(dialog).getByText('Sending a new email')).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText(/To/), { target: { value: 'purchasing@example.com' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Finalize & Send Quotation' }));
    expect(quotationAPI.quotes.finalizeAndSend).not.toHaveBeenCalled();
    expect(within(dialog).getByText(/Confirm the manually entered recipient/)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('checkbox', { name: /I checked this address/ }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Finalize & Send Quotation' }));

    await waitFor(() => expect(quotationAPI.quotes.finalizeAndSend).toHaveBeenCalledWith(
      21,
      expect.objectContaining({
        to: ['purchasing@example.com'],
        confirm_recipient: true,
        delivery_mode: 'new_email',
      })
    ));
  });

  test('keeps a correctable backend validation error editable and clears it after a field change', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    quotationAPI.quotes.retrieve.mockResolvedValue({ data: readyQuote });
    quotationAPI.quotes.finalizeAndSend.mockRejectedValueOnce({
      response: {
        status: 400,
        data: {
          code: 'email_delivery_error',
          detail: 'Enter a valid email body.',
          quote_finalized: false,
          retryable: false,
        },
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.click((await screen.findAllByRole('button', { name: 'Finalize' }))[0]);
    let dialog = await screen.findByRole('dialog', { name: 'Finalize and send quotation' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Finalize & Send Quotation' }));

    expect(await screen.findByText('Check the email details and try again.')).toBeInTheDocument();
    dialog = screen.getByRole('dialog', { name: 'Finalize and send quotation' });
    expect(within(dialog).getByRole('button', { name: 'Finalize & Send Quotation' })).toBeEnabled();
    expect(screen.queryByText(/Delivery status is unknown/)).not.toBeInTheDocument();

    fireEvent.change(within(dialog).getByLabelText(/Message/), {
      target: { value: 'Corrected customer-facing message.' },
    });
    expect(screen.queryByText('Check the email details and try again.')).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Finalize & Send Quotation' }));

    await waitFor(() => expect(quotationAPI.quotes.finalizeAndSend).toHaveBeenCalledTimes(2));
    consoleError.mockRestore();
  });

  test('keeps an attachment snapshot mismatch hard-blocked after email field edits', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    const finalizedQuote = { ...readyQuote, status: 'finalized', status_display: 'Finalized' };
    quotationAPI.quotes.retrieve.mockResolvedValue({ data: finalizedQuote });
    quotationAPI.quotes.emailPreview.mockResolvedValueOnce({
      data: {
        delivery_mode: 'gmail_reply',
        status: 'failed',
        to: ['buyer@example.com'],
        cc: [],
        subject: 'Re: RFQ',
        body: 'Please find attached our quotation.',
        attachment_filename: 'CUSTOMER-Q-0021.pdf',
      },
    });
    quotationAPI.quotes.sendEmail.mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          code: 'attachment_snapshot_mismatch',
          detail: 'The regenerated quotation PDF differs from the reviewed attachment.',
          quote_finalized: true,
          retryable: false,
          quote: finalizedQuote,
          delivery: { status: 'failed' },
        },
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Email Quotation' }));
    let dialog = await screen.findByRole('dialog', { name: 'Review and send quotation' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Send Quotation' }));

    expect(await screen.findByText('The regenerated quotation PDF differs from the reviewed attachment.')).toBeInTheDocument();
    dialog = screen.getByRole('dialog', { name: 'Review and send quotation' });
    expect(within(dialog).getByRole('button', { name: 'Sending disabled' })).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText(/Message/), {
      target: { value: 'This edit must not clear the snapshot mismatch.' },
    });
    expect(screen.getByText('The regenerated quotation PDF differs from the reviewed attachment.')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Sending disabled' })).toBeDisabled();
    expect(quotationAPI.quotes.sendEmail).toHaveBeenCalledTimes(1);
    consoleError.mockRestore();
  });

  test('starts Gmail reconnect with the exact quotation return path', async () => {
    quotationAPI.quotes.retrieve.mockResolvedValue({ data: readyQuote });
    quotationAPI.quotes.emailPreview.mockResolvedValueOnce({
      data: {
        delivery_mode: 'gmail_reply',
        to: ['buyer@example.com'],
        cc: [],
        subject: 'Re: RFQ',
        body: 'Please find attached our quotation.',
        attachment_filename: 'CUSTOMER-Q-0021.pdf',
        gmail_connected: true,
        gmail_send_authorized: false,
        gmail_can_manage: true,
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.click((await screen.findAllByRole('button', { name: 'Finalize' }))[0]);
    const dialog = await screen.findByRole('dialog', { name: 'Finalize and send quotation' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reconnect Gmail' }));

    await waitFor(() => expect(quotationAPI.gmail.connectUrl).toHaveBeenCalledWith(
      '/admin?quotation_tab=quotes&quote_id=21'
    ));
  });

  test('lets staff explicitly link a manual quotation to an exact verified Gmail message', async () => {
    const threadCandidate = {
      selection_token: 'signed-thread-token',
      gmail_message_id: 'message-1',
      gmail_thread_id: 'thread-1',
      sender_name: 'Maria Buyer',
      sender_email: 'buyer@example.com',
      subject: 'RFQ - Clinic supplies',
      received_at: '31 Jul 2026, 10:30',
      snippet: 'Please quote the attached list.',
    };
    quotationAPI.quotes.retrieve.mockResolvedValue({ data: readyQuote });
    quotationAPI.quotes.emailPreview
      .mockResolvedValueOnce({
        data: {
          delivery_mode: 'new_email',
          to: [],
          subject: 'Quotation Q-0021',
          body: 'Please find attached our quotation.',
          attachment_filename: 'CUSTOMER-Q-0021.pdf',
        },
      })
      .mockResolvedValueOnce({
        data: {
          delivery_mode: 'gmail_reply',
          to: ['buyer@example.com'],
          cc: [],
          subject: 'Re: RFQ - Clinic supplies',
          body: 'Dear Maria,\n\nPlease find attached our quotation.',
          attachment_filename: 'CUSTOMER-Q-0021.pdf',
          preview_fingerprint: 'selected-preview-fingerprint',
          trusted_source: {
            sender_name: 'Maria Buyer',
            sender_email: 'buyer@example.com',
            subject: 'RFQ - Clinic supplies',
          },
        },
      });
    quotationAPI.quotes.emailThreadCandidates.mockResolvedValueOnce({
      data: { recipient: 'buyer@example.com', candidates: [threadCandidate] },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.click((await screen.findAllByRole('button', { name: 'Finalize' }))[0]);
    let dialog = await screen.findByRole('dialog', { name: 'Finalize and send quotation' });
    fireEvent.change(within(dialog).getByLabelText(/To/), { target: { value: 'buyer@example.com' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Find original Gmail thread' }));

    expect(await screen.findByText('RFQ - Clinic supplies')).toBeInTheDocument();
    expect(quotationAPI.quotes.emailThreadCandidates).toHaveBeenCalledWith(21, 'buyer@example.com', 10);
    fireEvent.click(screen.getByRole('button', { name: 'Reply to this thread' }));

    dialog = await screen.findByRole('dialog', { name: 'Finalize and send quotation' });
    expect(await within(dialog).findByText('Replying in the verified Gmail thread')).toBeInTheDocument();
    expect(quotationAPI.quotes.emailPreview).toHaveBeenLastCalledWith(21, {
      quotation_review_fingerprint: 'quotation-review-fingerprint-1',
      thread_selection_token: 'signed-thread-token',
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Finalize & Send Quotation' }));

    await waitFor(() => expect(quotationAPI.quotes.finalizeAndSend).toHaveBeenCalledWith(
      21,
      expect.objectContaining({
        to: ['buyer@example.com'],
        delivery_mode: 'gmail_reply',
        preview_fingerprint: 'selected-preview-fingerprint',
        thread_selection_token: 'signed-thread-token',
      })
    ));
  });

  test('refreshes a stale selected-thread preview and requires another explicit send with the new fingerprint', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    const threadCandidate = {
      selection_token: 'signed-thread-token',
      gmail_message_id: 'message-1',
      gmail_thread_id: 'thread-1',
      sender_name: 'Maria Buyer',
      sender_email: 'buyer@example.com',
      subject: 'RFQ - Clinic supplies',
      received_at: '31 Jul 2026, 10:30',
      snippet: 'Please quote the attached list.',
    };
    const manualPreview = {
      delivery_mode: 'new_email',
      to: [],
      cc: [],
      subject: 'Quotation Q-0021',
      body: 'Please find attached our quotation.',
      attachment_filename: 'CUSTOMER-Q-0021.pdf',
      preview_fingerprint: 'manual-preview-fingerprint',
    };
    const selectedPreview = {
      delivery_mode: 'gmail_reply',
      to: ['buyer@example.com'],
      cc: [],
      subject: 'Re: RFQ - Clinic supplies',
      body: 'First reviewed email body.',
      attachment_filename: 'CUSTOMER-Q-0021.pdf',
      preview_fingerprint: 'stale-selected-fingerprint',
      trusted_source: {
        sender_name: 'Maria Buyer',
        sender_email: 'buyer@example.com',
        subject: 'RFQ - Clinic supplies',
      },
    };
    quotationAPI.quotes.retrieve.mockResolvedValue({ data: readyQuote });
    quotationAPI.quotes.emailPreview
      .mockResolvedValueOnce({ data: manualPreview })
      .mockResolvedValueOnce({ data: selectedPreview })
      .mockRejectedValueOnce({
        response: {
          status: 503,
          data: { detail: 'The latest preview is temporarily unavailable.' },
        },
      })
      .mockResolvedValueOnce({
        data: {
          ...selectedPreview,
          body: 'Updated preview after the quotation changed.',
          preview_fingerprint: 'fresh-selected-fingerprint',
        },
      });
    quotationAPI.quotes.emailThreadCandidates.mockResolvedValueOnce({
      data: { recipient: 'buyer@example.com', candidates: [threadCandidate] },
    });
    quotationAPI.quotes.finalizeAndSend.mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          code: 'stale_email_preview',
          detail: 'The quotation changed after this preview was prepared.',
          quote_finalized: false,
          retryable: true,
          delivery_status: 'not_sent',
          quote: readyQuote,
        },
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.click((await screen.findAllByRole('button', { name: 'Finalize' }))[0]);
    let dialog = await screen.findByRole('dialog', { name: 'Finalize and send quotation' });
    fireEvent.change(within(dialog).getByLabelText(/To/), {
      target: { value: 'buyer@example.com' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Find original Gmail thread' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Reply to this thread' }));

    dialog = await screen.findByRole('dialog', { name: 'Finalize and send quotation' });
    expect(await within(dialog).findByText('Replying in the verified Gmail thread')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Finalize & Send Quotation' }));
    expect(await screen.findByText('Refresh and review the quotation email before sending.')).toBeInTheDocument();
    expect(quotationAPI.quotes.finalizeAndSend).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: 'Refresh preview' }));
    await waitFor(() => expect(quotationAPI.quotes.emailPreview).toHaveBeenLastCalledWith(21, {
      quotation_review_fingerprint: 'quotation-review-fingerprint-1',
      thread_selection_token: 'signed-thread-token',
    }));
    expect(quotationAPI.quotes.emailPreview).toHaveBeenCalledTimes(3);
    expect(await screen.findByText('The latest preview is temporarily unavailable.')).toBeInTheDocument();
    expect(quotationAPI.quotes.finalizeAndSend).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: 'Retry preview' }));
    await waitFor(() => expect(quotationAPI.quotes.emailPreview).toHaveBeenCalledTimes(4));
    expect(quotationAPI.quotes.emailPreview).toHaveBeenLastCalledWith(21, {
      quotation_review_fingerprint: 'quotation-review-fingerprint-1',
      thread_selection_token: 'signed-thread-token',
    });
    dialog = await screen.findByRole('dialog', { name: 'Finalize and send quotation' });
    expect(await within(dialog).findByDisplayValue('Updated preview after the quotation changed.')).toBeInTheDocument();
    expect(quotationAPI.quotes.finalizeAndSend).toHaveBeenCalledTimes(1);

    fireEvent.click(within(dialog).getByRole('button', { name: 'Finalize & Send Quotation' }));
    await waitFor(() => expect(quotationAPI.quotes.finalizeAndSend).toHaveBeenCalledTimes(2));
    expect(quotationAPI.quotes.finalizeAndSend).toHaveBeenLastCalledWith(21, expect.objectContaining({
      preview_fingerprint: 'fresh-selected-fingerprint',
      thread_selection_token: 'signed-thread-token',
    }));
    consoleError.mockRestore();
  });

  test('merges a first failed frozen delivery, requires refresh, and retries its exact read-only email', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    const finalizedQuote = { ...readyQuote, status: 'finalized', status_display: 'Finalized' };
    const initialPreview = {
      delivery_mode: 'gmail_reply',
      to: ['buyer@example.com'],
      cc: [],
      subject: 'Re: RFQ',
      body: 'Initially reviewed body.',
      attachment_filename: 'CUSTOMER-Q-0021.pdf',
      preview_fingerprint: 'initial-preview-fingerprint',
      trusted_source: { sender_email: 'buyer@example.com', subject: 'RFQ' },
    };
    const frozenDelivery = {
      ...initialPreview,
      status: 'failed',
      outbound_snapshot_frozen: true,
      body: 'Exact frozen body.',
      last_error: 'Gmail rejected the first provider call.',
    };
    const refreshedFrozenPreview = {
      ...frozenDelivery,
      preview_fingerprint: 'frozen-preview-fingerprint',
    };
    quotationAPI.quotes.retrieve
      .mockReset()
      .mockResolvedValueOnce({ data: readyQuote })
      .mockResolvedValueOnce({ data: readyQuote })
      .mockResolvedValue({ data: finalizedQuote });
    quotationAPI.quotes.emailPreview
      .mockResolvedValueOnce({ data: initialPreview })
      .mockResolvedValueOnce({ data: refreshedFrozenPreview });
    quotationAPI.quotes.finalizeAndSend.mockRejectedValueOnce({
      response: {
        status: 400,
        data: {
          code: 'gmail_send_failed',
          detail: 'Gmail rejected the first provider call.',
          quote_finalized: true,
          retryable: true,
          refresh_preview: true,
          delivery_status: 'failed',
          quote: finalizedQuote,
          delivery: frozenDelivery,
        },
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.click((await screen.findAllByRole('button', { name: 'Finalize' }))[0]);
    let dialog = await screen.findByRole('dialog', { name: 'Finalize and send quotation' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Finalize & Send Quotation' }));

    expect(await screen.findByText(/failed provider attempt created an exact frozen email/i)).toBeInTheDocument();
    dialog = screen.getByRole('dialog', { name: 'Review and send quotation' });
    expect(within(dialog).getByDisplayValue('Exact frozen body.')).toHaveAttribute('readonly');
    expect(within(dialog).getByRole('button', { name: 'Sending disabled' })).toBeDisabled();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Refresh preview' }));

    await waitFor(() => expect(quotationAPI.quotes.emailPreview).toHaveBeenCalledTimes(2));
    dialog = await screen.findByRole('dialog', { name: 'Review and send quotation' });
    expect(within(dialog).getByDisplayValue('Exact frozen body.')).toHaveAttribute('readonly');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Send Quotation' }));

    await waitFor(() => expect(quotationAPI.quotes.sendEmail).toHaveBeenCalledWith(21, {
      to: ['buyer@example.com'],
      cc: [],
      subject: 'Re: RFQ',
      body: 'Exact frozen body.',
      confirm_recipient: true,
      delivery_mode: 'gmail_reply',
      preview_fingerprint: 'frozen-preview-fingerprint',
    }));
    expect(quotationAPI.quotes.finalizeAndSend).toHaveBeenCalledTimes(1);
    consoleError.mockRestore();
  });

  test('retries only the email when finalization succeeded but a definite delivery failure occurred', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    const finalizedQuote = { ...readyQuote, status: 'finalized', status_display: 'Finalized' };
    quotationAPI.quotes.retrieve.mockResolvedValue({ data: finalizedQuote });
    quotationAPI.quotes.retrieve
      .mockResolvedValueOnce({ data: readyQuote })
      .mockResolvedValueOnce({ data: readyQuote });
    quotationAPI.quotes.finalizeAndSend.mockRejectedValueOnce({
      response: {
        status: 503,
        data: {
          detail: 'Gmail temporarily rejected the message.',
          quote_finalized: true,
          retryable: true,
          delivery_status: 'failed',
        },
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.click((await screen.findAllByRole('button', { name: 'Finalize' }))[0]);
    const dialog = await screen.findByRole('dialog', { name: 'Finalize and send quotation' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Finalize & Send Quotation' }));

    expect(await screen.findByText('The quotation is finalized, but the email was not sent.')).toBeInTheDocument();
    const retryDialog = screen.getByRole('dialog', { name: 'Review and send quotation' });
    fireEvent.click(within(retryDialog).getByRole('button', { name: 'Send Quotation' }));

    await waitFor(() => expect(quotationAPI.quotes.sendEmail).toHaveBeenCalledTimes(1));
    expect(quotationAPI.quotes.finalizeAndSend).toHaveBeenCalledTimes(1);
    expect(quotationAPI.quotes.finalize).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });

  test('does not offer a resend when Gmail delivery status is unknown', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    const finalizedQuote = { ...readyQuote, status: 'finalized', status_display: 'Finalized' };
    quotationAPI.quotes.retrieve.mockResolvedValue({ data: finalizedQuote });
    quotationAPI.quotes.retrieve
      .mockResolvedValueOnce({ data: readyQuote })
      .mockResolvedValueOnce({ data: readyQuote });
    quotationAPI.quotes.finalizeAndSend.mockRejectedValueOnce({
      response: {
        status: 504,
        data: {
          detail: 'Gmail delivery acknowledgement timed out.',
          quote_finalized: true,
          retryable: false,
          delivery_status: 'unknown',
        },
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.click((await screen.findAllByRole('button', { name: 'Finalize' }))[0]);
    const dialog = await screen.findByRole('dialog', { name: 'Finalize and send quotation' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Finalize & Send Quotation' }));

    expect(await screen.findByText(/Check the Sent mailbox/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sending disabled' })).toBeDisabled();
    expect(quotationAPI.quotes.sendEmail).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });

  test('reconciles a reopened unknown delivery as sent without invoking a send endpoint', async () => {
    const finalizedQuote = { ...readyQuote, status: 'finalized', status_display: 'Finalized' };
    const sentQuote = { ...readyQuote, status: 'sent', status_display: 'Sent' };
    quotationAPI.quotes.retrieve.mockResolvedValue({ data: finalizedQuote });
    quotationAPI.quotes.emailPreview.mockResolvedValueOnce({
      data: {
        delivery_mode: 'gmail_reply',
        status: 'unknown',
        can_reconcile: true,
        to: ['buyer@example.com'],
        cc: [],
        subject: 'Re: RFQ',
        body: 'Please find attached our quotation.',
        attachment_filename: 'CUSTOMER-Q-0021.pdf',
      },
    });
    quotationAPI.quotes.reconcileEmail.mockResolvedValueOnce({
      data: {
        reconciled: true,
        detail: 'Gmail confirmed the previously attempted message.',
        quote: sentQuote,
        delivery: { status: 'sent' },
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Email Quotation' }));
    const dialog = await screen.findByRole('dialog', { name: 'Review and send quotation' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Check Gmail status' }));

    expect(await screen.findByText('Gmail confirmed the previously attempted message.')).toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: 'Review and send quotation' })).not.toBeInTheDocument();
    expect(quotationAPI.quotes.reconcileEmail).toHaveBeenCalledWith(21);
    expect(quotationAPI.quotes.finalizeAndSend).not.toHaveBeenCalled();
    expect(quotationAPI.quotes.sendEmail).not.toHaveBeenCalled();
  });

  test('keeps a reopened unknown delivery blocked when reconciliation finds no sent message', async () => {
    const finalizedQuote = { ...readyQuote, status: 'finalized', status_display: 'Finalized' };
    quotationAPI.quotes.retrieve.mockResolvedValue({ data: finalizedQuote });
    quotationAPI.quotes.emailPreview.mockResolvedValueOnce({
      data: {
        delivery_mode: 'gmail_reply',
        status: 'unknown',
        can_reconcile: true,
        to: ['buyer@example.com'],
        cc: [],
        subject: 'Re: RFQ',
        body: 'Please find attached our quotation.',
        attachment_filename: 'CUSTOMER-Q-0021.pdf',
      },
    });
    quotationAPI.quotes.reconcileEmail.mockResolvedValueOnce({
      data: {
        reconciled: false,
        detail: 'No matching sent message was found. Check again later.',
        quote: finalizedQuote,
        delivery: { status: 'unknown' },
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Email Quotation' }));
    let dialog = await screen.findByRole('dialog', { name: 'Review and send quotation' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Check Gmail status' }));

    expect(await screen.findByText('No matching sent message was found. Check again later.')).toBeInTheDocument();
    dialog = screen.getByRole('dialog', { name: 'Review and send quotation' });
    expect(within(dialog).getByRole('button', { name: 'Check Gmail before resending' })).toBeDisabled();
    expect(within(dialog).getByRole('button', { name: 'Check Gmail status' })).toBeEnabled();
    expect(quotationAPI.quotes.finalizeAndSend).not.toHaveBeenCalled();
    expect(quotationAPI.quotes.sendEmail).not.toHaveBeenCalled();
  });

  test('keeps a fresh persisted sending delivery blocked when reconciliation says it is still running', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    const finalizedQuote = { ...readyQuote, status: 'finalized', status_display: 'Finalized' };
    quotationAPI.quotes.retrieve.mockResolvedValue({ data: finalizedQuote });
    quotationAPI.quotes.emailPreview.mockResolvedValueOnce({
      data: {
        delivery_mode: 'gmail_reply',
        status: 'sending',
        can_reconcile: true,
        to: ['buyer@example.com'],
        cc: [],
        subject: 'Re: RFQ',
        body: 'Please find attached our quotation.',
        attachment_filename: 'CUSTOMER-Q-0021.pdf',
      },
    });
    quotationAPI.quotes.reconcileEmail.mockRejectedValueOnce({
      response: {
        status: 409,
        data: {
          detail: 'The Gmail delivery is still in progress. Check again later.',
          quote: finalizedQuote,
          delivery: { status: 'sending', can_reconcile: true },
        },
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Email Quotation' }));
    let dialog = await screen.findByRole('dialog', { name: 'Review and send quotation' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Check Gmail status' }));

    expect(await screen.findByText('The Gmail delivery is still in progress. Check again later.')).toBeInTheDocument();
    dialog = screen.getByRole('dialog', { name: 'Review and send quotation' });
    expect(within(dialog).getByRole('button', { name: 'Sending in progress' })).toBeDisabled();
    expect(within(dialog).getByRole('button', { name: 'Check Gmail status' })).toBeEnabled();
    expect(quotationAPI.quotes.sendEmail).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });

  test('shows the full catalog and ignores an older Product lookup that resolves last', async () => {
    const first = deferred();
    const second = deferred();
    quotationAPI.quotes.productPrice
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    const productSelect = await screen.findByLabelText('Product for Imported gloves');
    expect(within(productSelect).getByRole('option', { name: 'Gloves B' })).toBeInTheDocument();

    fireEvent.change(productSelect, { target: { value: '11' } });
    fireEvent.change(productSelect, { target: { value: '12' } });
    expect(screen.getByDisplayValue('Imported gloves')).toBeInTheDocument();

    await act(async () => second.resolve({ data: priceContext(12, 'Gloves B', 22) }));
    const priceInput = await screen.findByLabelText('Unit price for Imported gloves');
    await waitFor(() => expect(priceInput).toHaveValue(22));
    expect(within(screen.getByRole('dialog', { name: /price history/i })).getByText(/Gloves B/)).toBeInTheDocument();

    await act(async () => first.resolve({ data: priceContext(11, 'Gloves A', 10) }));
    expect(priceInput).toHaveValue(22);
    expect(within(screen.getByRole('dialog', { name: /price history/i })).getByText(/Gloves B/)).toBeInTheDocument();
  });

  test('reviews and saves exact ordered line mappings when confirming a manual LPO', async () => {
    const sentQuote = {
      ...quote,
      status: 'sent',
      status_display: 'Sent',
      lines: [{ ...quote.lines[0], match_status: 'confirmed' }],
    };
    const parsedLpo = {
      id: 91,
      lpo_number: 'LPO-MANUAL-77',
      lpo_date: '2026-07-15',
      notes: '',
      status: 'parsed',
      status_display: 'Parsed',
      source_filename: 'LPO-MANUAL-77.pdf',
      source_type_display: 'File',
      parsed_row_count: 1,
      received_at: '2026-07-15T08:00:00Z',
      warnings: [],
      parsed_meta: {
        outcome_suggestions: [{ quotation_line_id: 31 }],
      },
    };
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({ data: sentQuote });
    quotationAPI.quotes.lpos.mockResolvedValueOnce({ data: [parsedLpo] });
    quotationAPI.lpos.update.mockResolvedValueOnce({
      data: {
        ...parsedLpo,
        status: 'confirmed',
        status_display: 'Confirmed',
        parsed_meta: {
          ...parsedLpo.parsed_meta,
          applied_outcome_line_ids: [31],
        },
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    const mappingPanel = (await screen.findByText('Ordered quotation lines')).closest('.qm-lpo-warning');
    expect(within(mappingPanel).getByRole('checkbox')).toBeChecked();
    const detailsCard = screen.getByText('Review detected details').closest('.qm-lpo-card');
    fireEvent.change(within(detailsCard).getByLabelText('Status'), { target: { value: 'confirmed' } });
    fireEvent.click(within(detailsCard).getByRole('button', { name: /save lpo details/i }));

    await waitFor(() => expect(quotationAPI.lpos.update).toHaveBeenCalledWith(91, {
      lpo_number: 'LPO-MANUAL-77',
      lpo_date: '2026-07-15',
      notes: '',
      status: 'confirmed',
      applied_outcome_line_ids: [31],
    }));
  });

  test('keeps every attachment warning reviewable when an LPO has more than three', async () => {
    const sentQuote = {
      ...readyQuote,
      status: 'sent',
      status_display: 'Sent',
    };
    const warnings = [
      'Workbook contains formula cells; verify quantities and prices.',
      'Workbook contains hidden sheets, rows, or columns.',
      'Workbook contains merged cells; verify extracted row alignment.',
      "Stopped reading sheet 'Items' after 500 rows.",
      "Stopped reading columns in sheet 'Items' after 100 columns.",
      'Workbook has more than 10 visible sheets; later visible sheets were not parsed.',
      'Workbook contains explicit date cells; verify item codes.',
    ];
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({ data: sentQuote });
    quotationAPI.quotes.lpos.mockResolvedValueOnce({
      data: [{
        id: 92,
        lpo_number: 'LPO-WARN-92',
        lpo_date: '2026-07-31',
        notes: '',
        status: 'needs_review',
        status_display: 'Needs review',
        source_filename: 'warning-source.xlsx',
        source_type_display: 'File',
        parsed_row_count: 1,
        received_at: '2026-07-31T08:00:00Z',
        warnings,
        parsed_meta: {},
      }],
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    const warningPanel = await screen.findByRole('alert', { name: 'LPO attachment warnings' });
    expect(within(warningPanel).getByText(warnings[0])).toBeVisible();
    expect(within(warningPanel).getByText(warnings[2])).toBeVisible();
    // Material completeness warnings stay visible even when they occur after
    // the original three-warning display limit.
    expect(within(warningPanel).getByText(warnings[3])).toBeVisible();
    expect(within(warningPanel).getByText(warnings[4])).toBeVisible();
    expect(within(warningPanel).getByText(warnings[5])).toBeVisible();
    const expansion = within(warningPanel).getByText('Show 1 more warning');
    fireEvent.click(expansion);
    expect(within(warningPanel).getByText(warnings[6])).toBeVisible();
  });

  test('never overwrites a price typed while history is loading', async () => {
    const request = deferred();
    quotationAPI.quotes.productPrice.mockImplementationOnce(() => request.promise);

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.change(await screen.findByLabelText('Product for Imported gloves'), { target: { value: '11' } });
    expect(screen.getByDisplayValue('Imported gloves')).toBeInTheDocument();
    const priceInput = await screen.findByLabelText('Unit price for Imported gloves');
    fireEvent.change(priceInput, { target: { value: '73' } });

    await act(async () => request.resolve({ data: priceContext(11, 'Gloves A', 10) }));
    await waitFor(() => expect(priceInput).toHaveValue(73));
    expect(screen.getByText(/current price kept/i)).toBeInTheDocument();
    expect(screen.getByRole('dialog', { name: /price history/i })).toBeInTheDocument();
  });

  test('does not change existing or new quotation quantities and prices when the mouse wheel scrolls', async () => {
    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    const priceInput = await screen.findByLabelText('Unit price for Imported gloves');
    const quantityInput = screen.getByLabelText('Quantity for Imported gloves');
    fireEvent.change(quantityInput, { target: { value: '4.5' } });
    quantityInput.focus();
    fireEvent.wheel(quantityInput, { deltaY: 100 });

    expect(document.activeElement).not.toBe(quantityInput);
    expect(quantityInput).toHaveValue(4.5);

    fireEvent.change(priceInput, { target: { value: '12.5' } });
    priceInput.focus();
    fireEvent.wheel(priceInput, { deltaY: 100 });

    expect(document.activeElement).not.toBe(priceInput);
    expect(priceInput).toHaveValue(12.5);

    const newLineQuantity = screen.getByRole('spinbutton', { name: 'Qty' });
    fireEvent.change(newLineQuantity, { target: { value: '8' } });
    newLineQuantity.focus();
    fireEvent.wheel(newLineQuantity, { deltaY: 100 });

    expect(document.activeElement).not.toBe(newLineQuantity);
    expect(newLineQuantity).toHaveValue(8);
  });

  test('warns about a similar Product and only creates after an explicit override', async () => {
    quotationAPI.lines.createProduct.mockRejectedValue({
      response: {
        status: 409,
        data: {
          detail: 'A similar Product exists.',
          warning: 'A similar Product exists.',
          requires_confirmation: true,
          creation_blocked: false,
          candidates: [{
            product_id: 11,
            product_name: 'Gloves A',
            confidence: 0.92,
            pack_size: 'box',
          }],
        },
      },
    });
    quotationAPI.quotes.bulkCreateProductsForLines.mockResolvedValue({
      data: {
        updated_lines: [{ ...quote.lines[0], product: 13, product_name: 'Imported gloves', match_status: 'confirmed' }],
        confirmation_required: [],
        message: 'Created and linked one Product.',
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.change(await screen.findByLabelText('Product for Imported gloves'), { target: { value: '__create__' } });

    expect(await screen.findByText('Likely existing Product found')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Use Gloves A/i })).toBeInTheDocument();
    const override = screen.getByRole('button', { name: /Create new Product anyway/i });
    await waitFor(() => expect(override).toBeEnabled());
    fireEvent.click(override);

    await waitFor(() => expect(quotationAPI.quotes.bulkCreateProductsForLines).toHaveBeenCalledWith(21, {
      line_ids: [31],
      names: { 31: 'Imported gloves' },
      confirm_create_line_ids: [31],
    }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /create products/i })).not.toBeInTheDocument());
  });

  test('links a suggested Product without replacing the source snapshot or writing the alias separately', async () => {
    quotationAPI.lines.createProduct.mockRejectedValue({
      response: {
        status: 409,
        data: {
          detail: 'A similar Product exists.',
          warning: 'A similar Product exists.',
          requires_confirmation: true,
          creation_blocked: false,
          candidates: [{
            product_id: 11,
            product_name: 'Gloves A',
            confidence: 0.92,
            pack_size: 'box',
          }],
        },
      },
    });
    quotationAPI.quotes.bulkUpdateLines.mockResolvedValue({
      data: {
        quotation: {
          ...quote,
          lines: [{
            ...quote.lines[0],
            product: 11,
            product_name: 'Gloves A',
            item_name_snapshot: 'Imported gloves',
            match_status: 'confirmed',
          }],
        },
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.change(await screen.findByLabelText('Product for Imported gloves'), { target: { value: '__create__' } });
    fireEvent.click(await screen.findByRole('button', { name: /Use Gloves A/i }));

    await waitFor(() => expect(quotationAPI.quotes.bulkUpdateLines).toHaveBeenCalledWith(21, {
      lines: [expect.objectContaining({
        id: 31,
        product: '11',
        item_name_snapshot: 'Imported gloves',
        match_status: 'confirmed',
      })],
    }));
    expect(quotationAPI.lines.rememberAlias).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('dialog', { name: /create products/i })).not.toBeInTheDocument());
    expect(screen.getByDisplayValue('Imported gloves')).toBeInTheDocument();
  });

  test('shows bulk Product creation errors inside the open modal', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    quotationAPI.quotes.bulkCreateProductsForLines.mockRejectedValue({
      message: 'A retired alias blocked this Product name.',
      response: {
        status: 400,
        data: { detail: 'A retired alias blocked this Product name.' },
      },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Select visible unmatched' }));
    fireEvent.click(screen.getByRole('button', { name: 'Create Products for Selected Unmatched Rows' }));
    const dialog = await screen.findByRole('dialog', { name: 'Create Products from quotation lines' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Check catalog and continue' }));

    expect(await within(dialog).findByText('A retired alias blocked this Product name.')).toBeInTheDocument();
    expect(within(dialog).getByRole('alert')).toBeInTheDocument();
    expect(screen.getAllByRole('alert')).toHaveLength(1);
    expect(dialog).toBeInTheDocument();
    expect(consoleError).toHaveBeenCalled();
    consoleError.mockRestore();
  });

  test('keeps the create modal stable while a catalog check is pending', async () => {
    const request = deferred();
    quotationAPI.quotes.bulkCreateProductsForLines.mockImplementationOnce(() => request.promise);

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Select visible unmatched' }));
    fireEvent.click(screen.getByRole('button', { name: 'Create Products for Selected Unmatched Rows' }));
    const dialog = await screen.findByRole('dialog', { name: 'Create Products from quotation lines' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Check catalog and continue' }));

    expect(within(dialog).getByRole('button', { name: 'Close' })).toBeDisabled();
    expect(within(dialog).getByDisplayValue('Imported gloves')).toBeDisabled();

    await act(async () => request.resolve({
      data: {
        updated_lines: [],
        confirmation_required: [{
          line_id: 31,
          warning: 'A similar Product exists.',
          creation_blocked: false,
          candidates: [],
        }],
      },
    }));

    expect(await within(dialog).findByText('Likely existing Product found')).toBeInTheDocument();
    expect(within(dialog).getByDisplayValue('Imported gloves')).toBeEnabled();
  });

  test('automatically retries a transient quotation 500 without closing the editor', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    quotationAPI.quotes.retrieve
      .mockRejectedValueOnce({
        response: { status: 500, data: { detail: 'Temporary database connection failure.' } },
        config: { url: '/quotations/quotes/21/' },
      })
      .mockResolvedValueOnce({ data: quote });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    expect(await screen.findByText('Q-0021')).toBeInTheDocument();
    expect(quotationAPI.quotes.retrieve).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    consoleError.mockRestore();
  });

  test('does not automatically retry a non-transient quote error and offers an in-place retry', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    quotationAPI.quotes.retrieve.mockRejectedValueOnce({
      response: { status: 404, data: { detail: 'Quotation was not found.' } },
      config: { url: '/quotations/quotes/21/' },
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    const retryButton = await screen.findByRole('button', { name: 'Retry quotation' });
    expect(quotationAPI.quotes.retrieve).toHaveBeenCalledTimes(1);
    expect(screen.getByText('The quotation could not be loaded. Retry here without closing the editor.')).toBeInTheDocument();

    quotationAPI.quotes.retrieve.mockResolvedValueOnce({ data: quote });
    fireEvent.click(retryButton);

    expect(await screen.findByText('Q-0021')).toBeInTheDocument();
    expect(quotationAPI.quotes.retrieve).toHaveBeenCalledTimes(2);
    consoleError.mockRestore();
  });

  test('keeps the quotation visible and retries in place when the Product catalogue stays unavailable', async () => {
    const failedCatalogueRequest = {
      response: { status: 500, data: { detail: 'SSL connection has been closed unexpectedly.' } },
      config: { url: '/quotations/items/' },
    };
    let fullCatalogueAttempts = 0;
    quotationAPI.items.list.mockImplementation((params) => {
      if (params?.company_used) return Promise.resolve({ data: [products[0]] });
      fullCatalogueAttempts += 1;
      return Promise.reject(failedCatalogueRequest);
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    expect(await screen.findByText('Q-0021')).toBeInTheDocument();
    expect(await screen.findByText(/supporting data is temporarily unavailable/i)).toBeInTheDocument();
    expect(fullCatalogueAttempts).toBe(2);
    expect(screen.getByText('GET /quotations/items/?active=true')).toBeInTheDocument();
    expect(screen.getByLabelText('Product for Imported gloves')).toBeDisabled();
    const quantityInput = screen.getByLabelText('Quantity for Imported gloves');
    expect(quantityInput).toBeEnabled();
    fireEvent.change(quantityInput, { target: { value: '7.5' } });

    quotationAPI.items.list.mockImplementation((params) => Promise.resolve({
      data: params?.company_used ? [products[0]] : products,
    }));
    fireEvent.click(screen.getByRole('button', { name: 'Retry missing data' }));

    expect(await screen.findByRole('button', { name: 'Retrying missing data...' })).toBeDisabled();
    expect(screen.queryByText('Loading quotation...')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/supporting data is temporarily unavailable/i)).not.toBeInTheDocument());
    expect(await screen.findByLabelText('Product for Imported gloves')).toBeEnabled();
    expect(screen.getByLabelText('Quantity for Imported gloves')).toHaveValue(7.5);
    expect(quotationAPI.quotes.retrieve).toHaveBeenCalledTimes(1);
  });

  test('a supporting-data retry does not cancel an in-flight price-history preview', async () => {
    const pricedQuote = {
      ...quote,
      lines: [{
        ...quote.lines[0],
        product: 11,
        match_status: 'confirmed',
      }],
    };
    const priceHistoryRequest = deferred();
    const failedCatalogueRequest = {
      response: { status: 500, data: { detail: 'Temporary catalogue failure.' } },
      config: { url: '/quotations/items/' },
    };
    quotationAPI.quotes.retrieve.mockResolvedValue({ data: pricedQuote });
    quotationAPI.quotes.productPrices.mockReturnValue(priceHistoryRequest.promise);
    quotationAPI.items.list.mockImplementation((params) => (
      params?.company_used
        ? Promise.resolve({ data: [products[0]] })
        : Promise.reject(failedCatalogueRequest)
    ));

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    expect(await screen.findByText(/supporting data is temporarily unavailable/i)).toBeInTheDocument();
    quotationAPI.items.list.mockImplementation((params) => Promise.resolve({
      data: params?.company_used ? [products[0]] : products,
    }));
    fireEvent.click(screen.getByRole('button', { name: 'Retry missing data' }));
    await waitFor(() => expect(screen.queryByText(/supporting data is temporarily unavailable/i)).not.toBeInTheDocument());

    await act(async () => priceHistoryRequest.resolve({
      data: { results: { 11: priceContext(11, 'Gloves A', 10) } },
    }));

    expect(await screen.findByText(/Last quoted AED 10/)).toBeInTheDocument();
    expect(quotationAPI.quotes.productPrices).toHaveBeenCalledTimes(1);
  });

  test('ignores contacts returned for an older company selection', async () => {
    const oldCompanyContacts = deferred();
    const currentCompanyContacts = deferred();
    let contactRequestCount = 0;
    quotationAPI.companies.list.mockResolvedValue({
      data: [
        { id: 7, name: 'Customer A' },
        { id: 8, name: 'Customer B' },
      ],
    });
    quotationAPI.contacts.list.mockImplementation(({ company }) => {
      contactRequestCount += 1;
      if (contactRequestCount === 1) return Promise.resolve({ data: [] });
      return String(company) === '8'
        ? oldCompanyContacts.promise
        : currentCompanyContacts.promise;
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    const companyOption = await screen.findByRole('option', { name: 'Customer B' });
    const companySelect = companyOption.closest('select');
    fireEvent.change(companySelect, { target: { value: '8' } });
    fireEvent.change(companySelect, { target: { value: '7' } });

    await act(async () => currentCompanyContacts.resolve({
      data: [{ id: 71, company: 7, name: 'Buyer A' }],
    }));
    expect(await screen.findByRole('option', { name: 'Buyer A' })).toBeInTheDocument();

    await act(async () => oldCompanyContacts.resolve({
      data: [{ id: 81, company: 8, name: 'Buyer B' }],
    }));
    expect(screen.getByRole('option', { name: 'Buyer A' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'Buyer B' })).not.toBeInTheDocument();
  });

  test('ignores a late quotation response after the editor switches to another quote', async () => {
    const oldRequest = deferred();
    const nextQuote = {
      ...quote,
      id: 22,
      quotation_number: 'Q-0022',
    };
    quotationAPI.quotes.retrieve.mockImplementation((id) => (
      Number(id) === 21 ? oldRequest.promise : Promise.resolve({ data: nextQuote })
    ));

    const { rerender } = render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    rerender(<QuotationEditor quoteId={22} onClose={jest.fn()} />);

    expect(await screen.findByText('Q-0022')).toBeInTheDocument();
    await act(async () => oldRequest.resolve({ data: quote }));

    expect(screen.getByText('Q-0022')).toBeInTheDocument();
    expect(screen.queryByText('Q-0021')).not.toBeInTheDocument();
  });

  test('renders the progressive editor shell before supporting datasets and unlocks each dependent control independently', async () => {
    const fullCatalogue = deferred();
    const companyCatalogue = deferred();
    const companiesRequest = deferred();
    const contactsRequest = deferred();
    const lposRequest = deferred();
    const historyRequest = deferred();
    const progressiveQuote = withProgressiveLoad({
      ...readyQuote,
      status: 'approved',
      status_display: 'Approved',
      contact: 71,
      contact_name: 'Buyer A',
      lines: [{ ...readyQuote.lines[0], unit_price: '' }],
    });
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({ data: progressiveQuote });
    quotationAPI.items.list.mockImplementation((params) => (
      params?.company_used ? companyCatalogue.promise : fullCatalogue.promise
    ));
    quotationAPI.companies.list.mockReturnValue(companiesRequest.promise);
    quotationAPI.contacts.list.mockReturnValue(contactsRequest.promise);
    quotationAPI.quotes.lpos.mockReturnValue(lposRequest.promise);
    quotationAPI.quotes.productPrices.mockReturnValue(historyRequest.promise);

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    expect(await screen.findByText('Q-0021')).toBeInTheDocument();
    const priceInput = screen.getByLabelText('Unit price for Imported gloves');
    const quantityInput = screen.getByLabelText('Quantity for Imported gloves');
    const productSelect = screen.getByLabelText('Product for Imported gloves');
    const companySelect = screen.getByRole('option', { name: 'Customer A' }).closest('select');
    const contactSelect = screen.getByRole('option', { name: 'Buyer A' }).closest('select');

    expect(priceInput).toBeEnabled();
    expect(quantityInput).toBeEnabled();
    expect(productSelect).toBeDisabled();
    expect(companySelect).toBeDisabled();
    expect(contactSelect).toBeDisabled();
    expect(within(productSelect).getByRole('option', { name: 'Gloves A' })).toBeInTheDocument();
    expect(screen.getByText('Product catalogue and images: Loading')).toBeInTheDocument();
    expect(screen.getByText('Company directory: Loading')).toBeInTheDocument();
    expect(screen.getByText('Company contacts: Loading')).toBeInTheDocument();
    expect(screen.getByText('Loading LPO records...')).toBeInTheDocument();
    expect(screen.queryByText('No LPO recorded')).not.toBeInTheDocument();
    await waitFor(() => expect(document.activeElement).toBe(priceInput));

    await act(async () => companiesRequest.resolve({
      data: [{ id: 7, name: 'Customer A' }, { id: 8, name: 'Customer B' }],
    }));
    await waitFor(() => expect(companySelect).toBeEnabled());
    expect(productSelect).toBeDisabled();
    expect(contactSelect).toBeDisabled();

    await act(async () => fullCatalogue.resolve({ data: products }));
    await waitFor(() => expect(productSelect).toBeEnabled());
    expect(contactSelect).toBeDisabled();

    await act(async () => contactsRequest.resolve({
      data: [{ id: 71, company: 7, name: 'Buyer A' }],
    }));
    await waitFor(() => expect(contactSelect).toBeEnabled());

    await act(async () => lposRequest.resolve({ data: [] }));
    expect(await screen.findByText('No LPO recorded')).toBeInTheDocument();

    await act(async () => {
      companyCatalogue.resolve({ data: [products[0]] });
      historyRequest.resolve({ data: { results: { 11: priceContext(11, 'Gloves A', 10) } } });
    });
    await waitFor(() => expect(screen.queryByLabelText('Supporting quotation data status')).not.toBeInTheDocument());
  });

  test('keeps progressive selling prices blank and history review on demand for existing and new lines', async () => {
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({ data: withProgressiveLoad(quote) });
    quotationAPI.quotes.productPrice.mockResolvedValueOnce({
      data: priceContext(11, 'Gloves A', 10),
    });

    const { container } = render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    const existingProduct = await screen.findByLabelText('Product for Imported gloves');
    fireEvent.change(existingProduct, { target: { value: '11' } });

    const existingPrice = screen.getByLabelText('Unit price for Imported gloves');
    await waitFor(() => expect(screen.getByText(/Last quoted AED 10/)).toBeInTheDocument());
    expect(existingPrice).toHaveValue(null);
    expect(screen.queryByRole('dialog', { name: /price history/i })).not.toBeInTheDocument();

    const newLineProduct = container.querySelector('.qm-add-line select');
    fireEvent.change(newLineProduct, { target: { value: '12' } });
    expect(screen.getByPlaceholderText('Price')).toHaveValue(null);
    expect(quotationAPI.quotes.productPrice).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog', { name: /price history/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'View price history' }));
    expect(screen.getByRole('dialog', { name: /price history/i })).toBeInTheDocument();
  });

  test('does not let the initial progressive history batch overwrite a newer Product hint', async () => {
    const catalogueRequest = deferred();
    const initialHistoryRequest = deferred();
    const progressiveQuote = withProgressiveLoad({
      ...readyQuote,
      lines: [{ ...readyQuote.lines[0], unit_price: '' }],
    });
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({ data: progressiveQuote });
    quotationAPI.items.list.mockImplementation((params) => (
      params?.company_used
        ? Promise.resolve({ data: [products[0]] })
        : catalogueRequest.promise
    ));
    quotationAPI.quotes.productPrices.mockReturnValue(initialHistoryRequest.promise);
    quotationAPI.quotes.productPrice.mockResolvedValueOnce({
      data: priceContext(12, 'Gloves B', 22),
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    const productSelect = await screen.findByLabelText('Product for Imported gloves');
    expect(productSelect).toBeDisabled();
    await act(async () => catalogueRequest.resolve({ data: products }));
    await waitFor(() => expect(productSelect).toBeEnabled());

    fireEvent.change(productSelect, { target: { value: '12' } });
    expect(await screen.findByText(/Last quoted AED 22/)).toBeInTheDocument();

    await act(async () => initialHistoryRequest.resolve({
      data: { results: { 11: priceContext(11, 'Gloves A', 10) } },
    }));

    expect(screen.getByText(/Last quoted AED 22/)).toBeInTheDocument();
    expect(screen.queryByText(/Last quoted AED 10/)).not.toBeInTheDocument();
    expect(screen.getByLabelText('Unit price for Imported gloves')).toHaveValue(null);

    fireEvent.click(screen.getByRole('button', { name: 'View price history' }));
    const dialog = screen.getByRole('dialog', { name: /price history/i });
    expect(within(dialog).getByText(/Gloves B/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/Gloves A/)).not.toBeInTheDocument();
  });

  test('does not cache a Product price response after switching to another quotation', async () => {
    const quoteAHistory = deferred();
    const quoteB = {
      ...quote,
      id: 22,
      quotation_number: 'Q-0022',
      company: 8,
      company_name: 'Customer B',
      lines: [{
        ...quote.lines[0],
        id: 41,
        item_name_snapshot: 'Imported masks',
      }],
    };
    quotationAPI.quotes.retrieve.mockImplementation((id) => Promise.resolve({
      data: Number(id) === 21 ? quote : quoteB,
    }));
    quotationAPI.quotes.productPrice.mockImplementation((id) => (
      Number(id) === 21
        ? quoteAHistory.promise
        : Promise.resolve({ data: priceContext(11, 'Company B Gloves', 30) })
    ));

    const { rerender } = render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.change(await screen.findByLabelText('Product for Imported gloves'), { target: { value: '11' } });

    rerender(<QuotationEditor quoteId={22} onClose={jest.fn()} />);
    expect(await screen.findByText('Q-0022')).toBeInTheDocument();
    const quoteBProduct = await screen.findByLabelText('Product for Imported masks');
    fireEvent.change(quoteBProduct, { target: { value: '11' } });

    const quoteBPrice = screen.getByLabelText('Unit price for Imported masks');
    await waitFor(() => expect(quoteBPrice).toHaveValue(30));
    let dialog = await screen.findByRole('dialog', { name: /price history/i });
    expect(within(dialog).getByText(/Company B Gloves/)).toBeInTheDocument();

    await act(async () => quoteAHistory.resolve({
      data: priceContext(11, 'Company A Gloves', 10),
    }));
    expect(quoteBPrice).toHaveValue(30);
    expect(within(dialog).queryByText(/Company A Gloves/)).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Close' }));
    fireEvent.change(quoteBProduct, { target: { value: '' } });
    fireEvent.change(quoteBPrice, { target: { value: '' } });
    fireEvent.change(quoteBProduct, { target: { value: '11' } });

    await waitFor(() => expect(quoteBPrice).toHaveValue(30));
    dialog = await screen.findByRole('dialog', { name: /price history/i });
    expect(within(dialog).getByText(/Company B Gloves/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/Company A Gloves/)).not.toBeInTheDocument();
    expect(quotationAPI.quotes.productPrice).toHaveBeenCalledTimes(2);
  });

  test('requires the progressive feature flag to be the exact boolean true', async () => {
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({
      data: {
        ...quote,
        workflow_features: { quotation_editor_progressive_load: 'true' },
      },
    });
    quotationAPI.quotes.productPrice.mockResolvedValueOnce({
      data: priceContext(11, 'Gloves A', 10),
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    fireEvent.change(await screen.findByLabelText('Product for Imported gloves'), { target: { value: '11' } });

    await waitFor(() => expect(screen.getByLabelText('Unit price for Imported gloves')).toHaveValue(10));
    expect(screen.getByRole('dialog', { name: /price history/i })).toBeInTheDocument();
    expect(screen.queryByLabelText('Supporting quotation data status')).not.toBeInTheDocument();
  });

  test('focuses the first blank price once and advances only to later rendered blank prices', async () => {
    const line = (id, sortOrder, name, unitPrice, matchStatus = 'confirmed') => ({
      ...readyQuote.lines[0],
      id,
      sort_order: sortOrder,
      item_name_snapshot: name,
      product_name: name,
      unit_price: unitPrice,
      match_status: matchStatus,
    });
    const progressiveQuote = withProgressiveLoad({
      ...readyQuote,
      lines: [
        line(31, 0, 'First blank', ''),
        line(32, 1, 'Filled price', '4.00'),
        line(33, 2, 'Second blank', ''),
        line(34, 3, 'Last blank', ''),
        line(35, 4, 'Skipped blank', '', 'ignored'),
      ],
    });
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({ data: progressiveQuote });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    const first = await screen.findByLabelText('Unit price for First blank');
    const second = screen.getByLabelText('Unit price for Second blank');
    const last = screen.getByLabelText('Unit price for Last blank');
    await waitFor(() => expect(document.activeElement).toBe(first));

    const forwardTab = createEvent.keyDown(first, { key: 'Tab' });
    fireEvent(first, forwardTab);
    expect(forwardTab.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(second);

    const reverseTab = createEvent.keyDown(second, { key: 'Tab', shiftKey: true });
    fireEvent(second, reverseTab);
    expect(reverseTab.defaultPrevented).toBe(false);
    expect(document.activeElement).toBe(second);

    const enter = createEvent.keyDown(second, { key: 'Enter' });
    fireEvent(second, enter);
    expect(enter.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(last);

    const finalTab = createEvent.keyDown(last, { key: 'Tab' });
    fireEvent(last, finalTab);
    expect(finalTab.defaultPrevented).toBe(false);

    const quantity = screen.getByLabelText('Quantity for First blank');
    quantity.focus();
    fireEvent.change(screen.getByDisplayValue('Active lines'), { target: { value: 'all' } });
    expect(document.activeElement).toBe(quantity);
  });

  test('focuses a new quotation once its blank row becomes visible through the persisted filter', async () => {
    const quoteA = withProgressiveLoad(readyQuote);
    const quoteB = withProgressiveLoad({
      ...quote,
      id: 22,
      quotation_number: 'Q-0022',
      lines: [{
        ...quote.lines[0],
        id: 41,
        item_name_snapshot: 'Hidden blank row',
      }],
    });
    quotationAPI.quotes.retrieve.mockImplementation((id) => Promise.resolve({
      data: Number(id) === 21 ? quoteA : quoteB,
    }));

    const { rerender } = render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);
    const lineFilter = await screen.findByDisplayValue('Active lines');
    fireEvent.change(lineFilter, { target: { value: 'ready' } });
    expect(lineFilter).toHaveValue('ready');

    rerender(<QuotationEditor quoteId={22} onClose={jest.fn()} />);
    expect(await screen.findByText('Q-0022')).toBeInTheDocument();
    expect(screen.queryByLabelText('Unit price for Hidden blank row')).not.toBeInTheDocument();

    const persistedFilter = screen.getByDisplayValue('Ready');
    fireEvent.change(persistedFilter, { target: { value: 'active' } });
    const blankPrice = await screen.findByLabelText('Unit price for Hidden blank row');
    await waitFor(() => expect(document.activeElement).toBe(blankPrice));
  });

  test('does not focus a blank price when the progressive quotation is locked', async () => {
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({
      data: withProgressiveLoad({
        ...quote,
        status: 'finalized',
        status_display: 'Finalized',
      }),
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    const priceInput = await screen.findByLabelText('Unit price for Imported gloves');
    expect(priceInput).toBeDisabled();
    expect(document.activeElement).not.toBe(priceInput);
  });

  test('keeps an unrelated initial dataset current while retrying one failed progressive dataset', async () => {
    const companiesRequest = deferred();
    const catalogueRetry = deferred();
    const catalogueFailure = {
      response: { status: 500, data: { detail: 'Temporary catalogue failure.' } },
      config: { url: '/quotations/items/' },
    };
    let fullCatalogueAttempts = 0;
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({ data: withProgressiveLoad(quote) });
    quotationAPI.companies.list.mockReturnValue(companiesRequest.promise);
    quotationAPI.items.list.mockImplementation((params) => {
      if (params?.company_used) return Promise.resolve({ data: [products[0]] });
      fullCatalogueAttempts += 1;
      if (fullCatalogueAttempts <= 2) return Promise.reject(catalogueFailure);
      return catalogueRetry.promise;
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    expect(await screen.findByText(/supporting data is temporarily unavailable/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry missing data' }));
    expect(await screen.findByRole('button', { name: 'Retrying missing data...' })).toBeDisabled();

    await act(async () => companiesRequest.resolve({
      data: [{ id: 7, name: 'Customer A' }, { id: 8, name: 'Customer B' }],
    }));
    expect(await screen.findByRole('option', { name: 'Customer B' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Customer B' }).closest('select')).toBeEnabled();

    await act(async () => catalogueRetry.resolve({ data: products }));
    await waitFor(() => expect(screen.queryByText(/supporting data is temporarily unavailable/i)).not.toBeInTheDocument());
    expect(screen.getByLabelText('Product for Imported gloves')).toBeEnabled();
  });

  test('marks progressive contacts unavailable after a company-switch failure and recovers with targeted retry', async () => {
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    let companyBContactAttempts = 0;
    quotationAPI.quotes.retrieve.mockResolvedValueOnce({ data: withProgressiveLoad(quote) });
    quotationAPI.companies.list.mockResolvedValueOnce({
      data: [{ id: 7, name: 'Customer A' }, { id: 8, name: 'Customer B' }],
    });
    quotationAPI.contacts.list.mockImplementation(({ company }) => {
      if (String(company) === '7') return Promise.resolve({ data: [] });
      companyBContactAttempts += 1;
      if (companyBContactAttempts === 1) {
        return Promise.reject({
          response: { status: 400, data: { detail: 'Contacts unavailable.' } },
          config: { url: '/quotations/contacts/?company=8' },
        });
      }
      return Promise.resolve({ data: [{ id: 81, company: 8, name: 'Buyer B' }] });
    });

    render(<QuotationEditor quoteId={21} onClose={jest.fn()} />);

    const companyBOption = await screen.findByRole('option', { name: 'Customer B' });
    fireEvent.change(companyBOption.closest('select'), { target: { value: '8' } });

    expect(await screen.findByText('Company contacts: Unavailable')).toBeInTheDocument();
    const contactSelect = screen.getByLabelText('Contact / Purchaser');
    expect(contactSelect).toBeDisabled();
    expect(screen.getByText(/supporting data is temporarily unavailable/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Retry missing data' }));

    expect(await screen.findByRole('option', { name: 'Buyer B' })).toBeInTheDocument();
    await waitFor(() => expect(contactSelect).toBeEnabled());
    expect(screen.queryByText('Company contacts: Unavailable')).not.toBeInTheDocument();
    expect(screen.queryByText(/supporting data is temporarily unavailable/i)).not.toBeInTheDocument();
    expect(companyBContactAttempts).toBe(2);
    consoleError.mockRestore();
  });
});
