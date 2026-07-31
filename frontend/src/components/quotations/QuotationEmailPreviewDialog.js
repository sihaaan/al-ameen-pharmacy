import React, { useLayoutEffect, useMemo, useState } from 'react';

const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export const splitEmailAddresses = (value) => String(value || '')
  .split(/[;,\n]+/)
  .map((address) => address.trim())
  .filter(Boolean);

export const normalizeQuotationEmailPreview = (preview = {}) => {
  const source = preview || {};
  const deliveryMode = source.delivery_mode
    || (source.can_reply_to_thread || source.is_thread_reply ? 'gmail_reply' : 'new_email');
  const to = Array.isArray(source.to) ? source.to : splitEmailAddresses(source.to);
  const cc = Array.isArray(source.cc) ? source.cc : splitEmailAddresses(source.cc);
  return {
    ...source,
    delivery_mode: deliveryMode,
    to,
    cc,
    subject: String(source.subject || ''),
    body: String(source.body || ''),
    attachment_filename: source.attachment_filename || 'Quotation.pdf',
  };
};

const emailListError = (addresses, label, required = false) => {
  if (required && addresses.length === 0) return `${label} is required.`;
  const invalid = addresses.find((address) => !emailPattern.test(address));
  return invalid ? `${invalid} is not a valid email address.` : '';
};

const persistedDeliveryStates = {
  unknown: {
    title: 'The previous Gmail delivery could not be confirmed.',
    detail: 'Check the shared mailbox Sent folder before taking another action. Sending again is disabled until delivery is reconciled.',
    buttonLabel: 'Check Gmail before resending',
    tone: 'warning',
  },
  sending: {
    title: 'Gmail delivery is already in progress.',
    detail: 'Wait for the current request to finish, then close and reopen this preview to see the confirmed result.',
    buttonLabel: 'Sending in progress',
    tone: 'warning',
  },
  sent: {
    title: 'This quotation has already been emailed.',
    detail: 'Gmail recorded a successful delivery. Sending again is disabled here to prevent a duplicate email.',
    buttonLabel: 'Already Sent',
    tone: 'success',
  },
};

