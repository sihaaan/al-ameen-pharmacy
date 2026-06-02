import React, { useEffect, useMemo, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import CompanySelectWithCreate from './CompanySelectWithCreate';
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

  const selectedBatchImports = useMemo(() => selectedBatch?.imports || [], [selectedBatch]);
  const visibleBatchImportIds = useMemo(() => selectedBatchImports.map((entry) => entry.id), [selectedBatchImports]);
  const allBatchImportsSelected = visibleBatchImportIds.length > 0 && visibleBatchImportIds.every((id) => selectedBatchImportIds.includes(id));
  const wizardSummary = selectedBatch?.wizard_summary || selectedBatch?.summary || {};
  const lineCounts = wizardSummary.line_counts || {};
  const pendingActionCounts = wizardSummary.pending_suggestion_action_counts || {};
  const appliedActionCounts = wizardSummary.applied_suggestion_action_counts || {};

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
    setLineDrafts((current) => ({ ...nextLineDrafts, ...current }));
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

  const rememberCompany = (company) => {
    setCompanies((current) => {
      const withoutDuplicate = current.filter((candidate) => candidate.id !== company.id);
      return [...withoutDuplicate, company].sort((a, b) => a.name.localeCompare(b.name));
    });
  };

  const selectBatch = async (batch, step = 'analyze') => {
    setSelectedBatch(batch);
    setActiveStep(step);
    setSelectedBatchImportIds((batch.imports || []).map((entry) => entry.id));
    setSelectedSuggestionIds([]);
    setNotice(null);
    setErrorInfo(null);
    setImportDrafts({});
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

  const runBatchAiAnalyze = async () => {
    if (!selectedBatch || workingAction) return;
    const importIds = selectedBatchImportIds.length ? selectedBatchImportIds : visibleBatchImportIds;
    if (!importIds.length) {
      setNotice({ type: 'warning', message: 'Select at least one parsed import before running AI Analyze.' });
      return;
    }
    setWorkingAction('ai');
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.historicalImportBatches.runAiSuggestions(selectedBatch.id, { import_ids: importIds, mode: 'auto' });
      setSelectedBatch(response.data.batch || selectedBatch);
      await loadSuggestions(selectedBatch.id);
      await load();
      setNotice({
        type: response.data.summary.failed ? 'warning' : 'success',
        message: `AI Analyze finished: ${response.data.summary.suggested || 0} files analyzed, ${response.data.summary.failed || 0} failed.`,
      });
    } catch (error) {
      const details = await describeQuotationError(error, 'Run batch AI Analyze', `POST /quotations/historical-import-batches/${selectedBatch.id}/run_ai_suggestions/`);
      setErrorInfo(details);
      setNotice({ type: 'warning', message: 'AI Analyze failed. Parsed rows are still available.' });
      console.error(formatQuotationError(details), error);
    } finally {
      setWorkingAction('');
    }
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
    company: entry.company || '',
    suggested_company_name: entry.suggested_company_name || '',
    document_number: entry.document_number || '',
    document_date: entry.document_date || '',
    currency: entry.currency || 'AED',
    subtotal: entry.subtotal || '',
    vat_total: entry.vat_total || '',
    total: entry.total || '',
    ...(importDrafts[entry.id] || {}),
  });

  const saveImportDetails = async (entry) => {
    if (!entry || workingAction) return;
    const draft = draftForImport(entry);
    setWorkingAction(`save-import-${entry.id}`);
    setNotice(null);
    setErrorInfo(null);
    try {
      await quotationAPI.historicalImports.update(entry.id, {
        company: draft.company || null,
        suggested_company_name: draft.suggested_company_name || '',
        document_number: draft.document_number || '',
        document_date: draft.document_date || null,
        currency: draft.currency || 'AED',
        subtotal: draft.subtotal || null,
        vat_total: draft.vat_total || null,
        total: draft.total || null,
      });
      setImportDrafts((current) => {
        const next = { ...current };
        delete next[entry.id];
        return next;
      });
      await load();
      await refreshSelectedBatch();
      setNotice({ type: 'success', message: 'Document details saved.' });
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

  const saveSuggestionEdits = async (suggestion) => {
    if (!suggestion || workingAction) return;
    const suggestionDraft = suggestionDrafts[suggestion.id] || {};
    const lineDraft = suggestion.line ? lineDrafts[suggestion.line] : null;
    setWorkingAction(`save-suggestion-${suggestion.id}`);
    setNotice(null);
    setErrorInfo(null);
    try {
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
      await loadSuggestions(selectedBatch.id);
      await refreshSelectedBatch();
      setNotice({ type: 'success', message: 'Review row saved.' });
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
    const ids = groupSuggestions.filter((suggestion) => suggestion.status === 'pending').map((suggestion) => suggestion.id);
    setSelectedSuggestionIds((current) => (append ? Array.from(new Set([...current, ...ids])) : ids));
  };

  const applySelectedSuggestions = async (ids = selectedSuggestionIds) => {
    if (!ids.length || workingAction) return;
    if (!window.confirm('Apply selected AI decisions? This may create approved draft Products, aliases, or company links, but will not commit price history.')) return;
    setWorkingAction('apply');
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.historicalImportBatches.applyAiSuggestions(selectedBatch.id, { suggestion_ids: ids });
      setNotice({
        type: response.data.summary.conflict ? 'warning' : 'success',
        message: `AI decisions applied: ${response.data.summary.applied || 0}, conflicts: ${response.data.summary.conflict || 0}, failed: ${response.data.summary.failed || 0}.`,
      });
      setSelectedSuggestionIds([]);
      await load();
      await refreshSelectedBatch();
      await loadSuggestions(selectedBatch.id);
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

  const commitSelectedBatchImports = async () => {
    if (!selectedBatch || workingAction) return;
    const importIds = selectedBatchImportIds.length ? selectedBatchImportIds : visibleBatchImportIds;
    if (!importIds.length) return;
    if (!window.confirm('Commit ready rows from the selected imports into price history? Needs-review and skipped rows are ignored.')) return;
    setWorkingAction('commit');
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.historicalImportBatches.commitReadyImports(selectedBatch.id, { import_ids: importIds });
      setNotice({
        type: response.data.summary.failed ? 'warning' : 'success',
        message: `Batch commit complete: ${response.data.summary.committed || 0} committed, ${response.data.summary.failed || 0} failed.`,
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
    const lineSuggestions = suggestions.filter((suggestion) => suggestion.suggestion_type === 'line');
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
    return groups;
  }, [suggestions]);

  const companySuggestions = useMemo(() => (
    suggestions.filter((suggestion) => suggestion.suggestion_type === 'company')
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
        <button type="button" className="qm-primary" disabled={!selectedBatch || workingAction === 'ai'} onClick={runBatchAiAnalyze}>
          {workingAction === 'ai' ? 'Running AI Analyze...' : 'Run AI Analyze'}
        </button>
      </div>
      {!selectedBatch ? (
        <div className="qm-empty">Upload or open a batch first.</div>
      ) : (
        <>
          {renderBatchSummary()}
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
        <div className="qm-document-grid">
          {companySuggestions.length > 0 && (
            <div className="qm-company-suggestions">
              <h4>AI Company Suggestions</h4>
              {companySuggestions.map((suggestion) => {
                const draft = suggestionDrafts[suggestion.id] || {};
                return (
                  <div key={suggestion.id} className={`qm-decision-card status-${suggestion.status}`}>
                    <div>
                      <strong>{suggestion.historical_import_filename}</strong>
                      <p>{ACTION_LABELS[draft.action || suggestion.action]} - {confidencePercent(suggestion.confidence)}%</p>
                      <small>{suggestion.reason || '-'}</small>
                    </div>
                    <select value={draft.action || suggestion.action} disabled={suggestion.status !== 'pending'} onChange={(event) => updateSuggestionDraft(suggestion.id, { action: event.target.value })}>
                      <option value="match_existing_company">Match existing company</option>
                      <option value="create_new_company">Create new company</option>
                      <option value="needs_manual_review">Needs manual review</option>
                    </select>
                    {(draft.action || suggestion.action) === 'match_existing_company' ? (
                      <select value={draft.suggested_company || ''} disabled={suggestion.status !== 'pending'} onChange={(event) => updateSuggestionDraft(suggestion.id, { suggested_company: event.target.value || '' })}>
                        <option value="">Select company</option>
                        {companies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}
                      </select>
                    ) : (
                      <input value={draft.proposed_company_name || ''} disabled={suggestion.status !== 'pending'} onChange={(event) => updateSuggestionDraft(suggestion.id, { proposed_company_name: event.target.value })} placeholder="New company name" />
                    )}
                    <div className="qm-action-row">
                      <button type="button" className="qm-secondary small" disabled={suggestion.status !== 'pending' || Boolean(workingAction)} onClick={() => saveSuggestionEdits(suggestion)}>Save</button>
                      <button type="button" className="qm-primary small" disabled={suggestion.status !== 'pending' || Boolean(workingAction)} onClick={() => applySelectedSuggestions([suggestion.id])}>Apply</button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          {selectedBatchImports.map((entry) => {
            const draft = draftForImport(entry);
            const duplicateCheck = entry.duplicate_check || entry.parse_meta?.duplicate_check;
            return (
              <div key={entry.id} className="qm-document-card-compact">
                <div className="qm-card-title-row">
                  <div>
                    <h4>{entry.source_filename}</h4>
                    <p>{entry.lines?.length || 0} rows - <span className={`qm-badge status-${entry.status}`}>{entry.status}</span></p>
                  </div>
                  {duplicateCheck?.is_duplicate && <span className="qm-badge status-duplicate">duplicate</span>}
                </div>
                {duplicateCheck?.is_duplicate && (
                  <div className="qm-notice warning compact">
                    <strong>{duplicateCheck.message}</strong>
                    <p>{duplicateHelperText(duplicateCheck)}</p>
                  </div>
                )}
                <div className="qm-details-grid">
                  <label>
                    <span className="qm-label-text">Company</span>
                    <CompanySelectWithCreate
                      companies={companies}
                      value={draft.company || ''}
                      disabled={entry.status === 'committed'}
                      initialName={draft.suggested_company_name || ''}
                      suggestedName={draft.suggested_company_name || ''}
                      onChange={(companyId) => updateImportDraft(entry.id, { company: companyId })}
                      onCreated={rememberCompany}
                    />
                  </label>
                  <label><span className="qm-label-text">Document number</span>
                    <input disabled={entry.status === 'committed'} value={draft.document_number || ''} onChange={(event) => updateImportDraft(entry.id, { document_number: event.target.value })} />
                  </label>
                  <label><span className="qm-label-text">Document date</span>
                    <input disabled={entry.status === 'committed'} type="date" value={draft.document_date || ''} onChange={(event) => updateImportDraft(entry.id, { document_date: event.target.value })} />
                  </label>
                  <label><span className="qm-label-text">Total</span>
                    <input disabled={entry.status === 'committed'} type="number" step="0.01" value={draft.total || ''} onChange={(event) => updateImportDraft(entry.id, { total: event.target.value })} />
                  </label>
                </div>
                <button type="button" className="qm-secondary small" disabled={Boolean(workingAction) || entry.status === 'committed'} onClick={() => saveImportDetails(entry)}>
                  {workingAction === `save-import-${entry.id}` ? 'Saving...' : 'Save Document'}
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );

  const renderDecisionCard = (suggestion) => {
    const draft = suggestionDrafts[suggestion.id] || {};
    const lineDraft = suggestion.line ? (lineDrafts[suggestion.line] || emptyLineDraft) : emptyLineDraft;
    const locked = suggestion.status !== 'pending';
    return (
      <div key={suggestion.id} className={`qm-decision-card status-${suggestion.status}`}>
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
              <span>Price {lineDraft.unit_price || '-'}</span>
              <span>Total {lineDraft.line_total || '-'}</span>
              <span className={`qm-badge ${confidencePercent(suggestion.confidence) >= 85 ? 'success' : 'muted'}`}>{confidencePercent(suggestion.confidence)}%</span>
              <span className={`qm-badge status-${suggestion.status}`}>{suggestion.status}</span>
            </div>
          </div>
        </div>

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
            <label><span className="qm-label-text">Row status</span>
              <select value={lineDraft.status || 'needs_review'} disabled={suggestion.line_status === 'committed'} onChange={(event) => updateLineDraft(suggestion.line, { status: event.target.value })}>
                <option value="needs_review">Needs review</option>
                <option value="ready">Ready</option>
                <option value="skipped">Skipped</option>
              </select>
            </label>
          </div>
        )}

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
          <button type="button" className="qm-secondary small" disabled={Boolean(workingAction)} onClick={() => saveSuggestionEdits(suggestion)}>
            {workingAction === `save-suggestion-${suggestion.id}` ? 'Saving...' : 'Save Edits'}
          </button>
          <button type="button" className="qm-primary small" disabled={locked || Boolean(workingAction)} onClick={() => applySelectedSuggestions([suggestion.id])}>Apply This</button>
        </div>
      </div>
    );
  };

  const renderDecisionGroup = (key, title, helper, groupSuggestions, options = {}) => {
    const isCollapsed = key === 'skip' && !expandedGroups.skip;
    const pending = groupSuggestions.filter((suggestion) => suggestion.status === 'pending');
    const highConfidence = pending.filter((suggestion) => confidencePercent(suggestion.confidence) >= 85);
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
              <>
                <button type="button" className="qm-secondary small" disabled={!pending.length} onClick={() => selectSuggestionGroup(pending)}>Select Pending</button>
                <button type="button" className="qm-primary small" disabled={!highConfidence.length || Boolean(workingAction)} onClick={() => applySelectedSuggestions(highConfidence.map((suggestion) => suggestion.id))}>
                  Approve High Confidence
                </button>
              </>
            )}
          </div>
        </div>
        {!isCollapsed && (
          <div className={options.compact ? 'qm-decision-list compact' : 'qm-decision-list'}>
            {groupSuggestions.map(renderDecisionCard)}
            {!groupSuggestions.length && <div className="qm-empty compact">No rows in this group.</div>}
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
          <button type="button" className="qm-secondary" onClick={() => setActiveStep('commit')}>Final Review</button>
        </div>
      </div>
      {!selectedBatch ? (
        <div className="qm-empty">Open a batch first.</div>
      ) : (
        <>
          <div className="qm-bulk-toolbar compact">
            <strong>{selectedSuggestionIds.length} AI decisions selected</strong>
            <button type="button" className="qm-secondary small" disabled={!selectedSuggestionIds.length} onClick={() => setSelectedSuggestionIds([])}>Clear</button>
            <span className="qm-bulk-spacer" />
            <button type="button" className="qm-primary small" disabled={!selectedSuggestionIds.length || Boolean(workingAction)} onClick={() => applySelectedSuggestions()}>Apply Selected</button>
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
          <p>Nothing durable is committed until this step. Only ready rows create company-specific price history.</p>
        </div>
        <button type="button" className="qm-primary" disabled={!selectedBatch || workingAction === 'commit' || !(lineCounts.ready > 0)} onClick={commitSelectedBatchImports}>
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
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {selectedBatchImports.map((entry) => {
                  const ready = (entry.lines || []).filter((line) => line.status === 'ready').length;
                  const needsReview = (entry.lines || []).filter((line) => line.status === 'needs_review').length;
                  return (
                    <tr key={entry.id}>
                      <td className="qm-check-cell"><input type="checkbox" checked={selectedBatchImportIds.includes(entry.id)} onChange={() => toggleBatchImportSelection(entry.id)} /></td>
                      <td>{entry.source_filename}</td>
                      <td>{entry.company_name || entry.suggested_company_name || '-'}</td>
                      <td>{entry.document_number || '-'}</td>
                      <td>{entry.lines?.length || 0}</td>
                      <td>{ready}</td>
                      <td>{needsReview}</td>
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

      {activeStep === 'upload' && renderUploadStep()}
      {activeStep === 'analyze' && renderAnalyzeStep()}
      {activeStep === 'companies' && renderCompanyStep()}
      {activeStep === 'decisions' && renderDecisionStep()}
      {activeStep === 'commit' && renderCommitStep()}
    </div>
  );
};

export default HistoricalImportManager;
