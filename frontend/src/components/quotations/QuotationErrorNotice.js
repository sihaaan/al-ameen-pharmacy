import React from 'react';

const QuotationErrorNotice = ({
  error,
  onDismiss,
  onRetry,
  retrying = false,
  retryLabel = 'Try again',
}) => {
  if (!error) return null;

  return (
    <div className="qm-error" role="alert">
      <div>
        <strong>{error.action} failed</strong>
        <dl>
          <div>
            <dt>Endpoint</dt>
            <dd><code>{error.endpoint}</code></dd>
          </div>
          <div>
            <dt>Status</dt>
            <dd>{error.status}</dd>
          </div>
          <div>
            <dt>Detail</dt>
            <dd>{error.detail}</dd>
          </div>
        </dl>
      </div>
      {(onRetry || onDismiss) && (
        <div className="qm-action-row">
          {onRetry && (
            <button type="button" className="qm-primary small" disabled={retrying} onClick={onRetry}>
              {retrying ? 'Trying again...' : retryLabel}
            </button>
          )}
          {onDismiss && (
            <button type="button" className="qm-secondary small" disabled={retrying} onClick={onDismiss}>
              Dismiss
            </button>
          )}
        </div>
      )}
    </div>
  );
};

export default QuotationErrorNotice;
