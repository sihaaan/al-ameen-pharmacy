import React, { useEffect, useMemo, useState } from 'react';
import quotationAPI, { describeQuotationError } from '../../api/quotations';
import CompanySelectWithCreate from './CompanySelectWithCreate';
import QuotationErrorNotice from './QuotationErrorNotice';

const emptyForm = {
  company: '',
  contact: '',
  notes: '',
};

const emptyLpoForm = {
  text: '',
  file: null,
  use_ai: true,
};

const newLineDraft = (sortOrder = 0) => ({
  id: `new-${Date.now()}-${Math.random().toString(16).slice(2)}`,
  item_name: '',
  description: '',
  quantity: '1.000',
  unit: '',
  unit_price: '',
  vat_rate: '0.00',
  sort_order: sortOrder,
});

const formatMoney = (currency, value) => `${currency || 'AED'} ${Number(value || 0).toFixed(2)}`;

const formatDate = (value) => {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString('en-GB');
};

const safeDownloadNamePart = (value) => String(value || '')
  .trim()
  .toUpperCase()
  .replace(/[^A-Z0-9-]+/g, '_')
  .replace(/^_+|_+$/g, '')
  .slice(0, 80);

const proformaDownloadFilename = (proforma) => {
  const companyPart = safeDownloadNamePart(proforma?.company_name);
  const numberPart = safeDownloadNamePart(proforma?.proforma_number) || 'PROFORMA';
  return `${companyPart ? `${companyPart}-` : ''}${numberPart}.pdf`;
};

const contactOptionLabel = (contact) => {
  const details = [contact.role, contact.department].filter(Boolean).join(', ');
  return details ? `${contact.name} - ${details}` : contact.name;
};