const QuotationEmailPreviewDialog = ({
  preview,
  loading = false,
  previewError = '',
  sending = false,
  sendError = null,
  quoteIsFinalized = false,
  threadCandidates = [],
  threadCandidatesLoading = false,
  threadCandidatesError = '',
  threadSearchCompleted = false,
  gmailReconnectError = '',
  reconciling = false,
  reconcileFeedback = null,
  onRetryPreview,
  onReconnectGmail,
  onReconcileEmail,
  onClearCorrectableError,
  onFindThread,
  onSelectThread,
  onClearThreadCandidates,
  onClose,
  onFinalizeOnly,
  onSend,
}) => {
  const normalized = useMemo(() => normalizeQuotationEmailPreview(preview), [preview]);
  const [toText, setToText] = useState('');
  const [ccText, setCcText] = useState('');
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [recipientConfirmed, setRecipientConfirmed] = useState(false);
  const [attemptedSubmit, setAttemptedSubmit] = useState(false);

  useLayoutEffect(() => {
    setToText(normalized.to.join(', '));
    setCcText(normalized.cc.join(', '));
    setSubject(normalized.subject);
    setBody(normalized.body);
    setRecipientConfirmed(false);
    setAttemptedSubmit(false);
  }, [normalized]);

  const isThreadReply = normalized.delivery_mode === 'gmail_reply';
  const to = splitEmailAddresses(toText);
  const cc = splitEmailAddresses(ccText);
  const validationErrors = [
    emailListError(to, 'Recipient', true),
    emailListError(cc, 'CC'),
    !subject.trim() ? 'Subject is required.' : '',
    !body.trim() ? 'Email message is required.' : '',
    !isThreadReply && !recipientConfirmed
      ? 'Confirm the manually entered recipient before sending.'
      : '',
  ].filter(Boolean);
  const correctableSendError = sendError?.code === 'email_delivery_error';
  const hardErrorStatus = ['unknown', 'sending', 'sent'].includes(String(sendError?.deliveryStatus || '').toLowerCase());
  const attachmentSnapshotMismatch = sendError?.code === 'attachment_snapshot_mismatch';
  const retryBlocked = Boolean(sendError && (
    attachmentSnapshotMismatch
    || hardErrorStatus
    || (!correctableSendError && sendError.retryable !== true)
  ));
  const sendPermissionMissing = normalized.gmail_connected === false || normalized.gmail_send_authorized === false;
  const gmailCanManage = normalized.gmail_can_manage !== false;
  const persistedDeliveryStatus = String(normalized.status || '').toLowerCase();
  const persistedDeliveryState = persistedDeliveryStates[persistedDeliveryStatus] || null;
  const persistedSendBlocked = Boolean(persistedDeliveryState);
  const canReconcile = normalized.can_reconcile === true || (
    normalized.can_reconcile === undefined
    && ['unknown', 'sending'].includes(persistedDeliveryStatus)
  );
  const canSearchThread = !isThreadReply
    && normalized.gmail_connected !== false
    && to.length === 1
    && emailPattern.test(to[0])
    && !threadCandidatesLoading;
  const canSend = !loading
    && !previewError
    && !sending
    && !retryBlocked
    && !sendPermissionMissing
    && !persistedSendBlocked
    && validationErrors.length === 0;
  const trustedSource = normalized.trusted_source || {};
  const thread = normalized.thread || {};
  const sourceSubject = trustedSource.subject || thread.subject || normalized.subject;

  const submitSend = () => {
    setAttemptedSubmit(true);
    if (!canSend) return;
    onSend({
      to,
      cc,
      subject: subject.trim(),
      body: body.trim(),
      confirm_recipient: isThreadReply || recipientConfirmed,
      delivery_mode: normalized.delivery_mode,
      ...(normalized.thread_selection_token
        ? { thread_selection_token: normalized.thread_selection_token }
        : {}),
    });
  };

  const markEmailFieldEdited = () => {
    if (correctableSendError && onClearCorrectableError) onClearCorrectableError();
  };

  return (
    <div className="qm-modal-backdrop qm-email-preview-backdrop" role="presentation">
      <div
        className="qm-modal qm-email-preview-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="quotation-email-preview-title"
      >
        <div className="qm-email-preview-heading">
          <div>
            <span className="qm-eyebrow">Quotation delivery</span>
            <h3 id="quotation-email-preview-title">
              {quoteIsFinalized ? 'Review and send quotation' : 'Finalize and send quotation'}
            </h3>
            <p>Review the exact recipient and message. Nothing is emailed until you click the send button.</p>
          </div>
          <button type="button" className="qm-secondary small" disabled={sending || reconciling} onClick={onClose}>Close</button>
        </div>

        {loading && (
          <div className="qm-email-preview-loading" role="status">
            <span className="qm-email-preview-spinner" aria-hidden="true" />
            <div>
              <strong>Preparing the email preview...</strong>
              <p>Checking the quotation source and generating the final PDF details.</p>
            </div>
          </div>
        )}

        {!loading && previewError && (
          <div className="qm-feedback warning" role="alert">
            <div>
              <strong>The email preview could not be loaded.</strong>
              <p>{previewError}</p>
            </div>
            {onRetryPreview && <button type="button" className="qm-secondary small" onClick={onRetryPreview}>Retry preview</button>}
          </div>
        )}

        {!loading && !previewError && preview && (
          <>
            <section className={`qm-email-source-card ${isThreadReply ? 'trusted' : 'manual'}`}>
              <div className="qm-email-source-icon" aria-hidden="true">{isThreadReply ? 'RE' : 'NEW'}</div>
              <div>
                <div className="qm-email-source-title-row">
                  <strong>{isThreadReply ? 'Replying in the verified Gmail thread' : 'Sending a new email'}</strong>
                  <span className={`qm-email-mode-badge ${isThreadReply ? 'trusted' : 'manual'}`}>
                    {isThreadReply ? 'Verified reply' : 'Not a reply'}
                  </span>
                </div>
                {isThreadReply ? (
                  <p>
                    The recipient and subject come from the customer email linked to this quotation and cannot be changed here.
                  </p>
                ) : (
                  <p>
                    This quotation was not created from Gmail. Enter and confirm the recipient carefully; this starts a new conversation.
                  </p>
                )}
                {isThreadReply && (trustedSource.sender_email || sourceSubject) && (
                  <dl className="qm-email-thread-context">
                    {trustedSource.sender_email && (
                      <div><dt>Customer email</dt><dd>{trustedSource.sender_name ? `${trustedSource.sender_name} - ` : ''}{trustedSource.sender_email}</dd></div>
                    )}
                    {sourceSubject && <div><dt>Original subject</dt><dd>{sourceSubject}</dd></div>}
                    {trustedSource.received_at && <div><dt>Received</dt><dd>{trustedSource.received_at}</dd></div>}
                  </dl>
                )}
              </div>
            </section>

            {sendError && (
              <div className={`qm-feedback ${sendError.quoteFinalized ? 'warning' : 'error'}`} role="alert">
                <div>
                  <strong>
                    {correctableSendError
                      ? 'Check the email details and try again.'
                      : sendError.kind === 'finalize'
                      ? 'The quotation was not finalized.'
                      : sendError.quoteFinalized
                      ? 'The quotation is finalized, but the email was not sent.'
                      : 'The quotation email was not sent.'}
                  </strong>
                  <p>{sendError.detail}</p>
                  {correctableSendError && (
                    <p>Correct the recipient, CC, subject, message, or confirmation below. The quotation has not been sent.</p>
                  )}
                  {!correctableSendError && !hardErrorStatus && sendError.quoteFinalized && sendError.retryable && (
                    <p>No duplicate quotation will be created. Review the details and safely retry sending below.</p>
                  )}
                  {!correctableSendError && sendError.deliveryStatus === 'unknown' && (
                    <p>Delivery status is unknown. Check the Sent mailbox before taking another action; sending again is disabled to prevent a duplicate email.</p>
                  )}
                  {!correctableSendError && sendError.deliveryStatus === 'sending' && (
                    <p>Gmail delivery is still in progress. Do not start another send; use Check Gmail status when it becomes available.</p>
                  )}
                  {!correctableSendError && sendError.deliveryStatus === 'sent' && (
                    <p>Gmail already recorded this quotation as sent. Duplicate sending is disabled.</p>
                  )}
                  {!correctableSendError && !sendError.retryable && !hardErrorStatus && (
                    <p>Sending cannot be retried from this preview. Resolve the reported issue or refresh the quotation first.</p>
                  )}
                </div>
              </div>
            )}

            {persistedDeliveryState && (
              <div className={`qm-feedback ${persistedDeliveryState.tone}`} role={persistedDeliveryStatus === 'unknown' ? 'alert' : 'status'}>
                <div>
                  <strong>{persistedDeliveryState.title}</strong>
                  <p>{persistedDeliveryState.detail}</p>
                </div>
                {canReconcile && onReconcileEmail && (
                  <button type="button" className="qm-secondary small" disabled={reconciling} onClick={onReconcileEmail}>
                    {reconciling ? 'Checking Gmail...' : 'Check Gmail status'}
                  </button>
                )}
              </div>
            )}

            {reconcileFeedback && (
              <div className={`qm-feedback ${reconcileFeedback.type || 'warning'}`} role={reconcileFeedback.type === 'error' ? 'alert' : 'status'}>
                <div>
                  <strong>{reconcileFeedback.title || 'Gmail status checked.'}</strong>
                  <p>{reconcileFeedback.detail}</p>
                </div>
              </div>
            )}

            {sendPermissionMissing && (
              <div className="qm-feedback warning" role="alert">
                <div>
                  <strong>Gmail sending permission is not ready.</strong>
                  <p>Reconnect the shared Gmail mailbox once to approve read + send access. You can still use Finalize Only.</p>
                  {gmailReconnectError && <p className="qm-field-warning">{gmailReconnectError}</p>}
                </div>
                {gmailCanManage && onReconnectGmail && (
                  <button type="button" className="qm-secondary small" onClick={onReconnectGmail}>Reconnect Gmail</button>
                )}
                {!gmailCanManage && (
                  <p className="qm-field-warning">Ask the shared Gmail credential owner or a superuser to reconnect the mailbox and approve send access.</p>
                )}
              </div>
            )}

            <div className="qm-email-fields">
              <label>
                <span>To <b aria-hidden="true">*</b></span>
                <input
                  type="text"
                  value={toText}
                  readOnly={isThreadReply}
                  aria-readonly={isThreadReply}
                  onChange={(event) => {
                    setToText(event.target.value);
                    markEmailFieldEdited();
                    if (!isThreadReply && onClearThreadCandidates) onClearThreadCandidates();
                  }}
                  placeholder="customer@example.com"
                  autoComplete="off"
                />
                {isThreadReply && <small>Verified from the relevant inbound Gmail message.</small>}
              </label>
              <label>
                <span>CC</span>
                <input
                  type="text"
                  value={ccText}
                  onChange={(event) => {
                    setCcText(event.target.value);
                    markEmailFieldEdited();
                  }}
                  placeholder="Optional; separate addresses with commas"
                  autoComplete="off"
                />
              </label>
              <label className="qm-email-field-wide">
                <span>Subject <b aria-hidden="true">*</b></span>
                <input
                  type="text"
                  value={subject}
                  readOnly={isThreadReply}
                  aria-readonly={isThreadReply}
                  onChange={(event) => {
                    setSubject(event.target.value);
                    markEmailFieldEdited();
                  }}
                />
                {isThreadReply && <small>Locked so Gmail keeps the reply in the correct conversation.</small>}
              </label>
              <label className="qm-email-field-wide">
                <span>Message <b aria-hidden="true">*</b></span>
                <textarea rows="9" value={body} onChange={(event) => {
                  setBody(event.target.value);
                  markEmailFieldEdited();
                }} />
              </label>
            </div>

            {!isThreadReply && onFindThread && (
              <section className="qm-email-thread-finder" aria-label="Find original Gmail thread">
                <div className="qm-email-thread-finder-heading">
                  <div>
                    <strong>Was this inquiry originally received in Gmail?</strong>
                    <p>Search the shared mailbox for recent inbound messages from the exact address entered above. You choose the message; no thread is linked automatically.</p>
                  </div>
                  <button
                    type="button"
                    className="qm-secondary"
                    disabled={!canSearchThread}
                    onClick={() => onFindThread(to[0])}
                  >
                    {threadCandidatesLoading ? 'Searching Gmail...' : 'Find original Gmail thread'}
                  </button>
                </div>
                {to.length !== 1 && (
                  <small>Enter one valid recipient address above to search for an exact sender match.</small>
                )}
                {threadCandidatesError && <p className="qm-field-warning" role="alert">{threadCandidatesError}</p>}
                {threadCandidates.length > 0 && (
                  <div className="qm-email-thread-candidates">
                    <div className="qm-email-thread-candidates-title">
                      <strong>Verified messages from {to[0]}</strong>
                      {onClearThreadCandidates && (
                        <button type="button" className="qm-link-button" onClick={onClearThreadCandidates}>Keep as New Email</button>
                      )}
                    </div>
                    {threadCandidates.map((candidate) => (
                      <article className="qm-email-thread-candidate" key={candidate.selection_token}>
                        <div>
                          <strong>{candidate.subject || '(No subject)'}</strong>
                          <p>
                            {candidate.sender_name ? `${candidate.sender_name} - ` : ''}{candidate.sender_email}
                            {candidate.received_at ? ` | ${candidate.received_at}` : ''}
                          </p>
                          {candidate.snippet && <small>{candidate.snippet}</small>}
                        </div>
                        <button type="button" className="qm-primary small" onClick={() => onSelectThread(candidate)}>
                          Reply to this thread
                        </button>
                      </article>
                    ))}
                  </div>
                )}
                {!threadCandidatesLoading && !threadCandidatesError && threadCandidates.length === 0 && threadSearchCompleted && (
                  <p className="qm-email-thread-none">No recent inbound Gmail messages from this exact address were found. Keep New Email selected.</p>
                )}
              </section>
            )}

            <div className="qm-email-attachment-card">
              <span className="qm-email-file-icon" aria-hidden="true">PDF</span>
              <div>
                <strong>{normalized.attachment_filename}</strong>
                <p>The finalized quotation PDF will be generated and attached by the server.</p>
              </div>
              <span className="qm-email-attachment-status">Ready to attach</span>
            </div>

            {!isThreadReply && (
              <label className="qm-email-recipient-confirmation">
                <input
                  type="checkbox"
                  checked={recipientConfirmed}
                  onChange={(event) => {
                    setRecipientConfirmed(event.target.checked);
                    markEmailFieldEdited();
                  }}
                />
                <span>I checked this address and confirm it is the intended quotation recipient.</span>
              </label>
            )}

            {attemptedSubmit && validationErrors.length > 0 && (
              <div className="qm-email-validation" role="alert">
                <strong>Check the email details:</strong>
                <ul>{validationErrors.map((error) => <li key={error}>{error}</li>)}</ul>
              </div>
            )}

            {(normalized.warnings || []).length > 0 && (
              <div className="qm-email-preview-warnings">
                {(normalized.warnings || []).map((warning) => <p key={warning}>{warning}</p>)}
              </div>
            )}
          </>
        )}

        <div className="qm-email-preview-actions">
          <button type="button" className="qm-secondary" disabled={sending || reconciling} onClick={onClose}>Cancel</button>
          {!quoteIsFinalized && (
            <button type="button" className="qm-secondary" disabled={sending || reconciling} onClick={onFinalizeOnly}>
              {sending ? 'Please wait...' : 'Finalize Only'}
            </button>
          )}
          {!loading && !previewError && preview && (
            <button type="button" className="qm-primary qm-email-send-button" disabled={sending || reconciling || retryBlocked || sendPermissionMissing || persistedSendBlocked} onClick={submitSend}>
              {sending
                ? 'Sending...'
                : persistedDeliveryState
                  ? persistedDeliveryState.buttonLabel
                  : sendPermissionMissing
                    ? 'Reconnect Gmail to send'
                    : retryBlocked
                      ? 'Sending disabled'
                      : sendError?.quoteFinalized || quoteIsFinalized
                        ? 'Send Quotation'
                        : 'Finalize & Send Quotation'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default QuotationEmailPreviewDialog;
