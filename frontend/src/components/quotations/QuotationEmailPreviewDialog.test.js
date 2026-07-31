import { fireEvent, render, screen, within } from '@testing-library/react';
import QuotationEmailPreviewDialog, {
  normalizeQuotationEmailPreview,
  splitEmailAddresses,
} from './QuotationEmailPreviewDialog';

const gmailPreview = {
  delivery_mode: 'gmail_reply',
  can_reply_to_thread: true,
  trusted_source: {
    sender_name: 'Maria Buyer',
    sender_email: 'maria@example.com',
    subject: 'RFQ - First aid supplies',
    received_at: '31 Jul 2026, 10:30',
  },
  thread: { id: 'thread-1', subject: 'RFQ - First aid supplies' },
  to: ['maria@example.com'],
  cc: [],
  subject: 'Re: RFQ - First aid supplies',
  body: 'Dear Maria,\n\nPlease find attached our quotation.',
  attachment_filename: 'CUSTOMER-QT-20260731-0001.pdf',
};

const defaultProps = {
  preview: gmailPreview,
  onRetryPreview: jest.fn(),
  onClose: jest.fn(),
  onFinalizeOnly: jest.fn(),
  onSend: jest.fn(),
};

describe('QuotationEmailPreviewDialog', () => {
  beforeEach(() => jest.clearAllMocks());

  test('normalizes API address strings without putting message data in a URL', () => {
    expect(splitEmailAddresses('buyer@example.com; accounts@example.com\nmanager@example.com')).toEqual([
      'buyer@example.com',
      'accounts@example.com',
      'manager@example.com',
    ]);
    expect(normalizeQuotationEmailPreview({
      can_reply_to_thread: true,
      to: 'buyer@example.com',
      cc: 'accounts@example.com, manager@example.com',
    })).toMatchObject({
      delivery_mode: 'gmail_reply',
      to: ['buyer@example.com'],
      cc: ['accounts@example.com', 'manager@example.com'],
    });
  });

  test('locks the verified Gmail recipient and thread subject but keeps CC and body editable', () => {
    render(<QuotationEmailPreviewDialog {...defaultProps} />);

    expect(screen.getByText('Replying in the verified Gmail thread')).toBeInTheDocument();
    expect(screen.getByText('Verified reply')).toBeInTheDocument();
    expect(screen.getByDisplayValue('maria@example.com')).toHaveAttribute('readonly');
    expect(screen.getByDisplayValue('Re: RFQ - First aid supplies')).toHaveAttribute('readonly');

    fireEvent.change(screen.getByLabelText('CC'), { target: { value: 'accounts@example.com' } });
    fireEvent.change(screen.getByLabelText(/Message/), { target: { value: 'Updated approved wording.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Finalize & Send Quotation' }));

    expect(defaultProps.onSend).toHaveBeenCalledWith({
      to: ['maria@example.com'],
      cc: ['accounts@example.com'],
      subject: 'Re: RFQ - First aid supplies',
      body: 'Updated approved wording.',
      confirm_recipient: true,
      delivery_mode: 'gmail_reply',
    });
  });

  test('labels a manual quotation as a new email and requires explicit recipient confirmation', () => {
    render(
      <QuotationEmailPreviewDialog
        {...defaultProps}
        preview={{
          delivery_mode: 'new_email',
          to: [],
          subject: 'Quotation QT-20260731-0002',
          body: 'Dear Sir or Madam,\n\nPlease find attached our quotation.',
          attachment_filename: 'CUSTOMER-QT-20260731-0002.pdf',
        }}
      />
    );

    expect(screen.getByText('Sending a new email')).toBeInTheDocument();
    expect(screen.getByText('Not a reply')).toBeInTheDocument();
    const sendButton = screen.getByRole('button', { name: 'Finalize & Send Quotation' });
    fireEvent.click(sendButton);
    expect(defaultProps.onSend).not.toHaveBeenCalled();
    expect(screen.getByText('Recipient is required.')).toBeInTheDocument();
    expect(screen.getByText('Confirm the manually entered recipient before sending.')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/To/), { target: { value: 'buyer@example.com' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /I checked this address/ }));
    fireEvent.click(sendButton);

    expect(defaultProps.onSend).toHaveBeenCalledWith(expect.objectContaining({
      to: ['buyer@example.com'],
      confirm_recipient: true,
      delivery_mode: 'new_email',
    }));
  });

  test('does not submit malformed addresses or blank required email content', () => {
    render(
      <QuotationEmailPreviewDialog
        {...defaultProps}
        preview={{
          delivery_mode: 'new_email',
          to: ['not-an-email'],
          cc: ['also-invalid'],
          subject: '',
          body: '',
        }}
      />
    );

    fireEvent.click(screen.getByRole('checkbox', { name: /I checked this address/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Finalize & Send Quotation' }));

    expect(defaultProps.onSend).not.toHaveBeenCalled();
    expect(screen.getByText('not-an-email is not a valid email address.')).toBeInTheDocument();
    expect(screen.getByText('also-invalid is not a valid email address.')).toBeInTheDocument();
    expect(screen.getByText('Subject is required.')).toBeInTheDocument();
    expect(screen.getByText('Email message is required.')).toBeInTheDocument();
  });

  test('shows finalization-without-send as a separate explicit action', () => {
    render(<QuotationEmailPreviewDialog {...defaultProps} />);

    fireEvent.click(screen.getByRole('button', { name: 'Finalize Only' }));
    expect(defaultProps.onFinalizeOnly).toHaveBeenCalledTimes(1);
    expect(defaultProps.onSend).not.toHaveBeenCalled();
  });

  test('searches by the exact manual recipient and requires staff to choose a verified thread', () => {
    const onFindThread = jest.fn();
    const onSelectThread = jest.fn();
    const onClearThreadCandidates = jest.fn();
    const candidate = {
      selection_token: 'signed-selection-token',
      sender_name: 'Maria Buyer',
      sender_email: 'buyer@example.com',
      subject: 'RFQ - Clinic supplies',
      received_at: '31 Jul 2026, 10:30',
      snippet: 'Please quote the attached list.',
    };
    render(
      <QuotationEmailPreviewDialog
        {...defaultProps}
        preview={{
          delivery_mode: 'new_email',
          to: ['buyer@example.com'],
          subject: 'Quotation Q-0021',
          body: 'Please find attached our quotation.',
        }}
        threadCandidates={[candidate]}
        threadSearchCompleted
        onFindThread={onFindThread}
        onSelectThread={onSelectThread}
        onClearThreadCandidates={onClearThreadCandidates}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: 'Find original Gmail thread' }));
    expect(onFindThread).toHaveBeenCalledWith('buyer@example.com');
    expect(screen.getByText('RFQ - Clinic supplies')).toBeInTheDocument();
    expect(screen.getByText(/Please quote the attached list/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Reply to this thread' }));
    expect(onSelectThread).toHaveBeenCalledWith(candidate);
    fireEvent.click(screen.getByRole('button', { name: 'Keep as New Email' }));
    expect(onClearThreadCandidates).toHaveBeenCalled();
  });

  test('blocks email delivery and offers reconnect when Gmail send permission is missing', () => {
    const onReconnectGmail = jest.fn();
    render(
      <QuotationEmailPreviewDialog
        {...defaultProps}
        preview={{ ...gmailPreview, gmail_send_authorized: false }}
        onReconnectGmail={onReconnectGmail}
      />
    );

    expect(screen.getByText('Gmail sending permission is not ready.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reconnect Gmail to send' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect Gmail' }));
    expect(onReconnectGmail).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Finalize Only' })).toBeEnabled();
  });

  test('asks the credential owner to reconnect when the current staff user cannot manage Gmail', () => {
    render(
      <QuotationEmailPreviewDialog
        {...defaultProps}
        preview={{
          ...gmailPreview,
          gmail_send_authorized: false,
          gmail_can_manage: false,
        }}
        onReconnectGmail={jest.fn()}
      />
    );

    expect(screen.queryByRole('button', { name: 'Reconnect Gmail' })).not.toBeInTheDocument();
    expect(screen.getByText(/Ask the shared Gmail credential owner or a superuser/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reconnect Gmail to send' })).toBeDisabled();
  });

  test('allows field edits to clear only a correctable pre-send validation error', () => {
    const onClearCorrectableError = jest.fn();
    render(
      <QuotationEmailPreviewDialog
        {...defaultProps}
        sendError={{
          code: 'email_delivery_error',
          detail: 'Enter a valid recipient email address.',
          quoteFinalized: false,
          retryable: false,
          deliveryStatus: 'not_sent',
        }}
        onClearCorrectableError={onClearCorrectableError}
      />
    );

    expect(screen.getByText('Check the email details and try again.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Finalize & Send Quotation' })).toBeEnabled();
    fireEvent.change(screen.getByLabelText(/Message/), { target: { value: 'Corrected message.' } });
    expect(onClearCorrectableError).toHaveBeenCalledTimes(1);
  });

  test('does not clear or unlock an attachment snapshot mismatch when fields are edited', () => {
    const onClearCorrectableError = jest.fn();
    render(
      <QuotationEmailPreviewDialog
        {...defaultProps}
        quoteIsFinalized
        sendError={{
          code: 'attachment_snapshot_mismatch',
          detail: 'The finalized PDF no longer matches the reviewed attachment.',
          quoteFinalized: true,
          retryable: false,
          deliveryStatus: 'failed',
        }}
        onClearCorrectableError={onClearCorrectableError}
      />
    );

    expect(screen.getByRole('button', { name: 'Sending disabled' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Message/), { target: { value: 'Changing text cannot unlock this.' } });
    expect(onClearCorrectableError).not.toHaveBeenCalled();
    expect(screen.getByText('The finalized PDF no longer matches the reviewed attachment.')).toBeInTheDocument();
  });

  test('allows a definite failed delivery to be retried without finalizing again', () => {
    render(
      <QuotationEmailPreviewDialog
        {...defaultProps}
        quoteIsFinalized
        sendError={{
          detail: 'Google temporarily rejected the request.',
          quoteFinalized: true,
          retryable: true,
          deliveryStatus: 'failed',
        }}
      />
    );

    expect(screen.getByText('The quotation is finalized, but the email was not sent.')).toBeInTheDocument();
    expect(screen.getByText(/No duplicate quotation will be created/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Finalize Only' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Send Quotation' }));
    expect(defaultProps.onSend).toHaveBeenCalledTimes(1);
  });

  test('blocks a retry when delivery status is ambiguous', () => {
    render(
      <QuotationEmailPreviewDialog
        {...defaultProps}
        quoteIsFinalized
        sendError={{
          detail: 'The Gmail request timed out.',
          quoteFinalized: true,
          retryable: false,
          deliveryStatus: 'unknown',
        }}
      />
    );

    expect(screen.getByText(/Check the Sent mailbox/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sending disabled' })).toBeDisabled();
    expect(defaultProps.onSend).not.toHaveBeenCalled();
  });

  test.each([
    [
      'unknown',
      'The previous Gmail delivery could not be confirmed.',
      'Check Gmail before resending',
    ],
    [
      'sending',
      'Gmail delivery is already in progress.',
      'Sending in progress',
    ],
    [
      'sent',
      'This quotation has already been emailed.',
      'Already Sent',
    ],
  ])('blocks a reopened preview whose persisted delivery status is %s', (status, notice, buttonLabel) => {
    render(
      <QuotationEmailPreviewDialog
        {...defaultProps}
        quoteIsFinalized
        preview={{ ...gmailPreview, status }}
      />
    );

    expect(screen.getByText(notice)).toBeInTheDocument();
    const sendButton = screen.getByRole('button', { name: buttonLabel });
    expect(sendButton).toBeDisabled();
    fireEvent.click(sendButton);
    expect(defaultProps.onSend).not.toHaveBeenCalled();
  });

  test.each(['unknown', 'sending'])('checks a persisted %s delivery through reconciliation without invoking send', (status) => {
    const onReconcileEmail = jest.fn();
    render(
      <QuotationEmailPreviewDialog
        {...defaultProps}
        quoteIsFinalized
        preview={{ ...gmailPreview, status }}
        onReconcileEmail={onReconcileEmail}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: 'Check Gmail status' }));
    expect(onReconcileEmail).toHaveBeenCalledTimes(1);
    expect(defaultProps.onSend).not.toHaveBeenCalled();
    expect(screen.getByRole('button', {
      name: status === 'unknown' ? 'Check Gmail before resending' : 'Sending in progress',
    })).toBeDisabled();
  });

  test('honors an explicit backend can_reconcile false state', () => {
    render(
      <QuotationEmailPreviewDialog
        {...defaultProps}
        quoteIsFinalized
        preview={{ ...gmailPreview, status: 'sending', can_reconcile: false }}
        onReconcileEmail={jest.fn()}
      />
    );

    expect(screen.queryByRole('button', { name: 'Check Gmail status' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sending in progress' })).toBeDisabled();
  });

  test('keeps a definite persisted failed delivery eligible for safe retry', () => {
    render(
      <QuotationEmailPreviewDialog
        {...defaultProps}
        quoteIsFinalized
        preview={{ ...gmailPreview, status: 'failed' }}
      />
    );

    const sendButton = screen.getByRole('button', { name: 'Send Quotation' });
    expect(sendButton).toBeEnabled();
    fireEvent.click(sendButton);
    expect(defaultProps.onSend).toHaveBeenCalledTimes(1);
  });

  test('keeps Finalize Only available if preview generation fails', () => {
    render(
      <QuotationEmailPreviewDialog
        {...defaultProps}
        preview={null}
        previewError="Gmail is temporarily unavailable."
      />
    );

    const dialog = screen.getByRole('dialog', { name: 'Finalize and send quotation' });
    expect(within(dialog).getByText('The email preview could not be loaded.')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Finalize Only' }));
    expect(defaultProps.onFinalizeOnly).toHaveBeenCalledTimes(1);
  });
});
