import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import { releaseNumberWheelFocus } from '../../utils/numberInput';
import CompanySelectWithCreate from './CompanySelectWithCreate';
import QuotationErrorNotice from './QuotationErrorNotice';

let clientRowSequence = 0;

const nextClientRowId = (prefix) => `${prefix}-${++clientRowSequence}`;

const ensureClientRowId = (line, prefix = 'row') => ({
  ...line,
  _client_row_id: line?._client_row_id || nextClientRowId(prefix),
});

const newLine = () => ({
  _client_row_id: nextClientRowId('manual'),
  raw_name: '',
  quantity: '1',
  unit: '',
  notes: '',
  matched_product: '',
  match_status: 'unresolved',
});

const newImportLine = () => ({
  _client_row_id: nextClientRowId('import'),
  raw_name: '',
  quantity: '',
  unit: '',
  unit_price: '',
  vat_rate: '0',
  vat_amount: '',
  line_total: '',
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

export const inquiryUploadModeForFile = (file) => {
  const filename = String(file?.name || '').toLowerCase();
  const mimeType = String(file?.type || '').toLowerCase();
  if (/\.(xlsx|xlsb|xls)$/.test(filename) || mimeType.includes('spreadsheet') || mimeType.includes('excel')) return 'excel';
  if (filename.endsWith('.pdf') || mimeType === 'application/pdf') return 'pdf';
  if (/\.(png|jpe?g|webp)$/.test(filename) || ['image/png', 'image/jpeg', 'image/webp'].includes(mimeType)) return 'image';
  return '';
};

export const INQUIRY_UPLOAD_ACCEPT = '.xlsx,.xlsb,.xls,.pdf,.png,.jpg,.jpeg,.webp,application/pdf,image/png,image/jpeg,image/webp';

export const importedLineNameEditPatch = (rawName) => ({
  raw_name: rawName,
  matched_product: null,
  match_reason: '',
  match_status: 'unresolved',
  match_confirmed_by_user: false,
});

export const resetImportedMatchesForCompanyChange = (lines) => (lines || []).map((line) => ({
  ...line,
  matched_product: null,
  match_reason: '',
  match_status: 'unresolved',
  match_confirmed_by_user: false,
}));

export const importCompanyRequestIsCurrent = (
  requestContext,
  currentCompany,
  currentGeneration,
  currentRevision = requestContext?.revision
) => (
  Boolean(requestContext)
  && String(requestContext.company || '') === String(currentCompany || '')
  && Number(requestContext.generation) === Number(currentGeneration)
  && (
    requestContext.revision === undefined
    || Number(requestContext.revision) === Number(currentRevision)
  )
);

export const moveInquiryRow = (rows, fromIndex, toIndex) => {
  const next = [...(rows || [])];
  if (
    fromIndex < 0
    || fromIndex >= next.length
    || toIndex < 0
    || toIndex >= next.length
    || fromIndex === toIndex
  ) return next;
  const [row] = next.splice(fromIndex, 1);
  next.splice(toIndex, 0, row);
  return next;
};

export const insertInquiryRow = (rows, index, row) => {
  const next = [...(rows || [])];
  const safeIndex = Math.max(0, Math.min(Number(index) || 0, next.length));
  next.splice(safeIndex, 0, row);
  return next;
};

export const aiCandidateWouldLoseReviewedRows = (currentPreview, candidate) => {
  const originalLineCount = currentPreview?.lines?.length || 0;
  const candidateLineCount = candidate?.lines?.length || 0;
  const isStructuredExcel = (
    currentPreview?.source_type === 'excel'
    || /(?:openpyxl|calamine)_structured/i.test(String(currentPreview?.parse_method || ''))
  );
  return (
    originalLineCount > 0
    && (
      candidateLineCount === 0
      || (candidateLineCount < originalLineCount && isStructuredExcel)
    )
  );
};

export const importedInquiryLinePayload = (line) => ({
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
  match_confirmed_by_user: Boolean(line.match_confirmed_by_user),
});

const shouldShowMatchReason = (reason) => {
  const text = String(reason || '').trim();
  return text && !/no safe deterministic match found/i.test(text);
};

const contactOptionLabel = (contact) => {
  const details = [contact.role, contact.department].filter(Boolean).join(', ');
  return details ? `${contact.name} - ${details}` : contact.name;
};

const importLineKey = (line, index) => [
  line.id || '',
  line.sheet_name || '',
  line.row_number || '',
  line.raw_line || line.raw_source_line || '',
  line.raw_name || line.item_name || '',
  index,
].join('::');

const preserveParsedField = (currentLine, responseLine, field) => (
  Object.prototype.hasOwnProperty.call(currentLine, field)
    ? currentLine[field] ?? ''
    : responseLine[field] ?? ''
);

const useReferenceWhenBlank = (currentLine, responseLine, field) => {
  const currentValue = preserveParsedField(currentLine, responseLine, field);
  return String(currentValue || '').trim() ? currentValue : responseLine[field] ?? '';
};

const mergePriceReferenceLines = (currentLines, responseLines) => {
  const currentByKey = new Map(
    (currentLines || []).map((line, index) => [importLineKey(line, index), line])
  );

  return (responseLines || []).map((line, index) => {
    const currentLine = currentByKey.get(importLineKey(line, index)) || currentLines[index] || {};
    return {
      ...line,
      _client_row_id: currentLine._client_row_id || line._client_row_id,
      quantity: preserveParsedField(currentLine, line, 'quantity'),
      unit: useReferenceWhenBlank(currentLine, line, 'unit'),
    };
  });
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
  const [loading, setLoading] = useState(false);
  const [companiesLoading, setCompaniesLoading] = useState(true);
  const [contactLoadingCompanyId, setContactLoadingCompanyId] = useState('');
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [showInquiryHistory, setShowInquiryHistory] = useState(false);
  const [showManualEntry, setShowManualEntry] = useState(false);
  const [saving, setSaving] = useState(false);
  const [creatingQuoteId, setCreatingQuoteId] = useState(null);
  const [quoteSuccess, setQuoteSuccess] = useState(null);
  const [importMode, setImportMode] = useState('paste');
  const [importForm, setImportForm] = useState(emptyImportForm);
  const [importFile, setImportFile] = useState(null);
  const [importDragActive, setImportDragActive] = useState(false);
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
  const [expandedRawRowIds, setExpandedRawRowIds] = useState({});
  const [selectedImportRowIds, setSelectedImportRowIds] = useState([]);
  const [draggedImportRowId, setDraggedImportRowId] = useState(null);
  const [aiCleaning, setAiCleaning] = useState(false);
  const [aiUndoPreview, setAiUndoPreview] = useState(null);
  const [errorInfo, setErrorInfo] = useState(null);
  const [showImportContactForm, setShowImportContactForm] = useState(false);
  const [importContactForm, setImportContactForm] = useState(emptyContactForm);
  const [importContactSaving, setImportContactSaving] = useState(false);
  const [showManualContactForm, setShowManualContactForm] = useState(false);
  const [manualContactForm, setManualContactForm] = useState(emptyContactForm);
  const [manualNotice, setManualNotice] = useState(null);
  const [manualContactSaving, setManualContactSaving] = useState(false);
  const importPanelRef = useRef(null);
  const importCompanyRef = useRef(String(importForm.company || ''));
  const importCompanyGenerationRef = useRef(0);
  const importRevisionRef = useRef(0);
  const importOperationLockRef = useRef(false);
  const companyRequestSequenceRef = useRef(0);
  const loadedContactCompaniesRef = useRef(new Set());

  const captureImportCompanyRequest = () => ({
    company: importCompanyRef.current,
    generation: importCompanyGenerationRef.current,
    revision: importRevisionRef.current,
  });
  const isCurrentImportCompanyRequest = (requestContext) => importCompanyRequestIsCurrent(
    requestContext,
    importCompanyRef.current,
    importCompanyGenerationRef.current,
    importRevisionRef.current
  );
  const importWorkflowBusy = importParsing || aiCleaning || priceReferenceApplying || importSaving || importContactSaving;
  const importSourceMode = importMode === 'paste' ? 'paste' : 'upload';
  const detectedImportType = {
    excel: 'Excel',
    pdf: 'PDF',
    image: 'Image',
  }[importMode] || '';
  const activeImportSourceType = importMode === 'paste'
    ? 'pasted_text'
    : (['excel', 'pdf', 'image'].includes(importMode) ? importMode : 'manual');
  const acquireImportOperation = () => {
    if (importOperationLockRef.current) return false;
    importOperationLockRef.current = true;
    return true;
  };
  const releaseImportOperation = () => {
    importOperationLockRef.current = false;
  };
  const importOperationIsLocked = () => importWorkflowBusy || importOperationLockRef.current;

  const invalidateParsedImport = () => {
    importRevisionRef.current += 1;
    setImportPreview(null);
    setSavedImportedInquiry(null);
    setQuoteSuccess(null);
    setAiUndoPreview(null);
    setImportNotice(null);
    setImportActionNotice(null);
    setSelectedImportRowIds([]);
    setExpandedRawRowIds({});
  };

  const loadCompanies = useCallback(async (search = '') => {
    const requestSequence = ++companyRequestSequenceRef.current;
    setCompaniesLoading(true);
    try {
      const response = await quotationAPI.companies.list({
        active: 'true',
        limit: 100,
        ...(search.trim() ? { search: search.trim() } : {}),
      });
      setCompanies((current) => {
        const byId = new Map(current.map((company) => [String(company.id), company]));
        (response.data || []).forEach((company) => byId.set(String(company.id), company));
        return [...byId.values()].sort((a, b) => a.name.localeCompare(b.name));
      });
    } catch (error) {
      if (requestSequence !== companyRequestSequenceRef.current) return;
      const details = await describeQuotationError(
        error,
        'Load companies',
        'GET /quotations/companies/'
      );
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      if (requestSequence === companyRequestSequenceRef.current) setCompaniesLoading(false);
    }
  }, []);

  const loadItems = useCallback(async () => {
    try {
      const response = await quotationAPI.items.list({ active: 'true' });
      setItems(response.data || []);
    } catch (error) {
      const details = await describeQuotationError(error, 'Load quotation products', 'GET /quotations/items/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    }
  }, []);

  const loadInquiries = useCallback(async () => {
    setLoading(true);
    try {
      const response = await quotationAPI.inquiries.list({ limit: 100 });
      setInquiries(response.data || []);
      setHistoryLoaded(true);
    } catch (error) {
      const details = await describeQuotationError(error, 'Load inquiry history', 'GET /quotations/inquiries/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadContactsForCompany = useCallback(async (companyId) => {
    const normalizedCompanyId = String(companyId || '');
    if (!normalizedCompanyId || loadedContactCompaniesRef.current.has(normalizedCompanyId)) return;
    loadedContactCompaniesRef.current.add(normalizedCompanyId);
    setContactLoadingCompanyId(normalizedCompanyId);
    try {
      const response = await quotationAPI.contacts.list({ active: 'true', company: normalizedCompanyId });
      setContacts((current) => {
        const byId = new Map(current.map((contact) => [String(contact.id), contact]));
        (response.data || []).forEach((contact) => byId.set(String(contact.id), contact));
        return [...byId.values()].sort((a, b) => a.name.localeCompare(b.name));
      });
    } catch (error) {
      loadedContactCompaniesRef.current.delete(normalizedCompanyId);
      const details = await describeQuotationError(error, 'Load company contacts', 'GET /quotations/contacts/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setContactLoadingCompanyId((current) => current === normalizedCompanyId ? '' : current);
    }
  }, []);

  useEffect(() => {
    loadCompanies();
    loadItems();
  }, [loadCompanies, loadItems]);

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
      const response = await quotationAPI.inquiries.create(payload);
      setForm({ company: '', contact: '', subject: '', original_text: '', lines: [newLine()] });
      setManualNotice({ type: 'success', message: 'Inquiry saved. Creating and opening its quotation…' });
      const quoteId = await createQuote(response.data, { openAfterCreate: true });
      if (!quoteId) {
        if (historyLoaded) loadInquiries();
        setManualNotice({
          type: 'warning',
          message: 'The inquiry was saved, but its quotation could not be opened. Use Inquiry History to retry safely.',
        });
      }
    } catch (error) {
      const details = await describeQuotationError(error, 'Create inquiry', 'POST /quotations/inquiries/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const openCreatedQuote = (quoteId) => {
    if (quoteId && onOpenQuote) onOpenQuote(quoteId);
  };

  async function createQuote(inquiry, { openAfterCreate = false } = {}) {
    if (creatingQuoteId) return null;
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
      if (openAfterCreate) openCreatedQuote(inquiry.quotation_id);
      return inquiry.quotation_id;
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
      if (historyLoaded) loadInquiries();
      if (openAfterCreate) openCreatedQuote(response.data.id);
      return response.data.id;
    } catch (error) {
      const details = await describeQuotationError(error, 'Create quote from inquiry', `POST /quotations/inquiries/${inquiry.id}/create_quote/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
      return null;
    } finally {
      setCreatingQuoteId(null);
    }
  }

  const aiSourceLabel = (source) => {
    if (source === 'ai_vision_cleanup') return 'AI vision cleanup used';
    if (source === 'ai_text_cleanup') return 'AI text cleanup used';
    if (source === 'ai_failed_using_original_parse') return 'AI failed, using original parse';
    return 'Deterministic parse';
  };

  const editablePreview = (preview, { aiApplied = false } = {}) => ({
    ...preview,
    ai_candidate: null,
    result_source: preview.result_source || 'deterministic_parse',
    lines: (preview.lines || []).map((line) => ensureClientRowId({
      ...newImportLine(),
      ...line,
      raw_line: line.raw_line || line.raw_source_line || '',
      parse_status: aiApplied ? (line.parse_status || 'needs_review') : line.parse_status,
      parse_confidence: Number(line.parse_confidence || 0),
    }, 'import')),
  });

  const aiCandidateWouldLoseStructuredRows = (currentPreview, candidate) => {
    return aiCandidateWouldLoseReviewedRows(currentPreview, candidate);
  };

  const applyAiResult = (currentPreview, candidate) => {
    if (!candidate) return false;
    if (aiCandidateWouldLoseStructuredRows(currentPreview, candidate)) {
      setImportNotice({
        type: 'warning',
        message: `AI returned ${candidate.lines?.length || 0} rows, but the current parser found ${currentPreview.lines.length}. Kept the original rows so no items are lost.`,
      });
      return false;
    }
    importRevisionRef.current += 1;
    setAiUndoPreview(currentPreview);
    setSavedImportedInquiry(null);
    setExpandedRawRowIds({});
    setSelectedImportRowIds([]);
    setImportPreview(editablePreview({
      ...currentPreview,
      ...candidate,
      result_source: candidate.result_source,
      ai_status: candidate.ai_status,
      ai_status_label: candidate.ai_status_label,
    }, { aiApplied: true }));
    setImportNotice({
      type: 'success',
      message: `${aiSourceLabel(candidate.result_source)} and applied automatically. Review the rows below or undo the cleanup.`,
    });
    return true;
  };

  const setPreview = (preview) => {
    importRevisionRef.current += 1;
    setSavedImportedInquiry(null);
    setImportActionNotice(null);
    setExpandedRawRowIds({});
    setSelectedImportRowIds([]);
    setAiUndoPreview(null);
    const candidate = preview.ai_candidate || null;
    const deterministicPreview = editablePreview(preview);
    setImportPreview(deterministicPreview);
    if (candidate) {
      applyAiResult(deterministicPreview, candidate);
    } else {
      setImportNotice(null);
    }
  };

  const runAiCleanParse = async () => {
    if (!importPreview || importWorkflowBusy || !acquireImportOperation()) return;
    const requestContext = captureImportCompanyRequest();
    setAiCleaning(true);
    setErrorInfo(null);
    setImportNotice(null);
    try {
      const response = await quotationAPI.inquiries.aiCleanParse({
        preview: importPreview,
        company: importForm.company || null,
        mode: 'auto',
      });
      if (!isCurrentImportCompanyRequest(requestContext)) return;
      applyAiResult(importPreview, response.data);
    } catch (error) {
      if (!isCurrentImportCompanyRequest(requestContext)) return;
      const details = await describeQuotationError(error, 'AI clean inquiry parse', 'POST /quotations/inquiries/ai_clean_parse/');
      setErrorInfo(details);
      setImportNotice({ type: 'warning', message: 'AI failed, using original parse.' });
      console.error(formatQuotationError(details), error);
    } finally {
      setAiCleaning(false);
      releaseImportOperation();
    }
  };

  const undoAiCleanup = () => {
    if (!aiUndoPreview) return;
    importRevisionRef.current += 1;
    setImportPreview(aiUndoPreview);
    setAiUndoPreview(null);
    setExpandedRawRowIds({});
    setSelectedImportRowIds([]);
    setImportNotice({ type: 'success', message: 'Restored the rows from before AI cleanup.' });
  };

  const parsePastedText = async () => {
    if (importWorkflowBusy || !acquireImportOperation()) return;
    const requestContext = captureImportCompanyRequest();
    setImportParsing(true);
    setErrorInfo(null);
    setImportNotice(null);
    try {
      const response = await quotationAPI.inquiries.parseText({
        raw_text: importForm.raw_text,
        raw_html: importForm.raw_html || '',
        company: importForm.company || null,
      });
      if (!isCurrentImportCompanyRequest(requestContext)) return;
      setPreview(response.data);
    } catch (error) {
      if (!isCurrentImportCompanyRequest(requestContext)) return;
      const details = await describeQuotationError(error, 'Parse pasted inquiry text', 'POST /quotations/inquiries/parse_text/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setImportParsing(false);
      releaseImportOperation();
    }
  };

  const changeImportMode = (mode) => {
    const nextSourceMode = mode === 'paste' ? 'paste' : 'upload';
    if (importOperationIsLocked() || nextSourceMode === importSourceMode) return;
    invalidateParsedImport();
    setImportMode(nextSourceMode);
    setImportFile(null);
    setImportDragActive(false);
  };

  const handleImportFile = (file) => {
    if (importOperationIsLocked()) return;
    if (!file) {
      setImportFile(null);
      return;
    }
    const detectedMode = inquiryUploadModeForFile(file);
    if (!detectedMode) {
      invalidateParsedImport();
      setImportFile(null);
      setImportNotice({
        type: 'error',
        message: 'Use an Excel, PDF, PNG, JPEG, or WebP inquiry file.',
      });
      return;
    }
    invalidateParsedImport();
    setImportMode(detectedMode);
    setImportFile(file);
  };

  const parseUploadedFile = async () => {
    if (importWorkflowBusy) return;
    if (!importFile) {
      setImportNotice({ type: 'error', message: 'Choose or drop an Excel, PDF, or image file before parsing.' });
      return;
    }
    if (!acquireImportOperation()) return;
    const requestContext = captureImportCompanyRequest();
    setImportParsing(true);
    setErrorInfo(null);
    setImportNotice(null);
    try {
      const formData = new FormData();
      formData.append('file', importFile);
      if (importForm.company) formData.append('company', importForm.company);
      const response = await quotationAPI.inquiries.parseFile(formData);
      if (!isCurrentImportCompanyRequest(requestContext)) return;
      setPreview(response.data);
    } catch (error) {
      if (!isCurrentImportCompanyRequest(requestContext)) return;
      const details = await describeQuotationError(error, 'Parse inquiry file', 'POST /quotations/inquiries/parse_file/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setImportParsing(false);
      releaseImportOperation();
    }
  };

  const applyPriceReference = async () => {
    if (importWorkflowBusy) return;
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
    if (!acquireImportOperation()) return;
    const requestContext = captureImportCompanyRequest();
    setPriceReferenceApplying(true);
    setErrorInfo(null);
    setImportNotice(null);
    try {
      const formData = new FormData();
      if (priceReferenceMode === 'file') {
        formData.append('file', priceReferenceFile);
      } else {
        formData.append('raw_text', priceReferenceText);
        formData.append('raw_html', priceReferenceHtml);
      }
      formData.append('use_ai', priceReferenceUseAi ? 'true' : 'false');
      formData.append('preview', JSON.stringify(importPreview));
      const response = await quotationAPI.inquiries.applyPriceReference(formData);
      if (!isCurrentImportCompanyRequest(requestContext)) return;
      const currentLines = importPreview.lines || [];
      const responseLines = response.data.lines || [];
      setPreview({
        ...response.data,
        result_source: response.data.result_source || importPreview.result_source || 'deterministic_parse',
        lines: mergePriceReferenceLines(currentLines, responseLines),
      });
      const summary = response.data.price_reference_summary || {};
      setImportNotice({
        type: 'success',
        message: `Price reference applied. ${summary.matched_count || 0} prices filled, ${summary.needs_review_count || 0} likely matches need review, ${summary.unmatched_count || 0} unmatched.`,
      });
    } catch (error) {
      if (!isCurrentImportCompanyRequest(requestContext)) return;
      const details = await describeQuotationError(error, 'Apply inquiry price reference', 'POST /quotations/inquiries/apply_price_reference/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setPriceReferenceApplying(false);
      releaseImportOperation();
    }
  };

  const updateImportLine = (index, patch) => {
    if (importOperationIsLocked()) return;
    importRevisionRef.current += 1;
    setSavedImportedInquiry(null);
    setImportActionNotice(null);
    setImportPreview((current) => ({
      ...current,
      lines: current.lines.map((line, lineIndex) => lineIndex === index ? { ...line, ...patch } : line),
    }));
  };

  const removeImportLine = (rowId) => {
    if (importOperationIsLocked()) return;
    importRevisionRef.current += 1;
    setSavedImportedInquiry(null);
    setImportActionNotice(null);
    setSelectedImportRowIds((current) => current.filter((candidateId) => candidateId !== rowId));
    setExpandedRawRowIds((current) => {
      const next = { ...current };
      delete next[rowId];
      return next;
    });
    setImportPreview((current) => ({
      ...current,
      lines: current.lines.filter((line) => line._client_row_id !== rowId),
    }));
  };

  const insertManualLine = (index) => {
    setForm((current) => ({
      ...current,
      lines: insertInquiryRow(current.lines, index, newLine()),
    }));
  };

  const moveManualLine = (fromIndex, toIndex) => {
    setForm((current) => ({
      ...current,
      lines: moveInquiryRow(current.lines, fromIndex, toIndex),
    }));
  };

  const toggleImportRowSelection = (rowId) => {
    setSelectedImportRowIds((current) => (
      current.includes(rowId)
        ? current.filter((candidateId) => candidateId !== rowId)
        : [...current, rowId]
    ));
  };

  const toggleAllImportRows = () => {
    const allRowIds = (importPreview?.lines || []).map((line) => line._client_row_id);
    setSelectedImportRowIds((current) => current.length === allRowIds.length ? [] : allRowIds);
  };

  const removeSelectedImportRows = () => {
    if (importOperationIsLocked() || !selectedImportRowIds.length) return;
    importRevisionRef.current += 1;
    setSavedImportedInquiry(null);
    setImportActionNotice(null);
    setImportPreview((current) => ({
      ...current,
      lines: current.lines.filter((line) => !selectedImportRowIds.includes(line._client_row_id)),
    }));
    setSelectedImportRowIds([]);
  };

  const insertImportLine = (index) => {
    if (importOperationIsLocked()) return;
    importRevisionRef.current += 1;
    setSavedImportedInquiry(null);
    setImportActionNotice(null);
    setImportPreview((current) => ({
      ...(current || {
        source_type: activeImportSourceType,
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
      lines: insertInquiryRow(current?.lines || [], index, newImportLine()),
    }));
  };

  const addImportLine = () => insertImportLine(importPreview?.lines?.length || 0);

  const moveImportLine = (fromIndex, toIndex) => {
    if (importOperationIsLocked() || fromIndex === toIndex || toIndex < 0 || toIndex >= (importPreview?.lines?.length || 0)) return;
    importRevisionRef.current += 1;
    setSavedImportedInquiry(null);
    setImportActionNotice(null);
    setImportPreview((current) => ({
      ...current,
      lines: moveInquiryRow(current.lines, fromIndex, toIndex),
    }));
  };

  const dropImportLine = (targetIndex) => {
    if (!draggedImportRowId) return;
    const fromIndex = (importPreview?.lines || []).findIndex((line) => line._client_row_id === draggedImportRowId);
    moveImportLine(fromIndex, targetIndex);
    setDraggedImportRowId(null);
  };

  const toggleRawRow = (rowId) => {
    setExpandedRawRowIds((current) => ({ ...current, [rowId]: !current[rowId] }));
  };

  const saveImportedInquiry = async () => {
    if (importWorkflowBusy) return;
    const requestContext = captureImportCompanyRequest();
    const lines = (importPreview?.lines || [])
      .filter((line) => line.raw_name.trim())
      .map(importedInquiryLinePayload);
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
    if (!acquireImportOperation()) return;
    setImportSaving(true);
    setErrorInfo(null);
    setImportNotice(null);
    try {
      const response = await quotationAPI.inquiries.createImported({
        company: importForm.company,
        contact: importForm.contact || null,
        subject: importForm.subject,
        original_text: importPreview?.original_text || importForm.raw_text,
        source_type: importPreview?.source_type || activeImportSourceType,
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
      if (!isCurrentImportCompanyRequest(requestContext)) {
        return;
      }
      setSavedImportedInquiry(response.data);
      setImportNotice({ type: 'success', message: 'Imported inquiry saved. Creating and opening its quotation…' });
      setImportActionNotice({ type: 'success', message: 'Inquiry saved. Preparing the quotation for line editing.' });
      const quoteId = await createQuote(response.data, { openAfterCreate: true });
      if (!quoteId && isCurrentImportCompanyRequest(requestContext)) {
        if (historyLoaded) loadInquiries();
        setImportActionNotice({
          type: 'warning',
          message: 'The inquiry is safely saved, but the quotation could not be opened. Retry below; this will not create a duplicate quotation.',
        });
      }
    } catch (error) {
      if (!isCurrentImportCompanyRequest(requestContext)) return;
      const details = await describeQuotationError(error, 'Save imported inquiry', 'POST /quotations/inquiries/create_imported/');
      if (!isCurrentImportCompanyRequest(requestContext)) return;
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setImportSaving(false);
      releaseImportOperation();
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
            <p>Paste text or upload Excel, PDF, or image files, review the lines, then save and open the quotation. Source files stay private.</p>
          </div>
          <div className="qm-mode-tabs">
            <button type="button" disabled={importWorkflowBusy} className={importSourceMode === 'paste' ? 'active' : ''} onClick={() => changeImportMode('paste')}>Paste Text</button>
            <button type="button" disabled={importWorkflowBusy} className={importSourceMode === 'upload' ? 'active' : ''} onClick={() => changeImportMode('upload')}>Upload File</button>
          </div>
        </div>

        <div className="qm-inquiry-party-grid">
          <CompanySelectWithCreate
            companies={companies}
            value={importForm.company}
            required
            loading={companiesLoading}
            onSearch={loadCompanies}
            disabled={importParsing || aiCleaning || priceReferenceApplying || importSaving || importContactSaving}
            onChange={(companyId) => {
              const normalizedCompanyId = String(companyId || '');
              const companyChanged = normalizedCompanyId !== importCompanyRef.current;
              importCompanyRef.current = normalizedCompanyId;
              setImportForm((current) => ({ ...current, company: companyId, contact: '' }));
              if (companyChanged) {
                importCompanyGenerationRef.current += 1;
                setImportPreview((current) => (
                  current
                    ? { ...current, lines: resetImportedMatchesForCompanyChange(current.lines) }
                    : current
                ));
                setAiUndoPreview(null);
                setSavedImportedInquiry(null);
                setQuoteSuccess(null);
                setImportNotice(null);
                setImportActionNotice(null);
              }
              loadContactsForCompany(companyId);
              setImportContactForm(emptyContactForm);
              setShowImportContactForm(false);
            }}
            onCreated={rememberCompany}
          />
          <div className="qm-contact-control">
            <label>
              <span className="qm-label-text">Contact / Purchaser</span>
              <select disabled={!importForm.company || importWorkflowBusy} value={importForm.contact} onChange={(event) => setImportForm({ ...importForm, contact: event.target.value })}>
                <option value="">{contactLoadingCompanyId === String(importForm.company) ? 'Loading contacts…' : 'No contact'}</option>
                {filteredImportContacts.map((contact) => <option key={contact.id} value={contact.id}>{contactOptionLabel(contact)}</option>)}
              </select>
            </label>
            <button type="button" className="qm-secondary small" disabled={!importForm.company || importWorkflowBusy} onClick={() => setShowImportContactForm((value) => !value)}>
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
          <input className="qm-input" disabled={importWorkflowBusy} placeholder="Inquiry subject or LPO reference" value={importForm.subject} onChange={(event) => setImportForm({ ...importForm, subject: event.target.value })} />
        </label>

        {importSourceMode === 'paste' ? (
          <div className="qm-import-source">
            <label><span className="qm-label-text">Paste inquiry text</span>
              <textarea
                rows="5"
                disabled={importWorkflowBusy}
                value={importForm.raw_text}
                onPaste={(event) => {
                  const html = event.clipboardData?.getData('text/html') || '';
                  setImportForm((current) => ({ ...current, raw_html: html.includes('<table') ? html : current.raw_html }));
                }}
                onChange={(event) => {
                  if (importOperationIsLocked()) return;
                  if (event.target.value !== importForm.raw_text) invalidateParsedImport();
                  setImportForm({ ...importForm, raw_text: event.target.value, raw_html: event.target.value ? importForm.raw_html : '' });
                }}
                placeholder="Paste the customer's requested items here..."
              />
            </label>
            <button type="button" className="qm-primary" disabled={importWorkflowBusy || !importForm.raw_text.trim()} onClick={parsePastedText}>
              {importParsing ? 'Extracting...' : 'Extract Lines'}
            </button>
          </div>
        ) : (
          <div className="qm-import-source">
            <div
              className={`qm-file-dropzone${importDragActive ? ' drag-active' : ''}${importWorkflowBusy ? ' disabled' : ''}`}
              onDragEnter={(event) => {
                event.preventDefault();
                if (importWorkflowBusy) return;
                setImportDragActive(true);
              }}
              onDragOver={(event) => {
                event.preventDefault();
                if (importWorkflowBusy) return;
                event.dataTransfer.dropEffect = 'copy';
                setImportDragActive(true);
              }}
              onDragLeave={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget)) setImportDragActive(false);
              }}
              onDrop={(event) => {
                event.preventDefault();
                setImportDragActive(false);
                handleImportFile(event.dataTransfer.files?.[0] || null);
              }}
            >
              <label>
                <span className="qm-label-text">Inquiry file</span>
                <strong>{importFile ? importFile.name : 'Drag a file here, or choose from your computer'}</strong>
                <small>Supported: XLSX, XLSB, XLS, PDF, PNG, JPEG, and WebP.</small>
                {importFile && detectedImportType && <small>Detected: {detectedImportType}</small>}
                <input
                  aria-label="Inquiry file"
                  type="file"
                  disabled={importWorkflowBusy}
                  accept={INQUIRY_UPLOAD_ACCEPT}
                  onChange={(event) => {
                    handleImportFile(event.target.files?.[0] || null);
                    event.target.value = '';
                  }}
                />
              </label>
            </div>
            <button type="button" className="qm-primary" disabled={importWorkflowBusy || !importFile} onClick={parseUploadedFile}>
              {importParsing ? 'Parsing…' : 'Parse File'}
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
                  disabled={importWorkflowBusy}
                  className={priceReferenceMode === 'file' ? 'active' : ''}
                  onClick={() => setPriceReferenceMode('file')}
                >
                  Upload file
                </button>
                <button
                  type="button"
                  disabled={importWorkflowBusy}
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
                    disabled={importWorkflowBusy}
                    accept=".xlsx,.xls,.xlsb,.pdf"
                    onChange={(event) => setPriceReferenceFile(event.target.files?.[0] || null)}
                  />
                </label>
              ) : (
                <label>
                  <span className="qm-label-text">Pasted price reference</span>
                  <textarea
                    rows="4"
                    disabled={importWorkflowBusy}
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
                  disabled={importWorkflowBusy}
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
                  importWorkflowBusy ||
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
          <fieldset disabled={importWorkflowBusy} className="qm-import-preview qm-import-preview-fieldset">
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
              <span>Selected: {selectedImportRowIds.length}</span>
              {(importPreview.meta?.selected_sheets || []).map((sheet) => (
                <span key={sheet.sheet_name}>{sheet.sheet_name}: header row {sheet.header_row}</span>
              ))}
              {importPreview.source_file_ref && <span>Private source saved</span>}
            </div>
            <div className="qm-bulk-toolbar compact">
              <strong>{selectedImportRowIds.length} rows selected</strong>
              <button type="button" className="qm-secondary small" disabled={aiCleaning || (!importPreview.lines.length && !importPreview.source_file_ref)} onClick={runAiCleanParse}>
                {aiCleaning ? 'Cleaning & applying...' : 'AI Clean & Apply'}
              </button>
              {aiUndoPreview && (
                <button type="button" className="qm-secondary small" onClick={undoAiCleanup}>Undo AI cleanup</button>
              )}
              <button type="button" className="qm-secondary small" disabled={!importPreview.lines.length} onClick={toggleAllImportRows}>
                {selectedImportRowIds.length === importPreview.lines.length ? 'Deselect All' : 'Select All'}
              </button>
              <button type="button" className="qm-secondary small danger" disabled={!selectedImportRowIds.length} onClick={removeSelectedImportRows}>Delete Selected</button>
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
                    <th className="qm-check-cell"><input type="checkbox" checked={importPreview.lines.length > 0 && selectedImportRowIds.length === importPreview.lines.length} onChange={toggleAllImportRows} /></th>
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
                    <React.Fragment key={line._client_row_id}>
                      <tr
                        className={draggedImportRowId === line._client_row_id ? 'qm-row-dragging' : ''}
                        onDragOver={(event) => {
                          if (!draggedImportRowId) return;
                          event.preventDefault();
                          event.dataTransfer.dropEffect = 'move';
                        }}
                        onDrop={(event) => {
                          event.preventDefault();
                          dropImportLine(index);
                        }}
                      >
                        <td className="qm-check-cell"><input type="checkbox" checked={selectedImportRowIds.includes(line._client_row_id)} onChange={() => toggleImportRowSelection(line._client_row_id)} /></td>
                        <td className="qm-serial-cell">
                          <div className="qm-row-order-cell">
                            <button
                              type="button"
                              className="qm-row-drag-handle"
                              draggable={!importWorkflowBusy}
                              aria-label={`Drag row ${index + 1}`}
                              title="Drag this row to a new position"
                              onDragStart={(event) => {
                                if (importOperationIsLocked()) return;
                                setDraggedImportRowId(line._client_row_id);
                                event.dataTransfer.effectAllowed = 'move';
                                event.dataTransfer.setData('text/plain', line._client_row_id);
                              }}
                              onDragEnd={() => setDraggedImportRowId(null)}
                            >
                              ⋮⋮
                            </button>
                            <span>{index + 1}</span>
                          </div>
                        </td>
                        <td className="qm-import-item-cell"><input aria-label={`Requested item name row ${index + 1}`} value={line.raw_name} onChange={(event) => updateImportLine(index, importedLineNameEditPatch(event.target.value))} /></td>
                        <td className="qm-import-match-cell">
                          <select aria-label={`Matched product row ${index + 1}`} value={line.matched_product || ''} onChange={(event) => updateImportLine(index, {
                            matched_product: event.target.value || null,
                            match_status: event.target.value ? 'confirmed' : 'unresolved',
                            match_confirmed_by_user: Boolean(event.target.value),
                          })}>
                            <option value="">Unmatched</option>
                            {items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                          </select>
                          {shouldShowMatchReason(line.match_reason) && <small className="qm-muted-text">{line.match_reason}</small>}
                        </td>
                        <td className="qm-import-qty-cell"><input aria-label={`Quantity row ${index + 1}`} type="number" min="0" step="0.001" value={line.quantity || ''} onWheel={releaseNumberWheelFocus} onChange={(event) => updateImportLine(index, { quantity: event.target.value })} /></td>
                        <td className="qm-import-unit-cell"><input value={line.unit || ''} onChange={(event) => updateImportLine(index, { unit: event.target.value })} /></td>
                        <td className="qm-import-price-cell">
                          <input aria-label={`Unit price row ${index + 1}`} type="number" min="0" step="0.001" value={line.unit_price || ''} onWheel={releaseNumberWheelFocus} onChange={(event) => updateImportLine(index, { unit_price: event.target.value })} />
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
                          <button type="button" className="qm-secondary small" onClick={() => insertImportLine(index)}>+ Above</button>
                          <button type="button" className="qm-secondary small" disabled={index === 0} onClick={() => moveImportLine(index, index - 1)} aria-label={`Move row ${index + 1} up`}>↑</button>
                          <button type="button" className="qm-secondary small" disabled={index === importPreview.lines.length - 1} onClick={() => moveImportLine(index, index + 1)} aria-label={`Move row ${index + 1} down`}>↓</button>
                          <button type="button" className="qm-secondary small" onClick={() => toggleRawRow(line._client_row_id)}>
                            {expandedRawRowIds[line._client_row_id] ? 'Hide Raw' : 'View Raw'}
                          </button>
                          <button type="button" className="qm-secondary small danger" onClick={() => removeImportLine(line._client_row_id)}>Delete</button>
                        </td>
                      </tr>
                      {expandedRawRowIds[line._client_row_id] && (
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
                {importSaving ? 'Saving & opening…' : savedImportedInquiry ? 'Inquiry Saved' : 'Save & Open Quotation'}
              </button>
              {savedImportedInquiry && !savedQuoteForCurrentImport && (
                <button type="button" className="qm-secondary" disabled={Boolean(creatingQuoteId)} onClick={() => createQuote(savedImportedInquiry, { openAfterCreate: true })}>
                  {creatingQuoteId === savedImportedInquiry.id ? 'Creating…' : 'Retry Create & Open Quotation'}
                </button>
              )}
              {savedQuoteForCurrentImport && (
                <button type="button" className="qm-primary" onClick={() => openCreatedQuote(savedQuoteForCurrentImport.quoteId)}>
                  Open Quotation
                </button>
              )}
            </div>
          </fieldset>
        )}
      </div>

      <div className="qm-inquiry-secondary-launchers">
        <button
          type="button"
          className="qm-disclosure-card"
          aria-expanded={showManualEntry}
          onClick={() => setShowManualEntry((current) => !current)}
        >
          <span>
            <strong>Manual inquiry entry</strong>
            <small>For short requests that do not need file parsing.</small>
          </span>
          <b>{showManualEntry ? 'Hide' : 'Open'}</b>
        </button>
        <button
          type="button"
          className="qm-disclosure-card"
          aria-expanded={showInquiryHistory}
          onClick={() => {
            const next = !showInquiryHistory;
            setShowInquiryHistory(next);
            if (next && !historyLoaded && !loading) loadInquiries();
          }}
        >
          <span>
            <strong>Inquiry history</strong>
            <small>View recent saved inquiries or reopen their quotations.</small>
          </span>
          <b>{showInquiryHistory ? 'Hide' : 'Open'}</b>
        </button>
      </div>

      {(showInquiryHistory || showManualEntry) && (
      <div className={`qm-split wide-left${showInquiryHistory !== showManualEntry ? ' single-panel' : ''}`}>
        {showInquiryHistory && (
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
                            createQuote(inquiry, { openAfterCreate: true });
                          }}
                        >
                          {creatingQuoteId === inquiry.id ? 'Creating…' : 'Create & Open Quotation'}
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
        )}

        {showManualEntry && (
        <div className="qm-panel qm-manual-fallback">
        <div className="qm-workflow-step">
          <span>Step 1</span>
          <div>
            <h3>Manual Inquiry Entry</h3>
            <p>Use this when the customer request is short or the file cannot be parsed safely.</p>
          </div>
        </div>
        <form onSubmit={saveInquiry} className="qm-form">
          <fieldset disabled={saving || manualContactSaving} className="qm-manual-inquiry-fieldset">
          <CompanySelectWithCreate
            companies={companies}
            value={form.company}
            required
            loading={companiesLoading}
            onSearch={loadCompanies}
            disabled={saving || manualContactSaving}
            onChange={(companyId) => {
              setForm({ ...form, company: companyId, contact: '' });
              loadContactsForCompany(companyId);
              setManualContactForm(emptyContactForm);
              setShowManualContactForm(false);
            }}
            onCreated={rememberCompany}
          />
          <div className="qm-contact-control">
            <label>
              <span className="qm-label-text">Contact / Purchaser</span>
              <select disabled={!form.company} value={form.contact} onChange={(event) => setForm({ ...form, contact: event.target.value })}>
                <option value="">{contactLoadingCompanyId === String(form.company) ? 'Loading contacts…' : 'No contact'}</option>
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
              <button type="button" className="qm-secondary small" onClick={() => insertManualLine(form.lines.length)}>Add Line</button>
            </div>
            {form.lines.map((line, index) => (
              <div key={line._client_row_id} className="qm-line-form">
                <input aria-label="Requested item name" placeholder="Requested item name" required value={line.raw_name} onChange={(event) => updateLine(index, { raw_name: event.target.value })} />
                <input aria-label="Qty" type="number" min="0" step="0.001" placeholder="Qty" value={line.quantity} onWheel={releaseNumberWheelFocus} onChange={(event) => updateLine(index, { quantity: event.target.value })} />
                <input aria-label="Unit" placeholder="Unit" value={line.unit} onChange={(event) => updateLine(index, { unit: event.target.value })} />
                <select aria-label="Matched product" value={line.matched_product} onChange={(event) => {
                  const matched = event.target.value;
                  updateLine(index, { matched_product: matched, match_status: matched ? 'confirmed' : 'unresolved' });
                }}>
                  <option value="">Match status: Unmatched</option>
                  {items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
                <div className="qm-line-order-actions">
                  <button type="button" className="qm-secondary small" onClick={() => insertManualLine(index)}>+ Above</button>
                  <button type="button" className="qm-secondary small" disabled={index === 0} onClick={() => moveManualLine(index, index - 1)} aria-label={`Move manual row ${index + 1} up`}>↑</button>
                  <button type="button" className="qm-secondary small" disabled={index === form.lines.length - 1} onClick={() => moveManualLine(index, index + 1)} aria-label={`Move manual row ${index + 1} down`}>↓</button>
                  <button type="button" className="qm-icon danger" onClick={() => removeLine(index)} disabled={form.lines.length === 1}>Delete</button>
                </div>
              </div>
            ))}
          </div>

          <div className="qm-workflow-step compact">
            <span>Step 3</span>
            <div>
              <h4>Create Quote</h4>
              <p>Saving creates its quotation and opens it immediately for pricing.</p>
            </div>
          </div>
          <button type="submit" className="qm-primary" disabled={saving}>{saving ? 'Saving & opening…' : 'Save & Open Quotation'}</button>
          </fieldset>
        </form>
        </div>
        )}
      </div>
      )}
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
