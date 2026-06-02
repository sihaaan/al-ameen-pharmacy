import React, { useEffect, useMemo, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import CompanySelectWithCreate from './CompanySelectWithCreate';
import QuotationErrorNotice from './QuotationErrorNotice';

const statusOptions = [
  { value: 'needs_review', label: 'Needs Review' },
  { value: 'ready', label: 'Ready' },
  { value: 'skipped', label: 'Skipped' },
];

const confidencePercent = (value) => {
  const numeric = Number(value || 0);
  if (!Number.isFinite(numeric)) return 0;
  return numeric <= 1 ? Math.round(numeric * 100) : Math.round(numeric);
};

const HistoricalImportManager = () => {
  const [companies, setCompanies] = useState([]);
  const [items, setItems] = useState([]);
  const [imports, setImports] = useState([]);
  const [batches, setBatches] = useState([]);
  const [selectedImport, setSelectedImport] = useState(null);
  const [selectedBatch, setSelectedBatch] = useState(null);
  const [uploadFile, setUploadFile] = useState(null);
  const [batchFiles, setBatchFiles] = useState([]);
  const [batchUploading, setBatchUploading] = useState(false);
  const [batchProgress, setBatchProgress] = useState([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [bulkAction, setBulkAction] = useState('');
  const [selectedRowIds, setSelectedRowIds] = useState([]);
  const [rowFilter, setRowFilter] = useState('all');
  const [rowSearch, setRowSearch] = useState('');
  const [expandedRawRows, setExpandedRawRows] = useState({});
  const [notice, setNotice] = useState(null);
  const [errorInfo, setErrorInfo] = useState(null);
  const [previewUrl, setPreviewUrl] = useState('');
  const [headerDirty, setHeaderDirty] = useState(false);
  const [duplicateUploadWarning, setDuplicateUploadWarning] = useState(null);
  const [aiCleaning, setAiCleaning] = useState(false);
  const [applyingAiRows, setApplyingAiRows] = useState(false);
  const [aiCandidate, setAiCandidate] = useState(null);
  const [selectedBatchImportIds, setSelectedBatchImportIds] = useState([]);
  const [suggestions, setSuggestions] = useState([]);
  const [selectedSuggestionIds, setSelectedSuggestionIds] = useState([]);
  const [suggestionFilter, setSuggestionFilter] = useState('all');
  const [suggestionAction, setSuggestionAction] = useState('');

  const load = async () => {
    setLoading(true);
    setErrorInfo(null);
    try {
      const [companiesRes, itemsRes, importsRes, batchesRes] = await Promise.all([
        quotationAPI.companies.list({ active: 'true' }),
        quotationAPI.items.list({ active: 'true' }),
        quotationAPI.historicalImports.list(),
        quotationAPI.historicalImportBatches.list(),
      ]);
      setCompanies(companiesRes.data);
      setItems(itemsRes.data);
      setImports(importsRes.data);
      setBatches(batchesRes.data);
      if (selectedImport) {
        const refreshed = importsRes.data.find((entry) => entry.id === selectedImport.id);
        if (refreshed) setSelectedImport(refreshed);
      }
      if (selectedBatch) {
        const refreshedBatch = batchesRes.data.find((entry) => entry.id === selectedBatch.id);
        if (refreshedBatch) setSelectedBatch(refreshedBatch);
      }
    } catch (error) {
      const details = await describeQuotationError(
        error,
        'Load historical imports',
        'GET /quotations/historical-imports/, /quotations/historical-import-batches/'
      );
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    setSelectedRowIds([]);
    setExpandedRawRows({});
    setRowFilter('all');
    setRowSearch('');
    setAiCandidate(null);
  }, [selectedImport?.id]);

  useEffect(() => {
    setSelectedBatchImportIds([]);
    setSelectedSuggestionIds([]);
    setSuggestions([]);
    if (selectedBatch?.id) loadSuggestions(selectedBatch.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedBatch?.id]);

  const selectedSummary = useMemo(() => {
    const lines = selectedImport?.lines || [];
    return {
      total: lines.length,
      ready: lines.filter((line) => line.status === 'ready').length,
      needsReview: lines.filter((line) => line.status === 'needs_review').length,
      skipped: lines.filter((line) => line.status === 'skipped').length,
      committed: lines.filter((line) => line.status === 'committed').length,
      duplicates: lines.filter((line) => line.status === 'duplicate').length,
    };
  }, [selectedImport]);

  const selectedDuplicateCheck = selectedImport?.duplicate_check || selectedImport?.parse_meta?.duplicate_check || null;

  const selectedBatchImports = useMemo(() => selectedBatch?.imports || [], [selectedBatch]);
  const visibleBatchImportIds = useMemo(() => selectedBatchImports.map((entry) => entry.id), [selectedBatchImports]);
  const allBatchImportsSelected = visibleBatchImportIds.length > 0 && visibleBatchImportIds.every((id) => selectedBatchImportIds.includes(id));

  const filteredSuggestions = useMemo(() => {
    return suggestions.filter((suggestion) => {
      if (suggestionFilter === 'all') return true;
      if (suggestionFilter === 'uncertain') return suggestion.action === 'needs_manual_review' || suggestion.status === 'conflict';
      if (suggestionFilter === 'high_confidence') return confidencePercent(suggestion.confidence) >= 85 && suggestion.status === 'pending';
      return suggestion.action === suggestionFilter || suggestion.status === suggestionFilter;
    });
  }, [suggestions, suggestionFilter]);
  const visibleSuggestionIds = useMemo(() => filteredSuggestions.map((suggestion) => suggestion.id), [filteredSuggestions]);
  const allSuggestionsSelected = visibleSuggestionIds.length > 0 && visibleSuggestionIds.every((id) => selectedSuggestionIds.includes(id));

  const duplicateHelperText = (duplicateCheck) => {
    if (!duplicateCheck?.is_duplicate) return '';
    if (duplicateCheck.blocking || duplicateCheck.blocked_new_import) return 'No duplicate import was created.';
    return 'Please review before continuing.';
  };

  const loadSuggestions = async (batchId = selectedBatch?.id) => {
    if (!batchId) return;
    try {
      const response = await quotationAPI.historicalImportAiSuggestions.list({ batch: batchId });
      setSuggestions(response.data);
    } catch (error) {
      const details = await describeQuotationError(error, 'Load AI suggestions', 'GET /quotations/historical-import-ai-suggestions/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    }
  };

  const selectBatch = async (batch) => {
    setSelectedBatch(batch);
    setNotice(null);
    setErrorInfo(null);
    await loadSuggestions(batch.id);
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

  const toggleSuggestionSelection = (suggestionId) => {
    setSelectedSuggestionIds((current) => (
      current.includes(suggestionId)
        ? current.filter((candidate) => candidate !== suggestionId)
        : [...current, suggestionId]
    ));
  };

  const toggleAllSuggestions = () => {
    setSelectedSuggestionIds((current) => {
      if (allSuggestionsSelected) {
        return current.filter((id) => !visibleSuggestionIds.includes(id));
      }
      return Array.from(new Set([...current, ...visibleSuggestionIds]));
    });
  };

  const uploadBatchFiles = async () => {
    if (batchUploading || !batchFiles.length) return;
    const files = batchFiles.slice(0, 25);
    if (batchFiles.length > 25) {
      setNotice({ type: 'warning', message: 'Only the first 25 PDFs are processed in one batch for safety.' });
    }
    setBatchUploading(true);
    setErrorInfo(null);
    setDuplicateUploadWarning(null);
    const initialProgress = files.map((file) => ({ filename: file.name, status: 'queued', message: '' }));
    setBatchProgress(initialProgress);
    try {
      const batchName = `Historical batch ${new Date().toLocaleString()}`;
      const batchResponse = await quotationAPI.historicalImportBatches.create({ name: batchName });
      let currentBatch = batchResponse.data;
      setSelectedBatch(currentBatch);
      for (let index = 0; index < files.length; index += 1) {
        const file = files[index];
        setBatchProgress((current) => current.map((entry, entryIndex) => (
          entryIndex === index ? { ...entry, status: 'parsing', message: 'Parsing...' } : entry
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
          if (response.data.import && response.data.status !== 'duplicate') {
            setSelectedImport(response.data.import);
          }
        } catch (error) {
          const details = await describeQuotationError(error, `Parse ${file.name}`, `POST /quotations/historical-import-batches/${currentBatch.id}/upload_file/`);
          setBatchProgress((current) => current.map((entry, entryIndex) => (
            entryIndex === index ? { ...entry, status: 'failed', message: details.detail || 'Upload failed' } : entry
          )));
        }
      }
      setNotice({ type: 'success', message: 'Batch upload finished. Review parsed files and run AI suggestions when ready.' });
      await load();
      await loadSuggestions(currentBatch.id);
    } catch (error) {
      const details = await describeQuotationError(error, 'Create historical batch', 'POST /quotations/historical-import-batches/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setBatchUploading(false);
    }
  };

  const filteredRows = useMemo(() => {
    const term = rowSearch.trim().toLowerCase();
    return (selectedImport?.lines || []).filter((line) => {
      const unmatched = !line.product;
      const hasError = Boolean(line.duplicate_reason);
      const statusMatch =
        rowFilter === 'all' ||
        line.status === rowFilter ||
        (rowFilter === 'unmatched' && unmatched) ||
        (rowFilter === 'errors' && hasError);
      const searchMatch = !term ||
        (line.item_name || '').toLowerCase().includes(term) ||
        (line.product_name || line.quote_item_name || '').toLowerCase().includes(term);
      return statusMatch && searchMatch;
    });
  }, [selectedImport, rowFilter, rowSearch]);

  const visibleRowIds = useMemo(() => filteredRows.map((line) => line.id), [filteredRows]);
  const allVisibleSelected = visibleRowIds.length > 0 && visibleRowIds.every((rowId) => selectedRowIds.includes(rowId));

  const rememberCompany = (company) => {
    setCompanies((current) => {
      const withoutDuplicate = current.filter((candidate) => candidate.id !== company.id);
      return [...withoutDuplicate, company].sort((a, b) => a.name.localeCompare(b.name));
    });
  };

  const uploadHistoricalPdf = async () => {
    if (uploading || !uploadFile) return;
    setUploading(true);
    setNotice(null);
    setErrorInfo(null);
    setDuplicateUploadWarning(null);
    const formData = new FormData();
    formData.append('file', uploadFile);
    try {
      const response = await quotationAPI.historicalImports.parseFile(formData);
      const duplicateCheck = response.data.duplicate_check || response.data.parse_meta?.duplicate_check || null;
      if (duplicateCheck?.blocked_new_import) {
        setDuplicateUploadWarning(duplicateCheck);
        setNotice({ type: 'warning', message: duplicateCheck.message });
        await load();
        return;
      }
      setSelectedImport(response.data);
      setAiCandidate(response.data.ai_candidate || null);
      setHeaderDirty(false);
      setNotice(
        response.data.ai_candidate
          ? { type: 'success', message: 'Historical quotation parsed. AI cleaned candidate rows are available for review before applying.' }
          : duplicateCheck?.is_duplicate
          ? { type: 'warning', message: `${duplicateCheck.message} ${duplicateHelperText(duplicateCheck)}` }
          : { type: 'success', message: 'Historical quotation parsed. Review company, date, items, and prices before committing.' }
      );
      await load();
      setSelectedImport(response.data);
      try {
        const previewResponse = await quotationAPI.historicalImports.previewPage(response.data.id);
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        setPreviewUrl(URL.createObjectURL(previewResponse.data));
      } catch {
        setPreviewUrl('');
      }
    } catch (error) {
      const details = await describeQuotationError(error, 'Parse historical PDF', 'POST /quotations/historical-imports/parse_file/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setUploading(false);
    }
  };

  const openDuplicateImport = async (importId) => {
    if (!importId) return;
    setDuplicateUploadWarning(null);
    const existing = imports.find((entry) => entry.id === importId);
    if (existing) {
      await selectImport(existing);
      return;
    }
    try {
      const response = await quotationAPI.historicalImports.retrieve(importId);
      await selectImport(response.data);
    } catch (error) {
      const details = await describeQuotationError(error, 'Open duplicate import', `GET /quotations/historical-imports/${importId}/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    }
  };

  const selectImport = async (entry) => {
    setNotice(null);
    setErrorInfo(null);
    setSelectedImport(entry);
    setAiCandidate(null);
    setHeaderDirty(false);
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
      setPreviewUrl('');
    }
    try {
      const response = await quotationAPI.historicalImports.previewPage(entry.id);
      setPreviewUrl(URL.createObjectURL(response.data));
    } catch {
      setPreviewUrl('');
    }
  };

  const updateImportDraft = (patch) => {
    setHeaderDirty(true);
    setSelectedImport((current) => ({ ...current, ...patch }));
  };

  const updateImportFromBulkResponse = async (response) => {
    const updatedImport = response.data.import;
    setSelectedImport(updatedImport);
    setImports((current) => current.map((entry) => entry.id === updatedImport.id ? updatedImport : entry));
    if ((response.data.summary?.created || 0) > 0) {
      const itemsRes = await quotationAPI.items.list({ active: 'true' });
      setItems(itemsRes.data);
    }
    return response.data.summary;
  };

  const saveImportDetails = async () => {
    if (!selectedImport || saving) return;
    setSaving(true);
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.historicalImports.update(selectedImport.id, {
        company: selectedImport.company || null,
        suggested_company_name: selectedImport.suggested_company_name || '',
        document_number: selectedImport.document_number || '',
        document_date: selectedImport.document_date || null,
        currency: selectedImport.currency || 'AED',
        subtotal: selectedImport.subtotal || null,
        vat_total: selectedImport.vat_total || null,
        total: selectedImport.total || null,
      });
      setSelectedImport(response.data);
      setHeaderDirty(false);
      setNotice({ type: 'success', message: 'Historical import details saved.' });
      await load();
    } catch (error) {
      const details = await describeQuotationError(error, 'Save historical import', `PATCH /quotations/historical-imports/${selectedImport.id}/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const updateLine = async (lineId, patch) => {
    setSaving(true);
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.historicalImportLines.update(lineId, patch);
      setSelectedImport((current) => ({
        ...current,
        lines: current.lines.map((line) => line.id === lineId ? response.data : line),
      }));
    } catch (error) {
      const details = await describeQuotationError(error, 'Save historical import line', `PATCH /quotations/historical-import-lines/${lineId}/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const createQuoteItemForLine = async (line) => {
    if (bulkAction) return;
    setBulkAction('create-one');
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.historicalImports.bulkCreateQuoteItems(selectedImport.id, { row_ids: [line.id] });
      const summary = await updateImportFromBulkResponse(response);
      setNotice({ type: 'success', message: `Product action complete: ${summary.created} created, ${summary.linked_existing} linked existing, ${summary.failed} failed.` });
    } catch (error) {
      const details = await describeQuotationError(error, 'Create/link Product from historical line', `POST /quotations/historical-imports/${selectedImport.id}/bulk_create_quote_items/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setBulkAction('');
    }
  };

  const toggleRowSelection = (rowId) => {
    setSelectedRowIds((current) => (
      current.includes(rowId)
        ? current.filter((candidate) => candidate !== rowId)
        : [...current, rowId]
    ));
  };

  const toggleVisibleSelection = () => {
    setSelectedRowIds((current) => {
      if (allVisibleSelected) {
        return current.filter((rowId) => !visibleRowIds.includes(rowId));
      }
      return Array.from(new Set([...current, ...visibleRowIds]));
    });
  };

  const selectRowsBy = (predicate) => {
    const rowIds = filteredRows.filter(predicate).map((line) => line.id);
    setSelectedRowIds(Array.from(new Set(rowIds)));
  };

  const clearSelection = () => setSelectedRowIds([]);

  const runBulkCreateQuoteItems = async () => {
    if (!selectedImport || !selectedRowIds.length || bulkAction) return;
    setBulkAction('bulk-create');
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.historicalImports.bulkCreateQuoteItems(selectedImport.id, { row_ids: selectedRowIds });
      const summary = await updateImportFromBulkResponse(response);
      setNotice({ type: 'success', message: `Products processed: ${summary.created} created, ${summary.linked_existing} linked existing, ${summary.failed} failed.` });
      if (!summary.failed) clearSelection();
    } catch (error) {
      const details = await describeQuotationError(error, 'Bulk create/link Products', `POST /quotations/historical-imports/${selectedImport.id}/bulk_create_quote_items/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setBulkAction('');
    }
  };

  const runBulkStatus = async (statusValue) => {
    if (!selectedImport || !selectedRowIds.length || bulkAction) return;
    setBulkAction(`bulk-${statusValue}`);
    setNotice(null);
    setErrorInfo(null);
    try {
      const action = statusValue === 'skipped'
        ? quotationAPI.historicalImports.bulkSkipRows(selectedImport.id, { row_ids: selectedRowIds })
        : quotationAPI.historicalImports.bulkUpdateRows(selectedImport.id, { row_ids: selectedRowIds, status: statusValue });
      const response = await action;
      const summary = await updateImportFromBulkResponse(response);
      setNotice({ type: summary.failed ? 'warning' : 'success', message: `Rows updated: ${summary.updated} updated, ${summary.failed} failed.` });
      if (!summary.failed) clearSelection();
    } catch (error) {
      const details = await describeQuotationError(error, `Bulk mark ${statusValue}`, `POST /quotations/historical-imports/${selectedImport.id}/bulk_update_rows/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setBulkAction('');
    }
  };

  const toggleRawRow = (lineId) => {
    setExpandedRawRows((current) => ({ ...current, [lineId]: !current[lineId] }));
  };

  const handleRowAction = (line, actionValue) => {
    if (!actionValue) return;
    if (actionValue === 'create') createQuoteItemForLine(line);
    if (actionValue === 'skip') updateLine(line.id, { status: 'skipped' });
    if (actionValue === 'ready') updateLine(line.id, { status: 'ready' });
    if (actionValue === 'needs_review') updateLine(line.id, { status: 'needs_review' });
    if (actionValue === 'remember_alias') rememberAliasForLine(line);
    if (actionValue === 'raw') toggleRawRow(line.id);
    if (actionValue === 'reset') updateLine(line.id, { status: 'needs_review', product: null });
  };

  const rememberAliasForLine = async (line) => {
    if (saving || bulkAction) return;
    setSaving(true);
    setNotice(null);
    setErrorInfo(null);
    try {
      await quotationAPI.historicalImportLines.rememberAlias(line.id);
      setNotice({ type: 'success', message: 'Company-specific alias remembered for future imports.' });
      await load();
    } catch (error) {
      const details = await describeQuotationError(error, 'Remember product alias', `POST /quotations/historical-import-lines/${line.id}/remember_alias/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const aiSourceLabel = (source) => {
    if (source === 'ai_vision_cleanup') return 'AI vision cleanup used';
    if (source === 'ai_text_cleanup') return 'AI text cleanup used';
    return 'AI cleaned rows';
  };

  const runAiCleanRows = async () => {
    if (!selectedImport || aiCleaning || selectedImport.status === 'committed') return;
    setAiCleaning(true);
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.historicalImports.aiCleanRows(selectedImport.id, { mode: 'auto' });
      setAiCandidate(response.data);
      setNotice({ type: 'success', message: `${aiSourceLabel(response.data.result_source)}. Review the candidate rows before applying them.` });
    } catch (error) {
      const details = await describeQuotationError(error, 'AI clean historical import rows', `POST /quotations/historical-imports/${selectedImport.id}/ai_clean_rows/`);
      setErrorInfo(details);
      setNotice({ type: 'warning', message: 'AI failed, using original rows.' });
      console.error(formatQuotationError(details), error);
    } finally {
      setAiCleaning(false);
    }
  };

  const applyAiCleanRows = async () => {
    if (!selectedImport || !aiCandidate || applyingAiRows) return;
    if (!window.confirm('Replace the current staged rows with these AI-cleaned rows? Staff review is still required before committing.')) return;
    setApplyingAiRows(true);
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.historicalImports.applyAiCleanRows(selectedImport.id, {
        lines: aiCandidate.lines || [],
        result_source: aiCandidate.result_source || '',
        provider: aiCandidate.provider || '',
        model: aiCandidate.model || '',
        cache_hit: Boolean(aiCandidate.cache_hit),
      });
      setSelectedImport(response.data);
      setAiCandidate(null);
      setSelectedRowIds([]);
      setNotice({ type: 'success', message: 'AI cleaned rows applied. Review matches and mark rows ready before committing.' });
      await load();
    } catch (error) {
      const details = await describeQuotationError(error, 'Apply AI cleaned historical rows', `POST /quotations/historical-imports/${selectedImport.id}/apply_ai_clean_rows/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setApplyingAiRows(false);
    }
  };

  const runBatchAiSuggestions = async () => {
    if (!selectedBatch || suggestionAction) return;
    const importIds = selectedBatchImportIds.length ? selectedBatchImportIds : visibleBatchImportIds;
    if (!importIds.length) {
      setNotice({ type: 'warning', message: 'Select at least one parsed import before running AI suggestions.' });
      return;
    }
    setSuggestionAction('ai');
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.historicalImportBatches.runAiSuggestions(selectedBatch.id, { import_ids: importIds, mode: 'auto' });
      setSelectedBatch(response.data.batch || selectedBatch);
      await loadSuggestions(selectedBatch.id);
      setNotice({ type: response.data.summary.failed ? 'warning' : 'success', message: `AI suggestions finished: ${response.data.summary.suggested || 0} imports suggested, ${response.data.summary.failed || 0} failed.` });
    } catch (error) {
      const details = await describeQuotationError(error, 'Run batch AI suggestions', `POST /quotations/historical-import-batches/${selectedBatch.id}/run_ai_suggestions/`);
      setErrorInfo(details);
      setNotice({ type: 'warning', message: 'AI suggestions failed. Deterministic rows are still available.' });
      console.error(formatQuotationError(details), error);
    } finally {
      setSuggestionAction('');
    }
  };

  const runSelectedImportAiSuggestions = async () => {
    if (!selectedImport || suggestionAction || selectedImport.status === 'committed') return;
    setSuggestionAction('ai-one');
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.historicalImports.runAiSuggestions(selectedImport.id, { mode: 'auto' });
      if (selectedBatch?.id) await loadSuggestions(selectedBatch.id);
      setNotice({ type: 'success', message: `AI suggestions created: ${response.data.summary.suggested || 0}. Review before applying.` });
    } catch (error) {
      const details = await describeQuotationError(error, 'Run import AI suggestions', `POST /quotations/historical-imports/${selectedImport.id}/run_ai_suggestions/`);
      setErrorInfo(details);
      setNotice({ type: 'warning', message: 'AI suggestions failed. No Products, aliases, companies, or prices were created.' });
      console.error(formatQuotationError(details), error);
    } finally {
      setSuggestionAction('');
    }
  };

  const updateSuggestion = async (suggestionId, patch) => {
    try {
      const response = await quotationAPI.historicalImportAiSuggestions.update(suggestionId, patch);
      setSuggestions((current) => current.map((suggestion) => suggestion.id === suggestionId ? response.data : suggestion));
    } catch (error) {
      const details = await describeQuotationError(error, 'Update AI suggestion', `PATCH /quotations/historical-import-ai-suggestions/${suggestionId}/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    }
  };

  const applySelectedSuggestions = async () => {
    if (!selectedSuggestionIds.length || suggestionAction) return;
    if (!window.confirm('Apply selected AI suggestions? This may create approved draft Products, aliases, or company links, but will not commit price history.')) return;
    setSuggestionAction('apply');
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = selectedBatch
        ? await quotationAPI.historicalImportBatches.applyAiSuggestions(selectedBatch.id, { suggestion_ids: selectedSuggestionIds })
        : await quotationAPI.historicalImportAiSuggestions.apply({ suggestion_ids: selectedSuggestionIds });
      setNotice({ type: response.data.summary.conflict ? 'warning' : 'success', message: `Suggestions applied: ${response.data.summary.applied || 0}, conflicts: ${response.data.summary.conflict || 0}, failed: ${response.data.summary.failed || 0}.` });
      setSelectedSuggestionIds([]);
      await load();
      if (selectedBatch?.id) await loadSuggestions(selectedBatch.id);
      const itemsRes = await quotationAPI.items.list({ active: 'true' });
      setItems(itemsRes.data);
    } catch (error) {
      const details = await describeQuotationError(error, 'Apply AI suggestions', 'POST /quotations/historical-import-ai-suggestions/apply/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSuggestionAction('');
    }
  };

  const commitSelectedBatchImports = async () => {
    if (!selectedBatch || suggestionAction) return;
    const importIds = selectedBatchImportIds.length ? selectedBatchImportIds : visibleBatchImportIds;
    if (!importIds.length) return;
    if (!window.confirm('Commit ready rows from the selected imports into price history? Needs-review and skipped rows are ignored.')) return;
    setSuggestionAction('commit');
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.historicalImportBatches.commitReadyImports(selectedBatch.id, { import_ids: importIds });
      setSelectedBatch(response.data.batch || selectedBatch);
      setNotice({ type: response.data.summary.failed ? 'warning' : 'success', message: `Batch commit complete: ${response.data.summary.committed || 0} committed, ${response.data.summary.failed || 0} failed.` });
      await load();
    } catch (error) {
      const details = await describeQuotationError(error, 'Commit batch ready imports', `POST /quotations/historical-import-batches/${selectedBatch.id}/commit_ready_imports/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSuggestionAction('');
    }
  };

  const commitImport = async () => {
    if (!selectedImport || committing) return;
    if (!window.confirm('Commit reviewed rows into company price history? This cannot be edited like a draft quotation.')) return;
    setCommitting(true);
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.historicalImports.commit(selectedImport.id);
      setSelectedImport(response.data);
      setNotice({ type: 'success', message: 'Historical price history committed.' });
      await load();
    } catch (error) {
      const details = await describeQuotationError(error, 'Commit historical price history', `POST /quotations/historical-imports/${selectedImport.id}/commit/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setCommitting(false);
    }
  };

  return (
    <div className="qm-section">
      <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
      {notice && <div className={`qm-feedback ${notice.type}`}>{notice.message}</div>}

      <div className="qm-panel">
        <div className="qm-panel-heading">
          <div>
            <h3>Batch Historical Learning</h3>
            <p>Upload several old finalized quotation PDFs, process them one by one, then review AI suggestions before creating any Products, aliases, companies, or price history.</p>
          </div>
        </div>
        <div className="qm-import-source">
          <label>
            <span className="qm-label-text">Old finalized quotation PDFs</span>
            <input type="file" accept=".pdf" multiple onChange={(event) => setBatchFiles(Array.from(event.target.files || []))} />
          </label>
          <button type="button" className="qm-primary" disabled={batchUploading || !batchFiles.length} onClick={uploadBatchFiles}>
            {batchUploading ? 'Uploading batch...' : 'Upload Batch'}
          </button>
        </div>
        <div className="qm-helper compact">V1 processes up to 25 PDFs sequentially. AI suggestions are review-only until staff approves them.</div>
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
            <p>Select a batch to run AI suggestions, review uncertain rows, and commit approved ready imports.</p>
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
                  <th>Imports</th>
                  <th>Ready</th>
                  <th>Needs Review</th>
                  <th>AI Pending</th>
                  <th>Status</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {batches.map((batch) => (
                  <tr key={batch.id} className={selectedBatch?.id === batch.id ? 'selected' : ''}>
                    <td>{batch.name || `Batch #${batch.id}`}<br /><small>{batch.created_at}</small></td>
                    <td>{batch.summary?.import_count ?? batch.import_count ?? 0}</td>
                    <td>{batch.summary?.ready_row_count || 0}</td>
                    <td>{batch.summary?.needs_review_row_count || 0}</td>
                    <td>{batch.pending_suggestion_count || batch.summary?.pending_suggestion_count || 0}</td>
                    <td><span className={`qm-badge status-${batch.status}`}>{batch.status}</span></td>
                    <td><button type="button" className="qm-secondary small" onClick={() => selectBatch(batch)}>Open / Review</button></td>
                  </tr>
                ))}
                {!batches.length && (
                  <tr><td colSpan="7"><div className="qm-empty compact">No historical batches yet. Upload PDFs above to begin.</div></td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {selectedBatch && (
        <div className="qm-panel">
          <div className="qm-panel-heading">
            <div>
              <h3>Batch Review: {selectedBatch.name || `Batch #${selectedBatch.id}`}</h3>
              <p>Select imports to run AI suggestions or commit only rows already marked ready.</p>
            </div>
          </div>
          <div className="qm-bulk-toolbar">
            <strong>{selectedBatchImportIds.length} imports selected</strong>
            <button type="button" className="qm-secondary small" disabled={!visibleBatchImportIds.length} onClick={toggleAllBatchImports}>
              {allBatchImportsSelected ? 'Deselect All' : 'Select All'}
            </button>
            <span className="qm-bulk-spacer" />
            <button type="button" className="qm-primary small" disabled={suggestionAction || !visibleBatchImportIds.length} onClick={runBatchAiSuggestions}>
              {suggestionAction === 'ai' ? 'Running AI...' : 'Run AI Suggestions'}
            </button>
            <button type="button" className="qm-secondary small" disabled={suggestionAction || !visibleBatchImportIds.length} onClick={commitSelectedBatchImports}>
              {suggestionAction === 'commit' ? 'Committing...' : 'Commit Ready Imports'}
            </button>
          </div>
          <div className="qm-table-wrap">
            <table className="qm-table">
              <thead>
                <tr>
                  <th className="qm-check-cell"><input type="checkbox" checked={allBatchImportsSelected} onChange={toggleAllBatchImports} disabled={!visibleBatchImportIds.length} /></th>
                  <th>File</th>
                  <th>Company</th>
                  <th>Document</th>
                  <th>Date</th>
                  <th>Rows</th>
                  <th>Status</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {selectedBatchImports.map((entry) => (
                  <tr key={entry.id} className={selectedImport?.id === entry.id ? 'selected' : ''}>
                    <td className="qm-check-cell"><input type="checkbox" checked={selectedBatchImportIds.includes(entry.id)} onChange={() => toggleBatchImportSelection(entry.id)} /></td>
                    <td>{entry.source_filename}</td>
                    <td>{entry.company_name || entry.suggested_company_name || '-'}</td>
                    <td>{entry.document_number || '-'}</td>
                    <td>{entry.document_date || '-'}</td>
                    <td>{entry.lines?.length || 0}</td>
                    <td><span className={`qm-badge status-${entry.status}`}>{entry.status}</span></td>
                    <td><button type="button" className="qm-secondary small" onClick={() => selectImport(entry)}>Open File</button></td>
                  </tr>
                ))}
                {!selectedBatchImports.length && (
                  <tr><td colSpan="8"><div className="qm-empty compact">No imports are attached to this batch yet.</div></td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {selectedBatch && (
        <div className="qm-panel">
          <div className="qm-panel-heading">
            <div>
              <h3>AI Suggestion Review</h3>
              <p>AI suggestions are only pending review. Select and apply the ones you approve.</p>
            </div>
          </div>
          <div className="qm-row-review-controls">
            <div className="qm-controls">
              <select className="qm-input" value={suggestionFilter} onChange={(event) => setSuggestionFilter(event.target.value)}>
                <option value="all">All suggestions</option>
                <option value="high_confidence">High confidence pending</option>
                <option value="uncertain">Uncertain / conflicts</option>
                <option value="match_existing_product">Product matches</option>
                <option value="create_company_alias">Aliases</option>
                <option value="create_new_product">New Products</option>
                <option value="needs_manual_review">Needs manual review</option>
                <option value="skip">Skip rows</option>
              </select>
            </div>
            <div className="qm-bulk-toolbar">
              <strong>{selectedSuggestionIds.length} suggestions selected</strong>
              <button type="button" className="qm-secondary small" disabled={!visibleSuggestionIds.length} onClick={toggleAllSuggestions}>
                {allSuggestionsSelected ? 'Deselect Visible' : 'Select Visible'}
              </button>
              <span className="qm-bulk-spacer" />
              <button type="button" className="qm-primary small" disabled={!selectedSuggestionIds.length || Boolean(suggestionAction)} onClick={applySelectedSuggestions}>
                {suggestionAction === 'apply' ? 'Applying...' : 'Apply Selected Suggestions'}
              </button>
            </div>
          </div>
          <div className="qm-table-wrap">
            <table className="qm-table historical-table">
              <thead>
                <tr>
                  <th className="qm-check-cell"><input type="checkbox" checked={allSuggestionsSelected} onChange={toggleAllSuggestions} disabled={!visibleSuggestionIds.length} /></th>
                  <th>Source Row</th>
                  <th>Action</th>
                  <th>Target / Proposal</th>
                  <th>Confidence</th>
                  <th>Reason</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {filteredSuggestions.map((suggestion) => (
                  <tr key={suggestion.id} className={`qm-review-row row-${suggestion.status}`}>
                    <td className="qm-check-cell"><input type="checkbox" checked={selectedSuggestionIds.includes(suggestion.id)} disabled={suggestion.status !== 'pending'} onChange={() => toggleSuggestionSelection(suggestion.id)} /></td>
                    <td>
                      <strong>{suggestion.line_item_name || suggestion.proposed_company_name || suggestion.historical_import_filename}</strong>
                      <small className="qm-muted-text">{suggestion.historical_import_filename} · {suggestion.historical_import_company_name || '-'}</small>
                    </td>
                    <td>
                      <select disabled={suggestion.status !== 'pending'} value={suggestion.action} onChange={(event) => updateSuggestion(suggestion.id, { action: event.target.value })}>
                        <option value="match_existing_product">Match Product</option>
                        <option value="create_company_alias">Create Alias</option>
                        <option value="create_new_product">Create New Product</option>
                        <option value="needs_manual_review">Needs Review</option>
                        <option value="skip">Skip</option>
                        <option value="match_existing_company">Match Company</option>
                        <option value="create_new_company">Create Company</option>
                      </select>
                    </td>
                    <td>
                      {suggestion.action === 'create_new_product' ? (
                        <input disabled={suggestion.status !== 'pending'} value={suggestion.proposed_product_name || ''} onChange={(event) => updateSuggestion(suggestion.id, { proposed_product_name: event.target.value })} placeholder="New Product name" />
                      ) : suggestion.action === 'create_new_company' ? (
                        <input disabled={suggestion.status !== 'pending'} value={suggestion.proposed_company_name || ''} onChange={(event) => updateSuggestion(suggestion.id, { proposed_company_name: event.target.value })} placeholder="New company name" />
                      ) : suggestion.action === 'match_existing_company' ? (
                        <select disabled={suggestion.status !== 'pending'} value={suggestion.suggested_company || ''} onChange={(event) => updateSuggestion(suggestion.id, { suggested_company: event.target.value || null })}>
                          <option value="">Select company</option>
                          {companies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}
                        </select>
                      ) : (
                        <>
                          <select disabled={suggestion.status !== 'pending'} value={suggestion.suggested_product || ''} onChange={(event) => updateSuggestion(suggestion.id, { suggested_product: event.target.value || null })}>
                            <option value="">Select Product</option>
                            {items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                          </select>
                          {suggestion.action === 'create_company_alias' && (
                            <input disabled={suggestion.status !== 'pending'} value={suggestion.alias_text || ''} onChange={(event) => updateSuggestion(suggestion.id, { alias_text: event.target.value })} placeholder="Alias text" />
                          )}
                        </>
                      )}
                    </td>
                    <td>{confidencePercent(suggestion.confidence)}%</td>
                    <td>{suggestion.reason || suggestion.error_message || '-'}</td>
                    <td><span className={`qm-badge status-${suggestion.status}`}>{suggestion.status}</span></td>
                  </tr>
                ))}
                {!filteredSuggestions.length && (
                  <tr><td colSpan="7"><div className="qm-empty compact">No AI suggestions yet. Run AI suggestions on selected imports.</div></td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="qm-panel">
        <div className="qm-panel-heading">
          <div>
            <h3>Historical Price Backfill</h3>
            <p>Upload old finalized Al Ameen quotation PDFs, review extracted prices, then commit approved rows into company price history.</p>
          </div>
        </div>
        <div className="qm-import-source">
          <label>
            <span className="qm-label-text">Old finalized quotation PDF</span>
            <input type="file" accept=".pdf" onChange={(event) => setUploadFile(event.target.files?.[0] || null)} />
          </label>
          <button type="button" className="qm-primary" disabled={uploading || !uploadFile} onClick={uploadHistoricalPdf}>
            {uploading ? 'Parsing...' : 'Parse Historical PDF'}
          </button>
        </div>
        <div className="qm-helper compact">This does not create a new customer quotation to send. It only stages old approved prices for staff review.</div>
        {duplicateUploadWarning?.is_duplicate && (
          <div className="qm-notice warning">
            <strong>{duplicateUploadWarning.message}</strong>
            <p>{duplicateHelperText(duplicateUploadWarning)}</p>
            {duplicateUploadWarning.primary_match?.id && (
              <p className="qm-helper compact">Previous import: #{duplicateUploadWarning.primary_match.id}</p>
            )}
            {duplicateUploadWarning.primary_match?.id && (
              <button type="button" className="qm-secondary small" onClick={() => openDuplicateImport(duplicateUploadWarning.primary_match.id)}>
                View previous import
              </button>
            )}
          </div>
        )}
      </div>

      <div className="qm-panel historical-imports-panel">
        <div className="qm-panel-heading">
          <h3>Historical Imports</h3>
          <button type="button" className="qm-secondary small" onClick={load} disabled={loading}>Refresh</button>
        </div>
        {loading ? (
          <div className="qm-loading">Loading historical imports...</div>
        ) : (
          <div className="qm-table-wrap">
            <table className="qm-table">
              <thead>
                <tr>
                  <th>Document</th>
                  <th>Company</th>
                  <th>Date</th>
                  <th>Lines</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {imports.map((entry) => (
                  <tr key={entry.id} className={selectedImport?.id === entry.id ? 'selected' : ''} onClick={() => selectImport(entry)}>
                    <td>{entry.document_number || entry.source_filename}</td>
                    <td>{entry.company_name || entry.suggested_company_name || '-'}</td>
                    <td>{entry.document_date || '-'}</td>
                    <td>{entry.lines?.length || 0}</td>
                    <td><span className="qm-badge muted">{entry.status}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {selectedImport && (
        <div className="qm-panel qm-review-import-panel">
          <div className="qm-panel-heading">
            <div>
              <h3>Review Import</h3>
              <p>Review the source document, save company/header details, then fix and commit ready price rows below.</p>
            </div>
          </div>

          <div className="qm-review-import-shell">
            <div className="qm-review-card qm-document-card">
              <div className="qm-review-card-heading">
                <div>
                  <h4>Document Preview</h4>
                  <p>{selectedImport.source_filename}</p>
                </div>
                <span className={`qm-badge status-${selectedImport.status}`}>{selectedImport.status}</span>
              </div>
              {previewUrl ? (
                <div className="qm-pdf-preview">
                  <img src={previewUrl} alt="First page preview" />
                </div>
              ) : (
                <div className="qm-empty compact">Preview unavailable for this import.</div>
              )}
              <div className="qm-meta-grid">
                <div className="qm-meta-item"><span>Source file</span><strong>{selectedImport.source_filename || '-'}</strong></div>
                <div className="qm-meta-item"><span>Parser status</span><strong>{selectedImport.status || '-'}</strong></div>
                <div className="qm-meta-item"><span>Lines found</span><strong>{selectedSummary.total}</strong></div>
              </div>
            </div>

            <div className="qm-review-card qm-import-details-card">
              <div className="qm-review-card-heading">
                <div>
                  <h4>Import Details</h4>
                  <p>Company, date, and document totals used before committing price history.</p>
                </div>
                {headerDirty && <span className="qm-unsaved-badge">Unsaved changes</span>}
              </div>

              <section className="qm-details-section">
                <h5>Import Summary</h5>
                <div className="qm-summary-banner compact">
                  <div className="qm-summary-stat"><span>Total rows</span><strong>{selectedSummary.total}</strong></div>
                  <div className="qm-summary-stat success"><span>Ready</span><strong>{selectedSummary.ready}</strong></div>
                  <div className="qm-summary-stat warning"><span>Needs review</span><strong>{selectedSummary.needsReview}</strong></div>
                  <div className="qm-summary-stat muted"><span>Duplicates</span><strong>{selectedSummary.duplicates}</strong></div>
                </div>
                {(selectedImport.parse_meta?.warnings || []).length > 0 && (
                  <div className="qm-notice">
                    <strong>Parser warnings:</strong>
                    <ul>{selectedImport.parse_meta.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
                  </div>
                )}
                {selectedDuplicateCheck?.is_duplicate && (
                  <div className="qm-notice warning">
                    <strong>Possible duplicate import</strong>
                    <p>{selectedDuplicateCheck.message}</p>
                    <p>{duplicateHelperText(selectedDuplicateCheck)}</p>
                    {(selectedDuplicateCheck.matches || []).slice(0, 3).map((match) => (
                      <div key={match.id} className="qm-helper compact">
                        Existing import #{match.id}: {match.company_name || match.suggested_company_name || 'Unknown company'} · {match.document_number || 'No quotation #'} · {match.document_date || 'No date'} · {match.status}
                        {match.created_at ? ` · created ${match.created_at}` : ''}
                        {match.committed_at ? ` · committed ${match.committed_at}` : ''}
                      </div>
                    ))}
                    {selectedDuplicateCheck.primary_match?.id && selectedDuplicateCheck.primary_match.id !== selectedImport.id && (
                      <button type="button" className="qm-secondary small" onClick={() => openDuplicateImport(selectedDuplicateCheck.primary_match.id)}>
                        View previous import
                      </button>
                    )}
                  </div>
                )}
              </section>

              <section className="qm-details-section">
                <h5>Company</h5>
                <CompanySelectWithCreate
                  companies={companies}
                  value={selectedImport.company || ''}
                  required
                  disabled={selectedImport.status === 'committed'}
                  initialName={selectedImport.suggested_company_name || ''}
                  suggestedName={selectedImport.suggested_company_name || ''}
                  onChange={(companyId) => updateImportDraft({ company: companyId })}
                  onCreated={rememberCompany}
                />
                {!selectedImport.company && <p className="qm-field-warning">Company is required before committing price history.</p>}
              </section>

              <section className="qm-details-section">
                <h5>Quotation Details</h5>
                <div className="qm-details-grid">
                  <label><span className="qm-label-text">Quotation number</span>
                    <input disabled={selectedImport.status === 'committed'} value={selectedImport.document_number || ''} onChange={(event) => updateImportDraft({ document_number: event.target.value })} />
                  </label>
                  <label><span className="qm-label-text">Quotation date <span className="qm-required">*</span></span>
                    <input disabled={selectedImport.status === 'committed'} type="date" value={selectedImport.document_date || ''} onChange={(event) => updateImportDraft({ document_date: event.target.value })} />
                  </label>
                  <label><span className="qm-label-text">Subtotal</span>
                    <input disabled={selectedImport.status === 'committed'} type="number" step="0.01" value={selectedImport.subtotal || ''} onChange={(event) => updateImportDraft({ subtotal: event.target.value })} />
                  </label>
                  <label><span className="qm-label-text">VAT total</span>
                    <input disabled={selectedImport.status === 'committed'} type="number" step="0.01" value={selectedImport.vat_total || ''} onChange={(event) => updateImportDraft({ vat_total: event.target.value })} />
                  </label>
                </div>
                {!selectedImport.document_date && <p className="qm-field-warning">Quotation date is required before committing price history.</p>}
              </section>

              <section className="qm-details-section qm-details-actions">
                <button type="button" className="qm-primary" disabled={saving || selectedImport.status === 'committed'} onClick={saveImportDetails}>
                  {saving ? 'Saving...' : 'Save Import Details'}
                </button>
                <button type="button" className="qm-secondary" disabled={aiCleaning || selectedImport.status === 'committed'} onClick={runAiCleanRows}>
                  {aiCleaning ? 'Cleaning...' : 'AI Clean Rows'}
                </button>
                <button type="button" className="qm-secondary" disabled={Boolean(suggestionAction) || selectedImport.status === 'committed'} onClick={runSelectedImportAiSuggestions}>
                  {suggestionAction === 'ai-one' ? 'Thinking...' : 'AI Product Suggestions'}
                </button>
                <span className="qm-helper compact">Save company/date/header details before committing price rows.</span>
              </section>
            </div>
          </div>
        </div>
      )}

      {selectedImport && (
        <div className="qm-panel">
          <div className="qm-panel-heading">
            <div>
              <h3>Review Price Rows</h3>
              <p>Select rows, bulk create/link missing Products, fix anything marked needs review, then commit only ready rows.</p>
            </div>
          </div>
          {aiCandidate && (
            <div className="qm-ai-candidate">
              <div>
                <strong>{aiSourceLabel(aiCandidate.result_source)}</strong>
                <p>AI candidate rows are waiting. They will not replace current rows until you apply them.</p>
              </div>
              <div className="qm-ai-candidate-summary">
                <span>{aiCandidate.lines?.length || 0} candidate rows</span>
                <span>Provider: {aiCandidate.provider || '-'}</span>
                <span>Model: {aiCandidate.model || '-'}</span>
                {aiCandidate.cache_hit && <span>Cached result</span>}
              </div>
              <div className="qm-ai-candidate-preview">
                {(aiCandidate.lines || []).slice(0, 6).map((line, index) => (
                  <div key={`${line.item_name}-${index}`}>
                    <strong>{line.item_name}</strong>
                    <span>{line.quantity || '-'} {line.unit || ''}</span>
                    {line.unit_price && <span>Price {line.unit_price}</span>}
                    {line.line_total && <span>Total {line.line_total}</span>}
                    <em>{Math.round(Number(line.parse_confidence || 0) * 100)}%</em>
                  </div>
                ))}
              </div>
              <div className="qm-action-row">
                <button type="button" className="qm-primary small" disabled={applyingAiRows} onClick={applyAiCleanRows}>
                  {applyingAiRows ? 'Applying...' : 'Apply AI Cleaned Rows'}
                </button>
                <button type="button" className="qm-secondary small" disabled={applyingAiRows} onClick={() => setAiCandidate(null)}>Keep Original</button>
              </div>
            </div>
          )}
          <div className="qm-row-review-controls">
            <div className="qm-controls">
              <input className="qm-input" value={rowSearch} onChange={(event) => setRowSearch(event.target.value)} placeholder="Search imported or product item" />
              <select className="qm-input" value={rowFilter} onChange={(event) => setRowFilter(event.target.value)}>
                <option value="all">All rows</option>
                <option value="ready">Ready</option>
                <option value="needs_review">Needs Review</option>
                <option value="unmatched">Unmatched</option>
                <option value="skipped">Skipped</option>
                <option value="errors">Errors</option>
              </select>
            </div>
            <div className="qm-bulk-toolbar">
              <strong>{selectedRowIds.length} rows selected</strong>
              <button type="button" className="qm-secondary small" disabled={!filteredRows.length} onClick={toggleVisibleSelection}>
                {allVisibleSelected ? 'Deselect Visible' : 'Select Visible'}
              </button>
              <button type="button" className="qm-secondary small" disabled={!filteredRows.length} onClick={() => selectRowsBy((line) => !line.product)}>Select Unmatched</button>
              <button type="button" className="qm-secondary small" disabled={!filteredRows.length} onClick={() => selectRowsBy((line) => line.status === 'needs_review')}>Select Needs Review</button>
              <button type="button" className="qm-secondary small" disabled={!filteredRows.length} onClick={() => selectRowsBy((line) => line.status === 'ready')}>Select Ready</button>
              <span className="qm-bulk-spacer" />
              <button type="button" className="qm-primary small" disabled={!selectedRowIds.length || Boolean(bulkAction) || selectedImport.status === 'committed'} onClick={runBulkCreateQuoteItems}>
                {bulkAction === 'bulk-create' ? 'Creating...' : 'Create Products'}
              </button>
              <button type="button" className="qm-secondary small" disabled={!selectedRowIds.length || Boolean(bulkAction) || selectedImport.status === 'committed'} onClick={() => runBulkStatus('ready')}>Mark Ready</button>
              <button type="button" className="qm-secondary small" disabled={!selectedRowIds.length || Boolean(bulkAction) || selectedImport.status === 'committed'} onClick={() => runBulkStatus('needs_review')}>Needs Review</button>
              <button type="button" className="qm-secondary small danger" disabled={!selectedRowIds.length || Boolean(bulkAction) || selectedImport.status === 'committed'} onClick={() => runBulkStatus('skipped')}>Skip</button>
              <button type="button" className="qm-secondary small" disabled={!selectedRowIds.length || Boolean(bulkAction)} onClick={clearSelection}>Clear</button>
            </div>
          </div>
          <div className="qm-table-wrap">
            <table className="qm-table historical-table">
              <thead>
                <tr>
                  <th className="qm-check-cell"><input type="checkbox" checked={allVisibleSelected} onChange={toggleVisibleSelection} disabled={!visibleRowIds.length} /></th>
                  <th>Imported Item</th>
                  <th>Matched Product</th>
                  <th>Qty</th>
                  <th>Unit</th>
                  <th>Unit Price</th>
                  <th>VAT</th>
                  <th>Total</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredRows.map((line) => {
                  const locked = selectedImport.status === 'committed' || line.status === 'committed' || line.status === 'duplicate';
                  return (
                    <React.Fragment key={line.id}>
                    <tr className={`qm-review-row row-${line.status}${line.duplicate_reason ? ' row-error' : ''}`}>
                      <td className="qm-check-cell">
                        <input type="checkbox" checked={selectedRowIds.includes(line.id)} disabled={locked} onChange={() => toggleRowSelection(line.id)} />
                      </td>
                      <td className="qm-item-cell"><input disabled={locked} value={line.item_name || ''} onChange={(event) => updateLine(line.id, { item_name: event.target.value })} /></td>
                      <td>
                        <select disabled={locked} value={line.product || ''} onChange={(event) => updateLine(line.id, { product: event.target.value || null, status: event.target.value && line.quantity && line.unit_price ? 'ready' : line.status })}>
                          <option value="">Select Product</option>
                          {items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                        </select>
                        {line.match_reason && <small className="qm-muted-text">{line.match_reason}</small>}
                      </td>
                      <td><input disabled={locked} type="number" step="0.001" value={line.quantity || ''} onChange={(event) => updateLine(line.id, { quantity: event.target.value })} /></td>
                      <td><input disabled={locked} value={line.unit || ''} onChange={(event) => updateLine(line.id, { unit: event.target.value })} /></td>
                      <td><input disabled={locked} type="number" step="0.01" value={line.unit_price || ''} onChange={(event) => updateLine(line.id, { unit_price: event.target.value })} /></td>
                      <td><input disabled={locked} type="number" step="0.01" value={line.vat_amount || ''} onChange={(event) => updateLine(line.id, { vat_amount: event.target.value })} /></td>
                      <td><input disabled={locked} type="number" step="0.01" value={line.line_total || ''} onChange={(event) => updateLine(line.id, { line_total: event.target.value })} /></td>
                      <td>
                        <span className={`qm-badge status-${line.status}`}>{statusOptions.find((option) => option.value === line.status)?.label || line.status}</span>
                        {line.duplicate_reason && <small className="qm-danger-text">{line.duplicate_reason}</small>}
                      </td>
                      <td className="qm-row-actions">
                        <select className="qm-row-menu" disabled={locked || saving || Boolean(bulkAction)} value="" onChange={(event) => handleRowAction(line, event.target.value)}>
                          <option value="">Actions</option>
                          <option value="create">Create/link product</option>
                          <option value="ready">Mark ready</option>
                          <option value="needs_review">Needs review</option>
                          <option value="remember_alias">Remember alias</option>
                          <option value="skip">Skip row</option>
                          <option value="raw">{expandedRawRows[line.id] ? 'Hide raw' : 'View raw'}</option>
                          <option value="reset">Reset row</option>
                        </select>
                      </td>
                    </tr>
                    {expandedRawRows[line.id] && (
                      <tr className="qm-raw-row">
                        <td />
                        <td colSpan="9">
                          <strong>Raw source:</strong> {line.raw_line || '-'}
                          <div className="qm-raw-meta">
                            {line.source_page && <span>Page: {line.source_page}</span>}
                            {line.source_row && <span>Row: {line.source_row}</span>}
                            {line.serial_no && <span>Serial: {line.serial_no}</span>}
                            <span>Confidence: {Math.round(Number(line.parse_confidence || 0) * 100)}%</span>
                          </div>
                        </td>
                      </tr>
                    )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="qm-sticky-commit-bar">
            <div className="qm-commit-stats">
              <span>Total <strong>{selectedSummary.total}</strong></span>
              <span>Ready <strong>{selectedSummary.ready}</strong></span>
              <span>Needs review <strong>{selectedSummary.needsReview}</strong></span>
              <span>Skipped <strong>{selectedSummary.skipped}</strong></span>
              <span>Duplicates <strong>{selectedSummary.duplicates}</strong></span>
              <span>Selected <strong>{selectedRowIds.length}</strong></span>
            </div>
            <div className="qm-action-row">
              {headerDirty && <span className="qm-helper compact warning">Save import details before committing.</span>}
              <button type="button" className="qm-primary" disabled={committing || headerDirty || selectedImport.status === 'committed' || selectedSummary.ready === 0} onClick={commitImport}>
                {committing ? 'Committing...' : 'Commit Ready Rows to Price History'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default HistoricalImportManager;
