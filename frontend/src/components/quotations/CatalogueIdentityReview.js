import React, { useState } from 'react';
import quotationAPI from '../../api/quotations';
import { useAuth } from '../../context/AuthContext';

export default function CatalogueIdentityReview({ onUpdated }) {
  const { user } = useAuth() || {};
  const [report, setReport] = useState(null);
  const [previews, setPreviews] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const run = async (operation) => {
    setBusy(true); setError('');
    try { await operation(); } catch (err) { setError(err.response?.data?.detail || 'Could not complete the identity review. Refresh and try again.'); }
    finally { setBusy(false); }
  };
  const load = async (after = 0) => setReport((await quotationAPI.items.identityReport({ after })).data);
  const approve = (row, master) => run(async () => {
    const name = previews[row.id]?.standard_name || row.name;
    if (master && !window.confirm(`Use ${master.name} as the master for ${row.name}? ${row.history_count} historical price records will become discoverable through that master. Existing documents will retain their original wording and product IDs.`)) return;
    await quotationAPI.items.reviewIdentity(row.id, { updated_at: row.updated_at, standard_name: name, canonical_product_id: master?.id });
    await load(); onUpdated?.();
  });
  return <section className="qm-panel">
    <div className="qm-panel-heading"><div><h3>Catalogue identity review</h3><p>Review new items and duplicate candidates. Company prices stay separate from catalogue names.</p></div>
      <button type="button" className="qm-secondary" disabled={busy} onClick={() => run(() => load())}>{busy ? 'Checking…' : report ? 'Refresh identity review' : 'Find items to review'}</button></div>
    {error && <p role="alert">{error}</p>}
    {report && <>
      <p>{report.count} items to review · {report.unlinked_history_count} historical prices without a product link · {report.missing_accepted_price_count} historical prices without recorded acceptance.</p>
      {report.duplicate_companies?.length > 0 && <p>Possible duplicate companies: {report.duplicate_companies.map((c) => c.normalized_name).join(', ')}. Review company records separately.</p>}
      {report.results.map((row) => <article key={row.id} className="qm-subpanel">
        <strong>{row.name}</strong> <span className="qm-badge">{row.state === 'provisional' ? 'Provisional' : 'Possible duplicate'}</span>
        <p>{row.history_count} historical price records. {row.notes?.duplicate_override ? 'Created despite a similar existing item.' : ''}</p>
        {previews[row.id] && <p>Proposed name: <strong>{previews[row.id].standard_name}</strong> · {previews[row.id].ai_status === 'review_ready' ? 'AI proposal — check before approval' : 'Original wording retained'}</p>}
        <div className="qm-action-row">
          <button type="button" className="qm-secondary small" disabled={busy} onClick={() => run(async () => {
            const preview = (await quotationAPI.items.identityPreview({ name: row.name })).data;
            setPreviews((current) => ({ ...current, [row.id]: preview }));
          })}>Suggest standard name</button>
          {user?.is_superuser && !row.candidates.length && <button type="button" className="qm-primary small" disabled={busy} onClick={() => approve(row)}>Approve identity</button>}
        </div>
        {row.candidates.map((candidate) => <div key={candidate.id} className="qm-action-row">
          <span>{candidate.name} · {candidate.unit || 'Unit unspecified'} · {candidate.compatible ? 'Equivalent identity' : 'Different or missing attributes — keep separate'}</span>
          {user?.is_superuser && candidate.compatible && <button type="button" className="qm-primary small" disabled={busy} onClick={() => approve(row, candidate)}>Use as master</button>}
        </div>)}
        {user?.is_superuser && row.candidates.length > 0 && <button type="button" className="qm-secondary small" disabled={busy} onClick={() => approve(row)}>Keep as a distinct item</button>}
      </article>)}
      {report.next_after && <button type="button" className="qm-secondary" disabled={busy} onClick={() => run(() => load(report.next_after))}>Next items</button>}
    </>}
  </section>;
}
