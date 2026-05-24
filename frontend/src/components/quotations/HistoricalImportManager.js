import React, { useEffect, useMemo, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import CompanySelectWithCreate from './CompanySelectWithCreate';
import QuotationErrorNotice from './QuotationErrorNotice';

const statusOptions = [
  { value: 'needs_review', label: 'Needs Review' },
  { value: 'ready', label: 'Ready' },
  { value: 'skipped', label: 'Skipped' },
];

const HistoricalImportManager = () => {
  const [companies, setCompanies] = useState([]);
  const [items, setItems] = useState([]);
  const [imports, setImports] = useState([]);
  const [selectedImport, setSelectedImport] = useState(null);
  const [uploadFile, setUploadFile] = useState(null);
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

  const load = async () => {
    setLoading(true);
    setErrorInfo(null);
    try {
      const [companiesRes, itemsRes, importsRes] = await Promise.all([
        quotationAPI.companies.list({ active: 'true' }),
        quotationAPI.items.list({ active: 'true' }),
        quotationAPI.historicalImports.list(),
      ]);
      setCompanies(companiesRes.data);
      setItems(itemsRes.data);
      setImports(importsRes.data);
      if (selectedImport) {
        const refreshed = importsRes.data.find((entry) => entry.id === selectedImport.id);
        if (refreshed) setSelectedImport(refreshed);
      }
    } catch (error) {
      const details = await describeQuotationError(
        error,
        'Load historical imports',
        'GET /quotations/historical-imports/'
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
  }, [selectedImport?.id]);

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
    const formData = new FormData();
    formData.append('file', uploadFile);
    try {
      const response = await quotationAPI.historicalImports.parseFile(formData);
      setSelectedImport(response.data);
      setHeaderDirty(false);
      setNotice({ type: 'success', message: 'Historical quotation parsed. Review company, date, items, and prices before committing.' });
      await load();
    } catch (error) {
      const details = await describeQuotationError(error, 'Parse historical PDF', 'POST /quotations/historical-imports/parse_file/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setUploading(false);
    }
  };

  const selectImport = async (entry) => {
    setNotice(null);
    setErrorInfo(null);
    setSelectedImport(entry);
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
