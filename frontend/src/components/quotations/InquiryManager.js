import React, { useEffect, useMemo, useRef, useState } from 'react';
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
  unit_price: '',
  vat_rate: '0',
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
  raw_html: '',
};

const normalizeVatRate = (value) => {
  const numeric = Number(value || 0);
  return numeric === 5 ? '5' : '0';
};

const shouldShowMatchReason = (reason) => {
  const text = String(reason || '').trim();
  return text && !/no safe deterministic match found/i.test(text);
};

const contactOptionLabel = (contact) => {
  const details = [contact.role, contact.department].filter(Boolean).join(', ');
  return details ? `${contact.name} - ${details}` : contact.name;
};

const emptyContactForm = {
  name: '',
  email: '',
  phone: '',
  role: '',
  department: '',
  is_primary: false,
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
  const [priceReferenceMode, setPriceReferenceMode] = useState('file');
  const [priceReferenceFile, setPriceReferenceFile] = useState(null);
  const [priceReferenceText, setPriceReferenceText] = useState('');
  const [priceReferenceHtml, setPriceReferenceHtml] = useState('');
  const [priceReferenceUseAi, setPriceReferenceUseAi] = useState(true);
  const [importPreview, setImportPreview] = useState(null);
  const [savedImportedInquiry, setSavedImportedInquiry] = useState(null);
  const [importParsing, setImportParsing] = useState(false);
  const [importSaving, setImportSaving] = useState(false);
  const [priceReferenceApplying, setPriceReferenceApplying] = useState(false);
  const [importNotice, setImportNotice] = useState(null);
  const [importActionNotice, setImportActionNotice] = useState(null);
  const [importValidationDialog, setImportValidationDialog] = useState(null);
  const [expandedRawRows, setExpandedRawRows] = useState({});
  const [selectedImportRows, setSelectedImportRows] = useState([]);
  const [aiCleaning, setAiCleaning] = useState(false);
  const [aiCandidate, setAiCandidate] = useState(null);
  const [errorInfo, setErrorInfo] = useState(null);
  const [showImportContactForm, setShowImportContactForm] = useState(false);
  const [importContactForm, setImportContactForm] = useState(emptyContactForm);
  const [importContactSaving, setImportContactSaving] = useState(false);
  const [showManualContactForm, setShowManualContactForm] = useState(false);
  const [manualContactForm, setManualContactForm] = useState(emptyContactForm);
  const [manualNotice, setManualNotice] = useState(null);
  const [manualContactSaving, setManualContactSaving] = useState(false);
  const importPanelRef = useRef(null);

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

  const rememberContact = (contact) => {
    setContacts((current) => {
      const withoutDuplicate = current.filter((candidate) => candidate.id !== contact.id);
      return [...withoutDuplicate, contact].sort((a, b) => a.name.localeCompare(b.name));
    });
  };

  const createContactForInquiry = async (mode) => {
    const isImport = mode === 'import';
    const companyId = isImport ? importForm.company : form.company;
    const draft = isImport ? importContactForm : manualContactForm;
    if (!companyId || !draft.name.trim()) return;

    const setSavingState = isImport ? setImportContactSaving : setManualContactSaving;
    setSavingState(true);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.contacts.create({
        ...draft,
        company: companyId,
        name: draft.name.trim(),
      });
      rememberContact(response.data);
      if (isImport) {
        setImportForm((current) => ({ ...current, contact: response.data.id }));
        setImportContactForm(emptyContactForm);
        setShowImportContactForm(false);
        setImportNotice({ type: 'success', message: 'Contact created and selected for this imported inquiry.' });
      } else {
        setForm((current) => ({ ...current, contact: response.data.id }));
        setManualContactForm(emptyContactForm);
        setShowManualContactForm(false);
        setManualNotice({ type: 'success', message: 'Contact created and selected for this inquiry.' });
      }
    } catch (error) {
      const details = await describeQuotationError(error, 'Create inquiry contact', 'POST /quotations/contacts/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSavingState(false);
    }
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
      if (savedImportedInquiry?.id === inquiry.id) {
        setImportActionNotice({ type: 'success', message: 'Quotation already exists. Open it to continue editing the lines.' });
      }
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
      if (savedImportedInquiry?.id === inquiry.id) {
        setImportActionNotice({
          type: 'success',
          message: response.status === 200
            ? 'Quotation already exists. Open it to continue editing the lines.'
            : 'Quotation created. Open it to continue editing the lines.',
        });
      }
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
    setImportActionNotice(null);
    setExpandedRawRows({});
    setSelectedImportRows([]);
    const candidate = preview.ai_candidate || null;
    setAiCandidate(candidate);
    setImportPreview({
      ...preview,
      result_source: preview.result_source || 'deterministic_parse',
      lines: (preview.lines || []).map((line) => ({
        ...newImportLine(),
        ...line,
        raw_line: line.raw_line || line.raw_source_line || '',
        parse_confidence: Number(line.parse_confidence || 0),
      })),
    });
  };

  const aiSourceLabel = (source) => {
    if (source === 'ai_vision_cleanup') return 'AI vision cleanup used';
    if (source === 'ai_text_cleanup') return 'AI text cleanup used';
    if (source === 'ai_failed_using_original_parse') return 'AI failed, using original parse';
    return 'Deterministic parse';
  };

  const runAiCleanParse = async () => {
    if (!importPreview || aiCleaning) return;
    setAiCleaning(true);
    setErrorInfo(null);
    setImportNotice(null);
    try {
      const response = await quotationAPI.inquiries.aiCleanParse({
        preview: importPreview,
        company: importForm.company || null,
        mode: 'auto',
      });
      setAiCandidate(response.data);
      setImportNotice({ type: 'success', message: `${aiSourceLabel(response.data.result_source)}. Review the candidate rows before applying them.` });
    } catch (error) {
      const details = await describeQuotationError(error, 'AI clean inquiry parse', 'POST /quotations/inquiries/ai_clean_parse/');
      setErrorInfo(details);
      setImportNotice({ type: 'warning', message: 'AI failed, using original parse.' });
      console.error(formatQuotationError(details), error);
    } finally {
      setAiCleaning(false);
    }
  };

  const applyAiCandidate = () => {
    if (!aiCandidate) return;
    setSavedImportedInquiry(null);
    setExpandedRawRows({});
    setSelectedImportRows([]);
    setImportPreview({
      ...importPreview,
      ...aiCandidate,
      result_source: aiCandidate.result_source,
      ai_status: aiCandidate.ai_status,
      ai_status_label: aiCandidate.ai_status_label,
      lines: (aiCandidate.lines || []).map((line) => ({
        ...newImportLine(),
        ...line,
        raw_line: line.raw_line || line.raw_source_line || '',
        parse_confidence: Number(line.parse_confidence || 0),
      })),
    });
    setAiCandidate(null);
    setImportNotice({ type: 'success', message: 'AI cleaned rows applied. Review and edit before saving the inquiry.' });
  };

  const keepOriginalRows = () => {
    setAiCandidate(null);
    setImportNotice({ type: 'success', message: 'Kept deterministic parser rows.' });
  };

  const parsePastedText = async () => {
    if (importParsing) return;
    setImportParsing(true);
    setErrorInfo(null);
    setImportNotice(null);
    try {
      const response = await quotationAPI.inquiries.parseText({
        raw_text: importForm.raw_text,
        raw_html: importForm.raw_html || '',
        company: importForm.company || null,
      });
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

  const applyPriceReference = async () => {
    if (priceReferenceApplying) return;
    if (!importPreview?.lines?.length) {
      setImportNotice({ type: 'error', message: 'Parse an inquiry before applying a price reference.' });
      return;
    }
    if (priceReferenceMode === 'file' && !priceReferenceFile) {
      setImportNotice({ type: 'error', message: "Choose Dad's Excel or PDF price reference first." });
      return;
    }
    if (priceReferenceMode === 'paste' && !priceReferenceText.trim()) {
      setImportNotice({ type: 'error', message: "Paste Dad's price reference rows first." });
      return;
    }
    setPriceReferenceApplying(true);
    setErrorInfo(null);
    setImportNotice(null);
    const formData = new FormData();
    if (priceReferenceMode === 'file') {
      formData.append('file', priceReferenceFile);
    } else {
      formData.append('raw_text', priceReferenceText);
      formData.append('raw_html', priceReferenceHtml);
    }
    formData.append('use_ai', priceReferenceUseAi ? 'true' : 'false');
    formData.append('preview', JSON.stringify(importPreview));
    try {
      const response = await quotationAPI.inquiries.applyPriceReference(formData);
      const currentLines = importPreview.lines || [];
      const responseLines = response.data.lines || [];
      setPreview({
        ...response.data,
        result_source: response.data.result_source || importPreview.result_source || 'deterministic_parse',
        lines: responseLines.map((line, index) => {
          const currentLine = currentLines[index] || {};
          const hasCurrentQuantity = Object.prototype.hasOwnProperty.call(currentLine, 'quantity');
          const hasCurrentUnit = Object.prototype.hasOwnProperty.call(currentLine, 'unit');
          return {
            ...line,
            quantity: hasCurrentQuantity ? currentLine.quantity : line.quantity ?? '',
            unit: hasCurrentUnit ? currentLine.unit : line.unit ?? '',
          };
        }),
      });
      const summary = response.data.price_reference_summary || {};
      setImportNotice({
        type: 'success',
        message: `Price reference applied. ${summary.matched_count || 0} prices filled, ${summary.needs_review_count || 0} likely matches need review, ${summary.unmatched_count || 0} unmatched.`,
      });
    } catch (error) {
      const details = await describeQuotationError(error, 'Apply inquiry price reference', 'POST /quotations/inquiries/apply_price_reference/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setPriceReferenceApplying(false);
    }
  };

  const updateImportLine = (index, patch) => {
    setSavedImportedInquiry(null);
    setImportActionNotice(null);
    setImportPreview((current) => ({
      ...current,
      lines: current.lines.map((line, lineIndex) => lineIndex === index ? { ...line, ...patch } : line),
    }));
  };

  const removeImportLine = (index) => {
    setSavedImportedInquiry(null);
    setImportActionNotice(null);
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
    setImportActionNotice(null);
    setImportPreview((current) => ({
      ...current,
      lines: current.lines.filter((_, index) => !selectedImportRows.includes(index)),
    }));
    setSelectedImportRows([]);
  };

  const addImportLine = () => {
    setSavedImportedInquiry(null);
    setImportActionNotice(null);
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
        unit_price: line.unit_price || null,
        vat_rate: normalizeVatRate(line.vat_rate),
        notes: line.notes || '',
        parse_status: line.parse_status || 'needs_review',
        parse_confidence: Number(line.parse_confidence || 0),
        matched_product: line.matched_product || null,
        match_reason: line.match_reason || '',
        match_status: line.match_status || (line.matched_product ? 'confirmed' : 'unresolved'),
      }));
    if (!importForm.company) {
      const message = 'Select a company before saving this imported inquiry.';
      setImportActionNotice({ type: 'error', message });
      setImportValidationDialog({
        title: 'Company required',
        message,
        action: 'company',
      });
      return;
    }
    if (!lines.length) {
      const message = 'Add at least one reviewed inquiry line before saving.';
      setImportActionNotice({ type: 'error', message });
      setImportValidationDialog({
        title: 'No inquiry lines',
        message,
      });
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
      setImportActionNotice({ type: 'success', message: 'Inquiry saved. Create the quotation here, then open it for Step 4 line editing.' });
      await load();
    } catch (error) {
      const details = await describeQuotationError(error, 'Save imported inquiry', 'POST /quotations/inquiries/create_imported/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setImportSaving(false);
    }
  };

  const savedQuoteForCurrentImport = savedImportedInquiry && quoteSuccess?.inquiryId === savedImportedInquiry.id
    ? quoteSuccess
    : null;

  const goToImportCompany = () => {
    setImportValidationDialog(null);
    importPanelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
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
      <div className="qm-panel qm-import-panel" ref={importPanelRef}>
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

        <div className="qm-inquiry-party-grid">
          <CompanySelectWithCreate
            companies={companies}
            value={importForm.company}
            required
            onChange={(companyId) => {
              setImportForm({ ...importForm, company: companyId, contact: '' });
              setImportContactForm(emptyContactForm);
              setShowImportContactForm(false);
            }}
            onCreated={rememberCompany}
          />
          <div className="qm-contact-control">
            <label>
              <span className="qm-label-text">Contact / Purchaser</span>
              <select disabled={!importForm.company} value={importForm.contact} onChange={(event) => setImportForm({ ...importForm, contact: event.target.value })}>
                <option value="">No contact</option>
                {filteredImportContacts.map((contact) => <option key={contact.id} value={contact.id}>{contactOptionLabel(contact)}</option>)}
              </select>
            </label>
            <button type="button" className="qm-secondary small" disabled={!importForm.company} onClick={() => setShowImportContactForm((value) => !value)}>
              {showImportContactForm ? 'Cancel new contact' : '+ Create contact'}
            </button>
          </div>
        </div>
        {showImportContactForm && (
          <div className="qm-inline-card qm-contact-card">
            <label>Name<input required value={importContactForm.name} onChange={(event) => setImportContactForm({ ...importContactForm, name: event.target.value })} /></label>
            <label>Phone<input value={importContactForm.phone} onChange={(event) => setImportContactForm({ ...importContactForm, phone: event.target.value })} /></label>
            <label>Email<input type="email" value={importContactForm.email} onChange={(event) => setImportContactForm({ ...importContactForm, email: event.target.value })} /></label>
            <label>Position / Designation<input value={importContactForm.role} onChange={(event) => setImportContactForm({ ...importContactForm, role: event.target.value })} /></label>
            <label>Department<input value={importContactForm.department} onChange={(event) => setImportContactForm({ ...importContactForm, department: event.target.value })} /></label>
            <label className="qm-checkbox"><input type="checkbox" checked={importContactForm.is_primary} onChange={(event) => setImportContactForm({ ...importContactForm, is_primary: event.target.checked })} /> Primary contact</label>
            <button type="button" className="qm-primary" disabled={importContactSaving || !importContactForm.name.trim()} onClick={() => createContactForInquiry('import')}>
              {importContactSaving ? 'Creating contact...' : 'Create and select contact'}
            </button>
          </div>
        )}
        <label className="qm-full-label"><span className="qm-label-text">Subject</span>
          <input className="qm-input" placeholder="Inquiry subject or LPO reference" value={importForm.subject} onChange={(event) => setImportForm({ ...importForm, subject: event.target.value })} />
        </label>

        {importMode === 'paste' ? (
          <div className="qm-import-source">
            <label><span className="qm-label-text">Paste inquiry text</span>
              <textarea
                rows="5"
                value={importForm.raw_text}
                onPaste={(event) => {
                  const html = event.clipboardData?.getData('text/html') || '';
                  setImportForm((current) => ({ ...current, raw_html: html.includes('<table') ? html : current.raw_html }));
                }}
                onChange={(event) => setImportForm({ ...importForm, raw_text: event.target.value, raw_html: event.target.value ? importForm.raw_html : '' })}
                placeholder="Paste the customer's requested items here..."
              />
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

        <div className="qm-price-reference-box">
          <div className="qm-price-reference-header">
            <div className="qm-price-reference-copy">
              <h4>Optional: Fill Prices from Dad's Reference</h4>
              <p>Use a previous company Excel/PDF file or pasted price section to fill only unit price and VAT before saving.</p>
            </div>
            <span className="qm-price-reference-pill">Price + VAT only</span>
          </div>
          <div className="qm-price-reference-body">
            <div className="qm-price-reference-source">
              <span className="qm-mini-label">Source</span>
              <div className="qm-reference-tabs" role="tablist" aria-label="Price reference source">
                <button
                  type="button"
                  className={priceReferenceMode === 'file' ? 'active' : ''}
                  onClick={() => setPriceReferenceMode('file')}
                >
                  Upload file
                </button>
                <button
                  type="button"
                  className={priceReferenceMode === 'paste' ? 'active' : ''}
                  onClick={() => setPriceReferenceMode('paste')}
                >
                  Paste rows
                </button>
              </div>
            </div>
            <div className="qm-price-reference-input">
              {priceReferenceMode === 'file' ? (
                <label>
                  <span className="qm-label-text">Price reference file</span>
                  <input
                    type="file"
                    accept=".xlsx,.xls,.xlsb,.pdf"
                    onChange={(event) => setPriceReferenceFile(event.target.files?.[0] || null)}
                  />
                </label>
              ) : (
                <label>
                  <span className="qm-label-text">Pasted price reference</span>
                  <textarea
                    rows="4"
                    value={priceReferenceText}
                    onPaste={(event) => {
                      const html = event.clipboardData?.getData('text/html') || '';
                      setPriceReferenceHtml(html.includes('<table') ? html : '');
                    }}
                    onChange={(event) => {
                      setPriceReferenceText(event.target.value);
                      if (!event.target.value) setPriceReferenceHtml('');
                    }}
                    placeholder="Paste Dad's priced rows here..."
                  />
                </label>
              )}
              <label className="qm-checkbox qm-price-reference-ai">
                <input
                  type="checkbox"
                  checked={priceReferenceUseAi}
                  onChange={(event) => setPriceReferenceUseAi(event.target.checked)}
                />
                Use AI cleanup when available
              </label>
            </div>
            <div className="qm-price-reference-submit">
              <button
                type="button"
                className="qm-primary qm-price-reference-action"
                disabled={
                  priceReferenceApplying ||
                  !importPreview?.lines?.length ||
                  (priceReferenceMode === 'file' ? !priceReferenceFile : !priceReferenceText.trim())
                }
                onClick={applyPriceReference}
              >
                {priceReferenceApplying ? 'Applying prices...' : 'Apply Price Reference'}
              </button>
              <small>Matches by item name.</small>
            </div>
          </div>
        </div>

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
              <span className={`qm-source-badge source-${importPreview.result_source || 'deterministic_parse'}`}>
                {aiSourceLabel(importPreview.result_source)}
              </span>
              {importPreview.ai_status_label && <span>{importPreview.ai_status_label}</span>}
              <span>Total lines: {importPreview.lines.length}</span>
              <span>Selected: {selectedImportRows.length}</span>
              {(importPreview.meta?.selected_sheets || []).map((sheet) => (
                <span key={sheet.sheet_name}>{sheet.sheet_name}: header row {sheet.header_row}</span>
              ))}
              {importPreview.source_file_ref && <span>Private source saved</span>}
            </div>
            <div className="qm-bulk-toolbar compact">
              <strong>{selectedImportRows.length} rows selected</strong>
              <button type="button" className="qm-secondary small" disabled={aiCleaning || !importPreview.lines.length} onClick={runAiCleanParse}>
                {aiCleaning ? 'Cleaning...' : 'AI Clean Parse'}
              </button>
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
            {aiCandidate && (
              <div className="qm-ai-candidate">
                <div>
                  <strong>{aiSourceLabel(aiCandidate.result_source)}</strong>
                  <p>These are AI-cleaned candidate rows. They do not replace the current review rows until you apply them.</p>
                </div>
                <div className="qm-ai-candidate-summary">
                  <span>{aiCandidate.lines?.length || 0} candidate rows</span>
                  <span>Provider: {aiCandidate.provider || '-'}</span>
                  <span>Model: {aiCandidate.model || '-'}</span>
                  {aiCandidate.cache_hit && <span>Cached result</span>}
                </div>
                <div className="qm-ai-candidate-preview">
                  {(aiCandidate.lines || []).slice(0, 5).map((line, index) => (
                    <div key={`${line.raw_name}-${index}`}>
                      <strong>{line.raw_name}</strong>
                      <span>{line.quantity || '-'} {line.unit || ''}</span>
                      {line.unit_price && <span>Price {line.unit_price}</span>}
                      <em>{Math.round(Number(line.parse_confidence || 0) * 100)}%</em>
                    </div>
                  ))}
                </div>
                <div className="qm-action-row">
                  <button type="button" className="qm-primary small" onClick={applyAiCandidate}>Apply AI Cleaned Rows</button>
                  <button type="button" className="qm-secondary small" onClick={keepOriginalRows}>Keep Original</button>
                </div>
              </div>
            )}
            <div className="qm-table-wrap">
              <table className="qm-table import-table">
                <thead>
                  <tr>
                    <th className="qm-check-cell"><input type="checkbox" checked={importPreview.lines.length > 0 && selectedImportRows.length === importPreview.lines.length} onChange={toggleAllImportRows} /></th>
                    <th className="qm-serial-cell">#</th>
                    <th>Requested Item Name</th>
                    <th>Matched Product</th>
                    <th>Qty</th>
                    <th>Unit</th>
                    <th>Unit Price</th>
                    <th>VAT</th>
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
                        <td className="qm-serial-cell">{index + 1}</td>
                        <td className="qm-import-item-cell"><input value={line.raw_name} onChange={(event) => updateImportLine(index, { raw_name: event.target.value })} /></td>
                        <td className="qm-import-match-cell">
                          <select value={line.matched_product || ''} onChange={(event) => updateImportLine(index, {
                            matched_product: event.target.value || null,
                            match_status: event.target.value ? 'confirmed' : 'unresolved',
                          })}>
                            <option value="">Unmatched</option>
                            {items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                          </select>
                          {shouldShowMatchReason(line.match_reason) && <small className="qm-muted-text">{line.match_reason}</small>}
                        </td>
                        <td className="qm-import-qty-cell"><input type="number" min="0" step="0.001" value={line.quantity || ''} onChange={(event) => updateImportLine(index, { quantity: event.target.value })} /></td>
                        <td className="qm-import-unit-cell"><input value={line.unit || ''} onChange={(event) => updateImportLine(index, { unit: event.target.value })} /></td>
                        <td className="qm-import-price-cell">
                          <input type="number" min="0" step="0.01" value={line.unit_price || ''} onChange={(event) => updateImportLine(index, { unit_price: event.target.value })} />
                          {line.price_reference_match && (
                            <small className={`qm-price-match ${line.price_reference_status || ''}`}>
                              {line.price_reference_match.match_label} match: {line.price_reference_match.item_name} ({Math.round(Number(line.price_reference_match.confidence || 0) * 100)}%)
                            </small>
                          )}
                        </td>
                        <td className="qm-import-vat-cell">
                          <select value={normalizeVatRate(line.vat_rate)} onChange={(event) => updateImportLine(index, { vat_rate: event.target.value })}>
                            <option value="0">0%</option>
                            <option value="5">5%</option>
                          </select>
                        </td>
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
                          <td />
                          <td colSpan="9">
                            <label>
                              <span className="qm-label-text">Raw source line</span>
                              <textarea rows="2" value={line.raw_line || ''} onChange={(event) => updateImportLine(index, { raw_line: event.target.value })} />
                            </label>
                            <div className="qm-raw-meta">
                              {line.sheet_name && <span>Sheet: {line.sheet_name}</span>}
                              {line.row_number && <span>Row: {line.row_number}</span>}
                              {line.page_number && <span>Page: {line.page_number}</span>}
                              {line.serial_no && <span>Serial: {line.serial_no}</span>}
                              {line.unit_price && <span>Unit price: {line.unit_price}</span>}
                              {line.price_reference_match && <span>Price source: {line.price_reference_match.sheet_name} row {line.price_reference_match.row_number}</span>}
                              {line.line_total && <span>Total: {line.line_total}</span>}
                              {line.notes && <span>Notes: {line.notes}</span>}
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
              {importActionNotice && (
                <div className={`qm-action-feedback ${importActionNotice.type}`}>
                  {importActionNotice.message}
                </div>
              )}
              <button type="button" className="qm-secondary" onClick={addImportLine}>Add Missing Row</button>
              <button type="button" className="qm-primary" disabled={importSaving || Boolean(savedImportedInquiry)} onClick={saveImportedInquiry}>
                {importSaving ? 'Saving...' : savedImportedInquiry ? 'Inquiry Saved' : 'Save Inquiry'}
              </button>
              {savedImportedInquiry && !savedQuoteForCurrentImport && (
                <button type="button" className="qm-secondary" disabled={Boolean(creatingQuoteId)} onClick={() => createQuote(savedImportedInquiry)}>
                  {creatingQuoteId === savedImportedInquiry.id ? 'Creating...' : 'Create Quotation from This Inquiry'}
                </button>
              )}
              {savedQuoteForCurrentImport && (
                <button type="button" className="qm-primary" onClick={() => openCreatedQuote(savedQuoteForCurrentImport.quoteId)}>
                  Open Quotation
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
            onChange={(companyId) => {
              setForm({ ...form, company: companyId, contact: '' });
              setManualContactForm(emptyContactForm);
              setShowManualContactForm(false);
            }}
            onCreated={rememberCompany}
          />
          <div className="qm-contact-control">
            <label>
              <span className="qm-label-text">Contact / Purchaser</span>
              <select disabled={!form.company} value={form.contact} onChange={(event) => setForm({ ...form, contact: event.target.value })}>
                <option value="">No contact</option>
                {filteredContacts.map((contact) => <option key={contact.id} value={contact.id}>{contactOptionLabel(contact)}</option>)}
              </select>
            </label>
            <button type="button" className="qm-secondary small" disabled={!form.company} onClick={() => setShowManualContactForm((value) => !value)}>
              {showManualContactForm ? 'Cancel new contact' : '+ Create contact'}
            </button>
          </div>
          {showManualContactForm && (
            <div className="qm-inline-card qm-contact-card">
              <label>Name<input required value={manualContactForm.name} onChange={(event) => setManualContactForm({ ...manualContactForm, name: event.target.value })} /></label>
              <label>Phone<input value={manualContactForm.phone} onChange={(event) => setManualContactForm({ ...manualContactForm, phone: event.target.value })} /></label>
              <label>Email<input type="email" value={manualContactForm.email} onChange={(event) => setManualContactForm({ ...manualContactForm, email: event.target.value })} /></label>
              <label>Position / Designation<input value={manualContactForm.role} onChange={(event) => setManualContactForm({ ...manualContactForm, role: event.target.value })} /></label>
              <label>Department<input value={manualContactForm.department} onChange={(event) => setManualContactForm({ ...manualContactForm, department: event.target.value })} /></label>
              <label className="qm-checkbox"><input type="checkbox" checked={manualContactForm.is_primary} onChange={(event) => setManualContactForm({ ...manualContactForm, is_primary: event.target.checked })} /> Primary contact</label>
              <button type="button" className="qm-primary" disabled={manualContactSaving || !manualContactForm.name.trim()} onClick={() => createContactForInquiry('manual')}>
                {manualContactSaving ? 'Creating contact...' : 'Create and select contact'}
              </button>
            </div>
          )}
          {manualNotice && <div className={`qm-feedback ${manualNotice.type}`}>{manualNotice.message}</div>}
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
      {importValidationDialog && (
        <div className="qm-modal-backdrop" role="presentation">
          <div className="qm-modal qm-validation-modal" role="dialog" aria-modal="true" aria-labelledby="import-validation-title">
            <div className="qm-panel-heading">
              <div>
                <h3 id="import-validation-title">{importValidationDialog.title}</h3>
                <p>{importValidationDialog.message}</p>
              </div>
            </div>
            <div className="qm-action-row">
              {importValidationDialog.action === 'company' && (
                <button type="button" className="qm-primary" onClick={goToImportCompany}>
                  Go to company selection
                </button>
              )}
              <button type="button" className="qm-secondary" onClick={() => setImportValidationDialog(null)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default InquiryManager;
