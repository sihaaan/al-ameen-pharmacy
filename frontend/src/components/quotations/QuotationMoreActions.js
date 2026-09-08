import React, { useEffect, useRef } from 'react';

export default function QuotationMoreActions({ children }) {
  const disclosure = useRef(null);
  useEffect(() => {
    const dismiss = (event) => {
      if (!disclosure.current?.open) return;
      if (event.type === 'keydown' && event.key !== 'Escape') return;
      if (event.type === 'pointerdown' && disclosure.current.contains(event.target)) return;
      disclosure.current.open = false;
      if (event.type === 'keydown') disclosure.current.querySelector('summary').focus();
    };
    document.addEventListener('pointerdown', dismiss);
    document.addEventListener('keydown', dismiss);
    return () => {
      document.removeEventListener('pointerdown', dismiss);
      document.removeEventListener('keydown', dismiss);
    };
  }, []);
  return <details className="qm-quote-more" ref={disclosure}>
    <summary className="qm-secondary" role="button" aria-label="More actions">More actions</summary>
    <div className="qm-quote-more-panel" onClick={(event) => {
      if (event.target.closest('button:not(:disabled)')) {
        disclosure.current.open = false;
        disclosure.current.querySelector('summary').focus();
      }
    }}>{children}</div>
  </details>;
}
