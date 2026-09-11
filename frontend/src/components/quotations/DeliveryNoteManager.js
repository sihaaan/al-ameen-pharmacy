import React, { useCallback, useEffect, useRef, useState } from 'react';
import quotationAPI, { describeQuotationError } from '../../api/quotations';
import QuotationErrorNotice from './QuotationErrorNotice';
import { releaseNumberWheelFocus } from '../../utils/numberInput';
import './DeliveryNoteManager.css';

const labels = {
  accepted: 'Accepted', in_progress: 'Awaiting receipt', partially_delivered: 'Partially delivered',
  completed: 'Completed', needs_review: 'Acceptance needs review', cancelled: 'Cancelled',
  draft: 'Draft', issued: 'Issued / awaiting receipt', delivered: 'Delivery confirmed',
};
const today = () => new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Dubai', year: 'numeric', month: '2-digit', day: '2-digit',
}).format(new Date());
const blankLine = () => ({ item_name: '', description: '', unit: '', quantity: '1', quotation_line: null });
const blankForm = () => ({
  company: '', quotation: null, delivery_date: today(), lpo_number: '', quotation_reference: '', invoice_number: '',
  delivery_address: '', attention: '', contact_phone: '', notes: '', lines: [blankLine()],
});
const badge = (state) => <span className={`dn-badge dn-badge-${state}`}>{labels[state] || state}</span>;
const qty = (value) => Number(value || 0).toLocaleString('en-GB', { maximumFractionDigits: 3 });

