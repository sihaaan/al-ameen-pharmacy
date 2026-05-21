import React, { useCallback, useEffect, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import PriceHistoryPanel from './PriceHistoryPanel';
import AuditLogPanel from './AuditLogPanel';
import QuotationErrorNotice from './QuotationErrorNotice';

const editableStatuses = new Set(['draft', 'pending_review', 'approved']);

const emptyLine = {
  quote_item: '',
  item_name_snapshot: '',
  description: '',
  quantity: '1',
  unit: '',
  unit_price: '',
  vat_rate: '0',
  match_status: 'unresolved',
  notes: '',
};

const QuotationEditor = ({ quoteId, onClose }) => {
  const [quote, setQuote] = useState(null);
  const [items, setItems] = useState([]);
  const [lineForm, setLineForm] = useState(emptyLine);
  const [lineDrafts, setLineDrafts] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [historyItem, setHistoryItem] = useState('');
  const [errorInfo, setErrorInfo] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErrorInfo(null);
    try {
      const [quoteRes, itemsRes] = await Promise.all([
        quotationAPI.quotes.retrieve(quoteId),
        quotationAPI.items.list({ active: 'true' }),
      ]);
      setQuote(quoteRes.data);
      setItems(itemsRes.data);
      setLineDrafts(Object.fromEntries((quoteRes.data.lines || []).map((line) => [line.id, {
        quote_item: line.quote_item || '',
        item_name_snapshot: line.item_name_snapshot || '',
        description: line.description || '',
        quantity: line.quantity || '1',
        unit: line.unit || '',
        unit_price: line.unit_price || '',
        vat_rate: line.vat_rate || '0',
        match_status: line.match_status || 'unresolved',
          notes: line.notes || '',
      }])));
    } catch (error) {
      const details = await describeQuotationError(error, 'Load quotation', `GET /quotations/quotes/${quoteId}/ and GET /quotations/items/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setLoading(false);
    }
  }, [quoteId]);

  useEffect(() => {
    load();
  }, [load]);

  const isEditable = quote && editableStatuses.has(quote.status);

  const updateLineDraft = (lineId, patch) => {
    setLineDrafts((current) => ({
      ...current,
      [lineId]: { ...current[lineId], ...patch },
    }));
  };

  const payloadForLine = (draft) => ({
    ...draft,
    quote_item: draft.quote_item || null,
    unit_price: draft.unit_price || null,
    match_status: draft.quote_item && draft.match_status === 'unresolved' ? 'confirmed' : draft.match_status,
  });

  const saveLine = async (lineId) => {
    setSaving(true);
    setErrorInfo(null);
    try {
      await quotationAPI.lines.update(lineId, payloadForLine(lineDrafts[lineId]));
      await load();
    } catch (error) {
      const details = await describeQuotationError(error, 'Save quote line', `PATCH /quotations/quote-lines/${lineId}/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const addLine = async (event) => {
    event.preventDefault();
    setSaving(true);
    setErrorInfo(null);
    try {
      await quotationAPI.lines.create({
        ...payloadForLine(lineForm),
        quotation: quote.id,
        sort_order: quote.lines.length,
      });
      setLineForm(emptyLine);
      await load();
    } catch (error) {
      const details = await describeQuotationError(error, 'Add quote line', 'POST /quotations/quote-lines/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const deleteLine = async (lineId) => {
    if (!window.confirm('Delete this quotation line?')) return;
    setSaving(true);
    setErrorInfo(null);
    try {
      await quotationAPI.lines.delete(lineId);
      await load();
    } catch (error) {
      const details = await describeQuotationError(error, 'Delete quote line', `DELETE /quotations/quote-lines/${lineId}/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const actionEndpoint = (label) => {
    const endpointNames = {
      'Submit Review': 'submit_review',
      Approve: 'approve',
      Finalize: 'finalize',
      'Mark Sent': 'mark_sent',
      'Create Revision': 'revise',
      Cancel: 'cancel',
    };
    return `POST /quotations/quotes/${quote.id}/${endpointNames[label] || label.toLowerCase()}/`;
  };

  const runAction = async (label, action) => {
    if ((label === 'Finalize' || label === 'Cancel') && !window.confirm(`${label} this quotation?`)) return;
    setSaving(true);
    setErrorInfo(null);
    try {
      const response = await action(quote.id);
      if (label === 'Create Revision' && response.data?.id) {
        window.alert(`Created revision ${response.data.quotation_number}`);
      }
      await load();
    } catch (error) {
      const details = await describeQuotationError(error, label, actionEndpoint(label));
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const downloadPdf = async () => {
    setErrorInfo(null);
    try {
      const response = await quotationAPI.quotes.pdf(quote.id);
      const url = window.URL.createObjectURL(new Blob([response.data], { type: 'application/pdf' }));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `${quote.quotation_number}.pdf`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (error) {
      const details = await describeQuotationError(error, 'Download quotation PDF', `GET /quotations/quotes/${quote.id}/pdf/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    }
  };

  if (loading) return <div className="qm-loading">Loading quotation...</div>;
  if (!quote) {
    return (
      <div className="qm-section">
        <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
        <div className="qm-empty">Quotation not found</div>
      </div>
    );
  }

  return (
    <div className="qm-editor">
      <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
      <div className="qm-editor-header">
        <div>
          <button type="button" className="qm-secondary small" onClick={onClose}>Back to List</button>
          <h3>{quote.quotation_number}</h3>
          <p>{quote.company_name} - {quote.status_display} - Version {quote.version}</p>
        </div>
        <div className="qm-action-row">
          {quote.status === 'draft' && <button type="button" className="qm-secondary" disabled={saving} onClick={() => runAction('Submit Review', quotationAPI.quotes.submitReview)}>Submit Review</button>}
          {['draft', 'pending_review'].includes(quote.status) && <button type="button" className="qm-secondary" disabled={saving} onClick={() => runAction('Approve', quotationAPI.quotes.approve)}>Approve</button>}
          {['draft', 'pending_review', 'approved'].includes(quote.status) && <button type="button" className="qm-primary" disabled={saving} onClick={() => runAction('Finalize', quotationAPI.quotes.finalize)}>Finalize</button>}
          {quote.status === 'finalized' && <button type="button" className="qm-secondary" disabled={saving} onClick={() => runAction('Mark Sent', quotationAPI.quotes.markSent)}>Mark Sent</button>}
          {['finalized', 'sent'].includes(quote.status) && <button type="button" className="qm-secondary" disabled={saving} onClick={() => runAction('Create Revision', quotationAPI.quotes.revise)}>Create Revision</button>}
          {!['revised', 'cancelled'].includes(quote.status) && <button type="button" className="qm-secondary danger" disabled={saving} onClick={() => runAction('Cancel', quotationAPI.quotes.cancel)}>Cancel</button>}
          <button type="button" className="qm-secondary" onClick={downloadPdf}>PDF</button>
        </div>
      </div>

      {!isEditable && (
        <div className="qm-notice">This quotation is locked. Create a revision to make changes.</div>
      )}

      <div className="qm-panel">
        <div className="qm-panel-heading">
          <h3>Lines</h3>
          <div className="qm-total">
            <span>Subtotal {quote.currency} {parseFloat(quote.subtotal).toFixed(2)}</span>
            <strong>Total {quote.currency} {parseFloat(quote.total).toFixed(2)}</strong>
          </div>
        </div>

        <div className="qm-table-wrap">
          <table className="qm-table line-table">
            <thead>
              <tr>
                <th>Item</th>
                <th>Snapshot Name</th>
                <th>Qty</th>
                <th>Unit</th>
                <th>Unit Price</th>
                <th>VAT %</th>
                <th>Status</th>
                <th>Total</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {quote.lines.map((line) => {
                const draft = lineDrafts[line.id] || {};
                return (
                  <tr key={line.id}>
                    <td>
                      <select disabled={!isEditable} value={draft.quote_item || ''} onChange={(event) => {
                        const item = items.find((candidate) => String(candidate.id) === event.target.value);
                        updateLineDraft(line.id, {
                          quote_item: event.target.value,
                          item_name_snapshot: item ? item.name : draft.item_name_snapshot,
                          unit: item?.unit || draft.unit,
                          match_status: event.target.value ? 'confirmed' : 'unresolved',
                        });
                        setHistoryItem(event.target.value);
                      }}>
                        <option value="">Unmatched</option>
                        {items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                      </select>
                    </td>
                    <td><input disabled={!isEditable} value={draft.item_name_snapshot || ''} onChange={(event) => updateLineDraft(line.id, { item_name_snapshot: event.target.value })} /></td>
                    <td><input disabled={!isEditable} type="number" min="0" step="0.001" value={draft.quantity || ''} onChange={(event) => updateLineDraft(line.id, { quantity: event.target.value })} /></td>
                    <td><input disabled={!isEditable} value={draft.unit || ''} onChange={(event) => updateLineDraft(line.id, { unit: event.target.value })} /></td>
                    <td><input disabled={!isEditable} type="number" min="0" step="0.01" value={draft.unit_price || ''} onChange={(event) => updateLineDraft(line.id, { unit_price: event.target.value })} /></td>
                    <td><input disabled={!isEditable} type="number" min="0" step="0.01" value={draft.vat_rate || '0'} onChange={(event) => updateLineDraft(line.id, { vat_rate: event.target.value })} /></td>
                    <td>
                      <select disabled={!isEditable} value={draft.match_status || 'unresolved'} onChange={(event) => updateLineDraft(line.id, { match_status: event.target.value })}>
                        <option value="unresolved">Unresolved</option>
                        <option value="confirmed">Confirmed</option>
                        <option value="ignored">Ignored</option>
                      </select>
                    </td>
                    <td>{quote.currency} {parseFloat(line.line_total || 0).toFixed(2)}</td>
                    <td className="qm-row-actions">
                      <button type="button" className="qm-secondary small" disabled={!isEditable || saving} onClick={() => saveLine(line.id)}>Save</button>
                      <button type="button" className="qm-secondary small" onClick={() => setHistoryItem(draft.quote_item || '')}>History</button>
                      <button type="button" className="qm-secondary small danger" disabled={!isEditable || saving} onClick={() => deleteLine(line.id)}>Delete</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {isEditable && (
          <form onSubmit={addLine} className="qm-add-line">
            <select value={lineForm.quote_item} onChange={(event) => {
              const item = items.find((candidate) => String(candidate.id) === event.target.value);
              setLineForm({
                ...lineForm,
                quote_item: event.target.value,
                item_name_snapshot: item ? item.name : lineForm.item_name_snapshot,
                unit: item?.unit || lineForm.unit,
                match_status: event.target.value ? 'confirmed' : 'unresolved',
              });
            }}>
              <option value="">Select item</option>
              {items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
            <input placeholder="Snapshot name" required value={lineForm.item_name_snapshot} onChange={(event) => setLineForm({ ...lineForm, item_name_snapshot: event.target.value })} />
            <input type="number" min="0" step="0.001" value={lineForm.quantity} onChange={(event) => setLineForm({ ...lineForm, quantity: event.target.value })} />
            <input placeholder="Unit" value={lineForm.unit} onChange={(event) => setLineForm({ ...lineForm, unit: event.target.value })} />
            <input type="number" min="0" step="0.01" placeholder="Price" value={lineForm.unit_price} onChange={(event) => setLineForm({ ...lineForm, unit_price: event.target.value })} />
            <button type="submit" className="qm-primary" disabled={saving}>Add Line</button>
          </form>
        )}
      </div>

      <div className="qm-grid-two bottom-panels">
        <PriceHistoryPanel companyId={quote.company} itemId={historyItem} />
        <AuditLogPanel quotationId={quote.id} />
      </div>
    </div>
  );
};

export default QuotationEditor;
