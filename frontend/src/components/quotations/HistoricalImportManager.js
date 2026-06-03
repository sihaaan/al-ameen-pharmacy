import React, { useEffect, useMemo, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import QuotationErrorNotice from './QuotationErrorNotice';

const STEPS = [
  { id: 'upload', label: 'Upload', title: 'Upload Historical PDFs' },
  { id: 'analyze', label: 'AI Analyze', title: 'AI Analyze Batch' },
  { id: 'companies', label: 'Companies', title: 'Confirm Companies & Documents' },
  { id: 'decisions', label: 'Product Decisions', title: 'Review Product Decisions' },
  { id: 'commit', label: 'Commit', title: 'Final Review & Commit' },
];

const ACTION_LABELS = {
  match_existing_product: 'Existing Product match',
  create_company_alias: 'Company alias',
  create_new_product: 'New draft Product',
  needs_manual_review: 'Needs manual review',
  skip: 'Skip/noise row',
  match_existing_company: 'Existing company match',
  create_new_company: 'New company',
};

const LINE_ACTIONS = [
  'match_existing_product',
  'create_company_alias',
  'create_new_product',
  'needs_manual_review',
  'skip',
];

const confidencePercent = (value) => {
  const numeric = Number(value || 0);
  if (!Number.isFinite(numeric)) return 0;
  return numeric <= 1 ? Math.round(numeric * 100) : Math.round(numeric);
};

const emptyLineDraft = {
  item_name: '',
  quantity: '',
  unit: '',
  unit_price: '',
  vat_amount: '',
  vat_rate: '',
  line_total: '',
  status: 'needs_review',
};

const duplicateHelperText = (duplicateCheck) => {
  if (!duplicateCheck?.is_duplicate) return '';
  if (duplicateCheck.blocking || duplicateCheck.blocked_new_import) return 'No duplicate import was created.';
  return 'Please review before continuing.';
};

const statValue = (summary, key, fallback = 0) => summary?.[key] ?? fallback;

const formatMoney = (value) => {
  if (value === null || value === undefined || value === '') return '-';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return value;
  return `AED ${numeric.toFixed(2)}`;
};

const normalizeComparable = (value) => {
  if (value === null || value === undefined) return '';
  return String(value);
};

const changedFields = (draft, original, keys) => (
  keys.filter((key) => normalizeComparable(draft[key]) !== normalizeComparable(original[key]))
);

const CLOSED_LINE_STATUSES = new Set(['committed', 'duplicate']);

const isClosedLineSuggestion = (suggestion) => (
  suggestion.suggestion_type === 'line' && CLOSED_LINE_STATUSES.has(suggestion.line_status)
);

const effectiveSuggestionStatus = (suggestion) => (
  isClosedLineSuggestion(suggestion) ? suggestion.line_status : suggestion.status
);

const isSuggestionActionable = (suggestion) => (
  suggestion.status === 'pending' && !isClosedLineSuggestion(suggestion)
);

const cleanBlockers = (blockers = []) => (
  Array.isArray(blockers) ? blockers.filter(Boolean) : []
);

const dateSortValue = (value) => {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
};

const duplicatePrimaryMatch = (duplicateCheck) => duplicateCheck?.primary_match || duplicateCheck?.duplicate_match || null;

const HistoricalImportManager = () => {
  const [companies, setCompanies] = useState([]);
  const [items, setItems] = useState([]);
  const [batches, setBatches] = useState([]);
  const [selectedBatch, setSelectedBatch] = useState(null);
  const [activeStep, setActiveStep] = useState('upload');
  const [batchFiles, setBatchFiles] = useState([]);
  const [batchUploading, setBatchUploading] = useState(false);
  const [batchProgress, setBatchProgress] = useState([]);
  const [selectedBatchImportIds, setSelectedBatchImportIds] = useState([]);
  const [suggestions, setSuggestions] = useState([]);
  const [selectedSuggestionIds, setSelectedSuggestionIds] = useState([]);
  const [suggestionDrafts, setSuggestionDrafts] = useState({});
  const [lineDrafts, setLineDrafts] = useState({});
  const [importDrafts, setImportDrafts] = useState({});
  const [expandedGroups, setExpandedGroups] = useState({ skip: false });
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState(null);
  const [errorInfo, setErrorInfo] = useState(null);
  const [workingAction, setWorkingAction] = useState('');
  const [decisionFilter, setDecisionFilter] = useState('all');
  const [decisionCompanyFilter, setDecisionCompanyFilter] = useState('all');
  const [decisionFileFilter, setDecisionFileFilter] = useState('all');
  const [decisionConfidenceFilter, setDecisionConfidenceFilter] = useState('all');
  const [aiRunResults, setAiRunResults] = useState([]);
  const [lastAiRunFailed, setLastAiRunFailed] = useState(false);
  const [confirmAction, setConfirmAction] = useState(null);
  const [sourceModal, setSourceModal] = useState(null);
  const [duplicateModal, setDuplicateModal] = useState(null);
  const [actionFeedback, setActionFeedback] = useState({});
  const [selectedDocumentId, setSelectedDocumentId] = useState(null);
  const [companyModeByImport, setCompanyModeByImport] = useState({});
  const [groupLimits, setGroupLimits] = useState({});

  const selectedBatchImports = useMemo(() => selectedBatch?.imports || [], [selectedBatch]);
  const visibleBatchImportIds = useMemo(() => selectedBatchImports.map((entry) => entry.id), [selectedBatchImports]);
  const allBatchImportsSelected = visibleBatchImportIds.length > 0 && visibleBatchImportIds.every((id) => selectedBatchImportIds.includes(id));
  const wizardSummary = selectedBatch?.wizard_summary || selectedBatch?.summary || {};
  const lineCounts = wizardSummary.line_counts || {};
  const pendingActionCounts = wizardSummary.pending_suggestion_action_counts || {};
  const appliedActionCounts = wizardSummary.applied_suggestion_action_counts || {};
  const commitBlockers = wizardSummary.commit_blockers || [];
  const selectedDocument = selectedBatchImports.find((entry) => entry.id === selectedDocumentId) || selectedBatchImports[0] || null;
  const selectedReadyRowCount = selectedBatchImports
    .filter((entry) => selectedBatchImportIds.includes(entry.id))
    .reduce((total, entry) => total + ((entry.lines || []).filter((line) => line.status === 'ready').length), 0);

  const load = async () => {
    setLoading(true);
    setErrorInfo(null);
    try {
      const [companiesRes, itemsRes, batchesRes] = await Promise.all([
        quotationAPI.companies.list({ active: 'true' }),
        quotationAPI.items.list({ active: 'true' }),
        quotationAPI.historicalImportBatches.list(),
      ]);
      setCompanies(companiesRes.data);
      setItems(itemsRes.data);
      setBatches(batchesRes.data);
      if (selectedBatch?.id) {
        const refreshedBatch = batchesRes.data.find((entry) => entry.id === selectedBatch.id);
        if (refreshedBatch) setSelectedBatch(refreshedBatch);
      }
    } catch (error) {
      const details = await describeQuotationError(
        error,
        'Load historical imports',
        'GET /quotations/historical-import-batches/'
      );
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const buildDrafts = (nextSuggestions) => {
    const nextSuggestionDrafts = {};
    const nextLineDrafts = {};
    nextSuggestions.forEach((suggestion) => {
      nextSuggestionDrafts[suggestion.id] = {
        action: suggestion.action || 'needs_manual_review',
        suggested_product: suggestion.suggested_product || '',
        suggested_company: suggestion.suggested_company || '',
        alias_text: suggestion.alias_text || '',
        proposed_company_name: suggestion.proposed_company_name || '',
        proposed_product_name: suggestion.proposed_product_name || '',
        proposed_unit: suggestion.proposed_unit || '',
        proposed_pack_size: suggestion.proposed_pack_size || '',
        proposed_dosage: suggestion.proposed_dosage || '',
      };
      if (suggestion.line) {
        nextLineDrafts[suggestion.line] = {
          item_name: suggestion.line_item_name || '',
          quantity: suggestion.line_quantity || '',
          unit: suggestion.line_unit || '',
          unit_price: suggestion.line_unit_price || '',
          vat_amount: suggestion.line_vat_amount || '',
          vat_rate: suggestion.line_vat_rate || '',
          line_total: suggestion.line_total || '',
          status: suggestion.line_status || 'needs_review',
        };
      }
    });
    setSuggestionDrafts(nextSuggestionDrafts);
    setLineDrafts(nextLineDrafts);
  };

  const suggestionOriginalFor = (suggestion) => ({
    action: suggestion.action || 'needs_manual_review',
    suggested_product: suggestion.suggested_product || '',
    suggested_company: suggestion.suggested_company || '',
    alias_text: suggestion.alias_text || '',
    proposed_company_name: suggestion.proposed_company_name || '',
    proposed_product_name: suggestion.proposed_product_name || '',
    proposed_unit: suggestion.proposed_unit || '',
    proposed_pack_size: suggestion.proposed_pack_size || '',
    proposed_dosage: suggestion.proposed_dosage || '',
  });

  const lineOriginalFor = (suggestion) => (suggestion.line ? {
    item_name: suggestion.line_item_name || '',
    quantity: suggestion.line_quantity || '',
    unit: suggestion.line_unit || '',
    unit_price: suggestion.line_unit_price || '',
    vat_amount: suggestion.line_vat_amount || '',
    vat_rate: suggestion.line_vat_rate || '',
    line_total: suggestion.line_total || '',
    status: suggestion.line_status || 'needs_review',
  } : {});

  const suggestionHasDraftChanges = (suggestion) => {
    const suggestionOriginal = suggestionOriginalFor(suggestion);
    const suggestionDraft = suggestionDrafts[suggestion.id] || {};
    const suggestionChanges = changedFields(
      { ...suggestionOriginal, ...suggestionDraft },
      suggestionOriginal,
      Object.keys(suggestionOriginal)
    );
    const lineOriginal = lineOriginalFor(suggestion);
    const lineDraft = suggestion.line ? lineDrafts[suggestion.line] : null;
    const lineChanges = suggestion.line
      ? changedFields({ ...lineOriginal, ...(lineDraft || {}) }, lineOriginal, Object.keys(lineOriginal))
      : [];
    return Boolean(suggestionChanges.length || lineChanges.length);
  };

  const mergeUpdatedSuggestions = (updatedSuggestions = []) => {
    if (!updatedSuggestions.length) return;
    setSuggestions((current) => {
      const byId = new Map(current.map((suggestion) => [suggestion.id, suggestion]));
      updatedSuggestions.forEach((suggestion) => byId.set(suggestion.id, suggestion));
      const next = Array.from(byId.values()).sort((a, b) => (
        dateSortValue(b.historical_import_document_date) - dateSortValue(a.historical_import_document_date)
        || String(a.line_item_name || '').localeCompare(String(b.line_item_name || ''))
        || a.id - b.id
      ));
      buildDrafts(next);
      return next;
    });
  };

  const loadSuggestions = async (batchId = selectedBatch?.id) => {
    if (!batchId) return [];
    try {
      const response = await quotationAPI.historicalImportAiSuggestions.list({ batch: batchId });
      setSuggestions(response.data);
      buildDrafts(response.data);
      return response.data;
    } catch (error) {
      const details = await describeQuotationError(error, 'Load AI suggestions', 'GET /quotations/historical-import-ai-suggestions/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
      return [];
    }
  };

  const setInlineFeedback = (key, type, message) => {
    setActionFeedback((current) => ({
      ...current,
      [key]: { type, message, at: Date.now() },
    }));
  };

  const updateSelectedImport = (importId, updater) => {
    setSelectedBatch((current) => {
      if (!current) return current;
      return {
        ...current,
        imports: (current.imports || []).map((entry) => (
          entry.id === importId ? updater(entry) : entry
        )),
      };
    });
  };

  const removeSelectedImport = (importId) => {
    setSelectedBatch((current) => {
      if (!current) return current;
      const nextImports = (current.imports || []).filter((entry) => entry.id !== importId);
      return { ...current, imports: nextImports };
    });
    setSelectedBatchImportIds((current) => current.filter((id) => id !== importId));
    setSelectedDocumentId((current) => {
      if (current !== importId) return current;
      const nextImport = (selectedBatch?.imports || []).find((entry) => entry.id !== importId);
      return nextImport?.id || null;
    });
  };

  const selectBatch = async (batch, step = 'analyze') => {
    setSelectedBatch(batch);
    setActiveStep(step);
    setSelectedBatchImportIds((batch.imports || []).map((entry) => entry.id));
    setSelectedDocumentId((batch.imports || [])[0]?.id || null);
    setSelectedSuggestionIds([]);
    setNotice(null);
    setErrorInfo(null);
    setAiRunResults([]);
    setLastAiRunFailed(false);
    setImportDrafts({});
    setActionFeedback({});
    await loadSuggestions(batch.id);
  };

  const refreshSelectedBatch = async (batchId = selectedBatch?.id) => {
    if (!batchId) return null;
    const response = await quotationAPI.historicalImportBatches.retrieve(batchId);
    setSelectedBatch(response.data);
    return response.data;
  };

  const uploadBatchFiles = async () => {
    if (batchUploading || !batchFiles.length) return;
    const files = batchFiles.slice(0, 25);
    if (batchFiles.length > 25) {
      setNotice({ type: 'warning', message: 'Only the first 25 PDFs are processed in one batch for safety.' });
    } else {
      setNotice(null);
    }
    setBatchUploading(true);
    setErrorInfo(null);
    const initialProgress = files.map((file) => ({ filename: file.name, status: 'queued', message: '' }));
    setBatchProgress(initialProgress);
    try {
      const batchName = `Historical batch ${new Date().toLocaleString()}`;
      const batchResponse = await quotationAPI.historicalImportBatches.create({ name: batchName });
      let currentBatch = batchResponse.data;
      setSelectedBatch(currentBatch);
      setActiveStep('upload');
      for (let index = 0; index < files.length; index += 1) {
        const file = files[index];
        setBatchProgress((current) => current.map((entry, entryIndex) => (
          entryIndex === index ? { ...entry, status: 'parsing', message: 'Parsing file...' } : entry
        )));
        const formData = new FormData();
        formData.append('file', file);
        try {
          const response = await quotationAPI.historicalImportBatches.uploadFile(currentBatch.id, formData);
          currentBatch = response.data.batch || currentBatch;
          setSelectedBatch(currentBatch);
          const statusLabel = response.data.status === 'duplicate' ? 'duplicate' : 'parsed';
          setBatchProgress((current) => current.map((entry, entryIndex) => (
            entryIndex === index
              ? { ...entry, status: statusLabel, message: response.data.duplicate_check?.message || `${response.data.import?.lines?.length || 0} rows parsed` }
              : entry
          )));
        } catch (error) {
          const details = await describeQuotationError(error, `Parse ${file.name}`, `POST /quotations/historical-import-batches/${currentBatch.id}/upload_file/`);
          setBatchProgress((current) => current.map((entry, entryIndex) => (
            entryIndex === index ? { ...entry, status: 'failed', message: details.detail || 'Upload failed' } : entry
          )));
        }
      }
      await load();
      const refreshed = await refreshSelectedBatch(currentBatch.id);
      const importIds = (refreshed?.imports || []).map((entry) => entry.id);
      setSelectedBatchImportIds(importIds);
      setSelectedDocumentId(importIds[0] || null);
      await loadSuggestions(currentBatch.id);
      setNotice({ type: 'success', message: 'Batch upload finished. Continue to AI Analyze.' });
      setActiveStep('analyze');
    } catch (error) {
      const details = await describeQuotationError(error, 'Create historical batch', 'POST /quotations/historical-import-batches/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setBatchUploading(false);
    }
  };

  const toggleBatchImportSelection = (importId) => {
    setSelectedBatchImportIds((current) => (
      current.includes(importId)
        ? current.filter((candidate) => candidate !== importId)
        : [...current, importId]
    ));
  };

  const toggleAllBatchImports = () => {
    setSelectedBatchImportIds((current) => {
      if (allBatchImportsSelected) {
        return current.filter((id) => !visibleBatchImportIds.includes(id));
      }
      return Array.from(new Set([...current, ...visibleBatchImportIds]));
    });
  };

  const runBatchAiAnalyze = async (overrideImportIds = null) => {
    if (!selectedBatch || workingAction) return;
    const importIds = overrideImportIds || (selectedBatchImportIds.length ? selectedBatchImportIds : visibleBatchImportIds);
    if (!importIds.length) {
      setNotice({ type: 'warning', message: 'Select at least one parsed import before running AI Analyze.' });
      return;
    }
    setWorkingAction('ai');
    setNotice(null);
    setErrorInfo(null);
    setAiRunResults([]);
    setLastAiRunFailed(false);
    try {
      const response = await quotationAPI.historicalImportBatches.runAiSuggestions(selectedBatch.id, { import_ids: importIds, mode: 'auto' });
      setSelectedBatch(response.data.batch || selectedBatch);
      setAiRunResults(response.data.results || response.data.summary?.results || []);
      const failedCount = response.data.summary.failed || 0;
      const suggestedCount = response.data.summary.suggested || 0;
      setLastAiRunFailed(failedCount > 0);
      await loadSuggestions(selectedBatch.id);
      await load();
      setNotice({
        type: failedCount ? 'warning' : 'success',
        message: failedCount
          ? `AI Analyze finished with issues: ${suggestedCount} files analyzed, ${failedCount} failed. Review per-file reasons below.`
          : `AI Analyze finished: ${suggestedCount} files analyzed.`,
      });
    } catch (error) {
      const details = await describeQuotationError(error, 'Run batch AI Analyze', `POST /quotations/historical-import-batches/${selectedBatch.id}/run_ai_suggestions/`);
      setErrorInfo(details);
      setLastAiRunFailed(true);
      setNotice({ type: 'warning', message: 'AI Analyze failed. Parsed rows are still available.' });
      console.error(formatQuotationError(details), error);
    } finally {
      setWorkingAction('');
    }
  };

  const retryFailedAiAnalyze = async () => {
    const failedImportIds = aiRunResults
      .filter((result) => result.status === 'failed' && result.import_id)
      .map((result) => result.import_id);
    if (!failedImportIds.length) {
      setNotice({ type: 'warning', message: 'There are no failed AI files to retry.' });
      return;
    }
    setSelectedBatchImportIds(failedImportIds);
    await runBatchAiAnalyze(failedImportIds);
  };

  const updateImportDraft = (importId, patch) => {
    setImportDrafts((current) => ({
      ...current,
      [importId]: {
        ...(current[importId] || {}),
        ...patch,
      },
    }));
  };

  const draftForImport = (entry) => ({
    company: entry.company || suggestions.find((suggestion) => (
      suggestion.historical_import === entry.id
      && suggestion.suggestion_type === 'company'
      && suggestion.action === 'match_existing_company'
      && suggestion.suggested_company
    ))?.suggested_company || '',
    suggested_company_name: entry.suggested_company_name || suggestions.find((suggestion) => (
      suggestion.historical_import === entry.id
      && suggestion.suggestion_type === 'company'
      && suggestion.action === 'create_new_company'
      && suggestion.proposed_company_name
    ))?.proposed_company_name || '',
    document_number: entry.document_number || '',
    document_date: entry.document_date || '',
    currency: entry.currency || 'AED',
    subtotal: entry.subtotal || '',
    vat_total: entry.vat_total || '',
    total: entry.total || '',
    ...(importDrafts[entry.id] || {}),
  });

  const importHasDraftChanges = (entry) => {
    const draft = draftForImport(entry);
    const original = {
      company: entry.company || '',
      suggested_company_name: entry.suggested_company_name || '',
      document_number: entry.document_number || '',
      document_date: entry.document_date || '',
      currency: entry.currency || 'AED',
      subtotal: entry.subtotal || '',
      vat_total: entry.vat_total || '',
      total: entry.total || '',
    };
    return changedFields(draft, original, Object.keys(original)).length > 0;
  };

  const saveImportDetails = async (entry) => {
    if (!entry || workingAction) return;
    const draft = draftForImport(entry);
    const original = {
      company: entry.company || '',
      suggested_company_name: entry.suggested_company_name || '',
      document_number: entry.document_number || '',
      document_date: entry.document_date || '',
      currency: entry.currency || 'AED',
      subtotal: entry.subtotal || '',
      vat_total: entry.vat_total || '',
      total: entry.total || '',
    };
    if (!changedFields(draft, original, Object.keys(original)).length) {
      setInlineFeedback(`import-${entry.id}`, 'warning', 'No changes to save for this document.');
      setNotice({ type: 'warning', message: 'No changes to save for this document.' });
      return;
    }
    setWorkingAction(`save-import-${entry.id}`);
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.historicalImports.update(entry.id, {
        company: draft.company || null,
        suggested_company_name: draft.suggested_company_name || '',
        document_number: draft.document_number || '',
        document_date: draft.document_date || null,
        currency: draft.currency || 'AED',
        subtotal: draft.subtotal || null,
        vat_total: draft.vat_total || null,
        total: draft.total || null,
      });
      updateSelectedImport(entry.id, () => response.data);
      setImportDrafts((current) => {
        const next = { ...current };
        delete next[entry.id];
        return next;
      });
      setInlineFeedback(`import-${entry.id}`, 'success', 'Document edits saved.');
      await refreshSelectedBatch();
      await loadSuggestions(selectedBatch.id);
      setNotice({ type: 'success', message: `Edits saved for ${entry.source_filename}.` });
    } catch (error) {
      const details = await describeQuotationError(error, 'Save historical import', `PATCH /quotations/historical-imports/${entry.id}/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setWorkingAction('');
    }
  };

  const updateSuggestionDraft = (suggestionId, patch) => {
    setSuggestionDrafts((current) => ({
      ...current,
      [suggestionId]: {
        ...(current[suggestionId] || {}),
        ...patch,
      },
    }));
  };

  const updateLineDraft = (lineId, patch) => {
    setLineDrafts((current) => ({
      ...current,
      [lineId]: {
        ...(current[lineId] || emptyLineDraft),
        ...patch,
      },
    }));
  };

  const persistSuggestionEdits = async (suggestion, { skipNoopWarning = false } = {}) => {
    if (!suggestion) return false;
    const suggestionDraft = suggestionDrafts[suggestion.id] || {};
    const lineDraft = suggestion.line ? lineDrafts[suggestion.line] : null;
    const suggestionOriginal = suggestionOriginalFor(suggestion);
    const lineOriginal = lineOriginalFor(suggestion);
    const suggestionChanges = changedFields(
      { ...suggestionOriginal, ...suggestionDraft },
      suggestionOriginal,
      Object.keys(suggestionOriginal)
    );
    const lineChanges = suggestion.line
      ? changedFields({ ...lineOriginal, ...(lineDraft || {}) }, lineOriginal, Object.keys(lineOriginal))
      : [];
    if (!suggestionChanges.length && !lineChanges.length) {
      if (!skipNoopWarning) {
        setInlineFeedback(`suggestion-${suggestion.id}`, 'warning', 'No changes to save for this review row.');
        setNotice({ type: 'warning', message: 'No changes to save for this review row.' });
      }
      return false;
    }
    await quotationAPI.historicalImportAiSuggestions.update(suggestion.id, {
      action: suggestionDraft.action || suggestion.action,
      suggested_product: suggestionDraft.suggested_product || null,
      suggested_company: suggestionDraft.suggested_company || null,
      alias_text: suggestionDraft.alias_text || '',
      proposed_company_name: suggestionDraft.proposed_company_name || '',
      proposed_product_name: suggestionDraft.proposed_product_name || '',
      proposed_unit: suggestionDraft.proposed_unit || '',
      proposed_pack_size: suggestionDraft.proposed_pack_size || '',
      proposed_dosage: suggestionDraft.proposed_dosage || '',
    });
    if (suggestion.line && lineDraft) {
      await quotationAPI.historicalImportLines.update(suggestion.line, {
        item_name: lineDraft.item_name || '',
        quantity: lineDraft.quantity || null,
        unit: lineDraft.unit || '',
        unit_price: lineDraft.unit_price || null,
        vat_amount: lineDraft.vat_amount || null,
        vat_rate: lineDraft.vat_rate || '0',
        line_total: lineDraft.line_total || null,
        status: lineDraft.status || 'needs_review',
      });
    }
    return true;
  };

  const saveSuggestionEdits = async (suggestion) => {
    if (!suggestion || workingAction) return;
    setWorkingAction(`save-suggestion-${suggestion.id}`);
    setNotice(null);
    setErrorInfo(null);
    try {
      const saved = await persistSuggestionEdits(suggestion);
      if (saved) {
        setInlineFeedback(`suggestion-${suggestion.id}`, 'success', 'Review edits saved. Apply when you are ready to approve this decision.');
        await loadSuggestions(selectedBatch.id);
        await refreshSelectedBatch();
        setNotice({ type: 'success', message: 'Review row saved.' });
      }
    } catch (error) {
      const details = await describeQuotationError(error, 'Save AI suggestion edits', `PATCH /quotations/historical-import-ai-suggestions/${suggestion.id}/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setWorkingAction('');
    }
  };

  const toggleSuggestionSelection = (suggestionId) => {
    setSelectedSuggestionIds((current) => (
      current.includes(suggestionId)
        ? current.filter((candidate) => candidate !== suggestionId)
        : [...current, suggestionId]
    ));
  };

  const selectSuggestionGroup = (groupSuggestions, append = false) => {
    const ids = groupSuggestions.filter((suggestion) => isSuggestionActionable(suggestion)).map((suggestion) => suggestion.id);
    setSelectedSuggestionIds((current) => (append ? Array.from(new Set([...current, ...ids])) : ids));
  };

  const describeApplySelection = (ids) => {
    const chosen = suggestions.filter((suggestion) => ids.includes(suggestion.id));
    const counts = chosen.reduce((acc, suggestion) => {
      acc[suggestion.action] = (acc[suggestion.action] || 0) + 1;
      return acc;
    }, {});
    return [
      counts.match_existing_company ? `${counts.match_existing_company} company match(es)` : '',
      counts.create_new_company ? `${counts.create_new_company} new company approval(s)` : '',
      counts.match_existing_product ? `${counts.match_existing_product} Product match(es)` : '',
      counts.create_company_alias ? `${counts.create_company_alias} alias creation(s)` : '',
      counts.create_new_product ? `${counts.create_new_product} draft Product creation(s)` : '',
      counts.skip ? `${counts.skip} skipped/noise row(s)` : '',
      counts.needs_manual_review ? `${counts.needs_manual_review} row(s) left for manual review` : '',
    ].filter(Boolean);
  };

  const buildApplyNotice = (summary = {}) => {
    const parts = [];
    if (summary.applied) parts.push(`${summary.applied} selected decision(s) applied`);
    if (summary.applied_similar) parts.push(`${summary.applied_similar} exact repeated row(s) updated`);
    if (summary.auto_applied_similar && !summary.applied_similar) parts.push(`${summary.auto_applied_similar} similar row(s) updated`);
    if (summary.conflict) parts.push(`${summary.conflict} conflict(s) need review`);
    if (summary.failed) parts.push(`${summary.failed} failed`);
    if (summary.already_applied) parts.push(`${summary.already_applied} already applied`);
    return parts.length ? parts.join(', ') + '.' : 'No pending decisions were changed.';
  };

  const requestApplySuggestions = (ids = selectedSuggestionIds) => {
    if (!ids.length || workingAction) return;
    setConfirmAction({
      title: 'Apply selected AI decisions?',
      body: 'This applies staff-approved review decisions only. It may create company links, aliases, or draft/internal Products, but it will not commit price history.',
      details: describeApplySelection(ids),
      confirmLabel: 'Apply decisions',
      onConfirm: () => performApplySuggestions(ids),
    });
  };

  const performApplySuggestions = async (ids = selectedSuggestionIds) => {
    if (!ids.length || workingAction) return;
    setConfirmAction(null);
    setWorkingAction('apply');
    setNotice(null);
    setErrorInfo(null);
    try {
      const chosenSuggestions = suggestions.filter((suggestion) => ids.includes(suggestion.id) && isSuggestionActionable(suggestion));
      let savedDraftCount = 0;
      for (const suggestion of chosenSuggestions) {
        // Apply approves exactly what staff sees on the card. Persist visible
        // edits first so Apply This never depends on a separate Save Edits click.
        // eslint-disable-next-line no-await-in-loop
        const saved = await persistSuggestionEdits(suggestion, { skipNoopWarning: true });
        if (saved) savedDraftCount += 1;
      }
      const response = await quotationAPI.historicalImportBatches.applyAiSuggestions(selectedBatch.id, { suggestion_ids: ids });
      mergeUpdatedSuggestions(response.data.updated_suggestions || []);
      if (response.data.batch) {
        setSelectedBatch(response.data.batch);
      }
      (response.data.results || []).forEach((result) => {
        if (result.suggestion_id) {
          const type = result.status === 'conflict' || result.status === 'failed' ? 'warning' : 'success';
          setInlineFeedback(
            `suggestion-${result.suggestion_id}`,
            type,
            result.message || (result.status === 'applied' ? 'Decision applied.' : `Decision ${result.status}.`)
          );
        }
      });
      setNotice({
        type: response.data.summary.conflict ? 'warning' : 'success',
        message: `${savedDraftCount ? `${savedDraftCount} edited review row(s) saved first. ` : ''}${buildApplyNotice(response.data.summary)}`,
      });
      setSelectedSuggestionIds([]);
      const refreshed = await refreshSelectedBatch(response.data.batch?.id || selectedBatch.id);
      await loadSuggestions(response.data.batch?.id || selectedBatch.id);
      if (refreshed) {
        setSelectedBatchImportIds((current) => current.filter((id) => (refreshed.imports || []).some((entry) => entry.id === id)));
      }
      const itemsRes = await quotationAPI.items.list({ active: 'true' });
      setItems(itemsRes.data);
    } catch (error) {
      const details = await describeQuotationError(error, 'Apply AI suggestions', `POST /quotations/historical-import-batches/${selectedBatch.id}/apply_ai_suggestions/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setWorkingAction('');
    }
  };

  const markLinesReady = async (lineIds) => {
    if (!lineIds.length || workingAction) return;
    setWorkingAction('mark-ready');
    setNotice(null);
    setErrorInfo(null);
    try {
      const byImport = {};
      suggestions.forEach((suggestion) => {
        if (lineIds.includes(suggestion.line)) {
          byImport[suggestion.historical_import] = byImport[suggestion.historical_import] || [];
          byImport[suggestion.historical_import].push(suggestion.line);
        }
      });
      await Promise.all(Object.entries(byImport).map(([importId, rows]) => (
        quotationAPI.historicalImports.bulkUpdateRows(importId, { row_ids: rows, status: 'ready' })
      )));
      suggestions.forEach((suggestion) => {
        if (lineIds.includes(suggestion.line)) {
          setInlineFeedback(`suggestion-${suggestion.id}`, 'success', 'Row marked ready where validation allowed.');
        }
      });
      await load();
      await refreshSelectedBatch();
      await loadSuggestions(selectedBatch.id);
      setNotice({ type: 'success', message: 'Selected rows marked ready where validation allowed.' });
    } catch (error) {
      const details = await describeQuotationError(error, 'Mark selected rows ready', 'POST /quotations/historical-imports/{id}/bulk_update_rows/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setWorkingAction('');
    }
  };

  const openSourceContext = async (suggestion) => {
    const context = suggestion.source_context || {};
    setSourceModal({
      loading: true,
      title: suggestion.line_item_name || 'Source row',
      context,
      imageUrl: '',
      error: '',
    });
    if (!context.available || !suggestion.historical_import) {
      setSourceModal({
        loading: false,
        title: suggestion.line_item_name || 'Source row',
        context,
        imageUrl: '',
        error: context.message || 'Source preview unavailable for this historical import.',
      });
      return;
    }
    try {
      const response = await quotationAPI.historicalImports.previewPage(suggestion.historical_import, { page: context.page_number || 1 });
      const imageUrl = URL.createObjectURL(response.data);
      setSourceModal({
        loading: false,
        title: suggestion.line_item_name || 'Source row',
        context,
        imageUrl,
        error: '',
      });
    } catch (error) {
      const details = await describeQuotationError(error, 'Load source preview', `GET /quotations/historical-imports/${suggestion.historical_import}/preview_page/`);
      setSourceModal({
        loading: false,
        title: suggestion.line_item_name || 'Source row',
        context,
        imageUrl: '',
        error: details.detail || 'Source preview unavailable.',
      });
    }
  };

  const closeSourceModal = () => {
    if (sourceModal?.imageUrl) URL.revokeObjectURL(sourceModal.imageUrl);
    setSourceModal(null);
  };

  const openDuplicateInspection = (entry, duplicateCheck, mode = 'compare') => {
    const match = duplicatePrimaryMatch(duplicateCheck);
    if (!match) return;
    setDuplicateModal({
      mode,
      current: entry,
      duplicateCheck,
      match,
    });
  };

  const requestRemoveImportFromBatch = (entry) => {
    if (!entry || workingAction) return;
    setConfirmAction({
      title: 'Remove this import from the batch?',
      body: 'This cancels the staged import and removes its AI review rows from this batch. It does not delete committed price history.',
      details: [
        entry.source_filename || `Import #${entry.id}`,
        entry.company_name || entry.suggested_company_name || 'No company selected',
        'Use this for duplicate or accidental uploads before committing.',
      ],
      confirmLabel: 'Remove from batch',
      onConfirm: () => performRemoveImportFromBatch(entry),
    });
  };

  const performRemoveImportFromBatch = async (entry) => {
    if (!entry || workingAction) return;
    setConfirmAction(null);
    setWorkingAction(`remove-import-${entry.id}`);
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.historicalImports.removeFromBatch(entry.id);
      removeSelectedImport(entry.id);
      if (response.data.batch) {
        setSelectedBatch(response.data.batch);
      }
      await loadSuggestions(response.data.batch?.id || selectedBatch?.id);
      await load();
      setNotice({ type: 'success', message: `${entry.source_filename} was removed from this batch.` });
    } catch (error) {
      const details = await describeQuotationError(error, 'Remove historical import from batch', `POST /quotations/historical-imports/${entry.id}/remove_from_batch/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setWorkingAction('');
    }
  };

  const commitSelectedBatchImports = async () => {
    if (!selectedBatch || workingAction) return;
    const importIds = selectedBatchImportIds.length ? selectedBatchImportIds : visibleBatchImportIds;
    if (!importIds.length) return;
    const selectedReady = selectedBatchImports
      .filter((entry) => importIds.includes(entry.id))
      .reduce((total, entry) => total + ((entry.lines || []).filter((line) => line.status === 'ready').length), 0);
    if (!selectedReady) {
      setNotice({ type: 'warning', message: 'No selected imports have ready rows. Apply decisions or mark valid rows ready before committing.' });
      return;
    }
    setConfirmAction({
      title: 'Commit ready rows to price history?',
      body: 'Only rows already marked ready will create company-specific price history. Needs-review, skipped, duplicate, and unresolved rows are ignored.',
      details: [
        `${importIds.length} selected import(s)`,
        `${selectedReady} ready row(s) in the selected imports`,
        'No Products or aliases are created by this commit step.',
      ],
      confirmLabel: 'Commit price history',
      onConfirm: () => performCommitSelectedBatchImports(importIds),
    });
  };

  const performCommitSelectedBatchImports = async (importIds) => {
    if (!selectedBatch || workingAction || !importIds.length) return;
    setConfirmAction(null);
    setWorkingAction('commit');
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.historicalImportBatches.commitReadyImports(selectedBatch.id, { import_ids: importIds });
      const blocked = response.data.summary.blocked || 0;
      const failed = response.data.summary.failed || 0;
      const committed = response.data.summary.committed || 0;
      const blockedReasons = (response.data.results || [])
        .filter((result) => result.status === 'blocked' || result.status === 'failed')
        .slice(0, 3)
        .map((result) => `${result.filename || `Import #${result.import_id}`}: ${result.message}`)
        .join(' | ');
      setNotice({
        type: blocked || failed ? 'warning' : 'success',
        message: blocked || failed
          ? `Commit finished: ${committed} import(s) committed, ${blocked} blocked, ${failed} failed. ${blockedReasons}`
          : `Commit complete: ${committed} import(s) committed.`,
      });
      await load();
      await refreshSelectedBatch();
      await loadSuggestions(selectedBatch.id);
    } catch (error) {
      const details = await describeQuotationError(error, 'Commit batch ready imports', `POST /quotations/historical-import-batches/${selectedBatch.id}/commit_ready_imports/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setWorkingAction('');
    }
  };

  const suggestionGroups = useMemo(() => {
    const lineSuggestions = suggestions.filter((suggestion) => {
      if (suggestion.suggestion_type !== 'line') return false;
      if (decisionCompanyFilter !== 'all') {
        const companyName = suggestion.historical_import_company_name || '';
        if (companyName !== decisionCompanyFilter) return false;
      }
      if (decisionFileFilter !== 'all' && suggestion.historical_import_filename !== decisionFileFilter) return false;
      if (decisionConfidenceFilter === 'high' && confidencePercent(suggestion.confidence) < 85) return false;
      if (decisionConfidenceFilter === 'low' && confidencePercent(suggestion.confidence) >= 85) return false;
      if (decisionConfidenceFilter === 'unresolved' && !isSuggestionActionable(suggestion) && suggestion.status !== 'conflict') return false;
      return true;
    });
    const groups = {
      match_existing_product: [],
      create_company_alias: [],
      create_new_product: [],
      needs_manual_review: [],
      skip: [],
    };
    lineSuggestions.forEach((suggestion) => {
      const key = LINE_ACTIONS.includes(suggestion.action) ? suggestion.action : 'needs_manual_review';
      groups[key].push(suggestion);
    });
    Object.keys(groups).forEach((key) => {
      groups[key].sort((a, b) => (
        dateSortValue(b.historical_import_document_date) - dateSortValue(a.historical_import_document_date)
        || String(a.line_item_name || '').localeCompare(String(b.line_item_name || ''))
        || a.id - b.id
      ));
    });
    return groups;
  }, [decisionCompanyFilter, decisionConfidenceFilter, decisionFileFilter, suggestions]);

  const companySuggestions = useMemo(() => (
    suggestions.filter((suggestion) => suggestion.suggestion_type === 'company')
  ), [suggestions]);

  const highConfidenceCompanyMatches = useMemo(() => (
    companySuggestions.filter((suggestion) => (
      isSuggestionActionable(suggestion)
      && suggestion.action === 'match_existing_company'
      && suggestion.suggested_company
      && confidencePercent(suggestion.confidence) >= 85
    ))
  ), [companySuggestions]);

  const decisionCompanyOptions = useMemo(() => (
    Array.from(new Set(suggestions.map((suggestion) => suggestion.historical_import_company_name).filter(Boolean))).sort()
  ), [suggestions]);

  const decisionFileOptions = useMemo(() => (
    Array.from(new Set(suggestions.map((suggestion) => suggestion.historical_import_filename).filter(Boolean))).sort()
  ), [suggestions]);

  const filteredDecisionGroups = useMemo(() => {
    if (decisionFilter === 'all') return suggestionGroups;
    return { [decisionFilter]: suggestionGroups[decisionFilter] || [] };
  }, [decisionFilter, suggestionGroups]);

  const renderStepHeader = () => (
    <div className="qm-wizard-stepper" aria-label="Historical import workflow steps">
      {STEPS.map((step, index) => {
        const activeIndex = STEPS.findIndex((candidate) => candidate.id === activeStep);
        return (
          <button
            key={step.id}
            type="button"
            className={`qm-wizard-step${activeStep === step.id ? ' active' : ''}${index < activeIndex ? ' complete' : ''}`}
            onClick={() => setActiveStep(step.id)}
            disabled={step.id !== 'upload' && !selectedBatch}
          >
            <span>{index + 1}</span>
            <strong>{step.label}</strong>
          </button>
        );
      })}
    </div>
  );

  const renderBatchSummary = () => {
    if (!selectedBatch) return null;
    return (
      <div className="qm-wizard-summary">
        <div><span>Files</span><strong>{statValue(wizardSummary, 'file_count', selectedBatchImports.length)}</strong></div>
        <div><span>Parsed</span><strong>{statValue(wizardSummary, 'parsed_file_count', selectedBatchImports.length)}</strong></div>
        <div><span>Rows</span><strong>{lineCounts.total || 0}</strong></div>
        <div><span>Ready</span><strong>{lineCounts.ready || 0}</strong></div>
        <div><span>Pending AI</span><strong>{selectedBatch.pending_suggestion_count || wizardSummary.pending_suggestion_count || 0}</strong></div>
        <div><span>Unresolved</span><strong>{wizardSummary.unresolved_count || 0}</strong></div>
      </div>
    );
  };

  const renderUploadStep = () => (
    <div className="qm-wizard-grid">
      <div className="qm-panel">
        <div className="qm-panel-heading">
          <div>
            <h3>Upload Historical PDFs</h3>
            <p>Upload old finalized quotation or invoice PDFs as one batch. This only stages imports for review.</p>
          </div>
        </div>
        <div className="qm-import-source">
          <label>
            <span className="qm-label-text">Old finalized PDFs</span>
            <input type="file" accept=".pdf" multiple onChange={(event) => setBatchFiles(Array.from(event.target.files || []))} />
          </label>
          <button type="button" className="qm-primary" disabled={batchUploading || !batchFiles.length} onClick={uploadBatchFiles}>
            {batchUploading ? 'Uploading...' : 'Upload Batch'}
          </button>
        </div>
        <div className="qm-helper compact">V1 processes up to 25 PDFs sequentially. AI decisions stay review-only until staff approves them.</div>
        {batchFiles.length > 0 && (
          <div className="qm-file-chips">
            {batchFiles.slice(0, 25).map((file) => <span key={`${file.name}-${file.size}`}>{file.name}</span>)}
          </div>
        )}
        {batchProgress.length > 0 && (
          <div className="qm-batch-progress">
            {batchProgress.map((entry) => (
              <div key={entry.filename} className={`qm-batch-file status-${entry.status}`}>
                <strong>{entry.filename}</strong>
                <span>{entry.status}</span>
                {entry.message && <small>{entry.message}</small>}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="qm-panel">
        <div className="qm-panel-heading">
          <div>
            <h3>Historical Import Batches</h3>
            <p>Open a batch session to continue analysis, review, and commit.</p>
          </div>
          <button type="button" className="qm-secondary small" onClick={load} disabled={loading}>Refresh</button>
        </div>
        {loading ? (
          <div className="qm-loading">Loading batches...</div>
        ) : (
          <div className="qm-table-wrap">
            <table className="qm-table">
              <thead>
                <tr>
                  <th>Batch</th>
                  <th>Files</th>
                  <th>Rows</th>
                  <th>Pending AI</th>
                  <th>Unresolved</th>
                  <th>Status</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {batches.map((batch) => (
                  <tr key={batch.id} className={selectedBatch?.id === batch.id ? 'selected' : ''}>
                    <td>{batch.name || `Batch #${batch.id}`}<br /><small>{batch.created_at}</small></td>
                    <td>{batch.wizard_summary?.file_count ?? batch.summary?.import_count ?? batch.import_count ?? 0}</td>
                    <td>{batch.wizard_summary?.line_counts?.total ?? batch.summary?.total_row_count ?? 0}</td>
                    <td>{batch.pending_suggestion_count || batch.summary?.pending_suggestion_count || 0}</td>
                    <td>{batch.wizard_summary?.unresolved_count ?? batch.summary?.unresolved_count ?? 0}</td>
                    <td><span className={`qm-badge status-${batch.status}`}>{batch.status}</span></td>
                    <td><button type="button" className="qm-secondary small" onClick={() => selectBatch(batch)}>Open Workflow</button></td>
                  </tr>
                ))}
                {!batches.length && (
                  <tr><td colSpan="7"><div className="qm-empty compact">No historical batches yet. Upload PDFs to begin.</div></td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );

  const renderAnalyzeStep = () => (
    <div className="qm-panel">
      <div className="qm-panel-heading">
        <div>
          <h3>AI Analyze Batch</h3>
          <p>One action cleans parse rows, detects companies, suggests Products, aliases, new draft Products, and skips noise rows.</p>
        </div>
        <button type="button" className="qm-primary" disabled={!selectedBatch || workingAction === 'ai'} onClick={() => runBatchAiAnalyze()}>
          {workingAction === 'ai' ? 'Running AI Analyze...' : 'Run AI Analyze'}
        </button>
      </div>
      {!selectedBatch ? (
        <div className="qm-empty">Upload or open a batch first.</div>
      ) : (
        <>
          {renderBatchSummary()}
          {lastAiRunFailed && suggestions.length > 0 && (
            <div className="qm-notice warning">
              <strong>AI Analyze failed for at least one file in this run.</strong>
              <p>Showing previous pending suggestions from an earlier successful run. Retry failed files or inspect the per-file reasons below.</p>
            </div>
          )}
          {aiRunResults.length > 0 && (
            <div className="qm-ai-run-results">
              <div className="qm-card-title-row">
                <h4>Latest AI Analyze results</h4>
                <button type="button" className="qm-secondary small" disabled={workingAction === 'ai' || !aiRunResults.some((result) => result.status === 'failed')} onClick={retryFailedAiAnalyze}>
                  Retry failed files
                </button>
              </div>
              {aiRunResults.map((result) => {
                const entry = selectedBatchImports.find((candidate) => candidate.id === result.import_id);
                return (
                  <div key={`${result.import_id || result.filename}-${result.status}`} className={`qm-ai-run-row status-${result.status}`}>
                    <strong>{entry?.source_filename || result.filename || `Import #${result.import_id}`}</strong>
                    <span>{result.status}</span>
                    <small>{result.message || `${result.suggestion_count || 0} suggestions generated.`}</small>
                    {result.showing_previous_suggestions && (
                      <em>Showing {result.previous_suggestion_count} previous suggestion(s).</em>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          <div className="qm-ai-analysis-grid">
            <div className="qm-ai-analysis-card">
              <span>Existing Product matches</span>
              <strong>{pendingActionCounts.match_existing_product || appliedActionCounts.match_existing_product || 0}</strong>
              <small>AI thinks these are already in the Product catalog.</small>
            </div>
            <div className="qm-ai-analysis-card">
              <span>Company aliases suggested</span>
              <strong>{pendingActionCounts.create_company_alias || appliedActionCounts.create_company_alias || 0}</strong>
              <small>Customer wording that can map to an existing Product.</small>
            </div>
            <div className="qm-ai-analysis-card">
              <span>New draft Products</span>
              <strong>{pendingActionCounts.create_new_product || appliedActionCounts.create_new_product || 0}</strong>
              <small>Likely real Products missing from the catalog.</small>
            </div>
            <div className="qm-ai-analysis-card warning">
              <span>Manual review</span>
              <strong>{pendingActionCounts.needs_manual_review || lineCounts.needs_review || 0}</strong>
              <small>Uncertain or conflicting rows staff should fix.</small>
            </div>
            <div className="qm-ai-analysis-card muted">
              <span>Skipped/noise rows</span>
              <strong>{pendingActionCounts.skip || lineCounts.skipped || 0}</strong>
              <small>Headers, totals, footers, and non-item rows.</small>
            </div>
          </div>
          <div className="qm-table-wrap">
            <table className="qm-table">
              <thead>
                <tr>
                  <th className="qm-check-cell"><input type="checkbox" checked={allBatchImportsSelected} onChange={toggleAllBatchImports} /></th>
                  <th>File</th>
                  <th>Company</th>
                  <th>Document</th>
                  <th>Date</th>
                  <th>Rows</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {selectedBatchImports.map((entry) => (
                  <tr key={entry.id}>
                    <td className="qm-check-cell"><input type="checkbox" checked={selectedBatchImportIds.includes(entry.id)} onChange={() => toggleBatchImportSelection(entry.id)} /></td>
                    <td>{entry.source_filename}</td>
                    <td>{entry.company_name || entry.suggested_company_name || '-'}</td>
                    <td>{entry.document_number || '-'}</td>
                    <td>{entry.document_date || '-'}</td>
                    <td>{entry.lines?.length || 0}</td>
                    <td><span className={`qm-badge status-${entry.status}`}>{entry.status}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="qm-action-row">
            <button type="button" className="qm-secondary" onClick={() => setActiveStep('companies')}>Review Companies</button>
            <button type="button" className="qm-secondary" onClick={() => setActiveStep('decisions')}>Review Product Decisions</button>
          </div>
        </>
      )}
    </div>
  );

  const renderCompanyStep = () => (
    <div className="qm-panel">
      <div className="qm-panel-heading">
        <div>
          <h3>Confirm Companies & Documents</h3>
          <p>Review batch-level document details. Only open individual files when a company, number, date, or duplicate warning needs attention.</p>
        </div>
        <button type="button" className="qm-secondary" onClick={() => setActiveStep('decisions')}>Continue to Product Decisions</button>
      </div>
      {!selectedBatch ? (
        <div className="qm-empty">Open a batch first.</div>
      ) : (
        <>
        <div className="qm-bulk-toolbar compact company-approval">
          <strong>{highConfidenceCompanyMatches.length} high-confidence company match(es)</strong>
          <span>Approve repeated company matches in one action, then review only exceptions.</span>
          <span className="qm-bulk-spacer" />
          <button
            type="button"
            className="qm-primary small"
            disabled={!highConfidenceCompanyMatches.length || Boolean(workingAction)}
            onClick={() => requestApplySuggestions(highConfidenceCompanyMatches.map((suggestion) => suggestion.id))}
          >
            Approve High-Confidence Companies
          </button>
        </div>
        <div className="qm-document-review-layout">
          <div className="qm-document-list-panel">
            <h4>Documents in this batch</h4>
            {selectedBatchImports.map((entry) => {
              const duplicateCheck = entry.duplicate_check || entry.parse_meta?.duplicate_check;
              const companySuggestion = companySuggestions.find((suggestion) => suggestion.historical_import === entry.id);
              return (
                <button
                  key={entry.id}
                  type="button"
                  className={`qm-document-list-row${selectedDocument?.id === entry.id ? ' active' : ''}`}
                  onClick={() => setSelectedDocumentId(entry.id)}
                >
                  <strong>{entry.source_filename}</strong>
                  <span>{entry.company_name || entry.suggested_company_name || 'Company not selected'}</span>
                  {companySuggestion && (
                    <span>AI thinks: {companySuggestion.suggested_company_name || companySuggestion.proposed_company_name || '-'} ({confidencePercent(companySuggestion.confidence)}%)</span>
                  )}
                  <small>{entry.document_number || '-'} - {entry.document_date || '-'}</small>
                  <em>{entry.lines?.length || 0} rows - {entry.company_name ? 'linked' : 'pending'}</em>
                  {duplicateCheck?.is_duplicate && <i>{duplicateCheck.blocking ? 'Exact duplicate' : 'Similar import'}</i>}
                </button>
              );
            })}
          </div>

          <div className="qm-document-detail-panel">
            {!selectedDocument ? (
              <div className="qm-empty compact">Select a document to review details.</div>
            ) : (() => {
              const entry = selectedDocument;
              const draft = draftForImport(entry);
              const duplicateCheck = entry.duplicate_check || entry.parse_meta?.duplicate_check;
              const match = duplicatePrimaryMatch(duplicateCheck);
              const relatedCompanySuggestions = companySuggestions.filter((suggestion) => suggestion.historical_import === entry.id);
              const mode = companyModeByImport[entry.id] || (relatedCompanySuggestions[0]?.action === 'create_new_company' ? 'create' : 'match');
              const hasDocumentChanges = importHasDraftChanges(entry);
              return (
                <>
                  <div className="qm-card-title-row">
                    <div>
                      <h4>{entry.source_filename}</h4>
                      <p>
                        {entry.lines?.length || 0} rows - <span className={`qm-badge status-${entry.status}`}>{entry.status}</span>
                        {entry.company_name && <span className="qm-badge success">Company linked: {entry.company_name}</span>}
                      </p>
                    </div>
                    <span className="qm-badge muted">Document #{entry.id}</span>
                  </div>
                  {actionFeedback[`import-${entry.id}`] && (
                    <div className={`qm-inline-feedback ${actionFeedback[`import-${entry.id}`].type}`}>
                      {actionFeedback[`import-${entry.id}`].message}
                    </div>
                  )}

                  {duplicateCheck?.is_duplicate && (
                    <div className="qm-notice warning compact">
                      <strong>{duplicateCheck.blocking ? 'Exact duplicate or blocking duplicate' : 'Similar previous import'}</strong>
                      <p>{duplicateCheck.message} {duplicateHelperText(duplicateCheck)}</p>
                      {match && (
                        <div className="qm-duplicate-match">
                          <span>Previous import: #{match.id}</span>
                          <span>File: {match.source_filename || '-'}</span>
                          <span>Company: {match.company_name || '-'}</span>
                          <span>Date: {match.document_date || '-'}</span>
                          <span>Document: {match.document_number || '-'}</span>
                          <span>Rows: {match.line_count ?? '-'}</span>
                          <span>Reason: {(match.messages || []).join(' ') || duplicateCheck.message}</span>
                          <div className="qm-duplicate-actions">
                            <button type="button" className="qm-secondary small" onClick={() => openDuplicateInspection(entry, duplicateCheck, 'previous')}>
                              View previous import
                            </button>
                            <button type="button" className="qm-secondary small" onClick={() => openDuplicateInspection(entry, duplicateCheck, 'compare')}>
                              Compare with current
                            </button>
                            <button
                              type="button"
                              className="qm-danger small"
                              disabled={entry.status === 'committed' || Boolean(workingAction)}
                              onClick={() => requestRemoveImportFromBatch(entry)}
                            >
                              {workingAction === `remove-import-${entry.id}` ? 'Removing...' : 'Remove from batch'}
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {relatedCompanySuggestions.length > 0 && (
                    <div className="qm-company-suggestions compact">
                      <h4>AI company decision</h4>
                      {relatedCompanySuggestions.map((suggestion) => {
                        const suggestionDraft = suggestionDrafts[suggestion.id] || {};
                        const actionable = isSuggestionActionable(suggestion);
                        const hasDraftChanges = suggestionHasDraftChanges(suggestion);
                        return (
                          <div key={suggestion.id} className={`qm-company-decision-row status-${effectiveSuggestionStatus(suggestion)}`}>
                            <div>
                              <strong>{ACTION_LABELS[suggestionDraft.action || suggestion.action]}</strong>
                              <small>{confidencePercent(suggestion.confidence)}% - {suggestion.reason || 'No reason provided.'}</small>
                            </div>
                            <span className={`qm-badge status-${effectiveSuggestionStatus(suggestion)}`}>{effectiveSuggestionStatus(suggestion)}</span>
                            <button type="button" className="qm-secondary small" disabled={!actionable || Boolean(workingAction) || !hasDraftChanges} onClick={() => saveSuggestionEdits(suggestion)}>
                              {hasDraftChanges ? 'Save' : 'Saved'}
                            </button>
                            <button type="button" className="qm-primary small" disabled={!actionable || Boolean(workingAction)} onClick={() => performApplySuggestions([suggestion.id])}>
                              {workingAction === 'apply' ? 'Applying...' : 'Apply company decision'}
                            </button>
                            {actionFeedback[`suggestion-${suggestion.id}`] && (
                              <div className={`qm-inline-feedback ${actionFeedback[`suggestion-${suggestion.id}`].type}`}>
                                {actionFeedback[`suggestion-${suggestion.id}`].message}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}

                  <div className="qm-company-mode">
                    <button type="button" className={mode === 'match' ? 'active' : ''} onClick={() => setCompanyModeByImport((current) => ({ ...current, [entry.id]: 'match' }))}>Match existing company</button>
                    <button type="button" className={mode === 'create' ? 'active' : ''} onClick={() => setCompanyModeByImport((current) => ({ ...current, [entry.id]: 'create' }))}>Create new company</button>
                  </div>

                  <div className="qm-details-grid relaxed">
                    {mode === 'match' ? (
                      <label>
                        <span className="qm-label-text">Selected existing company</span>
                        <select disabled={entry.status === 'committed'} value={draft.company || ''} onChange={(event) => updateImportDraft(entry.id, { company: event.target.value })}>
                          <option value="">Select company</option>
                          {companies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}
                        </select>
                      </label>
                    ) : (
                      <label>
                        <span className="qm-label-text">New company name</span>
                        <input disabled={entry.status === 'committed'} value={draft.suggested_company_name || ''} onChange={(event) => updateImportDraft(entry.id, { suggested_company_name: event.target.value })} />
                      </label>
                    )}
                    <label><span className="qm-label-text">Document number <small>(optional)</small></span>
                      <input
                        disabled={entry.status === 'committed'}
                        placeholder={`Uses HIST-${String(entry.id).padStart(6, '0')} if blank`}
                        value={draft.document_number || ''}
                        onChange={(event) => updateImportDraft(entry.id, { document_number: event.target.value })}
                      />
                    </label>
                    <label><span className="qm-label-text">Document date</span>
                      <input disabled={entry.status === 'committed'} type="date" value={draft.document_date || ''} onChange={(event) => updateImportDraft(entry.id, { document_date: event.target.value })} />
                    </label>
                    <label><span className="qm-label-text">Subtotal</span>
                      <input disabled={entry.status === 'committed'} type="number" step="0.01" value={draft.subtotal || ''} onChange={(event) => updateImportDraft(entry.id, { subtotal: event.target.value })} />
                    </label>
                    <label><span className="qm-label-text">VAT</span>
                      <input disabled={entry.status === 'committed'} type="number" step="0.01" value={draft.vat_total || ''} onChange={(event) => updateImportDraft(entry.id, { vat_total: event.target.value })} />
                    </label>
                    <label><span className="qm-label-text">Total</span>
                      <input disabled={entry.status === 'committed'} type="number" step="0.01" value={draft.total || ''} onChange={(event) => updateImportDraft(entry.id, { total: event.target.value })} />
                    </label>
                  </div>
                  <div className="qm-action-row">
                    <button type="button" className="qm-secondary" disabled={Boolean(workingAction) || entry.status === 'committed' || !hasDocumentChanges} onClick={() => saveImportDetails(entry)}>
                      {workingAction === `save-import-${entry.id}` ? 'Saving document...' : (hasDocumentChanges ? 'Save Document' : 'Document Saved')}
                    </button>
                    <button type="button" className="qm-primary" onClick={() => setActiveStep('decisions')}>Review this document's Products</button>
                  </div>
                </>
              );
            })()}
          </div>
        </div>
        </>
      )}
    </div>
  );

  const renderDecisionCard = (suggestion) => {
    const draft = suggestionDrafts[suggestion.id] || {};
    const lineDraft = suggestion.line ? (lineDrafts[suggestion.line] || emptyLineDraft) : emptyLineDraft;
    const displayStatus = effectiveSuggestionStatus(suggestion);
    const actionable = isSuggestionActionable(suggestion);
    const locked = !actionable;
    const hasDraftChanges = suggestionHasDraftChanges(suggestion);
    const priceSummary = suggestion.price_history_summary || {};
    const lineBlockers = cleanBlockers(suggestion.line_ready_blockers);
    const importBlockers = cleanBlockers(suggestion.import_commit_blockers).filter((blocker) => blocker !== 'already committed');
    const lineReady = suggestion.line_status === 'ready' || (!lineBlockers.length && suggestion.status === 'applied');
    const decisionApplied = suggestion.status === 'applied' || displayStatus === 'committed';
    const readinessLabel = displayStatus === 'committed'
      ? 'committed'
      : lineReady
        ? 'ready'
        : (suggestion.line_status || 'needs_review');
    return (
      <div key={suggestion.id} className={`qm-decision-card status-${displayStatus}`}>
        <div className="qm-decision-card-main">
          <label className="qm-check-label">
            <input type="checkbox" checked={selectedSuggestionIds.includes(suggestion.id)} disabled={locked} onChange={() => toggleSuggestionSelection(suggestion.id)} />
            <span />
          </label>
          <div className="qm-decision-source">
            <strong>{suggestion.line_item_name || suggestion.proposed_company_name || 'Document suggestion'}</strong>
            <small>{suggestion.historical_import_filename} - {suggestion.historical_import_company_name || '-'}</small>
            <div className="qm-decision-meta">
              <span>{lineDraft.quantity || '-'} {lineDraft.unit || ''}</span>
              <span>Imported price {formatMoney(lineDraft.unit_price)}</span>
              <span>Total {lineDraft.line_total || '-'}</span>
              <span className={`qm-badge ${confidencePercent(suggestion.confidence) >= 85 ? 'success' : 'muted'}`}>{confidencePercent(suggestion.confidence)}%</span>
              <span className={`qm-badge status-${displayStatus}`}>{displayStatus}</span>
            </div>
          </div>
        </div>

        {actionable && (lineBlockers.length > 0 || importBlockers.length > 0) && (
          <div className="qm-inline-feedback warning">
            <strong>Before commit:</strong>{' '}
            {lineBlockers.length > 0 && <>Fix row: {lineBlockers.join(', ')}. </>}
            {importBlockers.length > 0 && <>Finish document: {importBlockers.join(', ')}.</>}
          </div>
        )}

        {!actionable && (
          <div className={`qm-applied-state status-${displayStatus}`}>
            <strong>{displayStatus === 'applied' ? 'Decision applied' : displayStatus}</strong>
            <span>
              {displayStatus === 'committed' && 'This source row is already committed to price history. No more approval is needed.'}
              {displayStatus === 'duplicate' && 'This source row is duplicate/blocked and cannot be approved.'}
              {suggestion.line_status === 'ready' && importBlockers.length === 0 && 'Row is ready for price history commit.'}
              {suggestion.line_status === 'ready' && importBlockers.length > 0 && `Product decision is approved and the row is ready. Finish document details before commit: ${importBlockers.join(', ')}.`}
              {suggestion.line_status === 'skipped' && 'Row is skipped and will not be committed.'}
              {suggestion.line_status === 'needs_review' && lineBlockers.length > 0 && `Row still needs review: ${lineBlockers.join(', ')}.`}
              {suggestion.line_status === 'needs_review' && !lineBlockers.length && 'Row mapping is approved, but the batch needs a refresh before commit.'}
              {suggestion.error_message && ` ${suggestion.error_message}`}
            </span>
          </div>
        )}
        {actionFeedback[`suggestion-${suggestion.id}`] && (
          <div className={`qm-inline-feedback ${actionFeedback[`suggestion-${suggestion.id}`].type}`}>
            {actionFeedback[`suggestion-${suggestion.id}`].message}
          </div>
        )}

        <div className="qm-decision-edit-grid">
          <label><span className="qm-label-text">Decision</span>
            <select disabled={locked} value={draft.action || suggestion.action} onChange={(event) => updateSuggestionDraft(suggestion.id, { action: event.target.value })}>
              <option value="match_existing_product">Existing Product</option>
              <option value="create_company_alias">Company alias</option>
              <option value="create_new_product">New draft Product</option>
              <option value="needs_manual_review">Needs manual review</option>
              <option value="skip">Skip/noise</option>
            </select>
          </label>
          {(draft.action || suggestion.action) === 'create_new_product' ? (
            <>
              <label><span className="qm-label-text">New Product name</span>
                <input disabled={locked} value={draft.proposed_product_name || ''} onChange={(event) => updateSuggestionDraft(suggestion.id, { proposed_product_name: event.target.value })} />
              </label>
              <label><span className="qm-label-text">Pack / unit</span>
                <input disabled={locked} value={draft.proposed_pack_size || ''} onChange={(event) => updateSuggestionDraft(suggestion.id, { proposed_pack_size: event.target.value })} />
              </label>
              <label><span className="qm-label-text">Dosage</span>
                <input disabled={locked} value={draft.proposed_dosage || ''} onChange={(event) => updateSuggestionDraft(suggestion.id, { proposed_dosage: event.target.value })} />
              </label>
            </>
          ) : (
            <>
              <label><span className="qm-label-text">Target Product</span>
                <select disabled={locked || (draft.action || suggestion.action) === 'skip' || (draft.action || suggestion.action) === 'needs_manual_review'} value={draft.suggested_product || ''} onChange={(event) => updateSuggestionDraft(suggestion.id, { suggested_product: event.target.value || '' })}>
                  <option value="">Select Product</option>
                  {items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
              </label>
              <label><span className="qm-label-text">Alias text</span>
                <input disabled={locked || (draft.action || suggestion.action) !== 'create_company_alias'} value={draft.alias_text || ''} onChange={(event) => updateSuggestionDraft(suggestion.id, { alias_text: event.target.value })} />
              </label>
            </>
          )}
        </div>

        {suggestion.line && (
          <div className="qm-line-edit-grid">
            <label><span className="qm-label-text">Imported item</span>
              <input value={lineDraft.item_name || ''} disabled={suggestion.line_status === 'committed'} onChange={(event) => updateLineDraft(suggestion.line, { item_name: event.target.value })} />
            </label>
            <label><span className="qm-label-text">Qty</span>
              <input type="number" step="0.001" value={lineDraft.quantity || ''} disabled={suggestion.line_status === 'committed'} onChange={(event) => updateLineDraft(suggestion.line, { quantity: event.target.value })} />
            </label>
            <label><span className="qm-label-text">Unit</span>
              <input value={lineDraft.unit || ''} disabled={suggestion.line_status === 'committed'} onChange={(event) => updateLineDraft(suggestion.line, { unit: event.target.value })} />
            </label>
            <label><span className="qm-label-text">Unit price</span>
              <input type="number" step="0.01" value={lineDraft.unit_price || ''} disabled={suggestion.line_status === 'committed'} onChange={(event) => updateLineDraft(suggestion.line, { unit_price: event.target.value })} />
            </label>
            <label><span className="qm-label-text">VAT</span>
              <input type="number" step="0.01" value={lineDraft.vat_amount || ''} disabled={suggestion.line_status === 'committed'} onChange={(event) => updateLineDraft(suggestion.line, { vat_amount: event.target.value })} />
            </label>
            <label><span className="qm-label-text">Total</span>
              <input type="number" step="0.01" value={lineDraft.line_total || ''} disabled={suggestion.line_status === 'committed'} onChange={(event) => updateLineDraft(suggestion.line, { line_total: event.target.value })} />
            </label>
            <div className="qm-derived-status">
              <span className="qm-label-text">Commit readiness</span>
              <span className={`qm-badge status-${readinessLabel}`}>
                {readinessLabel.replace('_', ' ')}
              </span>
              <small>
                {decisionApplied && lineReady && importBlockers.length === 0 && 'Ready to commit.'}
                {decisionApplied && lineReady && importBlockers.length > 0 && `Decision approved. Finish document details: ${importBlockers.join(', ')}.`}
                {decisionApplied && !lineReady && lineBlockers.length > 0 && `Decision approved, but row needs: ${lineBlockers.join(', ')}.`}
                {!decisionApplied && lineBlockers.length === 0 && importBlockers.length === 0 && 'Apply This approves the decision and marks this row ready.'}
                {!decisionApplied && (lineBlockers.length > 0 || importBlockers.length > 0) && 'Apply approves the product decision; remaining blockers are shown above.'}
              </small>
            </div>
          </div>
        )}

        <div className="qm-price-context">
          <div>
            <span>Previous company price</span>
            <strong>{priceSummary.last_company_price ? formatMoney(priceSummary.last_company_price) : '-'}</strong>
            <small>{priceSummary.last_company_price_date || priceSummary.message || 'No previous company price found.'}</small>
          </div>
          <div>
            <span>Difference</span>
            <strong>{priceSummary.price_difference ? formatMoney(priceSummary.price_difference) : '-'}</strong>
            <small>{priceSummary.price_difference_percent ? `${priceSummary.price_difference_percent}%` : `${priceSummary.recent_company_price_count || 0} recent price(s)`}</small>
          </div>
          <div>
            <span>Product base price</span>
            <strong>{formatMoney(priceSummary.product_base_price)}</strong>
            <small>{priceSummary.variance_warning || 'Compare before approving.'}</small>
          </div>
        </div>

        <details className="qm-decision-reason">
          <summary>AI reason and candidates</summary>
          <p>{suggestion.reason || suggestion.error_message || 'No reason provided.'}</p>
          {(suggestion.candidate_products || []).length > 0 && (
            <div className="qm-candidate-list">
              {suggestion.candidate_products.map((candidate) => (
                <span key={candidate.id}>{candidate.name}{candidate.pack_size ? ` - ${candidate.pack_size}` : ''}</span>
              ))}
            </div>
          )}
        </details>

        <div className="qm-action-row">
          <span className="qm-action-help">Save Edits only updates review fields. Apply This approves the mapping and marks valid rows ready.</span>
          <button type="button" className="qm-secondary small" onClick={() => openSourceContext(suggestion)}>View Source</button>
          <button type="button" className="qm-secondary small" disabled={locked || Boolean(workingAction) || !hasDraftChanges} onClick={() => saveSuggestionEdits(suggestion)}>
            {workingAction === `save-suggestion-${suggestion.id}` ? 'Saving...' : (hasDraftChanges ? 'Save Edits' : 'Saved')}
          </button>
          <button type="button" className="qm-primary small" disabled={locked || Boolean(workingAction)} onClick={() => performApplySuggestions([suggestion.id])}>
            {workingAction === 'apply' ? 'Applying...' : 'Apply This'}
          </button>
        </div>
      </div>
    );
  };

  const renderDecisionGroup = (key, title, helper, groupSuggestions, options = {}) => {
    const isCollapsed = key === 'skip' && !expandedGroups.skip;
    const pending = groupSuggestions.filter((suggestion) => isSuggestionActionable(suggestion));
    const highConfidence = pending.filter((suggestion) => confidencePercent(suggestion.confidence) >= 85);
    const visibleLimit = groupLimits[key] || 25;
    const visibleSuggestions = groupSuggestions.slice(0, visibleLimit);
    const groupActionButtons = (
      <>
        <button type="button" className="qm-secondary small" disabled={!pending.length} onClick={() => selectSuggestionGroup(pending)}>Select Pending</button>
        <button type="button" className="qm-primary small" disabled={!highConfidence.length || Boolean(workingAction)} onClick={() => requestApplySuggestions(highConfidence.map((suggestion) => suggestion.id))}>
          Approve High Confidence
        </button>
      </>
    );
    return (
      <div className={`qm-decision-group group-${key}`} key={key}>
        <div className="qm-decision-group-header">
          <div>
            <h4>{title}</h4>
            <p>{helper}</p>
          </div>
          <div className="qm-decision-group-actions">
            <span className="qm-badge muted">{groupSuggestions.length} rows</span>
            {pending.length > 0 && <span className="qm-badge status-pending">{pending.length} pending</span>}
            {highConfidence.length > 0 && <span className="qm-badge success">{highConfidence.length} high confidence</span>}
            {key === 'skip' ? (
              <button type="button" className="qm-secondary small" onClick={() => setExpandedGroups((current) => ({ ...current, skip: !current.skip }))}>
                {isCollapsed ? 'Show skipped rows' : 'Hide skipped rows'}
              </button>
            ) : (
              groupActionButtons
            )}
          </div>
        </div>
        {!isCollapsed && (
          <div className={options.compact ? 'qm-decision-list compact' : 'qm-decision-list'}>
            {visibleSuggestions.map(renderDecisionCard)}
            {!groupSuggestions.length && <div className="qm-empty compact">No rows in this group.</div>}
            {groupSuggestions.length > visibleSuggestions.length && (
              <button type="button" className="qm-secondary" onClick={() => setGroupLimits((current) => ({ ...current, [key]: visibleLimit + 25 }))}>
                Show 25 more ({groupSuggestions.length - visibleSuggestions.length} remaining)
              </button>
            )}
            {key !== 'skip' && pending.length > 0 && (
              <div className="qm-decision-group-footer">
                <span>{pending.length} pending in this group</span>
                {groupActionButtons}
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  const renderDecisionStep = () => (
    <div className="qm-panel">
      <div className="qm-panel-heading">
        <div>
          <h3>Review Product Decisions</h3>
          <p>Review AI decisions by type. Unmatched rows should become Product matches, company aliases, new draft Products, manual review, or skips.</p>
        </div>
        <div className="qm-action-row">
          <select className="qm-input" value={decisionFilter} onChange={(event) => setDecisionFilter(event.target.value)}>
            <option value="all">All decision groups</option>
            <option value="match_existing_product">Existing Product matches</option>
            <option value="create_company_alias">Suggested aliases</option>
            <option value="create_new_product">New draft Products</option>
            <option value="needs_manual_review">Needs manual review</option>
            <option value="skip">Skipped/noise rows</option>
          </select>
          <select className="qm-input" value={decisionCompanyFilter} onChange={(event) => setDecisionCompanyFilter(event.target.value)}>
            <option value="all">All companies</option>
            {decisionCompanyOptions.map((companyName) => <option key={companyName} value={companyName}>{companyName}</option>)}
          </select>
          <select className="qm-input" value={decisionFileFilter} onChange={(event) => setDecisionFileFilter(event.target.value)}>
            <option value="all">All files</option>
            {decisionFileOptions.map((filename) => <option key={filename} value={filename}>{filename}</option>)}
          </select>
          <select className="qm-input" value={decisionConfidenceFilter} onChange={(event) => setDecisionConfidenceFilter(event.target.value)}>
            <option value="all">All confidence</option>
            <option value="high">High confidence</option>
            <option value="low">Needs closer review</option>
            <option value="unresolved">Pending/conflict only</option>
          </select>
          <button type="button" className="qm-secondary" onClick={() => setActiveStep('commit')}>Final Review</button>
        </div>
      </div>
      {!selectedBatch ? (
        <div className="qm-empty">Open a batch first.</div>
      ) : (
        <>
          <div className="qm-bulk-toolbar selection-toolbar">
            <strong>{selectedSuggestionIds.length} AI decisions selected</strong>
            <button type="button" className="qm-secondary small" disabled={!selectedSuggestionIds.length} onClick={() => setSelectedSuggestionIds([])}>Clear</button>
            <span className="qm-bulk-spacer" />
            <button type="button" className="qm-primary small" disabled={!selectedSuggestionIds.length || Boolean(workingAction)} onClick={() => requestApplySuggestions()}>Apply Selected</button>
            <button type="button" className="qm-secondary small" disabled={!selectedSuggestionIds.length || Boolean(workingAction)} onClick={() => markLinesReady(suggestions.filter((suggestion) => selectedSuggestionIds.includes(suggestion.id)).map((suggestion) => suggestion.line).filter(Boolean))}>Mark Selected Rows Ready</button>
          </div>
          {Object.entries(filteredDecisionGroups).map(([key, group]) => {
            if (key === 'match_existing_product') return renderDecisionGroup(key, 'Existing Product matches', 'AI believes these source rows already map to Products in the catalog.', group);
            if (key === 'create_company_alias') return renderDecisionGroup(key, 'Suggested company aliases', 'AI believes these are customer-specific names for existing Products.', group);
            if (key === 'create_new_product') return renderDecisionGroup(key, 'Suggested new draft/internal Products', 'AI believes these are real Products missing from the catalog. Approved Products are created as draft/internal.', group);
            if (key === 'needs_manual_review') return renderDecisionGroup(key, 'AI could not confidently decide these items', 'Fix these rows manually by selecting a Product, alias, new Product, skip, or ready status.', group);
            if (key === 'skip') return renderDecisionGroup(key, 'Skipped / noise rows', 'Headers, totals, footers, and non-item rows are collapsed here for traceability.', group, { compact: true });
            return null;
          })}
        </>
      )}
    </div>
  );

  const renderCommitStep = () => (
    <div className="qm-panel">
      <div className="qm-panel-heading">
        <div>
          <h3>Final Review & Commit</h3>
          <p>Nothing durable is committed until this step. Document number is optional; if blank, a stable HIST import reference is used.</p>
        </div>
        <button type="button" className="qm-primary" disabled={!selectedBatch || workingAction === 'commit' || !(selectedReadyRowCount > 0)} onClick={commitSelectedBatchImports}>
          {workingAction === 'commit' ? 'Committing...' : 'Commit Approved Rows to Price History'}
        </button>
      </div>
      {!selectedBatch ? (
        <div className="qm-empty">Open a batch first.</div>
      ) : (
        <>
          {renderBatchSummary()}
          <div className="qm-final-review-grid">
            <div><span>Documents ready</span><strong>{statValue(wizardSummary, 'company_ready_count', 0)} / {selectedBatchImports.length}</strong></div>
            <div><span>Rows ready</span><strong>{lineCounts.ready || 0}</strong></div>
            <div><span>Aliases to create/apply</span><strong>{pendingActionCounts.create_company_alias || 0}</strong></div>
            <div><span>New draft Products to create</span><strong>{pendingActionCounts.create_new_product || 0}</strong></div>
            <div><span>Companies to create/apply</span><strong>{companySuggestions.filter((suggestion) => suggestion.status === 'pending').length}</strong></div>
            <div><span>Skipped rows</span><strong>{lineCounts.skipped || pendingActionCounts.skip || 0}</strong></div>
            <div><span>Unresolved</span><strong>{wizardSummary.unresolved_count || 0}</strong></div>
            <div><span>Committed rows</span><strong>{lineCounts.committed || 0}</strong></div>
          </div>
          <div className="qm-table-wrap">
            <table className="qm-table">
              <thead>
                <tr>
                  <th className="qm-check-cell"><input type="checkbox" checked={allBatchImportsSelected} onChange={toggleAllBatchImports} /></th>
                  <th>File</th>
                  <th>Company</th>
                  <th>Document</th>
                  <th>Rows</th>
                  <th>Ready</th>
                  <th>Needs Review</th>
                  <th>Commit Check</th>
                  <th>Fix</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {selectedBatchImports.map((entry) => {
                  const ready = (entry.lines || []).filter((line) => line.status === 'ready').length;
                  const needsReview = (entry.lines || []).filter((line) => line.status === 'needs_review').length;
                  const commitInfo = commitBlockers.find((item) => item.import_id === entry.id);
                  return (
                    <tr key={entry.id}>
                      <td className="qm-check-cell"><input type="checkbox" checked={selectedBatchImportIds.includes(entry.id)} onChange={() => toggleBatchImportSelection(entry.id)} /></td>
                      <td>{entry.source_filename}</td>
                      <td>{entry.company_name || entry.suggested_company_name || '-'}</td>
                      <td>{entry.document_number || '-'}</td>
                      <td>{entry.lines?.length || 0}</td>
                      <td>{ready}</td>
                      <td>{needsReview}</td>
                      <td>
                        {commitInfo?.can_commit ? (
                          <span className="qm-badge success">Ready</span>
                        ) : (
                          <span className="qm-blocker-text">{(commitInfo?.blockers || ['not ready']).join(', ')}</span>
                        )}
                      </td>
                      <td>
                        {(commitInfo?.blockers || []).includes('missing company') || (commitInfo?.blockers || []).includes('missing document date') ? (
                          <button
                            type="button"
                            className="qm-secondary small"
                            onClick={() => {
                              setSelectedDocumentId(entry.id);
                              setActiveStep('companies');
                            }}
                          >
                            Fix details
                          </button>
                        ) : (commitInfo?.blockers || []).includes('no ready rows') ? (
                          <button type="button" className="qm-secondary small" onClick={() => setActiveStep('decisions')}>Review rows</button>
                        ) : (
                          <span className="qm-muted">-</span>
                        )}
                      </td>
                      <td><span className={`qm-badge status-${entry.status}`}>{entry.status}</span></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );

  const activeStepConfig = STEPS.find((step) => step.id === activeStep) || STEPS[0];

  return (
    <div className="qm-section historical-wizard">
      <div className="qm-panel qm-wizard-hero">
        <div>
          <h3>{activeStepConfig.title}</h3>
          <p>Upload files, let AI suggest decisions, approve only what staff trusts, then commit ready rows to price history.</p>
        </div>
        {selectedBatch && <span className={`qm-badge status-${selectedBatch.status}`}>{selectedBatch.name || `Batch #${selectedBatch.id}`}</span>}
      </div>

      {renderStepHeader()}
      {notice && <div className={`qm-feedback ${notice.type || 'success'}`}>{notice.message}</div>}
      {errorInfo && <QuotationErrorNotice errorInfo={errorInfo} onDismiss={() => setErrorInfo(null)} />}
      {confirmAction && (
        <div className="qm-modal-backdrop" role="presentation">
          <div className="qm-confirm-modal" role="dialog" aria-modal="true" aria-label={confirmAction.title}>
            <h3>{confirmAction.title}</h3>
            <p>{confirmAction.body}</p>
            {confirmAction.details?.length > 0 && (
              <ul>
                {confirmAction.details.map((detail) => <li key={detail}>{detail}</li>)}
              </ul>
            )}
            <div className="qm-action-row">
              <button type="button" className="qm-secondary" onClick={() => setConfirmAction(null)}>Cancel</button>
              <button type="button" className="qm-primary" onClick={confirmAction.onConfirm}>{confirmAction.confirmLabel || 'Confirm'}</button>
            </div>
          </div>
        </div>
      )}
      {sourceModal && (
        <div className="qm-modal-backdrop" role="presentation">
          <div className="qm-source-modal" role="dialog" aria-modal="true" aria-label="Source quotation preview">
            <div className="qm-card-title-row">
              <div>
                <h3>Source quotation evidence</h3>
                <p>{sourceModal.context?.filename || 'Historical source'} - page {sourceModal.context?.page_number || 1}, row {sourceModal.context?.source_row || '-'}</p>
              </div>
              <button type="button" className="qm-secondary small" onClick={closeSourceModal}>Close</button>
            </div>
            <div className="qm-source-context-grid">
              <div className="qm-source-preview">
                {sourceModal.loading && <div className="qm-loading">Loading source preview...</div>}
                {!sourceModal.loading && sourceModal.imageUrl && <img src={sourceModal.imageUrl} alt="Source PDF page preview" />}
                {!sourceModal.loading && !sourceModal.imageUrl && <div className="qm-empty compact">{sourceModal.error || 'Source preview unavailable.'}</div>}
              </div>
              <div className="qm-source-row-context">
                <h4>{sourceModal.title}</h4>
                <p>{sourceModal.context?.raw_line || 'No extracted row context was stored.'}</p>
                <small>Use the source page and row text to verify pack size, dosage, quantity, and price before approval.</small>
              </div>
            </div>
          </div>
        </div>
      )}
      {duplicateModal && (
        <div className="qm-modal-backdrop" role="presentation">
          <div className="qm-confirm-modal qm-duplicate-modal" role="dialog" aria-modal="true" aria-label="Duplicate import details">
            <div className="qm-card-title-row">
              <div>
                <h3>{duplicateModal.mode === 'previous' ? 'Previous import details' : 'Compare duplicate warning'}</h3>
                <p>{duplicateModal.duplicateCheck?.blocking ? 'Exact duplicate or blocking duplicate' : 'Similar historical import'} - {duplicateModal.duplicateCheck?.message}</p>
              </div>
              <button type="button" className="qm-secondary small" onClick={() => setDuplicateModal(null)}>Close</button>
            </div>
            <div className="qm-duplicate-compare-grid">
              <div>
                <h4>Current upload</h4>
                <dl>
                  <dt>Import ID</dt><dd>#{duplicateModal.current?.id}</dd>
                  <dt>File</dt><dd>{duplicateModal.current?.source_filename || '-'}</dd>
                  <dt>Company</dt><dd>{duplicateModal.current?.company_name || duplicateModal.current?.suggested_company_name || '-'}</dd>
                  <dt>Date</dt><dd>{duplicateModal.current?.document_date || '-'}</dd>
                  <dt>Document</dt><dd>{duplicateModal.current?.document_number || '-'}</dd>
                  <dt>Rows</dt><dd>{duplicateModal.current?.lines?.length ?? '-'}</dd>
                </dl>
              </div>
              <div>
                <h4>Previous import</h4>
                <dl>
                  <dt>Import ID</dt><dd>#{duplicateModal.match?.id}</dd>
                  <dt>File</dt><dd>{duplicateModal.match?.source_filename || '-'}</dd>
                  <dt>Company</dt><dd>{duplicateModal.match?.company_name || '-'}</dd>
                  <dt>Date</dt><dd>{duplicateModal.match?.document_date || '-'}</dd>
                  <dt>Document</dt><dd>{duplicateModal.match?.document_number || '-'}</dd>
                  <dt>Rows</dt><dd>{duplicateModal.match?.line_count ?? '-'}</dd>
                </dl>
              </div>
            </div>
            <div className="qm-helper compact">
              Reason: {(duplicateModal.match?.messages || []).join(' ') || duplicateModal.duplicateCheck?.message || 'Matched by duplicate detection.'}
            </div>
          </div>
        </div>
      )}

      {activeStep === 'upload' && renderUploadStep()}
      {activeStep === 'analyze' && renderAnalyzeStep()}
      {activeStep === 'companies' && renderCompanyStep()}
      {activeStep === 'decisions' && renderDecisionStep()}
      {activeStep === 'commit' && renderCommitStep()}
    </div>
  );
};

export default HistoricalImportManager;
