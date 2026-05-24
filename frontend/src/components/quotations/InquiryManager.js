import React, { useEffect, useMemo, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import CompanySelectWithCreate from './CompanySelectWithCreate';
import QuotationErrorNotice from './QuotationErrorNotice';

const newLine = () => ({
  raw_name: '',
  quantity: '1',
  unit: '',
  notes: '',
  matched_product: '',
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
  matched_product: '',
  match_reason: '',
  match_status: 'unresolved',
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
  const [expandedRawRows, setExpandedRawRows] = useState({});
  const [selectedImportRows, setSelectedImportRows] = useState([]);
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

  const rememberCompany = (company) => {
    setCompanies((current) => {
      const withoutDuplicate = current.filter((candidate) => candidate.id !== company.id);
      return [...withoutDuplicate, company].sort((a, b) => a.name.localeCompare(b.name));
    });
  };

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
          matched_product: line.matched_product || null,
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
    setExpandedRawRows({});
    setSelectedImportRows([]);
    setImportPreview({
      ...preview,
      lines: (preview.lines || []).map((line) => ({
        ...newImportLine(),
        ...line,
        raw_line: line.raw_line || line.raw_source_line || '',
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
      const response = await quotationAPI.inquiries.parseText({ raw_text: importForm.raw_text, company: importForm.company || null });
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
    if (importForm.company) formData.append('company', importForm.company);
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
    setSelectedImportRows((current) => current.filter((rowIndex) => rowIndex !== index).map((rowIndex) => rowIndex > index ? rowIndex - 1 : rowIndex));
    setImportPreview((current) => ({
      ...current,
      lines: current.lines.filter((_, lineIndex) => lineIndex !== index),
    }));
  };

  const toggleImportRowSelection = (index) => {
    setSelectedImportRows((current) => (
      current.includes(index)
        ? current.filter((rowIndex) => rowIndex !== index)
        : [...current, index]
    ));
  };

  const toggleAllImportRows = () => {
    const allIndexes = (importPreview?.lines || []).map((_, index) => index);
    setSelectedImportRows((current) => current.length === allIndexes.length ? [] : allIndexes);
  };

  const removeSelectedImportRows = () => {
    if (!selectedImportRows.length) return;
    setSavedImportedInquiry(null);
    setImportPreview((current) => ({
      ...current,
      lines: current.lines.filter((_, index) => !selectedImportRows.includes(index)),
    }));
    setSelectedImportRows([]);
  };

  const addImportLine = () => {
    setSavedImportedInquiry(null);
    setImportPreview((current) => ({
      ...(current || {
        source_type: importMode === 'paste' ? 'pasted_text' : importMode,
        source_filename: importFile?.name || '',
        source_mime_type: importFile?.type || '',
        source_sha256: '',
        source_file_ref: '',
        source_file_size: null,
        parse_method: 'manual_review',
        original_text: importForm.raw_text,
        warnings: [],
        summary: {},
        meta: {},
      }),
      lines: [...(current?.lines || []), newImportLine()],
    }));
  };

  const toggleRawRow = (index) => {
    setExpandedRawRows((current) => ({ ...current, [index]: !current[index] }));
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
        matched_product: line.matched_product || null,
        match_reason: line.match_reason || '',
        match_status: line.match_status || (line.matched_product ? 'confirmed' : 'unresolved'),
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
        source_file_ref: importPreview?.source_file_ref || '',
        source_file_size: importPreview?.source_file_size || null,
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
            <p>Paste text or preview a supported file, review the lines, then save the inquiry. Source files are kept private and are never exposed publicly.</p>
          </div>
          <div className="qm-mode-tabs">
            <button type="button" className={importMode === 'paste' ? 'active' : ''} onClick={() => { setImportMode('paste'); setImportFile(null); }}>Paste Text</button>
            <button type="button" className={importMode === 'excel' ? 'active' : ''} onClick={() => { setImportMode('excel'); setImportFile(null); }}>Upload Excel</button>
            <button type="button" className={importMode === 'pdf' ? 'active' : ''} onClick={() => { setImportMode('pdf'); setImportFile(null); }}>Upload PDF</button>
          </div>
        </div>

        <div className="qm-grid-two">
          <CompanySelectWithCreate
            companies={companies}
            value={importForm.company}
            required
            onChange={(companyId) => setImportForm({ ...importForm, company: companyId, contact: '' })}
            onCreated={rememberCompany}
          />
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
            <label><span className="qm-label-text">{importMode === 'excel' ? 'Upload Excel file' : 'Upload digitally generated .pdf file'}</span>
              <input
                key={importMode}
                type="file"
                accept={importMode === 'excel' ? '.xlsx,.xlsb,.xls' : '.pdf'}
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
            <div className="qm-summary-banner">
              <div className="qm-summary-stat">
                <span>Source</span>
                <strong>{importPreview.source_filename || importPreview.source_type}</strong>
              </div>
              <div className="qm-summary-stat">
                <span>Method</span>
                <strong>{importPreview.parse_method || 'manual_review'}</strong>
              </div>
              <div className="qm-summary-stat success">
                <span>Parsed</span>
                <strong>{importPreview.summary?.parsed_count ?? 0}</strong>
              </div>
              <div className="qm-summary-stat warning">
                <span>Needs review</span>
                <strong>{importPreview.summary?.needs_review_count ?? 0}</strong>
              </div>
              <div className="qm-summary-stat muted">
                <span>Skipped</span>
                <strong>{importPreview.summary?.skipped_count ?? 0}</strong>
              </div>
            </div>
            <div className="qm-preview-meta">
              <span>Total lines: {importPreview.lines.length}</span>
              <span>Selected: {selectedImportRows.length}</span>
              {(importPreview.meta?.selected_sheets || []).map((sheet) => (
                <span key={sheet.sheet_name}>{sheet.sheet_name}: header row {sheet.header_row}</span>
              ))}
              {importPreview.source_file_ref && <span>Private source saved</span>}
            </div>
            <div className="qm-bulk-toolbar compact">
              <strong>{selectedImportRows.length} rows selected</strong>
              <button type="button" className="qm-secondary small" disabled={!importPreview.lines.length} onClick={toggleAllImportRows}>
                {selectedImportRows.length === importPreview.lines.length ? 'Deselect All' : 'Select All'}
              </button>
              <button type="button" className="qm-secondary small danger" disabled={!selectedImportRows.length} onClick={removeSelectedImportRows}>Delete Selected</button>
              <button type="button" className="qm-secondary small" disabled={!selectedImportRows.length} onClick={() => setSelectedImportRows([])}>Clear</button>
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
                    <th className="qm-check-cell"><input type="checkbox" checked={importPreview.lines.length > 0 && selectedImportRows.length === importPreview.lines.length} onChange={toggleAllImportRows} /></th>
                    <th>Requested Item Name</th>
                    <th>Matched Product</th>
                    <th>Qty</th>
                    <th>Unit</th>
                    <th>Status</th>
                    <th>Confidence</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {importPreview.lines.map((line, index) => (
                    <React.Fragment key={`${line.raw_line || line.raw_name}-${index}`}>
                      <tr>
                        <td className="qm-check-cell"><input type="checkbox" checked={selectedImportRows.includes(index)} onChange={() => toggleImportRowSelection(index)} /></td>
                        <td className="qm-import-item-cell"><input value={line.raw_name} onChange={(event) => updateImportLine(index, { raw_name: event.target.value })} /></td>
                        <td>
                          <select value={line.matched_product || ''} onChange={(event) => updateImportLine(index, {
                            matched_product: event.target.value || null,
                            match_status: event.target.value ? 'confirmed' : 'unresolved',
                          })}>
                            <option value="">Unmatched</option>
                            {items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                          </select>
                          {line.match_reason && <small className="qm-muted-text">{line.match_reason}</small>}
                        </td>
                        <td className="qm-import-qty-cell"><input type="number" min="0" step="0.001" value={line.quantity || ''} onChange={(event) => updateImportLine(index, { quantity: event.target.value })} /></td>
                        <td className="qm-import-unit-cell"><input value={line.unit || ''} onChange={(event) => updateImportLine(index, { unit: event.target.value })} /></td>
                        <td className="qm-import-status-cell">
                          <select value={line.parse_status || 'needs_review'} onChange={(event) => updateImportLine(index, { parse_status: event.target.value })}>
                            <option value="parsed">Parsed</option>
                            <option value="needs_review">Needs Review</option>
                            <option value="unparsed">Unparsed</option>
                            <option value="manual">Manual</option>
                          </select>
                        </td>
                        <td><span className={`qm-confidence status-${line.parse_status || 'needs_review'}`}>{Math.round(Number(line.parse_confidence || 0) * 100)}%</span></td>
                        <td className="qm-row-actions">
                          <button type="button" className="qm-secondary small" onClick={() => toggleRawRow(index)}>
                            {expandedRawRows[index] ? 'Hide Raw' : 'View Raw'}
                          </button>
                          <button type="button" className="qm-secondary small danger" onClick={() => removeImportLine(index)}>Delete</button>
                        </td>
                      </tr>
                      {expandedRawRows[index] && (
                        <tr className="qm-raw-row">
                          <td />
                          <td colSpan="7">
                            <label>
                              <span className="qm-label-text">Raw source line</span>
                              <textarea rows="2" value={line.raw_line || ''} onChange={(event) => updateImportLine(index, { raw_line: event.target.value })} />
                            </label>
                            <div className="qm-raw-meta">
                              {line.sheet_name && <span>Sheet: {line.sheet_name}</span>}
                              {line.row_number && <span>Row: {line.row_number}</span>}
                              {line.page_number && <span>Page: {line.page_number}</span>}
                              {line.serial_no && <span>Serial: {line.serial_no}</span>}
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="qm-import-actions sticky">
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
                      <td>{line.matched_product_name || line.matched_quote_item_name || '-'}</td>
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
          <CompanySelectWithCreate
            companies={companies}
            value={form.company}
            required
            onChange={(companyId) => setForm({ ...form, company: companyId, contact: '' })}
            onCreated={rememberCompany}
          />
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
              <p>Add the items requested by the customer. Match now if you know the product item.</p>
              </div>
              <button type="button" className="qm-secondary small" onClick={() => setForm({ ...form, lines: [...form.lines, newLine()] })}>Add Line</button>
            </div>
            {form.lines.map((line, index) => (
              <div key={index} className="qm-line-form">
                <input aria-label="Requested item name" placeholder="Requested item name" required value={line.raw_name} onChange={(event) => updateLine(index, { raw_name: event.target.value })} />
                <input aria-label="Qty" type="number" min="0" step="0.001" placeholder="Qty" value={line.quantity} onChange={(event) => updateLine(index, { quantity: event.target.value })} />
                <input aria-label="Unit" placeholder="Unit" value={line.unit} onChange={(event) => updateLine(index, { unit: event.target.value })} />
                <select aria-label="Matched product" value={line.matched_product} onChange={(event) => {
                  const matched = event.target.value;
                  updateLine(index, { matched_product: matched, match_status: matched ? 'confirmed' : 'unresolved' });
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
