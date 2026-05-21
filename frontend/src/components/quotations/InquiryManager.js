import React, { useEffect, useMemo, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import QuotationErrorNotice from './QuotationErrorNotice';

const newLine = () => ({
  raw_name: '',
  quantity: '1',
  unit: '',
  notes: '',
  matched_quote_item: '',
  match_status: 'unresolved',
});

const newImportLine = () => ({
  raw_name: '',
  quantity: '',
  unit: '',
  raw_line: '',
  parse_status: 'needs_review',
  parse_confidence: 0,
  notes: '',
});

const emptyImportForm = {
  company: '',
  contact: '',
  subject: '',
  raw_text: '',
};

const InquiryManager = ({ onOpenQuote }) => {
  const [companies, setCompanies] = useState([]);
  const [contacts, setContacts] = useState([]);
  const [items, setItems] = useState([]);
  const [inquiries, setInquiries] = useState([]);
  const [selectedInquiry, setSelectedInquiry] = useState(null);
  const [form, setForm] = useState({
    company: '',
    contact: '',
    subject: '',
    original_text: '',
    lines: [newLine()],
  });
  const [statusFilter, setStatusFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [creatingQuoteId, setCreatingQuoteId] = useState(null);
  const [quoteSuccess, setQuoteSuccess] = useState(null);
  const [importMode, setImportMode] = useState('paste');
  const [importForm, setImportForm] = useState(emptyImportForm);
  const [importFile, setImportFile] = useState(null);
  const [importPreview, setImportPreview] = useState(null);
  const [savedImportedInquiry, setSavedImportedInquiry] = useState(null);
  const [importParsing, setImportParsing] = useState(false);
  const [importSaving, setImportSaving] = useState(false);
  const [importNotice, setImportNotice] = useState(null);
  const [errorInfo, setErrorInfo] = useState(null);

  const load = async () => {
    setLoading(true);
    setErrorInfo(null);
    try {
      const [companiesRes, contactsRes, itemsRes, inquiriesRes] = await Promise.all([
        quotationAPI.companies.list({ active: 'true' }),
        quotationAPI.contacts.list({ active: 'true' }),
        quotationAPI.items.list({ active: 'true' }),
        quotationAPI.inquiries.list(),
      ]);
      setCompanies(companiesRes.data);
      setContacts(contactsRes.data);
      setItems(itemsRes.data);
      setInquiries(inquiriesRes.data);
    } catch (error) {
      const details = await describeQuotationError(
        error,
        'Load inquiries',
        'GET /quotations/companies/, /quotations/contacts/, /quotations/items/, /quotations/inquiries/'
      );
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const filteredContacts = contacts.filter((contact) => String(contact.company) === String(form.company));
  const filteredImportContacts = contacts.filter((contact) => String(contact.company) === String(importForm.company));
  const filteredInquiries = useMemo(() => (
    statusFilter ? inquiries.filter((inquiry) => inquiry.status === statusFilter) : inquiries
  ), [inquiries, statusFilter]);

  const updateLine = (index, patch) => {
    setForm((current) => ({
      ...current,
      lines: current.lines.map((line, lineIndex) => lineIndex === index ? { ...line, ...patch } : line),
    }));
  };

  const removeLine = (index) => {
    setForm((current) => ({
      ...current,
      lines: current.lines.filter((_, lineIndex) => lineIndex !== index),
    }));
  };

  const saveInquiry = async (event) => {
    event.preventDefault();
    setSaving(true);
    setErrorInfo(null);
    const payload = {
      company: form.company,
      contact: form.contact || null,
      subject: form.subject,
      original_text: form.original_text,
      lines: form.lines
        .filter((line) => line.raw_name.trim())
        .map((line, index) => ({
          raw_name: line.raw_name,
          quantity: line.quantity || null,
          unit: line.unit,
          notes: line.notes,
          matched_quote_item: line.matched_quote_item || null,
          match_status: line.match_status,
          sort_order: index,
        })),
    };
    try {
      await quotationAPI.inquiries.create(payload);
      setForm({ company: '', contact: '', subject: '', original_text: '', lines: [newLine()] });
      await load();
    } catch (error) {
      const details = await describeQuotationError(error, 'Create inquiry', 'POST /quotations/inquiries/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const createQuote = async (inquiry) => {
    if (creatingQuoteId) return;
    if (inquiry.quotation_id) {
      setQuoteSuccess({
        inquiryId: inquiry.id,
        quoteId: inquiry.quotation_id,
        quotationNumber: inquiry.quotation_number,
        reused: true,
      });
      return;
    }
    setCreatingQuoteId(inquiry.id);
    setErrorInfo(null);
    setQuoteSuccess(null);
    try {
      const response = await quotationAPI.inquiries.createQuote(inquiry.id);
      setQuoteSuccess({
        inquiryId: inquiry.id,
        quoteId: response.data.id,
        quotationNumber: response.data.quotation_number,
        reused: response.status === 200,
      });
      await load();
    } catch (error) {
      const details = await describeQuotationError(error, 'Create quote from inquiry', `POST /quotations/inquiries/${inquiry.id}/create_quote/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setCreatingQuoteId(null);
    }
  };

  const openCreatedQuote = (quoteId) => {
    if (quoteId && onOpenQuote) onOpenQuote(quoteId);
  };

  const setPreview = (preview) => {
    setSavedImportedInquiry(null);
    setImportNotice(null);
    setImportPreview({
      ...preview,
      lines: (preview.lines || []).map((line) => ({
        ...newImportLine(),
        ...line,
        parse_confidence: Number(line.parse_confidence || 0),
      })),
    });
  };

  const parsePastedText = async () => {
    if (importParsing) return;
    setImportParsing(true);
    setErrorInfo(null);
    setImportNotice(null);
    try {
      const response = await quotationAPI.inquiries.parseText({ raw_text: importForm.raw_text });
      setPreview(response.data);
    } catch (error) {
      const details = await describeQuotationError(error, 'Parse pasted inquiry text', 'POST /quotations/inquiries/parse_text/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setImportParsing(false);
    }
  };

  const parseUploadedFile = async () => {
    if (importParsing) return;
    if (!importFile) {
      setImportNotice({ type: 'error', message: 'Choose an Excel or PDF file before parsing.' });
      return;
    }
    setImportParsing(true);
    setErrorInfo(null);
    setImportNotice(null);
    const formData = new FormData();
    formData.append('file', importFile);
    try {
      const response = await quotationAPI.inquiries.parseFile(formData);
      setPreview(response.data);
    } catch (error) {
      const details = await describeQuotationError(error, 'Parse inquiry file', 'POST /quotations/inquiries/parse_file/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setImportParsing(false);
    }
  };

  const updateImportLine = (index, patch) => {
    setSavedImportedInquiry(null);
    setImportPreview((current) => ({
      ...current,
      lines: current.lines.map((line, lineIndex) => lineIndex === index ? { ...line, ...patch } : line),
    }));
  };

  const removeImportLine = (index) => {
    setSavedImportedInquiry(null);
    setImportPreview((current) => ({
      ...current,
      lines: current.lines.filter((_, lineIndex) => lineIndex !== index),
    }));
  };

  const addImportLine = () => {
    setSavedImportedInquiry(null);
    setImportPreview((current) => ({
      ...(current || {
        source_type: importMode === 'paste' ? 'pasted_text' : importMode,
        source_filename: importFile?.name || '',
        source_mime_type: importFile?.type || '',
        source_sha256: '',
        parse_method: 'manual_review',
        original_text: importForm.raw_text,
        warnings: [],
        meta: {},
      }),
      lines: [...(current?.lines || []), newImportLine()],
    }));
  };

  const saveImportedInquiry = async () => {
    if (importSaving) return;
    const lines = (importPreview?.lines || [])
      .filter((line) => line.raw_name.trim())
      .map((line) => ({
        raw_name: line.raw_name,
        raw_line: line.raw_line || line.raw_name,
        quantity: line.quantity || null,
        unit: line.unit || '',
        notes: line.notes || '',
        parse_status: line.parse_status || 'needs_review',
        parse_confidence: Number(line.parse_confidence || 0),
      }));
    if (!importForm.company) {
      setImportNotice({ type: 'error', message: 'Select a company before saving the imported inquiry.' });
      return;
    }
    if (!lines.length) {
      setImportNotice({ type: 'error', message: 'Add at least one reviewed inquiry line before saving.' });
      return;
    }
    setImportSaving(true);
    setErrorInfo(null);
    setImportNotice(null);
    try {
      const response = await quotationAPI.inquiries.createImported({
        company: importForm.company,
        contact: importForm.contact || null,
        subject: importForm.subject,
        original_text: importPreview?.original_text || importForm.raw_text,
        source_type: importPreview?.source_type || (importMode === 'paste' ? 'pasted_text' : importMode),
        source_filename: importPreview?.source_filename || importFile?.name || '',
        source_mime_type: importPreview?.source_mime_type || importFile?.type || '',
        source_sha256: importPreview?.source_sha256 || '',
        parse_method: importPreview?.parse_method || 'manual_review',
        parse_meta: {
          ...(importPreview?.meta || {}),
          warnings: importPreview?.warnings || [],
        },
        lines,
      });
      setSavedImportedInquiry(response.data);
      setImportNotice({ type: 'success', message: 'Imported inquiry saved. You can now create a quotation from it.' });
      await load();
    } catch (error) {
      const details = await describeQuotationError(error, 'Save imported inquiry', 'POST /quotations/inquiries/create_imported/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setImportSaving(false);
    }
  };

  return (
    <div className="qm-section">
      <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
      {quoteSuccess && (
        <div className="qm-success">
          <div>
            <strong>{quoteSuccess.reused ? 'Quotation already exists.' : 'Quotation created.'}</strong>
            <p>{quoteSuccess.quotationNumber || `Quotation #${quoteSuccess.quoteId}`} is ready for line editing and review.</p>
          </div>
          <button type="button" className="qm-primary" onClick={() => openCreatedQuote(quoteSuccess.quoteId)}>
            Open Quotation
          </button>
        </div>
      )}
      <div className="qm-panel qm-import-panel">
        <div className="qm-panel-heading">
          <div>
            <h3>Import Inquiry</h3>
            <p>Paste text or preview a supported file, review the lines, then save the inquiry. Files are parsed only for preview and are not stored.</p>
          </div>
          <div className="qm-mode-tabs">
            <button type="button" className={importMode === 'paste' ? 'active' : ''} onClick={() => { setImportMode('paste'); setImportFile(null); }}>Paste Text</button>
            <button type="button" className={importMode === 'excel' ? 'active' : ''} onClick={() => { setImportMode('excel'); setImportFile(null); }}>Upload Excel</button>
            <button type="button" className={importMode === 'pdf' ? 'active' : ''} onClick={() => { setImportMode('pdf'); setImportFile(null); }}>Upload PDF</button>
          </div>
        </div>

        <div className="qm-grid-two">
          <label><span className="qm-label-text">Company <span className="qm-required">*</span></span>
            <select value={importForm.company} onChange={(event) => setImportForm({ ...importForm, company: event.target.value, contact: '' })}>
              <option value="">Select company</option>
              {companies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}
            </select>
          </label>
          <label><span className="qm-label-text">Contact</span>
            <select value={importForm.contact} onChange={(event) => setImportForm({ ...importForm, contact: event.target.value })}>
              <option value="">No contact</option>
              {filteredImportContacts.map((contact) => <option key={contact.id} value={contact.id}>{contact.name}</option>)}
            </select>
          </label>
        </div>
        <label className="qm-full-label"><span className="qm-label-text">Subject</span>
          <input className="qm-input" placeholder="Inquiry subject or LPO reference" value={importForm.subject} onChange={(event) => setImportForm({ ...importForm, subject: event.target.value })} />
        </label>

        {importMode === 'paste' ? (
          <div className="qm-import-source">
            <label><span className="qm-label-text">Paste inquiry text</span>
              <textarea rows="5" value={importForm.raw_text} onChange={(event) => setImportForm({ ...importForm, raw_text: event.target.value })} placeholder="Paste the customer's requested items here..." />
            </label>
            <button type="button" className="qm-primary" disabled={importParsing || !importForm.raw_text.trim()} onClick={parsePastedText}>
              {importParsing ? 'Extracting...' : 'Extract Lines'}
            </button>
          </div>
        ) : (
          <div className="qm-import-source">
            <label><span className="qm-label-text">{importMode === 'excel' ? 'Upload .xlsx file' : 'Upload digitally generated .pdf file'}</span>
              <input
                key={importMode}
                type="file"
                accept={importMode === 'excel' ? '.xlsx' : '.pdf'}
                onChange={(event) => setImportFile(event.target.files?.[0] || null)}
              />
            </label>
            <button type="button" className="qm-primary" disabled={importParsing || !importFile} onClick={parseUploadedFile}>
              {importParsing ? 'Parsing...' : 'Parse File'}
            </button>
          </div>
        )}

        {importNotice && <div className={`qm-feedback ${importNotice.type}`}>{importNotice.message}</div>}
        {importPreview && (
          <div className="qm-import-preview">
            <div className="qm-preview-meta">
              <span>Source: {importPreview.source_filename || importPreview.source_type}</span>
              <span>Method: {importPreview.parse_method}</span>
              <span>Lines: {importPreview.lines.length}</span>
            </div>
            {(importPreview.warnings || []).length > 0 && (
              <div className="qm-notice">
                <strong>Review warnings:</strong>
                <ul>{importPreview.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
              </div>
            )}
            <div className="qm-table-wrap">
              <table className="qm-table import-table">
                <thead>
                  <tr>
                    <th>Requested Item Name</th>
                    <th>Qty</th>
                    <th>Unit</th>
                    <th>Parse Status</th>
                    <th>Confidence</th>
                    <th>Raw Source Line</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {importPreview.lines.map((line, index) => (
                    <tr key={`${line.raw_line}-${index}`}>
                      <td><input value={line.raw_name} onChange={(event) => updateImportLine(index, { raw_name: event.target.value })} /></td>
                      <td><input type="number" min="0" step="0.001" value={line.quantity || ''} onChange={(event) => updateImportLine(index, { quantity: event.target.value })} /></td>
                      <td><input value={line.unit || ''} onChange={(event) => updateImportLine(index, { unit: event.target.value })} /></td>
                      <td>
                        <select value={line.parse_status || 'needs_review'} onChange={(event) => updateImportLine(index, { parse_status: event.target.value })}>
                          <option value="parsed">Parsed</option>
                          <option value="needs_review">Needs Review</option>
                          <option value="unparsed">Unparsed</option>
                          <option value="manual">Manual</option>
                        </select>
                      </td>
                      <td><span className="qm-confidence">{Math.round(Number(line.parse_confidence || 0) * 100)}%</span></td>
                      <td><input value={line.raw_line || ''} onChange={(event) => updateImportLine(index, { raw_line: event.target.value })} /></td>
                      <td><button type="button" className="qm-secondary small danger" onClick={() => removeImportLine(index)}>Delete</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="qm-import-actions">
              <button type="button" className="qm-secondary" onClick={addImportLine}>Add Missing Row</button>
              <button type="button" className="qm-primary" disabled={importSaving || Boolean(savedImportedInquiry)} onClick={saveImportedInquiry}>
                {importSaving ? 'Saving...' : savedImportedInquiry ? 'Inquiry Saved' : 'Save Inquiry'}
              </button>
              {savedImportedInquiry && (
                <button type="button" className="qm-secondary" disabled={Boolean(creatingQuoteId)} onClick={() => createQuote(savedImportedInquiry)}>
                  {creatingQuoteId === savedImportedInquiry.id ? 'Creating...' : 'Create Quotation from This Inquiry'}
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      <div className="qm-split wide-left">
        <div className="qm-panel">
        <div className="qm-panel-heading">
          <h3>Inquiries</h3>
          <select className="qm-input" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">All statuses</option>
            <option value="draft">Draft</option>
            <option value="quoted">Quoted</option>
            <option value="cancelled">Cancelled</option>
          </select>
        </div>
        {loading ? (
          <div className="qm-loading">Loading inquiries...</div>
        ) : (
          <div className="qm-table-wrap">
            <table className="qm-table">
              <thead>
                <tr>
                  <th>Subject</th>
                  <th>Company</th>
                  <th>Lines</th>
                  <th>Status</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {filteredInquiries.map((inquiry) => (
                  <tr key={inquiry.id} className={selectedInquiry?.id === inquiry.id ? 'selected' : ''} onClick={() => setSelectedInquiry(inquiry)}>
                    <td>{inquiry.subject || `Inquiry #${inquiry.id}`}</td>
                    <td>{inquiry.company_name}</td>
                    <td>{inquiry.lines?.length || 0}</td>
                    <td><span className="qm-badge muted">{inquiry.status}</span></td>
                    <td>
                      {inquiry.quotation_id ? (
                        <button
                          type="button"
                          className="qm-secondary small"
                          onClick={(event) => {
                            event.stopPropagation();
                            openCreatedQuote(inquiry.quotation_id);
                          }}
                        >
                          Open Quotation
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="qm-secondary small"
                          disabled={creatingQuoteId === inquiry.id || Boolean(creatingQuoteId)}
                          onClick={(event) => {
                            event.stopPropagation();
                            createQuote(inquiry);
                          }}
                        >
                          {creatingQuoteId === inquiry.id ? 'Creating...' : 'Create Quotation from Inquiry'}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {selectedInquiry && (
          <div className="qm-subpanel">
            <h4>{selectedInquiry.subject || `Inquiry #${selectedInquiry.id}`}</h4>
            <p>{selectedInquiry.original_text || 'No source text entered.'}</p>
            <div className="qm-table-wrap compact">
              <table className="qm-table">
                <thead><tr><th>Requested Item</th><th>Qty</th><th>Match</th><th>Status</th></tr></thead>
                <tbody>
                  {(selectedInquiry.lines || []).map((line) => (
                    <tr key={line.id}>
                      <td>{line.raw_name}</td>
                      <td>{line.quantity || '-'}</td>
                      <td>{line.matched_quote_item_name || '-'}</td>
                      <td>{line.match_status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        </div>

        <div className="qm-panel qm-manual-fallback">
        <div className="qm-workflow-step">
          <span>Step 1</span>
          <div>
            <h3>Manual Inquiry Fallback</h3>
            <p>Use this when the customer request is short or the file cannot be parsed safely.</p>
          </div>
        </div>
        <form onSubmit={saveInquiry} className="qm-form">
          <label><span className="qm-label-text">Company <span className="qm-required">*</span></span>
            <select required value={form.company} onChange={(event) => setForm({ ...form, company: event.target.value, contact: '' })}>
              <option value="">Select company</option>
              {companies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}
            </select>
          </label>
          <label><span className="qm-label-text">Contact</span>
            <select value={form.contact} onChange={(event) => setForm({ ...form, contact: event.target.value })}>
              <option value="">No contact</option>
              {filteredContacts.map((contact) => <option key={contact.id} value={contact.id}>{contact.name}</option>)}
            </select>
          </label>
          <label><span className="qm-label-text">Subject</span><input placeholder="Inquiry subject or LPO reference" value={form.subject} onChange={(event) => setForm({ ...form, subject: event.target.value })} /></label>
          <label><span className="qm-label-text">Original Inquiry Text</span><textarea rows="4" value={form.original_text} onChange={(event) => setForm({ ...form, original_text: event.target.value })} /></label>

          <div className="qm-line-editor">
            <div className="qm-workflow-step compact">
              <span>Step 2</span>
              <div>
                <h4>Inquiry Lines</h4>
                <p>Add the items requested by the customer. Match now if you know the private quote item.</p>
              </div>
              <button type="button" className="qm-secondary small" onClick={() => setForm({ ...form, lines: [...form.lines, newLine()] })}>Add Line</button>
            </div>
            {form.lines.map((line, index) => (
              <div key={index} className="qm-line-form">
                <input aria-label="Requested item name" placeholder="Requested item name" required value={line.raw_name} onChange={(event) => updateLine(index, { raw_name: event.target.value })} />
                <input aria-label="Qty" type="number" min="0" step="0.001" placeholder="Qty" value={line.quantity} onChange={(event) => updateLine(index, { quantity: event.target.value })} />
                <input aria-label="Unit" placeholder="Unit" value={line.unit} onChange={(event) => updateLine(index, { unit: event.target.value })} />
                <select aria-label="Match status" value={line.matched_quote_item} onChange={(event) => {
                  const matched = event.target.value;
                  updateLine(index, { matched_quote_item: matched, match_status: matched ? 'confirmed' : 'unresolved' });
                }}>
                  <option value="">Match status: Unmatched</option>
                  {items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
                <button type="button" className="qm-icon danger" onClick={() => removeLine(index)} disabled={form.lines.length === 1}>Delete</button>
              </div>
            ))}
          </div>

          <div className="qm-workflow-step compact">
            <span>Step 3</span>
            <div>
              <h4>Create Quote</h4>
              <p>Save the inquiry first, then use the table action to create one quotation from it.</p>
            </div>
          </div>
          <button type="submit" className="qm-primary" disabled={saving}>{saving ? 'Saving...' : 'Create Inquiry'}</button>
        </form>
        </div>
      </div>
    </div>
  );
};

export default InquiryManager;
