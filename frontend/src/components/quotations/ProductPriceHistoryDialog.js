import React, { useEffect, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import QuotationErrorNotice from './QuotationErrorNotice';

const formatMoney = (value, currency = 'AED') => {
  if (value === null || value === undefined || value === '') return 'Not recorded';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return `${currency || 'AED'} ${value}`;
  return `${currency || 'AED'} ${numeric.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 3,
  })}`;
};

const formatDate = (value) => {
  if (!value) return 'Date not recorded';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleDateString('en-AE');
};

const formatQuantity = (value, unit) => {
  if (value === null || value === undefined || value === '') return '—';
  const numeric = Number(value);
  const quantity = Number.isFinite(numeric) ? numeric.toLocaleString(undefined, { maximumFractionDigits: 3 }) : value;
  return `${quantity}${unit ? ` ${unit}` : ''}`;
};

const outcomeLabel = (status) => ({
  accepted: 'Accepted',
  quantity_changed: 'Accepted with quantity change',
  rejected: 'Rejected',
  pending: 'Pending',
  unknown: 'Not reviewed',
}[status] || 'Not reviewed');

const SummaryCard = ({ label, row, accepted = false }) => (
  <div className="qm-price-history-summary-card">
    <span>{label}</span>
    {row ? (
      <>
        <strong>{formatMoney(accepted ? row.accepted_unit_price : row.quoted_unit_price, row.currency)}</strong>
        <small>
          {row.quotation_number || 'Quotation'} · {formatDate(accepted ? row.accepted_at : row.quoted_at)}
          {accepted && row.lpo_number ? ` · LPO ${row.lpo_number}` : ''}
        </small>
      </>
    ) : (
      <strong className="qm-price-history-missing">No history</strong>
    )}
  </div>
);

const ProductPriceHistoryDialog = ({ quoteId, productId, productName = '', initialContext, onClose }) => {
  const [context, setContext] = useState(initialContext || null);
  const [loading, setLoading] = useState(!initialContext);
  const [errorInfo, setErrorInfo] = useState(null);

  useEffect(() => {
    setContext(initialContext || null);
    if (initialContext) {
      setLoading(false);
      setErrorInfo(null);
      return undefined;
    }

    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setErrorInfo(null);
      try {
        const response = await quotationAPI.quotes.productPrice(quoteId, { product: productId, history_limit: 50 });
        if (!cancelled) setContext(response.data);
      } catch (error) {
        const details = await describeQuotationError(
          error,
          'Load Product price history',
          `GET /quotations/quotes/${quoteId}/product_price/?product=${productId}`
        );
        if (!cancelled) setErrorInfo(details);
        console.error(formatQuotationError(details), error);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [initialContext, productId, quoteId]);

  useEffect(() => {
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', closeOnEscape);
    return () => document.removeEventListener('keydown', closeOnEscape);
  }, [onClose]);

  const history = context?.history || [];

  return (
    <div className="qm-modal-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <div className="qm-modal qm-price-history-modal" role="dialog" aria-modal="true" aria-labelledby="product-price-history-title">
        <div className="qm-panel-heading">
          <div>
            <h3 id="product-price-history-title">Price history</h3>
            <p>{context?.product_name || productName || 'Selected Product'} · quoted and accepted customer prices</p>
          </div>
          <button type="button" className="qm-secondary small" onClick={onClose}>Close</button>
        </div>

        <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
        {loading ? (
          <div className="qm-loading">Loading quoted and accepted prices...</div>
        ) : context ? (
          <>
            <div className="qm-price-history-summary">
              <SummaryCard label="Last quoted" row={context.latest_quoted} />
              <SummaryCard label="Last accepted / LPO" row={context.latest_accepted} accepted />
            </div>
            {history.length === 0 ? (
              <div className="qm-empty">No earlier prices exist for this customer and Product.</div>
            ) : (
              <div className="qm-table-wrap">
                <table className="qm-table qm-price-history-table">
                  <thead>
                    <tr>
                      <th>Quotation</th>
                      <th>Quoted</th>
                      <th>Accepted / LPO</th>
                      <th>Outcome</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((row, index) => (
                      <tr key={`${row.quotation || row.quotation_number}-${index}`}>
                        <td>
                          <strong>{row.quotation_number || 'Quotation'}</strong>
                          <small>{formatDate(row.quoted_at)}</small>
                        </td>
                        <td>
                          <strong>{formatMoney(row.quoted_unit_price, row.currency)}</strong>
                          <small>{formatQuantity(row.quantity, row.unit)}</small>
                        </td>
                        <td>
                          <strong>{formatMoney(row.accepted_unit_price, row.currency)}</strong>
                          <small>
                            {row.accepted_unit_price ? formatQuantity(row.accepted_quantity, row.unit) : 'No accepted price'}
                            {row.lpo_number ? ` · LPO ${row.lpo_number}` : ''}
                          </small>
                        </td>
                        <td><span className={`qm-line-status ${row.outcome_status || 'unknown'}`}>{outcomeLabel(row.outcome_status)}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        ) : !errorInfo ? (
          <div className="qm-empty">Price history could not be loaded.</div>
        ) : null}
      </div>
    </div>
  );
};

export default ProductPriceHistoryDialog;
