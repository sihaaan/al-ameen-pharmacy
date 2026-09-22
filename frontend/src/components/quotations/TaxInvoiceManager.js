import React, { useCallback, useEffect, useRef, useState } from 'react';
import quotationAPI, { describeQuotationError } from '../../api/quotations';
import CompanySelectWithCreate from './CompanySelectWithCreate';
import QuotationErrorNotice from './QuotationErrorNotice';
import { releaseNumberWheelFocus } from '../../utils/numberInput';
import './TaxInvoiceManager.css';

const api = quotationAPI.taxInvoices;
const today = () => new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Dubai' });
const newLine = () => ({ item_name: '', description: '', quantity: '1', unit: '', unit_price: '', vat_rate: '', discount: '0' });
const newInvoice = () => ({ company: '', customer_name: '', customer_address: '', customer_trn: '', attention: '',
  invoice_date: today(), supply_date: today(), quotation_reference: '', lpo_number: '', notes: '', currency: 'AED', lines: [newLine()] });
const amount = (value) => `AED ${Number(value || 0).toLocaleString('en-AE', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const toForm = (data) => ({ ...data, lines: data.lines.map((line) => ({ ...line, unit_price: line.unit_price ?? '', vat_rate: line.vat_rate ?? '' })) });

export default function TaxInvoiceManager() {
  const [companies, setCompanies] = useState([]);
  const [invoices, setInvoices] = useState([]);
  const [page, setPage] = useState(1);
  const [count, setCount] = useState(0);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [form, setForm] = useState(null);
  const [saved, setSaved] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [feedback, setFeedback] = useState('');
  const [file, setFile] = useState(null);
  const [text, setText] = useState('');
  const [preview, setPreview] = useState(null);
  const [reviewed, setReviewed] = useState(false);
  const editor = useRef(null);
  const listVersion = useRef(0);
  const locked = saved?.status === 'issued';
  const fieldDisabled = locked || Boolean(busy);
  const reportError = useCallback(async (err, action, endpoint = '/quotations/tax-invoices/') => {
    setError(await describeQuotationError(err, action, endpoint));
  }, []);

  const loadList = useCallback(async () => {
    const version = ++listVersion.current;
    setLoading(true);
    try {
      const { data } = await api.list({ page, search, status });
      if (version === listVersion.current) { setInvoices(data.results); setCount(data.count); }
    } catch (err) { if (version === listVersion.current) await reportError(err, 'Load tax invoices'); }
    finally { if (version === listVersion.current) setLoading(false); }
  }, [page, search, status, reportError]);

  useEffect(() => { const timer = setTimeout(loadList, 250); return () => clearTimeout(timer); }, [loadList]);
  useEffect(() => {
    quotationAPI.companies.list().then(({ data }) => setCompanies(data.results || data))
      .catch((err) => reportError(err, 'Load customers', '/quotations/companies/'));
  }, [reportError]);
  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (event) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const focusEditor = () => requestAnimationFrame(() => {
    editor.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
    editor.current?.focus({ preventScroll: true });
  });
  const canLeave = () => !dirty || window.confirm('Discard the unsaved changes to this invoice?');
  const resetScan = () => { setFile(null); setText(''); setPreview(null); setReviewed(false); };
  const start = () => {
    if (!canLeave()) return;
    setForm(newInvoice()); setSaved(null); setDirty(false); setError(null); setFeedback(''); resetScan(); focusEditor();
  };
  const open = async (id) => {
    if (!canLeave()) return;
    setBusy('open'); setError(null);
    try {
      const { data } = await api.retrieve(id);
      setSaved(data); setForm(toForm(data)); setDirty(false); setFeedback(''); resetScan(); focusEditor();
    } catch (err) { await reportError(err, 'Open tax invoice'); }
    finally { setBusy(''); }
  };
  const change = (key, value) => {
    setForm((current) => ({ ...current, [key]: value })); setDirty(true); setReviewed(false); setFeedback('');
  };
  const changeLine = (index, key, value) => change('lines', form.lines.map((line, i) => i === index ? { ...line, [key]: value } : line));
  const loadCompany = async (id, initial) => {
    let match = initial || companies.find((entry) => String(entry.id) === String(id));
    if (id && !Object.prototype.hasOwnProperty.call(match || {}, 'billing_address')) {
      const { data } = await quotationAPI.companies.retrieve(id);
      match = data;
      setCompanies((current) => [...current.filter((entry) => entry.id !== data.id), data]);
    }
    return match;
  };
  const selectCompany = async (id, company) => {
    setBusy('customer'); setError(null);
    try {
      const match = await loadCompany(id, company);
      setForm((current) => ({ ...current, company: id, customer_name: match?.name || '',
        customer_address: match?.billing_address || '', customer_trn: match?.trn || '' }));
      setDirty(true); setReviewed(false);
    } catch (err) { await reportError(err, 'Load customer details', '/quotations/companies/'); }
    finally { setBusy(''); }
  };

  const parse = async () => {
    setBusy('scan'); setError(null); setPreview(null);
    try {
      const data = new FormData();
      if (file) data.append('file', file); else data.append('text', text);
      data.append('use_ai', 'true');
      const response = await api.parseDocument(data);
      setPreview(response.data);
    } catch (err) { await reportError(err, 'Scan quotation', 'POST /quotations/tax-invoices/parse-document/'); }
    finally { setBusy(''); }
  };
  const applyPreview = async () => {
    setBusy('apply'); setError(null);
    try {
      const details = preview.details || {};
      const match = await loadCompany(preview.company);
      setForm((current) => ({ ...current, company: preview.company || '',
        customer_name: details.customer_name || match?.name || '',
        customer_address: details.customer_address || match?.billing_address || '',
        customer_trn: details.customer_trn || match?.trn || '', attention: details.attention || '',
        quotation_reference: details.quotation_number || '', lpo_number: details.lpo_number || '',
        import_token: preview.import_token, source_filename: preview.source_filename,
        lines: preview.lines.map((line) => ({ ...line, unit_price: line.unit_price ?? '', vat_rate: line.vat_rate ?? '' })),
      }));
      setDirty(true); setReviewed(false); setPreview(null);
      setFeedback('Quotation loaded. Check the customer, items, prices, discounts and VAT below, then save the draft.');
    } catch (err) { await reportError(err, 'Load quotation customer', '/quotations/companies/'); }
    finally { setBusy(''); }
  };

  const save = async (event) => {
    event.preventDefault(); setBusy('save'); setError(null);
    const keys = ['company', 'customer_name', 'customer_address', 'customer_trn', 'attention', 'invoice_date', 'supply_date',
      'quotation_reference', 'lpo_number', 'notes', 'currency', 'import_token'];
    const payload = Object.fromEntries(keys.filter((key) => form[key] !== undefined).map((key) => [key, form[key]]));
    payload.lines = form.lines.map((line) => ({ item_name: line.item_name, description: line.description || '', quantity: line.quantity,
      unit: line.unit, unit_price: line.unit_price === '' ? null : line.unit_price, vat_rate: line.vat_rate === '' ? null : line.vat_rate,
      discount: line.discount || '0' }));
    if (saved) payload.expected_revision = saved.revision;
    try {
      const { data } = saved ? await api.update(saved.id, payload) : await api.create(payload);
      setSaved(data); setForm(toForm(data)); setDirty(false); setReviewed(false); setFeedback('Draft saved. Review it before issuing.');
      await loadList();
    } catch (err) { await reportError(err, 'Save tax invoice'); }
    finally { setBusy(''); }
  };
  const issue = async () => {
    setBusy('issue'); setError(null);
    try {
      const { data } = await api.issue(saved.id, saved.revision);
      setSaved(data); setForm(toForm(data)); setDirty(false); setFeedback(`${data.invoice_number} issued. The original PDF is now saved.`);
      await loadList();
    } catch (err) { await reportError(err, 'Issue tax invoice'); }
    finally { setBusy(''); }
  };
  const download = async () => {
    setBusy('pdf'); setError(null);
    try {
      const { data } = await api.pdf(saved.id);
      const url = URL.createObjectURL(data);
      const link = document.createElement('a'); link.href = url; link.download = `${saved.invoice_number || `DRAFT-${saved.id}`}.pdf`;
      document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000);
    } catch (err) { await reportError(err, 'Download invoice PDF'); }
    finally { setBusy(''); }
  };
  const missing = form?.lines.filter((line) => line.unit_price === '' || line.vat_rate === '').length || 0;
  const input = (label, key, options = {}) => <label className="ti-field">{label}
    <input className="qm-input" value={form[key] || ''} onChange={(event) => change(key, event.target.value)} disabled={fieldDisabled} {...options} />
  </label>;

  return <div className="ti-manager">
    <section className="qm-panel">
      <div className="qm-panel-heading"><div><h3>Tax invoices</h3><p>Scan a quotation or add items manually. Review, issue and download.</p></div>
        <button className="qm-primary" disabled={Boolean(busy)} onClick={start}>New tax invoice</button></div>
      <div className="ti-filters">
        <input className="qm-input" aria-label="Search tax invoices" placeholder="Search invoice, customer or reference" value={search}
          onChange={(event) => { setSearch(event.target.value); setPage(1); }} />
        <select className="qm-input" aria-label="Invoice status" value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}>
          <option value="">All statuses</option><option value="draft">Drafts</option><option value="issued">Issued</option></select>
      </div>
      <div className="qm-table-wrap"><table className="qm-table"><thead><tr><th>Invoice</th><th>Customer</th><th>Date</th><th>Status</th><th>Total</th><th>Action</th></tr></thead>
        <tbody>{invoices.map((invoice) => <tr key={invoice.id}><td>{invoice.invoice_number || `Draft #${invoice.id}`}</td><td>{invoice.customer_name}</td>
          <td>{invoice.invoice_date}</td><td>{invoice.status === 'issued' ? 'Issued' : 'Draft'}</td><td>{amount(invoice.total)}</td>
          <td><button className="qm-secondary small" disabled={Boolean(busy)} onClick={() => open(invoice.id)}>Open invoice</button></td></tr>)}</tbody></table></div>
      {loading ? <p role="status">Loading invoices…</p> : !invoices.length && <p>No tax invoices found. Create one above.</p>}
      {count > 30 && <div className="ti-actions"><button className="qm-secondary" disabled={page === 1 || loading} onClick={() => setPage(page - 1)}>Previous</button>
        <span>Page {page} of {Math.ceil(count / 30)}</span><button className="qm-secondary" disabled={page * 30 >= count || loading} onClick={() => setPage(page + 1)}>Next</button></div>}
    </section>
    {!form && <QuotationErrorNotice error={error} onDismiss={() => setError(null)} />}
    {form && <section className="qm-panel ti-editor" ref={editor} tabIndex={-1} aria-label="Tax invoice editor">
      <div className="qm-panel-heading"><div><h3>{saved?.invoice_number || (saved ? `Draft #${saved.id}` : 'New tax invoice')}</h3>
        <p>{locked ? 'Issued invoice · Original details and PDF are locked.' : 'Start with a quotation scan, or enter the customer and items below.'}</p></div>
        <button className="qm-secondary" disabled={Boolean(busy)} onClick={() => { if (canLeave()) { setForm(null); setDirty(false); } }}>Back to invoices</button></div>
      {!locked && <details className="ti-scan" open={!saved && !form.source_filename}>
        <summary>Scan or paste a quotation</summary>
        <p>Upload a PDF, photo or spreadsheet. Extraction creates a preview for you to check.</p>
        <label className="ti-field">Quotation file<input type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.xlsx,.xls,.xlsb" disabled={Boolean(busy)}
          onChange={(event) => { setFile(event.target.files[0] || null); setPreview(null); }} /></label>
        <label className="ti-field">Or paste quotation text<textarea className="qm-input" rows={3} value={text} disabled={Boolean(busy) || Boolean(file)}
          onChange={(event) => { setText(event.target.value); setPreview(null); }} /></label>
        <button type="button" className="qm-secondary" onClick={parse} disabled={Boolean(busy) || (!file && !text.trim())}>{busy === 'scan' ? 'Scanning quotation…' : 'Scan quotation'}</button>
        {preview && <div className="ti-preview" role="region" aria-label="Quotation scan preview"><strong>{preview.lines.length} items detected · {preview.details.customer_name || 'Customer needs confirmation'}</strong>
          <ul>{preview.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>
          <p>{preview.lines.slice(0, 5).map((line) => line.item_name).join(' · ')}{preview.lines.length > 5 ? '…' : ''}</p>
          <button type="button" className="qm-primary" disabled={Boolean(busy)} onClick={applyPreview}>Use these items{form.lines.some((line) => line.item_name) ? ' (replace current items)' : ''}</button>
        </div>}
      </details>}
      {form.source_filename && <p className="ti-source">Source: {form.source_filename}</p>}
      <form onSubmit={save}>
        <fieldset disabled={fieldDisabled} className="ti-fields"><legend>Customer & invoice details</legend>
          <CompanySelectWithCreate companies={companies} value={form.company} required disabled={fieldDisabled} onChange={selectCompany}
            onCreated={(company) => { setCompanies((current) => [...current.filter((entry) => entry.id !== company.id), company]); selectCompany(company.id, company); }} />
          <div className="ti-grid">
            {input('Customer name', 'customer_name', { required: true, maxLength: 255 })}
            {input('Customer TRN (if registered)', 'customer_trn', { pattern: '[0-9]{15}', maxLength: 15 })}
            {input('Invoice date', 'invoice_date', { type: 'date', required: true })}
            {input('Date of supply', 'supply_date', { type: 'date', required: true })}
            {input('Quotation reference', 'quotation_reference', { maxLength: 120 })}
            {input('LPO number (optional)', 'lpo_number', { maxLength: 120 })}
            {input('Attention (optional)', 'attention', { maxLength: 255 })}
            <label className="ti-field ti-address">Customer billing address<textarea className="qm-input" rows={2} maxLength={2000} value={form.customer_address}
              onChange={(event) => change('customer_address', event.target.value)} /></label>
          </div>
        </fieldset>
        <div className="qm-panel-heading ti-items-heading"><div><h3>Invoice items</h3><p>Prices exclude VAT · Currency: AED · Discount is the amount for the whole line.</p></div>
          {!locked && <button className="qm-secondary" type="button" disabled={Boolean(busy) || form.lines.length >= 300} onClick={() => change('lines', [...form.lines, newLine()])}>+ Add item</button>}</div>
        <div className="qm-table-wrap"><table className="qm-table ti-lines"><thead><tr><th>Item / description</th><th>Qty</th><th>Unit</th><th>Unit price</th><th>Discount</th><th>VAT %</th><th>Action</th></tr></thead>
          <tbody>{form.lines.map((line, index) => <tr key={index}>
            <td><input className="qm-input" aria-label={`Item ${index + 1}`} required maxLength={255} disabled={fieldDisabled} value={line.item_name} onChange={(event) => changeLine(index, 'item_name', event.target.value)} />
              <input className="qm-input ti-description" aria-label={`Description ${index + 1}`} placeholder="Extra description (optional)" maxLength={2000} disabled={fieldDisabled} value={line.description || ''} onChange={(event) => changeLine(index, 'description', event.target.value)} /></td>
            <td><input className="qm-input" aria-label={`Quantity ${index + 1}`} type="number" min="0.001" step="0.001" required onWheel={releaseNumberWheelFocus} disabled={fieldDisabled} value={line.quantity} onChange={(event) => changeLine(index, 'quantity', event.target.value)} /></td>
            <td><input className="qm-input" aria-label={`Unit ${index + 1}`} maxLength={50} disabled={fieldDisabled} value={line.unit} onChange={(event) => changeLine(index, 'unit', event.target.value)} /></td>
            <td><input className="qm-input" aria-label={`Unit price ${index + 1}`} type="number" min="0" step="0.001" placeholder="Missing" onWheel={releaseNumberWheelFocus} disabled={fieldDisabled} value={line.unit_price} onChange={(event) => changeLine(index, 'unit_price', event.target.value)} /></td>
            <td><input className="qm-input" aria-label={`Discount ${index + 1}`} type="number" min="0" step="0.01" onWheel={releaseNumberWheelFocus} disabled={fieldDisabled} value={line.discount} onChange={(event) => changeLine(index, 'discount', event.target.value)} /></td>
            <td><select className="qm-input" aria-label={`VAT ${index + 1}`} disabled={fieldDisabled} value={line.vat_rate === '' ? '' : Number(line.vat_rate)} onChange={(event) => changeLine(index, 'vat_rate', event.target.value)}>
              <option value="">Select</option><option value="0">0%</option><option value="5">5%</option></select></td>
            <td>{!locked && <button type="button" className="qm-danger small" aria-label={`Remove item ${index + 1}`} disabled={Boolean(busy) || form.lines.length === 1} onClick={() => change('lines', form.lines.filter((_, i) => i !== index))}>Remove</button>}</td>
          </tr>)}</tbody></table></div>
        <label className="ti-field ti-notes">Notes<textarea className="qm-input" rows={2} maxLength={5000} disabled={fieldDisabled} value={form.notes} onChange={(event) => change('notes', event.target.value)} /></label>
        {saved && <div className="ti-totals"><span>{dirty ? 'Last saved totals' : 'Totals'}</span><span>Subtotal {amount(saved.subtotal)}</span><span>VAT {amount(saved.vat_total)}</span><strong>Total {amount(saved.total)}</strong></div>}
        {!locked && <div className="ti-review">
          {missing > 0 && <p>{missing} item(s) need a price or VAT rate. You can save a draft while these are incomplete.</p>}
          {saved && !dirty && !missing && <label><input type="checkbox" checked={reviewed} disabled={Boolean(busy)} onChange={(event) => setReviewed(event.target.checked)} /> I checked the customer, dates, items, discounts and VAT. Issuing locks this invoice.</label>}
        </div>}
        <div className="ti-actions ti-sticky">
          <div className="ti-action-messages">
            <QuotationErrorNotice error={error} onDismiss={() => setError(null)} />
            {feedback && <div className="qm-feedback success" role="status">{feedback}</div>}
          </div>
          <span>{locked ? 'Issued' : dirty ? 'Unsaved changes' : saved ? 'Draft saved' : 'New draft'}</span>
          <button className="qm-secondary" type="button" disabled={Boolean(busy)} onClick={() => { if (canLeave()) { setForm(null); setDirty(false); } }}>Back to list</button>
          {!locked && <button className="qm-secondary" type="submit" disabled={Boolean(busy) || (saved && !dirty)}>{busy === 'save' ? 'Saving…' : 'Save draft'}</button>}
          {saved && <button className="qm-secondary" type="button" disabled={Boolean(busy) || dirty} onClick={download}>{busy === 'pdf' ? 'Preparing PDF…' : locked ? 'Download tax invoice' : 'Preview draft PDF'}</button>}
          {!locked && <button className="qm-primary" type="button" disabled={Boolean(busy) || !saved || dirty || !reviewed || missing > 0} onClick={issue}>{busy === 'issue' ? 'Issuing…' : 'Issue tax invoice'}</button>}
        </div>
      </form>
    </section>}
  </div>;
}
