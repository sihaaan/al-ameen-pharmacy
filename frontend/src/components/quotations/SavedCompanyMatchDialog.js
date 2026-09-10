import React, { useEffect, useRef, useState } from 'react';
import ProductSelect from './ProductSelect';
import './SavedCompanyMatchDialog.css';

export default function SavedCompanyMatchDialog({ review, catalogue, selectedProduct = '', saving, error, onResolve, onClose }) {
  const [replacement, setReplacement] = useState(String(selectedProduct || ''));
  const dialog = useRef(null);
  useEffect(() => {
    const previous = document.activeElement;
    dialog.current?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, []);
  const company = review.company_name || 'this company';
  const keyDown = (event) => {
    if (event.key === 'Escape' && !saving) { event.preventDefault(); onClose(); }
    if (event.key !== 'Tab') return;
    const controls = Array.from(dialog.current.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled)'));
    const first = controls[0], last = controls[controls.length - 1];
    if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) {
      event.preventDefault(); last?.focus();
    } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog.current)) {
      event.preventDefault(); first?.focus();
    }
  };
  return (
    <div className="qm-modal-backdrop">
      <div className="qm-modal qm-saved-match-dialog" role="dialog" aria-modal="true" aria-labelledby="saved-match-title"
        tabIndex={-1} ref={dialog} onKeyDown={keyDown}>
        <header>
          <div><span className="qm-saved-match-company">{company}</span><h3 id="saved-match-title">Review saved product match</h3></div>
          <button type="button" className="qm-secondary" disabled={saving} onClick={onClose}>Close</button>
        </header>
        <div className="qm-saved-match-body">
          <p className="qm-saved-match-request"><strong>Customer requested</strong><span>{review.source_wording}</span></p>
          <div className="qm-saved-match-differences">
            <strong>Why this needs a check</strong>
            <ul>{(review.differences || [review.reason]).map((reason) => <li key={reason}>{reason}</li>)}</ul>
          </div>
          {error && <p role="alert" className="qm-saved-match-error">{error}</p>}
          {(review.products || []).map((product) => (
            <section className="qm-saved-match-previous" key={product.product_id}>
              <span>Previously linked to</span>
              <strong>{product.product_name}</strong>
              {(product.dosage || product.pack_size) && <small>{[product.dosage, product.pack_size].filter(Boolean).join(' · ')}</small>}
              <button type="button" className="qm-primary" disabled={saving || !product.can_confirm}
                onClick={() => onResolve(product.product_id, 'confirm')}>Confirm this saved product</button>
            </section>
          ))}
          <section className="qm-saved-match-correction">
            <h4>Correct the company match</h4>
            <ProductSelect catalogue={catalogue} value={replacement} label="Correct product for this company"
              searchLabel="Search for the correct product" placeholder="Choose a different product"
              disabled={saving} onChange={setReplacement} />
            <button type="button" className="qm-secondary" disabled={saving || !replacement}
              onClick={() => onResolve(Number(replacement), 'correct')}>Save corrected company match</button>
          </section>
          <p className="qm-saved-match-scope">This decision is remembered for {company}. Other companies’ links stay separate.
            Your entered price is kept. Changes to the product’s details will require a fresh check.</p>
        </div>
        <footer><button type="button" className="qm-secondary" disabled={saving} onClick={onClose}>Decide later</button>
          {saving && <span role="status">Saving company match…</span>}</footer>
      </div>
    </div>
  );
}
