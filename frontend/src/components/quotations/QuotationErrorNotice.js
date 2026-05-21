import React from 'react';

const QuotationErrorNotice = ({ error, onDismiss }) => {
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
      {onDismiss && (
        <button type="button" className="qm-secondary small" onClick={onDismiss}>
          Dismiss
        </button>
      )}
    </div>
  );
};

export default QuotationErrorNotice;