const ProformaInvoiceManager = () => {
  const [companies, setCompanies] = useState([]);
  const [contacts, setContacts] = useState([]);
  const [proformas, setProformas] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [selected, setSelected] = useState(null);
  const [form, setForm] = useState(emptyForm);
  const [lpoForm, setLpoForm] = useState(emptyLpoForm);
  const [lineDrafts, setLineDrafts] = useState([]);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [parsing, setParsing] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [feedback, setFeedback] = useState(null);
  const [errorInfo, setErrorInfo] = useState(null);

  const filteredProformas = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return proformas.filter((proforma) => {
      if (statusFilter && proforma.status !== statusFilter) return false;
      if (!needle) return true;
      return [
        proforma.proforma_number,
        proforma.company_name,
        proforma.lpo_number,
        proforma.source_filename,
      ].some((value) => String(value || '').toLowerCase().includes(needle));
    });
  }, [proformas, search, statusFilter]);

  const visibleLineDrafts = lineDrafts.filter((line) => !line._delete);

  const rememberCompany = (company) => {
    setCompanies((current) => {
      const withoutDuplicate = current.filter((candidate) => candidate.id !== company.id);
      return [...withoutDuplicate, company].sort((a, b) => a.name.localeCompare(b.name));
    });
    setForm((current) => ({ ...current, company: company.id, contact: '' }));
    loadContactsForCompany(company.id);
  };

  const loadContactsForCompany = async (companyId) => {
    if (!companyId) {
      setContacts([]);
      return;
    }
    try {
      const response = await quotationAPI.contacts.list({ company: companyId, active: 'true' });
      setContacts(response.data);
    } catch (error) {
      const details = await describeQuotationError(error, 'Load proforma contacts', `GET /quotations/contacts/?company=${companyId}`);
      setErrorInfo(details);
    }
  };

  const loadProformas = async () => {
    const response = await quotationAPI.proformas.list();
    setProformas(response.data);
    return response.data;
  };

  const loadInitial = async () => {
    setLoading(true);
    setErrorInfo(null);
    try {
      const [companiesRes, proformasRes] = await Promise.all([
        quotationAPI.companies.list(),
        quotationAPI.proformas.list(),
      ]);
      setCompanies(companiesRes.data);
      setProformas(proformasRes.data);
    } catch (error) {
      const details = await describeQuotationError(error, 'Load Proforma Invoices', 'GET /quotations/companies/, /quotations/proformas/');
      setErrorInfo(details);
    } finally {
      setLoading(false);
    }
  };

  const openProforma = async (id) => {
    setErrorInfo(null);
    setFeedback(null);
    try {
      const response = await quotationAPI.proformas.retrieve(id);
      const proforma = response.data;
      setSelectedId(proforma.id);
      setSelected(proforma);
      setForm({
        company: proforma.company,
        contact: proforma.contact || '',
        notes: proforma.notes || '',
      });
      setLineDrafts((proforma.lines || []).map((line) => ({
        ...line,
        quantity: line.quantity || '',
        unit_price: line.unit_price || '',
        vat_rate: line.vat_rate || '0.00',
      })));
      loadContactsForCompany(proforma.company);
    } catch (error) {
      const details = await describeQuotationError(error, 'Open Proforma Invoice', `GET /quotations/proformas/${id}/`);
      setErrorInfo(details);
    }
  };

  useEffect(() => {
    loadInitial();
  }, []);

  const createProforma = async (event) => {
    event.preventDefault();
    if (!form.company || saving) return;
    setSaving(true);
    setErrorInfo(null);
    setFeedback(null);
    try {
      const response = await quotationAPI.proformas.create({
        company: form.company,
        contact: form.contact || null,
        currency: 'AED',
        notes: form.notes,
      });
      setFeedback({ type: 'success', message: `Proforma ${response.data.proforma_number} created. Upload or paste the LPO next.` });
      await loadProformas();
      await openProforma(response.data.id);
    } catch (error) {
      const details = await describeQuotationError(error, 'Create Proforma Invoice', 'POST /quotations/proformas/');
      setErrorInfo(details);
    } finally {
      setSaving(false);
    }
  };

  const saveDetails = async () => {
    if (!selected || saving) return;
    setSaving(true);
    setErrorInfo(null);
    setFeedback(null);
    try {
      const response = await quotationAPI.proformas.update(selected.id, {
        company: form.company,
        contact: form.contact || null,
        notes: form.notes,
        proforma_date: selected.proforma_date,
        lpo_number: selected.lpo_number || '',
        lpo_date: selected.lpo_date || null,
      });
      setSelected(response.data);
      await loadProformas();
      setFeedback({ type: 'success', message: 'Proforma details saved.' });
    } catch (error) {
      const details = await describeQuotationError(error, 'Save Proforma Invoice details', `PATCH /quotations/proformas/${selected.id}/`);
      setErrorInfo(details);
    } finally {
      setSaving(false);
    }
  };

  const parseLpo = async () => {
    if (!selected || parsing) return;
    setParsing(true);
    setErrorInfo(null);
    setFeedback(null);
    try {
      let response;
      if (lpoForm.file) {
        const formData = new FormData();
        formData.append('file', lpoForm.file);
        formData.append('use_ai', lpoForm.use_ai ? '1' : '0');
        response = await quotationAPI.proformas.uploadLpo(selected.id, formData, true);
      } else {
        response = await quotationAPI.proformas.uploadLpo(selected.id, {
          text: lpoForm.text,
          use_ai: lpoForm.use_ai,
        });
      }
      const proforma = response.data.proforma;
      setSelected(proforma);
      setLineDrafts((proforma.lines || []).map((line) => ({
        ...line,
        quantity: line.quantity || '',
        unit_price: line.unit_price || '',
        vat_rate: line.vat_rate || '0.00',
      })));
      setLpoForm(emptyLpoForm);
      await loadProformas();
      setFeedback({ type: 'success', message: response.data.message || 'LPO parsed. Review the lines before downloading.' });
    } catch (error) {
      const details = await describeQuotationError(error, 'Parse standalone Proforma LPO', `POST /quotations/proformas/${selected.id}/upload_lpo/`);
      setErrorInfo(details);
    } finally {
      setParsing(false);
    }
  };

  const updateLineDraft = (lineId, updates) => {
    setLineDrafts((current) => current.map((line) => (
      line.id === lineId ? { ...line, ...updates } : line
    )));
  };

  const addLine = () => {
    setLineDrafts((current) => [...current, newLineDraft(current.length + 1)]);
  };

  const removeLine = (lineId) => {
    setLineDrafts((current) => current.map((line) => (
      line.id === lineId ? { ...line, _delete: true } : line
    )));
  };

  const saveLines = async () => {
    if (!selected || saving) return null;
    setSaving(true);
    setErrorInfo(null);
    setFeedback(null);
    try {
      const payload = lineDrafts.map((line, index) => ({
        id: String(line.id).startsWith('new-') ? undefined : line.id,
        item_name: line.item_name,
        description: line.description || '',
        quantity: line.quantity || '1.000',
        unit: line.unit || '',
        unit_price: line.unit_price === '' ? null : line.unit_price,
        vat_rate: line.vat_rate || '0.00',
        sort_order: index + 1,
        _delete: Boolean(line._delete),
      }));
      const response = await quotationAPI.proformas.bulkUpdateLines(selected.id, { lines: payload });
      const proforma = response.data;
      setSelected(proforma);
      setLineDrafts((proforma.lines || []).map((line) => ({
        ...line,
        quantity: line.quantity || '',
        unit_price: line.unit_price || '',
        vat_rate: line.vat_rate || '0.00',
      })));
      await loadProformas();
      setFeedback({ type: 'success', message: 'Proforma lines saved.' });
      return proforma;
    } catch (error) {
      const details = await describeQuotationError(error, 'Save Proforma Invoice lines', `POST /quotations/proformas/${selected.id}/bulk_update_lines/`);
      setErrorInfo(details);
      return null;
    } finally {
      setSaving(false);
    }
  };

  const downloadPdf = async () => {
    if (!selected || downloading) return;
    setDownloading(true);
    setErrorInfo(null);
    setFeedback(null);
    try {
      const saved = await saveLines();
      if (!saved) return;
      const response = await quotationAPI.proformas.pdf(saved.id);
      const url = window.URL.createObjectURL(new Blob([response.data], { type: 'application/pdf' }));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', proformaDownloadFilename(saved));
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      await openProforma(saved.id);
      await loadProformas();
      setFeedback({ type: 'success', message: 'Proforma Invoice downloaded.' });
    } catch (error) {
      const details = await describeQuotationError(error, 'Download standalone Proforma Invoice', `GET /quotations/proformas/${selected.id}/pdf/`);
      setErrorInfo(details);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <div className="qm-proforma">
      {errorInfo && <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />}
      {feedback && <div className={`qm-feedback ${feedback.type}`}>{feedback.message}</div>}

      <div className="qm-panel qm-proforma-hero">
        <div>
          <span className="qm-step-kicker">LPO to Proforma</span>
          <h3>Standalone Proforma Invoices</h3>
          <p>Create a Proforma Invoice directly from a customer LPO, even when no quotation exists.</p>
        </div>
        <div className="qm-proforma-hero-stats">
          <span><strong>{proformas.length}</strong> tracked</span>
          <span><strong>{proformas.filter((item) => item.status === 'draft').length}</strong> draft</span>
          <span><strong>{proformas.filter((item) => item.status === 'issued').length}</strong> issued</span>
        </div>
      </div>

      <div className="qm-proforma-layout">
        <div className="qm-panel">
          <div className="qm-panel-heading">
            <div>
              <h3>Recent Proformas</h3>
              <p>Select an existing PI or create a new one from an LPO.</p>
            </div>
            <button type="button" className="qm-secondary small" onClick={loadInitial} disabled={loading}>Refresh</button>
          </div>
          <div className="qm-controls stacked">
            <input className="qm-input" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search PI, company, LPO" />
            <select className="qm-input" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
              <option value="">All statuses</option>
              <option value="draft">Draft</option>
              <option value="issued">Issued</option>
              <option value="cancelled">Cancelled</option>
            </select>
          </div>
          <div className="qm-proforma-list">
            {filteredProformas.map((proforma) => (
              <button
                key={proforma.id}
                type="button"
                className={`qm-proforma-list-item ${selectedId === proforma.id ? 'active' : ''}`}
                onClick={() => openProforma(proforma.id)}
              >
                <strong>{proforma.proforma_number}</strong>
                <span>{proforma.company_name}</span>
                <small>
                  {proforma.lpo_number ? `LPO ${proforma.lpo_number}` : 'No LPO number yet'} - {formatDate(proforma.proforma_date)}
                </small>
                <span className={`qm-badge status-${proforma.status}`}>{proforma.status_display || proforma.status}</span>
              </button>
            ))}
            {!filteredProformas.length && <p className="qm-muted">{loading ? 'Loading...' : 'No Proforma Invoices found.'}</p>}
          </div>
        </div>

        <div className="qm-proforma-workspace">
          <div className="qm-panel">
            <div className="qm-panel-heading">
              <div>
                <h3>1. Start Proforma</h3>
                <p>Choose the customer, then create a draft PI workspace.</p>
              </div>
              {selected && <span className={`qm-badge status-${selected.status}`}>{selected.status_display}</span>}
            </div>
            <form className="qm-form qm-proforma-start" onSubmit={createProforma}>
              <div className="qm-proforma-start-card">
                <div className="qm-card-eyebrow">Customer</div>
                <CompanySelectWithCreate
                  companies={companies}
                  value={form.company}
                  required
                  onChange={(companyId) => {
                    setForm({ ...form, company: companyId, contact: '' });
                    loadContactsForCompany(companyId);
                  }}
                  onCreated={rememberCompany}
                />
              </div>
              <div className="qm-proforma-start-card">
                <div className="qm-card-eyebrow">Contact & Notes</div>
                <label>Contact
                  <select value={form.contact} disabled={!form.company} onChange={(event) => setForm({ ...form, contact: event.target.value })}>
                    <option value="">No contact</option>
                    {contacts.map((contact) => <option key={contact.id} value={contact.id}>{contactOptionLabel(contact)}</option>)}
                  </select>
                </label>
                <label>Internal notes
                  <textarea rows="3" value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} placeholder="Optional internal note" />
                </label>
              </div>
              <div className="qm-proforma-start-actions">
                <div>
                  <strong>{selected ? selected.proforma_number : 'New Proforma Invoice'}</strong>
                  <span>{selected ? 'Save customer/contact changes or create another PI from the selected customer.' : 'Create the draft first, then upload or paste the LPO.'}</span>
                </div>
                <div className="qm-actions">
                  {selected && (
                    <button type="button" className="qm-secondary" disabled={saving} onClick={saveDetails}>
                      {saving ? 'Saving...' : 'Save Details'}
                    </button>
                  )}
                  <button type="submit" className="qm-primary" disabled={!form.company || saving}>
                    {saving ? 'Creating...' : selected ? 'Create Another Proforma' : 'Create New Proforma'}
                  </button>
                </div>
              </div>
            </form>
          </div>

          {selected ? (
            <>
              <div className="qm-panel qm-lpo-workflow">
                <div className="qm-panel-heading">
                  <div>
                    <h3>2. Upload or Paste LPO</h3>
                    <p>Use a customer LPO PDF/Excel or pasted LPO text. AI cleanup is review-only.</p>
                  </div>
                  <span className="qm-lpo-status-pill">{selected.lines?.length || 0} line(s)</span>
                </div>
                <div className="qm-proforma-upload-grid">
                  <label>Upload LPO file
                    <input type="file" accept=".pdf,.xlsx,.xls,.csv,.txt" onChange={(event) => setLpoForm({ ...lpoForm, file: event.target.files?.[0] || null })} />
                  </label>
                  <label>Paste LPO text
                    <textarea rows="5" value={lpoForm.text} onChange={(event) => setLpoForm({ ...lpoForm, text: event.target.value })} placeholder="Paste LPO lines here if no file is available" />
                  </label>
                  <label className="qm-checkbox">
                    <input type="checkbox" checked={lpoForm.use_ai} onChange={(event) => setLpoForm({ ...lpoForm, use_ai: event.target.checked })} />
                    Use AI cleanup when available
                  </label>
                </div>
                <button type="button" className="qm-primary" disabled={parsing || (!lpoForm.file && !lpoForm.text.trim())} onClick={parseLpo}>
                  {parsing ? 'Parsing LPO...' : 'Parse LPO'}
                </button>
                {!!selected.warnings?.length && (
                  <div className="qm-helper warning">
                    {selected.warnings.map((warning, index) => <p key={`${warning}-${index}`}>{warning}</p>)}
                  </div>
                )}
              </div>

              <div className="qm-panel">
                <div className="qm-panel-heading">
                  <div>
                    <h3>3. Review Lines</h3>
                    <p>Edit parsed lines before downloading the Proforma Invoice.</p>
                  </div>
                  <div className="qm-actions">
                    <button type="button" className="qm-secondary small" onClick={addLine}>Add Line</button>
                    <button type="button" className="qm-secondary small" disabled={saving} onClick={saveLines}>{saving ? 'Saving...' : 'Save Lines'}</button>
                    <button type="button" className="qm-primary small" disabled={downloading || !visibleLineDrafts.length} onClick={downloadPdf}>
                      {downloading ? 'Preparing...' : 'Download PI'}
                    </button>
                  </div>
                </div>
                <div className="qm-details-grid compact">
                  <label>Proforma Date
                    <input type="date" value={selected.proforma_date || ''} onChange={(event) => setSelected({ ...selected, proforma_date: event.target.value })} onBlur={() => saveDetails()} />
                  </label>
                  <label>LPO Number
                    <input value={selected.lpo_number || ''} onChange={(event) => setSelected({ ...selected, lpo_number: event.target.value })} onBlur={() => saveDetails()} placeholder="Customer LPO number" />
                  </label>
                  <label>LPO Date
                    <input type="date" value={selected.lpo_date || ''} onChange={(event) => setSelected({ ...selected, lpo_date: event.target.value })} onBlur={() => saveDetails()} />
                  </label>
                  <div className="qm-proforma-total-card">
                    <span>Total</span>
                    <strong>{formatMoney(selected.currency, selected.total)}</strong>
                  </div>
                </div>
                <div className="qm-table-wrap compact">
                  <table className="qm-table qm-proforma-lines-table">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>Item</th>
                        <th>Qty</th>
                        <th>Unit</th>
                        <th>Unit Price</th>
                        <th>VAT %</th>
                        <th>Total</th>
                        <th>Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleLineDrafts.map((line, index) => {
                        const qty = Number(line.quantity || 0);
                        const unitPrice = Number(line.unit_price || 0);
                        const vatRate = Number(line.vat_rate || 0);
                        const lineTotal = qty * unitPrice * (1 + (vatRate / 100));
                        return (
                          <tr key={line.id}>
                            <td>{index + 1}</td>
                            <td>
                              <input value={line.item_name || ''} onChange={(event) => updateLineDraft(line.id, { item_name: event.target.value })} placeholder="Item description" />
                              <input value={line.description || ''} onChange={(event) => updateLineDraft(line.id, { description: event.target.value })} placeholder="Optional detail" />
                            </td>
                            <td><input type="number" step="0.001" value={line.quantity || ''} onChange={(event) => updateLineDraft(line.id, { quantity: event.target.value })} /></td>
                            <td><input value={line.unit || ''} onChange={(event) => updateLineDraft(line.id, { unit: event.target.value })} /></td>
                            <td><input type="number" step="0.01" value={line.unit_price || ''} onChange={(event) => updateLineDraft(line.id, { unit_price: event.target.value })} /></td>
                            <td>
                              <select value={line.vat_rate || '0.00'} onChange={(event) => updateLineDraft(line.id, { vat_rate: event.target.value })}>
                                <option value="0.00">0%</option>
                                <option value="5.00">5%</option>
                              </select>
                            </td>
                            <td>{formatMoney(selected.currency, lineTotal)}</td>
                            <td><button type="button" className="qm-secondary small danger" onClick={() => removeLine(line.id)}>Remove</button></td>
                          </tr>
                        );
                      })}
                      {!visibleLineDrafts.length && (
                        <tr>
                          <td colSpan="8">No lines yet. Parse an LPO or add a line manually.</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          ) : (
            <div className="qm-panel qm-empty-state">
              <h3>Select or create a Proforma Invoice</h3>
              <p>Start by choosing the company and creating a draft PI, or open a recent Proforma Invoice from the list.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default ProformaInvoiceManager;
