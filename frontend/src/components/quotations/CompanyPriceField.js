import React, { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import './CompanyPriceField.css';

export const isBlankPrice = (value) => value === '' || value === null || value === undefined;
export const historyPricePatch = (recommendation) => ({
  unit_price: recommendation.amount,
  price_source_history: recommendation.history_id,
  price_original_history: recommendation.history_id,
  price_provenance: { kind: 'history', ...recommendation },
  price_review_required: false,
  price_reviewed: false,
});
export const manualPricePatch = (draft, value) => ({
  unit_price: value, price_source_history: null,
  price_original_history: draft.price_original_history || null,
  price_provenance: { kind: 'manual', previous_source: draft.price_provenance?.kind === 'history'
    ? draft.price_provenance : draft.price_provenance?.previous_source },
});

export default function CompanyPriceField({ draft, recommendation, loading, failed, onRetry, onPatch,
  onWrongProduct, onHistory, inputRef, ...inputProps }) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({});
  const trigger = useRef(null);
  const panel = useRef(null);
  const panelId = useId();
  useEffect(() => {
    if (!open) return undefined;
    panel.current?.focus();
    const dismiss = (event) => {
      if (event.type === 'keydown' && event.key !== 'Escape') return;
      if (event.type === 'pointerdown' && (panel.current?.contains(event.target) || trigger.current?.contains(event.target))) return;
      setOpen(false);
      if (event.type === 'keydown') trigger.current?.focus();
    };
    document.addEventListener('pointerdown', dismiss);
    document.addEventListener('keydown', dismiss);
    window.addEventListener('resize', dismiss);
    return () => { document.removeEventListener('pointerdown', dismiss); document.removeEventListener('keydown', dismiss); window.removeEventListener('resize', dismiss); };
  }, [open]);
  const source = draft.price_provenance || {};
  const needsReview = draft.price_review_required && !draft.price_reviewed;
  const kind = needsReview ? 'review' : source.kind === 'history' ? 'history' : isBlankPrice(draft.unit_price) ? 'empty' : 'manual';
  const label = needsReview ? 'Price needs review' : kind === 'history' ? `Filled from last ${source.basis} company price`
    : kind === 'manual' ? 'Manually entered price' : loading ? 'Loading price history' : failed ? 'Price lookup failed'
      : !draft.product ? 'Confirm a product to find its price' : recommendation?.reason || 'Look up company price';
  const original = source.kind === 'history' ? source : source.previous_source;
  return <div className={`qm-company-price ${kind}`}>
    <input {...inputProps} ref={inputRef} value={draft.unit_price ?? ''} onChange={(event) => onPatch(manualPricePatch(draft, event.target.value))} />
    <div className="qm-price-details">
      <button type="button" className="qm-price-trigger" ref={trigger} aria-label={label} title={original?.history_id ? `${label}: ${original.currency} ${original.amount} / ${original.unit}, ${original.quotation_number}, ${original.date}` : label}
        aria-expanded={open} aria-controls={open ? panelId : undefined} onClick={() => {
          const rect = trigger.current.getBoundingClientRect();
          setPosition({ left: Math.max(8, Math.min(rect.right - 300, window.innerWidth - 308)), top: Math.max(8, Math.min(rect.bottom + 6, window.innerHeight - 360)) });
          setOpen(!open);
        }}>
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8">
          {kind === 'history' ? <><circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/></>
            : kind === 'manual' ? <path d="m4 16 12-12 4 4L8 20H4zm10-10 4 4"/>
              : <><path d="m12 3 10 18H2zM12 9v5"/><path d="M12 17v1"/></>}
        </svg>
      </button>
      {open && createPortal(<div className="qm-price-popover" id={panelId} ref={panel} tabIndex={-1} role="dialog" aria-label="Company price details" style={position}>
        <strong>{label}</strong>
        {original?.history_id && <p>{original.currency} {original.amount} / {original.unit} · {original.quotation_number} · {original.date}<br/>Historical quantity: {original.quantity}</p>}
        {recommendation?.reason && <p>{recommendation.reason}</p>}
        {failed && <p>Could not retrieve history. Your entered price has been kept.</p>}
        {!inputProps.disabled && <>
          {onRetry && <button type="button" onClick={onRetry}>{failed ? 'Retry price lookup' : 'Refresh company price'}</button>}
          {needsReview && draft.product && !isBlankPrice(draft.unit_price) && <button type="button" onClick={() => onPatch({ ...manualPricePatch(draft, draft.unit_price), price_reviewed: true, price_review_required: false })}>I checked this product and price</button>}
          {original?.history_id && <button type="button" onClick={() => {
            if (window.confirm('Stop recommending this and all older prices for this company, product, unit and currency after saving? Historical documents stay available.')) {
              onPatch({ ...manualPricePatch(draft, draft.unit_price), price_feedback: 'outdated' });
            }
          }}>Previous price is outdated</button>}
          {kind === 'manual' && <button type="button" onClick={() => onPatch({ price_feedback: 'one_off' })}>Record as a one-off price</button>}
          {draft.product && onWrongProduct && <button type="button" onClick={onWrongProduct}>Wrong product</button>}
        </>}
        {onHistory && draft.product && <button type="button" onClick={onHistory}>View price history</button>}
        {draft.price_feedback && <small>Reason recorded when you save.</small>}
        <button type="button" onClick={() => { setOpen(false); trigger.current?.focus(); }}>Close price details</button>
      </div>, document.body)}
    </div>
  </div>;
}