const DeliveryNoteManager = ({ onReviewOutcome, initialNote = null }) => {
  const [view, setView] = useState('orders');
  const [rows, setRows] = useState([]);
  const [summary, setSummary] = useState({});
  const [companies, setCompanies] = useState([]);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const [pagination, setPagination] = useState({ count: 0, next: null, previous: null });
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [errorInfo, setErrorInfo] = useState(null);
  const [feedback, setFeedback] = useState('');
  const [order, setOrder] = useState(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [note, setNote] = useState(null);
  const [form, setForm] = useState(blankForm);
  const [receipt, setReceipt] = useState(null);
  const [cancelOpen, setCancelOpen] = useState(false);
  const [cancelReason, setCancelReason] = useState('');
  const [lpoInput, setLpoInput] = useState({ file: null, text: '', useAI: true, documentType: 'auto' });
  const [lpoPreview, setLpoPreview] = useState(null);
  const [lpoImportToken, setLpoImportToken] = useState('');
  const detailRequest = useRef(0);
  const editorForm = useRef(null);
  const editorHeading = useRef(null);
  const lpoFileInput = useRef(null);
  useEffect(() => { if (!lpoInput.file && lpoFileInput.current) lpoFileInput.current.value = ''; }, [lpoInput.file]);

  const reportError = useCallback(async (error, operation) => {
    setErrorInfo(await describeQuotationError(error, operation, 'Orders & delivery notes'));
  }, []);

  useEffect(() => {
    let current = true;
    quotationAPI.companies.list().then(({ data }) => {
      if (current) setCompanies(Array.isArray(data) ? data : data.results || []);
    }).catch((error) => { if (current) reportError(error, 'Load delivery customers'); });
    return () => { current = false; };
  }, [reportError]);

  useEffect(() => {
    let current = true;
    setLoading(true);
    const endpoint = view === 'orders' ? quotationAPI.deliveryOrders : quotationAPI.deliveryNotes;
    endpoint.list({ search, status, page }).then(({ data }) => {
      if (!current) return;
      setRows(data.results || []);
      setPagination({ count: data.count, next: data.next, previous: data.previous });
      if (data.summary) setSummary(data.summary);
    }).catch((error) => { if (current) reportError(error, 'Load orders and deliveries'); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [view, search, status, page, revision, reportError]);

  const showNote = useCallback((data) => {
    detailRequest.current += 1;
    setLpoPreview(null); setLpoImportToken('');
    setLpoInput({ file: null, text: '', useAI: true, documentType: 'auto' });
    setNote(data);
    setForm({
      ...blankForm(), ...data,
      lines: (data.lines || []).map((line) => ({ ...line })),
    });
    setReceipt({
      received_by: '', received_date: today(), receipt_reference: '', receipt_notes: '',
      lines: (data.lines || []).map((line) => ({ id: line.id, received_quantity: line.quantity })),
    });
    setEditorOpen(true);
    setOrder(null);
    setCancelOpen(false);
    setCancelReason('');
  }, []);

  useEffect(() => {
    if (!initialNote) return;
    showNote(initialNote);
    setFeedback('Acceptance approved. Review the quantities for this delivery, then save and issue the DO.');
  }, [initialNote, showNote]);

  const run = async (operation, work) => {
    if (busy) return;
    setBusy(true);
    setErrorInfo(null);
    setFeedback('');
    try { await work(); } catch (error) { await reportError(error, operation); }
    finally { setBusy(false); }
  };

  const openNote = (id) => run('Open delivery note', async () => {
    const requestId = ++detailRequest.current;
    const { data } = await quotationAPI.deliveryNotes.retrieve(id);
    if (requestId === detailRequest.current) showNote(data);
  });
  const openOrder = (id) => run('Open accepted order', async () => {
    const requestId = ++detailRequest.current;
    const { data } = await quotationAPI.deliveryOrders.retrieve(id);
    if (requestId !== detailRequest.current) return;
    setOrder(data);
    setEditorOpen(false);
  });
  const switchView = (next) => {
    detailRequest.current += 1;
    setView(next); setPage(1); setStatus(''); setSearch('');
    setOrder(null); setEditorOpen(false); setErrorInfo(null); setFeedback('');
  };
  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }));
  const updateLine = (index, key, value) => setForm((current) => ({
    ...current, lines: current.lines.map((line, i) => i === index ? { ...line, [key]: value } : line),
  }));

  const newNote = () => {
    detailRequest.current += 1;
    setNote(null); setForm(blankForm()); setOrder(null); setEditorOpen(true);
    setErrorInfo(null); setFeedback(''); setCancelOpen(false);
    setLpoPreview(null); setLpoImportToken('');
    setLpoInput({ file: null, text: '', useAI: true, documentType: 'auto' });
    window.requestAnimationFrame(() => {
      editorHeading.current?.focus({ preventScroll: true });
      editorHeading.current?.scrollIntoView?.({
        block: 'start',
        behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',
      });
    });
  };
  const parseDocument = () => run('Read source document for delivery note', async () => {
    const requestId = detailRequest.current;
    const payload = new FormData();
    if (lpoInput.file) payload.append('file', lpoInput.file);
    else payload.append('text', lpoInput.text);
    payload.append('use_ai', String(lpoInput.useAI));
    payload.append('document_type', lpoInput.documentType);
    const { data } = await quotationAPI.deliveryNotes.parseDocument(payload, true);
    if (requestId !== detailRequest.current) return;
    setLpoPreview(data);
  });
  const applyLpo = () => {
    const details = lpoPreview.details || {};
    setForm((current) => ({
      ...current,
      company: current.company || String(lpoPreview.company || ''),
      lpo_number: details.lpo_number || '',
      quotation_reference: details.quotation_number || '',
      ...Object.fromEntries(['delivery_address', 'attention', 'contact_phone']
        .filter((key) => details[key]).map((key) => [key, details[key]])),
      lines: lpoPreview.lines.map((line) => ({ ...line })),
    }));
    setLpoImportToken(lpoPreview.import_token);
    setFeedback(lpoPreview.lines.length + ' document items filled in. Review the customer, delivery date and quantities before saving.');
    setLpoPreview(null);
    window.requestAnimationFrame(() => editorForm.current?.querySelector('input, select')?.focus());
  };
  const createFromOrder = () => run('Create order delivery note', async () => {
    const lines = order.lines.filter((line) => Number(line.available_quantity) > 0).map((line) => ({
      quotation_line: line.id, quantity: line.available_quantity,
    }));
    const { data } = await quotationAPI.deliveryNotes.create({ quotation: order.id, lines });
    showNote(data);
    setFeedback('Draft created from the remaining accepted quantities. Review the quantities before issuing.');
    setRevision((value) => value + 1);
  });

  const saveDraft = async () => {
    const payload = {
      company: form.company, quotation: form.quotation || null,
      delivery_date: form.delivery_date, lpo_number: form.lpo_number, invoice_number: form.invoice_number,
      quotation_reference: form.quotation_reference,
      delivery_address: form.delivery_address, attention: form.attention, contact_phone: form.contact_phone,
      notes: form.notes,
      ...(lpoImportToken ? { lpo_import_token: lpoImportToken } : {}),
      lines: form.lines.map((line) => ({
        quotation_line: line.quotation_line || null, item_name: line.item_name,
        description: line.description || '', unit: line.unit || '', quantity: line.quantity,
      })),
    };
    const response = note?.id
      ? await quotationAPI.deliveryNotes.update(note.id, payload)
      : await quotationAPI.deliveryNotes.create(payload);
    showNote(response.data);
    setRevision((value) => value + 1);
    return response.data;
  };
  const submitDraft = (event, issue = false) => {
    event.preventDefault();
    if (lpoPreview || busy) return;
    run(issue ? 'Issue delivery note' : 'Save delivery note', async () => {
      const saved = await saveDraft();
      if (issue) {
        const { data } = await quotationAPI.deliveryNotes.issue(saved.id);
        showNote(data);
        setRevision((value) => value + 1);
      }
      setFeedback(issue ? 'Delivery note issued. Confirm receipt when the customer receives the goods.' : 'Delivery note saved as a draft.');
    });
  };
  const download = () => run('Download delivery note', async () => {
    const { data } = await quotationAPI.deliveryNotes.pdf(note.id);
    const url = URL.createObjectURL(new Blob([data], { type: 'application/pdf' }));
    const link = document.createElement('a');
    link.href = url; link.download = `${note.delivery_number}.pdf`;
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  const confirmReceipt = (event) => {
    event.preventDefault();
    run('Confirm received quantities', async () => {
      const { data } = await quotationAPI.deliveryNotes.confirmReceipt(note.id, receipt);
      showNote(data);
      setRevision((value) => value + 1);
      setFeedback('Receipt recorded. Order completion now reflects the quantities actually received.');
    });
  };
  const confirmCancellation = (event) => {
    event.preventDefault();
    run('Cancel delivery note', async () => {
      const { data } = await quotationAPI.deliveryNotes.cancel(note.id, { reason: cancelReason });
      showNote(data); setRevision((value) => value + 1);
      setFeedback('Note cancelled. Its history is retained and the order quantities have been recalculated.');
    });
  };
  const editable = !note || note.status === 'draft';
  const draftChanged = note?.status === 'draft' && (
    Boolean(lpoImportToken) ||
    ['delivery_date', 'lpo_number', 'quotation_reference', 'invoice_number', 'delivery_address', 'attention', 'contact_phone', 'notes']
      .some((key) => (form[key] || '') !== (note[key] || ''))
    || JSON.stringify(form.lines) !== JSON.stringify(note.lines)
  );
  const states = view === 'orders'
    ? ['accepted', 'in_progress', 'partially_delivered', 'completed', 'needs_review', 'cancelled']
    : ['draft', 'issued', 'delivered', 'cancelled'];

  return <div className="dn-manager">
    <div className="qm-panel-heading">
      <div><h3>Orders & delivery notes</h3><p>Track accepted quantities through dispatch and confirmed receipt.</p></div>
      <button className="qm-primary" onClick={newNote} disabled={busy}>New standalone delivery note</button>
    </div>
    <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
    {feedback && <p className="dn-feedback" role="status">{feedback}</p>}
    <div className="dn-view-switch" role="group" aria-label="Delivery workspace">
      <button aria-pressed={view === 'orders'} onClick={() => switchView('orders')} disabled={busy}>Accepted orders</button>
      <button aria-pressed={view === 'notes'} onClick={() => switchView('notes')} disabled={busy}>Delivery notes</button>
    </div>
    {view === 'orders' && <>
      <div className="dn-stats">
        {['accepted', 'in_progress', 'partially_delivered', 'completed'].map((state) => <button key={state} onClick={() => { setStatus(state); setPage(1); }} aria-pressed={status === state}>
          <span>{labels[state]}</span><strong>{summary[state] || 0}</strong>
        </button>)}
      </div>
      <p className="dn-help">Acceptance comes from reviewed quotation outcomes or confirmed LPOs. An order is completed only when all accepted quantities have confirmed receipts. Standalone notes are tracked separately; historical deliveries need to be recorded.</p>
    </>}
    <div className="dn-filters">
      <label>Search<input value={search} placeholder="Customer, quotation, LPO or note" onChange={(event) => { setSearch(event.target.value); setPage(1); }} /></label>
      <label>Status<select value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}><option value="">All statuses</option>{states.map((value) => <option key={value} value={value}>{labels[value]}</option>)}</select></label>
      <button onClick={() => setRevision((value) => value + 1)} disabled={loading}>Refresh</button>
    </div>
    {loading ? <p role="status">Loading {view === 'orders' ? 'orders' : 'delivery notes'}…</p> : <div className="qm-table-wrap">
      <table className="qm-table"><thead><tr><th>{view === 'orders' ? 'Quotation / order' : 'Delivery note'}</th><th>Customer</th><th>Status</th><th>{view === 'orders' ? 'Delivered lines' : 'References'}</th><th>Action</th></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.id}>
          <td>{view === 'orders' ? row.quotation_number : row.delivery_number}</td><td>{row.company_name}</td>
          <td>{badge(view === 'orders' ? row.delivery_status : row.status)}</td>
          <td>{view === 'orders' ? `${row.completed_line_count} / ${row.line_count}` : [row.lpo_number, row.quotation_number || row.quotation_reference, row.invoice_number].filter(Boolean).join(' / ') || '—'}</td>
          <td><button disabled={busy} onClick={() => view === 'orders' ? openOrder(row.id) : openNote(row.id)}>{view === 'orders' ? 'View order' : 'Open note'}</button></td>
        </tr>)}{!rows.length && <tr><td colSpan="5">No {view === 'orders' ? 'accepted orders' : 'delivery notes'} match these filters.</td></tr>}</tbody>
      </table>
    </div>}
    <div className="dn-pagination"><span>{pagination.count || 0} records · Page {page}</span><button disabled={!pagination.previous || loading} onClick={() => setPage((value) => value - 1)}>Previous</button><button disabled={!pagination.next || loading} onClick={() => setPage((value) => value + 1)}>Next</button></div>

    {order && <section className="qm-panel dn-detail" aria-label="Order delivery progress">
      <div className="qm-panel-heading"><div><h3>{order.quotation_number} · {order.company_name}</h3><p>{order.lpo_numbers.join(' · ') || 'No LPO reference recorded'}</p></div>{badge(order.delivery_status)}</div>
      <div className="qm-table-wrap"><table className="qm-table"><thead><tr><th>Item</th><th>Unit</th><th>Accepted</th><th>Received</th><th>Awaiting receipt</th><th>Still to deliver</th></tr></thead><tbody>
        {order.lines.map((line) => <tr key={line.id}><td>{line.item_name}</td><td>{line.unit}</td><td>{qty(line.accepted_quantity)}</td><td>{qty(line.delivered_quantity)}</td><td>{qty(line.issued_quantity)}</td><td>{qty(line.remaining_quantity)}</td></tr>)}
      </tbody></table></div>
      {order.delivery_status === 'needs_review' && <p>Record the accepted line quantities in the quotation outcome before creating a delivery note.</p>}
      <div className="dn-actions">
        {onReviewOutcome && <button onClick={() => onReviewOutcome(order.id)}>Manage order</button>}
        <button className="qm-primary" disabled={busy || order.delivery_status === 'cancelled' || !order.lines.some((line) => Number(line.available_quantity) > 0)} onClick={createFromOrder}>Create delivery note for remaining items</button>
        <button onClick={() => setOrder(null)}>Close order</button>
      </div>
    </section>}

    {editorOpen && <section className="qm-panel dn-detail" aria-label="Delivery note editor">
      <div className="qm-panel-heading"><div><h3 ref={editorHeading} className="dn-editor-title" tabIndex={-1}>{note?.delivery_number || 'New delivery note'}</h3><p>{note?.quotation_number ? `Linked to ${note.quotation_number}` : 'Standalone delivery document'}</p></div>{badge(note?.status || 'draft')}</div>
      {editable && !form.quotation && <section className="dn-lpo-import" aria-label="Import delivery items from LPO or quotation">
        <div><h4>Fill from an LPO or quotation</h4><p>Upload either document, review the detected items, then fill the delivery note. A quotation does not need an LPO.</p></div>
        {!lpoPreview ? <>
          <div className="dn-form-grid">
            <label>Source file<input ref={lpoFileInput} type="file" accept=".pdf,.xlsx,.xls,.xlsb,.png,.jpg,.jpeg,.webp" disabled={busy}
              onChange={(event) => setLpoInput((current) => ({ ...current, file: event.target.files?.[0] || null, text: '' }))} /></label>
            <label>Or paste document text<textarea value={lpoInput.text} disabled={busy || Boolean(lpoInput.file)}
              placeholder="Paste the LPO, quotation or item table"
              onChange={(event) => setLpoInput((current) => ({ ...current, text: event.target.value }))} /></label>
          </div>
          {lpoInput.file && <p>Selected: {lpoInput.file.name}</p>}
          <div className="dn-actions">
            <label>Document type<select value={lpoInput.documentType} disabled={busy}
              onChange={(event) => setLpoInput((current) => ({ ...current, documentType: event.target.value }))}>
              <option value="auto">Detect automatically</option><option value="lpo">LPO / purchase order</option><option value="quotation">Quotation</option>
            </select></label>
            <label className="dn-ai-toggle"><input type="checkbox" checked={lpoInput.useAI} disabled={busy}
              onChange={(event) => setLpoInput((current) => ({ ...current, useAI: event.target.checked }))} />Use AI to read and clean up the document</label>
            <button type="button" className="qm-primary" disabled={busy || (!lpoInput.file && !lpoInput.text.trim())} onClick={parseDocument}>{busy ? 'Reading document…' : 'Read document'}</button>
            {lpoInput.file && <button type="button" disabled={busy} onClick={() => setLpoInput({ ...lpoInput, file: null, text: '' })}>Use pasted text instead</button>}
          </div>
        </> : <div className="dn-lpo-preview" role="region" aria-label="Detected document details">
          <div className="dn-form-grid">
            <div><strong>{lpoPreview.details.customer_name || 'Customer needs selection'}</strong>
              <p>{lpoPreview.document_type === 'quotation' ? 'Quotation' : 'LPO'} · {lpoPreview.lines.length} items</p>
              {lpoPreview.details.lpo_number && <p>LPO: {lpoPreview.details.lpo_number}</p>}
              {lpoPreview.details.quotation_number && <p>Quotation: {lpoPreview.details.quotation_number}</p>}
            </div>
            <div><strong>Deliver to</strong><p className="dn-preserve-lines">{lpoPreview.details.delivery_address || 'Address needs review'}</p><p>{lpoPreview.details.attention}</p></div>
          </div>
          {lpoPreview.details.requested_delivery_date && <p>Requested delivery date on document: {lpoPreview.details.requested_delivery_date}. Set the actual delivery date below.</p>}
          {!!lpoPreview.warnings?.length && <ul className="dn-lpo-warnings">{lpoPreview.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
          <div className="qm-table-wrap dn-lpo-items"><table className="qm-table"><thead><tr><th>Detected item</th><th>Quantity</th><th>Unit</th></tr></thead><tbody>
            {lpoPreview.lines.map((line, index) => <tr key={index}><td>{line.item_name}</td><td>{line.quantity || 'Check quantity'}</td><td>{line.unit}</td></tr>)}
          </tbody></table></div>
          <div className="dn-actions"><button type="button" className="qm-primary" onClick={applyLpo}>{form.lines.some((line) => line.item_name.trim()) ? 'Replace items with this document' : 'Fill delivery note'}</button>
            <button type="button" onClick={() => setLpoPreview(null)}>Discard preview</button></div>
        </div>}
      </section>}
      {form.quotation && editable && <p className="dn-help">Items and quantities are filled from the approved order. To upload or change an LPO, use Manage order on the quotation.
        {onReviewOutcome && <button type="button" disabled={busy} onClick={() => onReviewOutcome(form.quotation)}>Manage order / upload LPO</button>}</p>}
      {note?.lpo_source?.source_filename && <p className="dn-help">Source document: {note.lpo_source.source_filename}</p>}
      <form onSubmit={submitDraft} ref={editorForm}>
        <fieldset disabled={!editable || busy}>
          <div className="dn-form-grid">
            <label>Customer<select required value={form.company} disabled={Boolean(note?.id || form.quotation)} onChange={(event) => {
              const company = companies.find((entry) => String(entry.id) === event.target.value);
              setForm((current) => ({ ...current, company: event.target.value, delivery_address: current.delivery_address || company?.billing_address || '' }));
            }}><option value="">Select customer</option>{companies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}</select></label>
            <label>Delivery date<input type="date" required value={form.delivery_date} onChange={(event) => update('delivery_date', event.target.value)} /></label>
            <label>LPO number<input maxLength={120} value={form.lpo_number} onChange={(event) => update('lpo_number', event.target.value)} /></label>
            <label>Quotation reference<input maxLength={120} value={form.quotation ? form.quotation_number || form.quotation_reference : form.quotation_reference} readOnly={Boolean(form.quotation)} onChange={(event) => update('quotation_reference', event.target.value)} /></label>
            <label>Invoice reference<input maxLength={120} value={form.invoice_number} onChange={(event) => update('invoice_number', event.target.value)} /></label>
            <label>Attention<input maxLength={255} value={form.attention} onChange={(event) => update('attention', event.target.value)} /></label>
            <label>Contact phone<input maxLength={100} value={form.contact_phone} onChange={(event) => update('contact_phone', event.target.value)} /></label>
            <label className="dn-wide">Delivery address<textarea maxLength={2000} value={form.delivery_address} onChange={(event) => update('delivery_address', event.target.value)} /></label>
          </div>
          <div className="qm-table-wrap"><table className="qm-table"><thead><tr><th>Item / description</th><th>Quantity</th><th>Unit</th>{editable && <th>Action</th>}</tr></thead><tbody>
            {form.lines.map((line, index) => <tr key={line.id || index}>
              <td><input aria-label={`Item ${index + 1}`} required maxLength={255} value={line.item_name} readOnly={Boolean(form.quotation)} onChange={(event) => updateLine(index, 'item_name', event.target.value)} /><textarea aria-label={`Description ${index + 1}`} maxLength={2000} value={line.description || ''} readOnly={Boolean(form.quotation)} onChange={(event) => updateLine(index, 'description', event.target.value)} /></td>
              <td><input aria-label={`Quantity ${index + 1}`} className="dn-quantity" type="number" min="0.001" max="999999999.999" step="0.001" required value={line.quantity} onWheel={releaseNumberWheelFocus} onChange={(event) => updateLine(index, 'quantity', event.target.value)} /></td>
              <td><input aria-label={`Unit ${index + 1}`} maxLength={50} value={line.unit} readOnly={Boolean(form.quotation)} onChange={(event) => updateLine(index, 'unit', event.target.value)} /></td>
              {editable && <td><button type="button" onClick={() => update('lines', form.lines.filter((_, i) => i !== index))} aria-label={`Remove item ${index + 1}`}>Remove</button></td>}
            </tr>)}
          </tbody></table></div>
          {!form.quotation && <button type="button" disabled={form.lines.length >= 300} onClick={() => update('lines', [...form.lines, blankLine()])}>Add item</button>}
          <label className="dn-notes">Delivery instructions<textarea maxLength={4000} value={form.notes} onChange={(event) => update('notes', event.target.value)} /></label>
        </fieldset>
        {editable && <div className="dn-actions"><button type="submit" disabled={busy || Boolean(lpoPreview) || !form.lines.length}>Save draft</button><button className="qm-primary" type="button" disabled={busy || Boolean(lpoPreview) || !form.lines.length} onClick={(event) => {
          if (event.currentTarget.form.reportValidity()) submitDraft(event, true);
        }}>Save & issue delivery note</button></div>}
      </form>
      <div className="dn-actions">
        {note && <button disabled={busy || draftChanged || Boolean(lpoPreview)} onClick={download}>Download {note.status === 'draft' ? 'draft ' : ''}PDF</button>}
        {note && note.status !== 'cancelled' && <button disabled={busy} onClick={() => setCancelOpen(!cancelOpen)}>Cancel note</button>}
        <button disabled={busy} onClick={() => setEditorOpen(false)}>Close note</button>
      </div>
      {draftChanged && <p className="dn-help">Save your changes before downloading the PDF.</p>}
      {note?.status === 'issued' && receipt && <form className="dn-receipt" onSubmit={confirmReceipt} aria-label="Confirm delivery receipt">
        <h4>Confirm what the customer received</h4><p>Record shortages here. Only the quantities received count toward order completion.</p>
        <div className="dn-form-grid">
          <label>Received by<input required maxLength={255} value={receipt.received_by} onChange={(event) => setReceipt({ ...receipt, received_by: event.target.value })} /></label>
          <label>Receipt date<input type="date" required max={today()} value={receipt.received_date} onChange={(event) => setReceipt({ ...receipt, received_date: event.target.value })} /></label>
          <label className="dn-wide">Signed note / receipt reference<input maxLength={255} placeholder="Optional filing reference for the signed delivery note" value={receipt.receipt_reference} onChange={(event) => setReceipt({ ...receipt, receipt_reference: event.target.value })} /></label>
        </div>
        <div className="qm-table-wrap"><table className="qm-table"><thead><tr><th>Item</th><th>Issued</th><th>Received quantity</th></tr></thead><tbody>{note.lines.map((line, index) => <tr key={line.id}><td>{line.item_name}</td><td>{qty(line.quantity)} {line.unit}</td><td><input aria-label={`Received quantity ${index + 1}`} type="number" min="0" max={line.quantity} step="0.001" required value={receipt.lines[index].received_quantity} onWheel={releaseNumberWheelFocus} onChange={(event) => setReceipt({ ...receipt, lines: receipt.lines.map((row, i) => i === index ? { ...row, received_quantity: event.target.value } : row) })} /></td></tr>)}</tbody></table></div>
        <label>Receipt notes<textarea maxLength={4000} value={receipt.receipt_notes} onChange={(event) => setReceipt({ ...receipt, receipt_notes: event.target.value })} /></label>
        <button className="qm-primary" disabled={busy} type="submit">Confirm received quantities</button>
      </form>}
      {note?.status === 'delivered' && <div className="dn-receipt"><h4>Receipt recorded</h4><p>{note.received_by} · {note.received_date}{note.receipt_reference ? ` · ${note.receipt_reference}` : ''}</p>
        <ul>{note.lines.map((line) => <li key={line.id}>{line.item_name}: {qty(line.received_quantity)} of {qty(line.quantity)} {line.unit} received</li>)}</ul><p>{note.receipt_notes}</p></div>}
      {note?.status === 'cancelled' && <p className="dn-help">Cancellation reason: {note.cancellation_reason}</p>}
      {cancelOpen && <form className="dn-cancel" onSubmit={confirmCancellation}>
        <p>Cancellation removes this note’s quantities from order progress and keeps its history.</p>
        <label>Cancellation reason<textarea required maxLength={4000} value={cancelReason} onChange={(event) => setCancelReason(event.target.value)} /></label>
        <button disabled={busy} type="submit">Confirm cancellation</button><button type="button" onClick={() => setCancelOpen(false)}>Keep note</button>
      </form>}
    </section>}
  </div>;
};

export default DeliveryNoteManager;
