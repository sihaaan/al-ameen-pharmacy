import React, { useEffect, useState } from 'react';
import LpoWarningReview from './LpoWarningReview';

const draftFor = (record) => ({ lpo_number: record?.lpo_number || '', lpo_date: record?.lpo_date || '', notes: record?.notes || '' });

const OrderLpoDetails = ({ records, selectedId, onSelect, onReview, onSave, busy, hasUnsavedLines, onDirtyChange }) => {
  const record = records.find((entry) => String(entry.id) === String(selectedId));
  const [draft, setDraft] = useState(() => draftFor(record));
  useEffect(() => { setDraft(draftFor(record)); }, [record]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(draftFor(record));
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  if (!records.length) return null;
  return <div className="qm-order-lpo-record">
    <div className="qm-order-lpo-select">
      <label>Saved customer LPO
        <select value={selectedId || ''} disabled={busy || dirty || hasUnsavedLines} onChange={(event) => onSelect(event.target.value)}>
          <option value="">Choose an LPO</option>
          {records.map((entry) => <option key={entry.id} value={entry.id}>{entry.lpo_number || entry.source_filename || `LPO ${entry.id}`} · {entry.status_display || entry.status}</option>)}
        </select>
      </label>
      <button type="button" className="qm-secondary" disabled={!record || busy || dirty || hasUnsavedLines} onClick={() => onReview(record.id)}>Review saved LPO items</button>
    </div>
    {record && <>
      <details className="qm-order-lpo-details">
        <summary>LPO reference &amp; notes{dirty ? ' · Unsaved changes' : ''}</summary>
        <div className="qm-lpo-detail-grid">
          <label>LPO number<input value={draft.lpo_number} disabled={busy} onChange={(event) => setDraft({ ...draft, lpo_number: event.target.value })} /></label>
          <label>LPO date<input type="date" value={draft.lpo_date} disabled={busy} onChange={(event) => setDraft({ ...draft, lpo_date: event.target.value })} /></label>
          <label className="span-two">Notes<textarea rows="2" value={draft.notes} disabled={busy} onChange={(event) => setDraft({ ...draft, notes: event.target.value })} /></label>
        </div>
        <button type="button" className="qm-secondary small" disabled={!dirty || busy} onClick={() => onSave(record.id, { ...draft, lpo_date: draft.lpo_date || null })}>Save LPO details</button>
      </details>
      <LpoWarningReview warnings={record.warnings} />
    </>}
  </div>;
};

export default OrderLpoDetails;
