import React, { useEffect, useMemo, useRef, useState } from 'react';
import accountingAPI, { describeAccountingError } from '../../api/accounting';
import './AccountingModule.css';

const categories = [
  { value: '', label: 'All categories' },
  { value: 'credit', label: 'Credit' },
  { value: 'insurance', label: 'Insurance' },
  { value: 'clinic', label: 'Clinic' },
  { value: 'branch', label: 'Branch' },
  { value: 'card', label: 'Card' },
  { value: 'misc', label: 'Misc' },
  { value: 'unknown', label: 'Unknown' },
];

const statusOptions = [
  { value: 'due', label: 'Due only' },
  { value: '', label: 'All statuses' },
  { value: 'not_due', label: 'Not due' },
  { value: 'ignored', label: 'Ignored' },
  { value: 'needs_review', label: 'Needs review' },
  { value: 'email_missing', label: 'Email missing' },
];

const ageingOptions = [
  { value: '', label: 'All ageing' },
  { value: 'over_30', label: 'Over 30 days' },
  { value: 'over_60', label: 'Over 60 days' },
  { value: 'over_90', label: 'Over 90 days' },
  { value: 'has_30_60', label: 'Has 30-60 balance' },
  { value: 'has_60_90', label: 'Has 60-90 balance' },
  { value: 'has_over_90', label: 'Has Over 90 balance' },
];

const sortOptions = [
  { value: '-overdue_amount', label: 'Highest overdue' },
  { value: '-total_outstanding', label: 'Highest total' },
  { value: '-max_days', label: 'Oldest invoice' },
  { value: 'company', label: 'Company A-Z' },
  { value: '-invoice_count', label: 'Most invoices' },
];

const categoryLabel = (value) => categories.find((option) => option.value === value)?.label || value || '-';
const formatMoney = (value) => `AED ${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const statementPeriodText = (customer) => {
  const period = customer?.statement_period || {};
  if (period.display) return period.display;
  const start = period.display_from || period.from;
  const end = period.display_to || period.to;
  if (start && end) return start === end ? start : `${start} to ${end}`;
  return start || end || 'No invoice rows';
};

const saveBlob = (blob, filename) => {
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  window.URL.revokeObjectURL(url);
};

const AccountingModule = () => {
  const [dashboard, setDashboard] = useState(null);
  const [imports, setImports] = useState([]);
  const [selectedImport, setSelectedImport] = useState(null);
  const [duplicateImport, setDuplicateImport] = useState(null);
  const [customers, setCustomers] = useState([]);
  const [selectedCustomer, setSelectedCustomer] = useState(null);
  const [uploadFile, setUploadFile] = useState(null);
  const [categoryFile, setCategoryFile] = useState(null);
  const [applyCategoryFile, setApplyCategoryFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [applyingCategories, setApplyingCategories] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savingEmailId, setSavingEmailId] = useState(null);
  const [downloading, setDownloading] = useState('');
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [emailDrafts, setEmailDrafts] = useState({});
  const [selectedCustomerIds, setSelectedCustomerIds] = useState([]);
  const [filters, setFilters] = useState({ search: '', status: 'due', category: '', ageing: '', ordering: '-overdue_amount' });
  const [dateRange, setDateRange] = useState({ from: '', to: '' });
  const [appliedDateRange, setAppliedDateRange] = useState({ from: '', to: '' });
  const [editForm, setEditForm] = useState({ email: '', category: 'unknown', is_ignored: false, notes: '' });
  const customerLoadSeq = useRef(0);
  const uploadFileInputRef = useRef(null);
  const categoryFileInputRef = useRef(null);
  const applyCategoryInputRef = useRef(null);

  const selectedImportId = selectedImport?.id;
  const filteredImport = useMemo(() => selectedImport || null, [selectedImport]);
  const statementParams = () => ({
    ...(appliedDateRange.from ? { date_from: appliedDateRange.from } : {}),
    ...(appliedDateRange.to ? { date_to: appliedDateRange.to } : {}),
  });

  const loadImports = async () => {
    const [dashboardRes, importsRes] = await Promise.all([
      accountingAPI.dashboard.retrieve(),
      accountingAPI.imports.list(),
    ]);
    setDashboard(dashboardRes.data);
    setImports(importsRes.data);
    setSelectedImport((current) => (current ? importsRes.data.find((item) => item.id === current.id) || current : null));
  };

  const loadCustomers = async () => {
    if (!selectedImportId) {
      customerLoadSeq.current += 1;
      setCustomers([]);
      return;
    }
    const requestId = customerLoadSeq.current + 1;
    customerLoadSeq.current = requestId;
    setLoading(true);
    setError('');
    setCustomers([]);
    setSelectedCustomerIds([]);
    try {
      const params = { import_id: selectedImportId };
      if (filters.search) params.search = filters.search;
      if (filters.status === 'due') params.due_only = true;
      if (filters.status === 'email_missing') params.email_missing = true;
      if (filters.status && !['due', 'email_missing'].includes(filters.status)) params.status = filters.status;
      if (filters.category) params.category = filters.category;
      if (filters.ageing) params.ageing = filters.ageing;
      if (filters.ordering) params.ordering = filters.ordering;
      Object.assign(params, statementParams());
      const response = await accountingAPI.importCustomers.list(params);
      if (requestId !== customerLoadSeq.current) return;
      setCustomers(response.data);
      setSelectedCustomerIds((current) => current.filter((id) => response.data.some((customer) => customer.id === id)));
      setEmailDrafts((current) => {
        const next = { ...current };
        response.data.forEach((customer) => {
          if (next[customer.id] === undefined) next[customer.id] = customer.email || '';
        });
        return next;
      });
    } catch (err) {
      if (requestId !== customerLoadSeq.current) return;
      setError((await describeAccountingError(err, 'Load accounting customers', 'GET /accounting/import-customers/')).detail);
    } finally {
      if (requestId === customerLoadSeq.current) {
        setLoading(false);
      }
    }
  };

  useEffect(() => {
    loadImports().catch(async (err) => {
      setError((await describeAccountingError(err, 'Load accounting dashboard', 'GET /accounting/dashboard/')).detail);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    loadCustomers();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedImportId, filters.status, filters.category, filters.ageing, filters.ordering, appliedDateRange.from, appliedDateRange.to]);

  useEffect(() => {
    if (!selectedCustomer) return;
    setEditForm({
      email: selectedCustomer.email || '',
      category: selectedCustomer.category || 'unknown',
      is_ignored: !!selectedCustomer.is_ignored,
      notes: selectedCustomer.customer_notes || '',
    });
  }, [selectedCustomer]);

  const uploadImport = async (event) => {
    event.preventDefault();
    if (!uploadFile) {
      setError('Choose the monthly outstanding CSV/XLSX file first.');
      return;
    }
    setUploading(true);
    setNotice('');
    setError('');
    setDuplicateImport(null);
    const formData = new FormData();
    formData.append('file', uploadFile);
    if (categoryFile) formData.append('category_file', categoryFile);
    try {
      const response = await accountingAPI.imports.upload(formData);
      const categoryUpdate = response.data.category_update;
      const categoryText = response.data.category_update_message
        || (categoryUpdate?.matched !== undefined
          ? ` Category workbook applied. Matched ${categoryUpdate.matched} customers: ${categoryUpdate.updated || 0} updated, ${categoryUpdate.unchanged || 0} already up to date, ${categoryUpdate.unmatched || 0} unmatched.`
          : '');
      if (response.data.duplicate) {
        setNotice(response.data.duplicate_message || 'This outstanding file has already been uploaded before. No duplicate import was created.');
        setDuplicateImport(response.data);
        setSelectedImport(null);
      } else {
        setNotice(response.data.message || `Accounting import parsed successfully.${categoryText ? ` ${categoryText}` : ''}`);
        setSelectedImport(response.data);
      }
      clearUploadInputs();
      await loadImports();
      if (!response.data.duplicate) {
        setSelectedImport(response.data);
      }
    } catch (err) {
      const info = await describeAccountingError(err, 'Upload accounting import', 'POST /accounting/imports/upload/');
      setError(info.detail);
    } finally {
      setUploading(false);
    }
  };

  const applyCategories = async (event) => {
    event.preventDefault();
    if (!filteredImport || !applyCategoryFile) {
      setError('Choose a category workbook first.');
      return;
    }
    setApplyingCategories(true);
    setNotice('');
    setError('');
    const formData = new FormData();
    formData.append('category_file', applyCategoryFile);
    try {
      const response = await accountingAPI.imports.applyCategories(filteredImport.id, formData);
      const update = response.data.category_update || {};
      setNotice(response.data.message || `Category workbook applied. Matched ${update.matched || 0} customers: ${update.updated || 0} updated, ${update.unchanged || 0} already up to date, ${update.unmatched || 0} unmatched.`);
      setSelectedImport(response.data);
      setApplyCategoryFile(null);
      if (applyCategoryInputRef.current) applyCategoryInputRef.current.value = '';
      await loadImports();
      await loadCustomers();
    } catch (err) {
      setError((await describeAccountingError(err, 'Apply category workbook', `POST /accounting/imports/${filteredImport.id}/apply_categories/`)).detail);
    } finally {
      setApplyingCategories(false);
    }
  };

  const openCustomer = async (customer) => {
    setError('');
    try {
      const response = await accountingAPI.importCustomers.retrieve(customer.id, statementParams());
      setSelectedCustomer(response.data);
    } catch (err) {
      setError((await describeAccountingError(err, 'Load accounting customer detail', `GET /accounting/import-customers/${customer.id}/`)).detail);
    }
  };

  const saveCustomer = async () => {
    if (!selectedCustomer) return;
    setSaving(true);
    setNotice('');
    setError('');
    try {
      const response = await accountingAPI.importCustomers.update(selectedCustomer.id, editForm);
      const detailResponse = await accountingAPI.importCustomers.retrieve(response.data.id, statementParams());
      setSelectedCustomer(detailResponse.data);
      setEmailDrafts((current) => ({ ...current, [response.data.id]: response.data.email || '' }));
      setNotice('Customer accounting details saved.');
      await loadCustomers();
      await loadImports();
    } catch (err) {
      setError((await describeAccountingError(err, 'Save accounting customer', `PATCH /accounting/import-customers/${selectedCustomer.id}/`)).detail);
    } finally {
      setSaving(false);
    }
  };

  const saveRowEmail = async (customer) => {
    setSavingEmailId(customer.id);
    setError('');
    try {
      const response = await accountingAPI.importCustomers.update(customer.id, { email: emailDrafts[customer.id] || '' });
      setNotice(`Email saved for ${customer.customer_name}.`);
      if (selectedCustomer?.id === customer.id) {
        const detailResponse = await accountingAPI.importCustomers.retrieve(response.data.id, statementParams());
        setSelectedCustomer(detailResponse.data);
      }
      await loadCustomers();
      await loadImports();
    } catch (err) {
      setError((await describeAccountingError(err, 'Save customer email', `PATCH /accounting/import-customers/${customer.id}/`)).detail);
    } finally {
      setSavingEmailId(null);
    }
  };

  const toggleIgnored = async (customer) => {
    setSavingEmailId(customer.id);
    setError('');
    try {
      await accountingAPI.importCustomers.update(customer.id, { is_ignored: !customer.is_ignored });
      setNotice(customer.is_ignored ? `${customer.customer_name} is included again.` : `${customer.customer_name} is ignored for ZIP downloads.`);
      await loadCustomers();
      await loadImports();
    } catch (err) {
      setError((await describeAccountingError(err, 'Update ignored status', `PATCH /accounting/import-customers/${customer.id}/`)).detail);
    } finally {
      setSavingEmailId(null);
    }
  };

  const downloadStatement = async (customer = selectedCustomer) => {
    if (!customer) return;
    setDownloading(`statement-${customer.id}`);
    setError('');
    try {
      const response = await accountingAPI.importCustomers.statementPdf(customer.id, 'statement', statementParams());
      const baseName = customer.email_preview?.attachment_filename || `${customer.customer_name || 'statement'}.pdf`;
      const periodSuffix = appliedDateRange.from || appliedDateRange.to ? '_filtered' : '';
      const filename = baseName.replace(/(_classic|_professional)?\.pdf$/i, `${periodSuffix}.pdf`);
      saveBlob(response.data, filename);
    } catch (err) {
      setError((await describeAccountingError(err, 'Download statement PDF', `GET /accounting/import-customers/${customer.id}/statement_pdf/`)).detail);
    } finally {
      setDownloading('');
    }
  };

  const downloadStatementExcel = async (customer = selectedCustomer) => {
    if (!customer) return;
    setDownloading(`excel-${customer.id}`);
    setError('');
    try {
      const response = await accountingAPI.importCustomers.statementExcel(customer.id, statementParams());
      const baseName = customer.email_preview?.attachment_filename || `${customer.customer_name || 'statement'}.pdf`;
      const periodSuffix = appliedDateRange.from || appliedDateRange.to ? '_filtered' : '';
      const filename = baseName.replace(/\.pdf$/i, `${periodSuffix}.xlsx`);
      saveBlob(response.data, filename);
    } catch (err) {
      setError((await describeAccountingError(err, 'Download statement Excel', `GET /accounting/import-customers/${customer.id}/statement_excel/`)).detail);
    } finally {
      setDownloading('');
    }
  };

  const downloadZip = async (selectedOnly = false) => {
    if (!filteredImport) return;
    const customerIds = selectedOnly ? selectedCustomerIds : [];
    if (selectedOnly && customerIds.length === 0) {
      setError('Select at least one due customer before downloading a selected ZIP.');
      return;
    }
    setDownloading(selectedOnly ? 'zip-selected' : 'zip');
    setError('');
    try {
      const response = await accountingAPI.imports.statementsZip(filteredImport.id, 'statement', customerIds, statementParams());
      const periodSuffix = appliedDateRange.from || appliedDateRange.to ? '-filtered' : '';
      saveBlob(response.data, `accounting-statements-${selectedOnly ? 'selected-' : ''}${filteredImport.id}${periodSuffix}.zip`);
    } catch (err) {
      setError((await describeAccountingError(err, 'Download accounting statements ZIP', `GET /accounting/imports/${filteredImport.id}/statements_zip/`)).detail);
    } finally {
      setDownloading('');
    }
  };

  const downloadExcelZip = async (selectedOnly = false) => {
    if (!filteredImport) return;
    const customerIds = selectedOnly ? selectedCustomerIds : [];
    if (selectedOnly && customerIds.length === 0) {
      setError('Select at least one due customer before downloading a selected Excel ZIP.');
      return;
    }
    setDownloading(selectedOnly ? 'excel-zip-selected' : 'excel-zip');
    setError('');
    try {
      const response = await accountingAPI.imports.statementsExcelZip(filteredImport.id, customerIds, statementParams());
      const periodSuffix = appliedDateRange.from || appliedDateRange.to ? '-filtered' : '';
      saveBlob(response.data, `accounting-excel-statements-${selectedOnly ? 'selected-' : ''}${filteredImport.id}${periodSuffix}.zip`);
    } catch (err) {
      setError((await describeAccountingError(err, 'Download accounting Excel statements ZIP', `GET /accounting/imports/${filteredImport.id}/statements_excel_zip/`)).detail);
    } finally {
      setDownloading('');
    }
  };

  const zipLimit = filteredImport?.zip_sync_limit || 75;
  const largeZipBatched = !!filteredImport && filteredImport.due_customer_count > zipLimit;
  const visibleSelectableIds = customers.filter((customer) => customer.is_due && !customer.is_ignored).map((customer) => customer.id);
  const allVisibleSelected = visibleSelectableIds.length > 0 && visibleSelectableIds.every((id) => selectedCustomerIds.includes(id));

  const toggleVisibleSelection = () => {
    if (allVisibleSelected) {
      setSelectedCustomerIds((current) => current.filter((id) => !visibleSelectableIds.includes(id)));
    } else {
      setSelectedCustomerIds((current) => Array.from(new Set([...current, ...visibleSelectableIds])));
    }
  };

  const toggleCustomerSelection = (customer) => {
    if (!customer.is_due || customer.is_ignored) return;
    setSelectedCustomerIds((current) => (
      current.includes(customer.id)
        ? current.filter((id) => id !== customer.id)
            : [...current, customer.id]
    ));
  };

  const clearSelection = () => setSelectedCustomerIds([]);

  const applyDateFilter = async () => {
    customerLoadSeq.current += 1;
    setAppliedDateRange({ ...dateRange });
    if (selectedCustomer) {
      setSelectedCustomer(null);
    }
  };

  const clearDateFilter = async () => {
    customerLoadSeq.current += 1;
    setDateRange({ from: '', to: '' });
    setAppliedDateRange({ from: '', to: '' });
    if (selectedCustomer) {
      setSelectedCustomer(null);
    }
  };

  const hasDateFilter = !!(appliedDateRange.from || appliedDateRange.to);
  const openImport = (item) => {
    setSelectedImport(item);
    setDuplicateImport(null);
    setSelectedCustomer(null);
    setSelectedCustomerIds([]);
    setError('');
  };

  const clearUploadInputs = () => {
    setUploadFile(null);
    setCategoryFile(null);
    if (uploadFileInputRef.current) uploadFileInputRef.current.value = '';
    if (categoryFileInputRef.current) categoryFileInputRef.current.value = '';
  };

  return (
    <div className="accounting-module">
      <div className="accounting-header">
        <div>
          <h2>Accounting</h2>
          <p>Upload agewise outstanding exports, review due customers, and prepare statement files.</p>
        </div>
        {filteredImport && (
          <div className="accounting-actions">
            <button type="button" className="accounting-primary" onClick={() => downloadZip(false)} disabled={downloading === 'zip'}>
              {downloading === 'zip' ? 'Preparing all due...' : 'Download All Due Statements'}
            </button>
            <button type="button" className="accounting-secondary" onClick={() => downloadExcelZip(false)} disabled={downloading === 'excel-zip'}>
              {downloading === 'excel-zip' ? 'Preparing Excel...' : 'Download All Due Excel'}
            </button>
          </div>
        )}
      </div>

      <div className="accounting-notice">
        This does not send emails yet. It only prepares statement files and email previews for review and download.
      </div>
      {notice && <div className="accounting-notice">{notice}</div>}
      {duplicateImport && (
        <div className="accounting-warning accounting-duplicate-banner">
          <div>
            <strong>Duplicate import detected.</strong>
            <p>No duplicate import was created. Open the previous import only if you want to review its customers.</p>
          </div>
          <button type="button" className="accounting-secondary" onClick={() => openImport(duplicateImport)}>
            Open existing import
          </button>
        </div>
      )}
      {error && <div className="accounting-error">{error}</div>}
      {largeZipBatched && (
        <div className="accounting-warning">
          This import has {filteredImport.due_customer_count} due customers. Full ZIP downloads are automatically split into batches of {zipLimit} statements inside one download. Use Ignore to exclude customers, select visible rows for a smaller ZIP, or apply a statement date range first.
        </div>
      )}

      <div className="accounting-grid">
        <section className="accounting-panel">
          <h3>Upload Monthly Outstanding</h3>
          <p>Upload the POS agewise outstanding CSV/XLSX export. Source files are parsed and discarded.</p>
          <form className="accounting-form" onSubmit={uploadImport}>
            <label>
              Outstanding export *
              <input ref={uploadFileInputRef} type="file" accept=".csv,.xlsx" onChange={(event) => setUploadFile(event.target.files?.[0] || null)} />
            </label>
            <label>
              Customer category workbook
              <input ref={categoryFileInputRef} type="file" accept=".xlsx" onChange={(event) => setCategoryFile(event.target.files?.[0] || null)} />
            </label>
            <button type="submit" className="accounting-primary" disabled={uploading}>
              {uploading ? 'Parsing...' : 'Upload and Parse'}
            </button>
          </form>
        </section>

        <section className="accounting-panel">
          <div className="accounting-panel-heading">
            <div>
              <h3>Import History</h3>
              <p>
                Select an import to review due customers, or upload a new monthly outstanding file.
                {dashboard?.import_count ? ` ${dashboard.import_count} imports tracked.` : ''}
              </p>
            </div>
            <button type="button" className="accounting-secondary" onClick={() => loadImports()}>
              Refresh
            </button>
          </div>
          {filteredImport ? (
            <div className="accounting-stat-grid">
              <div className="accounting-stat"><span>{filteredImport.parsed_row_count || 0}</span><p>Parsed rows</p></div>
              <div className="accounting-stat"><span>{filteredImport.customer_count || 0}</span><p>Customers</p></div>
              <div className="accounting-stat"><span>{filteredImport.due_customer_count || 0}</span><p>Due</p></div>
              <div className="accounting-stat"><span>{filteredImport.email_missing_count || 0}</span><p>Email missing</p></div>
            </div>
          ) : (
            <div className="accounting-empty-state">
              No import is open. Choose <strong>Open / Review</strong> from the import history below.
            </div>
          )}
          {filteredImport && (
            <form className="accounting-inline-form" onSubmit={applyCategories}>
              <label>
                Apply/update categories for this import
                <input ref={applyCategoryInputRef} type="file" accept=".xlsx" onChange={(event) => setApplyCategoryFile(event.target.files?.[0] || null)} />
              </label>
              <button type="submit" className="accounting-secondary" disabled={applyingCategories}>
                {applyingCategories ? 'Applying...' : 'Apply Categories'}
              </button>
            </form>
          )}
          {filteredImport?.warnings?.length > 0 && (
            <div className="accounting-warning">
              <strong>Warnings</strong>
              <ul>
                {filteredImport.warnings.slice(0, 6).map((warning, index) => <li key={index}>{warning}</li>)}
              </ul>
            </div>
          )}
          <div className="accounting-table-wrap">
            <table className="accounting-table">
              <thead>
                <tr>
                  <th>File</th>
                  <th>Report Date</th>
                  <th>Uploaded</th>
                  <th>By</th>
                  <th>Rows</th>
                  <th>Customers</th>
                  <th>Due</th>
                  <th>Email Missing</th>
                  <th>Status</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {imports.length === 0 && (
                  <tr><td colSpan="10">No accounting imports yet.</td></tr>
                )}
                {imports.map((item) => (
                  <tr key={item.id} className={filteredImport?.id === item.id ? 'active-row' : ''}>
                    <td>{item.source_filename}</td>
                    <td>{item.report_date_display || item.report_date || '-'}</td>
                    <td>{item.created_at_display || (item.created_at ? new Date(item.created_at).toLocaleString() : '-')}</td>
                    <td>{item.uploaded_by_name || '-'}</td>
                    <td>{item.parsed_row_count}</td>
                    <td>{item.customer_count}</td>
                    <td>{item.due_customer_count}</td>
                    <td>{item.email_missing_count ?? '-'}</td>
                    <td><span className="accounting-badge">{item.status}</span></td>
                    <td>
                      <button type="button" className="accounting-secondary" onClick={() => openImport(item)}>
                        {filteredImport?.id === item.id ? 'Open' : 'Open / Review'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <section className="accounting-panel">
        <div className="accounting-panel-heading">
          <div>
            <h3>Due Customers</h3>
            <p>
              {filteredImport
                ? `Reviewing ${filteredImport.source_filename}. Default view shows customers with invoices older than 30 days or overdue ageing buckets.`
                : 'Select an import to review due customers, or upload a new monthly outstanding file.'}
            </p>
          </div>
        </div>
        {!filteredImport ? (
          <div className="accounting-empty-state">
            No import selected. Customer rows and download actions will appear here after you open an import.
          </div>
        ) : (
          <>
        <div className="accounting-filter-row">
          <input
            placeholder="Search customer, code, or email"
            value={filters.search}
            onChange={(event) => setFilters((current) => ({ ...current, search: event.target.value }))}
            onBlur={loadCustomers}
          />
          <select value={filters.status} onChange={(event) => setFilters((current) => ({ ...current, status: event.target.value }))}>
            {statusOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          <select value={filters.category} onChange={(event) => setFilters((current) => ({ ...current, category: event.target.value }))}>
            {categories.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          <select value={filters.ageing} onChange={(event) => setFilters((current) => ({ ...current, ageing: event.target.value }))}>
            {ageingOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          <select value={filters.ordering} onChange={(event) => setFilters((current) => ({ ...current, ordering: event.target.value }))}>
            {sortOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
          <button type="button" className="accounting-secondary" onClick={loadCustomers}>Search</button>
        </div>
        <div className="accounting-date-filter">
          <div>
            <strong>Statement invoice date range</strong>
            <p>Limits customer totals, detail rows, PDFs, and ZIP statements to invoice dates in this period.</p>
          </div>
          <label>From date<input type="date" value={dateRange.from} onChange={(event) => setDateRange((current) => ({ ...current, from: event.target.value }))} /></label>
          <label>To date<input type="date" value={dateRange.to} onChange={(event) => setDateRange((current) => ({ ...current, to: event.target.value }))} /></label>
          <button type="button" className="accounting-primary" onClick={applyDateFilter}>Apply filter</button>
          <button type="button" className="accounting-secondary" onClick={clearDateFilter} disabled={!dateRange.from && !dateRange.to && !hasDateFilter}>Clear filter</button>
          {hasDateFilter && <span className="accounting-badge ready">Filtered statement period active</span>}
        </div>
        <div className="accounting-selection-bar">
          <span>{selectedCustomerIds.length} selected</span>
          <button type="button" className="accounting-secondary" onClick={toggleVisibleSelection} disabled={visibleSelectableIds.length === 0}>
            {allVisibleSelected ? 'Clear Visible' : 'Select Visible'}
          </button>
          <button type="button" className="accounting-secondary" onClick={clearSelection} disabled={selectedCustomerIds.length === 0}>
            Clear Selection
          </button>
          <button type="button" className="accounting-primary" onClick={() => downloadZip(true)} disabled={selectedCustomerIds.length === 0 || downloading === 'zip-selected'}>
            {downloading === 'zip-selected' ? 'Preparing...' : 'Download Selected Statements ZIP'}
          </button>
          <button type="button" className="accounting-secondary" onClick={() => downloadExcelZip(true)} disabled={selectedCustomerIds.length === 0 || downloading === 'excel-zip-selected'}>
            {downloading === 'excel-zip-selected' ? 'Preparing Excel...' : 'Download Selected Excel ZIP'}
          </button>
        </div>
        <div className="accounting-table-wrap">
          <table className="accounting-table accounting-customers-table">
            <thead>
              <tr>
                <th>
                  <input
                    type="checkbox"
                    checked={allVisibleSelected}
                    onChange={toggleVisibleSelection}
                    aria-label="Select visible due customers"
                  />
                </th>
                <th>Code</th>
                <th>Company</th>
                <th>Type</th>
                <th>Email</th>
                <th>Total</th>
                <th>Overdue</th>
                <th>Max Days</th>
                <th>Invoices</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading && <tr><td colSpan="11">Loading customers...</td></tr>}
              {!loading && customers.length === 0 && (
                <tr>
                  <td colSpan="11">
                    {hasDateFilter ? 'No customers found for this date range.' : 'No customers match this filter.'}
                  </td>
                </tr>
              )}
              {customers.map((customer) => (
                <tr key={customer.id} className={selectedCustomer?.id === customer.id ? 'active-row' : ''}>
                  <td>
                    <input
                      type="checkbox"
                      checked={selectedCustomerIds.includes(customer.id)}
                      disabled={!customer.is_due || customer.is_ignored}
                      onChange={() => toggleCustomerSelection(customer)}
                      aria-label={`Select ${customer.customer_name}`}
                    />
                  </td>
                  <td>{customer.customer_code || '-'}</td>
                  <td>{customer.customer_name}</td>
                  <td>{categoryLabel(customer.category)}</td>
                  <td>
                    <div className="accounting-email-cell">
                      <input
                        value={emailDrafts[customer.id] ?? customer.email ?? ''}
                        placeholder="Email address"
                        onChange={(event) => setEmailDrafts((current) => ({ ...current, [customer.id]: event.target.value }))}
                      />
                      <button
                        type="button"
                        className="accounting-secondary"
                        onClick={() => saveRowEmail(customer)}
                        disabled={savingEmailId === customer.id}
                      >
                        {savingEmailId === customer.id ? 'Saving...' : 'Save'}
                      </button>
                    </div>
                    {!customer.email && <span className="accounting-badge warning">Email missing</span>}
                    {customer.email && <span className="accounting-badge ready">Ready</span>}
                  </td>
                  <td>{formatMoney(customer.total_outstanding)}</td>
                  <td>{formatMoney(customer.overdue_amount)}</td>
                  <td>{customer.max_days}</td>
                  <td>{customer.invoice_count}</td>
                  <td><span className={`accounting-badge ${customer.is_due ? 'due' : 'ready'}`}>{customer.status}</span></td>
                  <td className="accounting-actions">
                    <button type="button" className="accounting-secondary" onClick={() => openCustomer(customer)}>View</button>
                    <button type="button" className="accounting-secondary" onClick={() => downloadStatement(customer)} disabled={downloading === `statement-${customer.id}`}>
                      Statement PDF
                    </button>
                    <button type="button" className="accounting-secondary" onClick={() => downloadStatementExcel(customer)} disabled={downloading === `excel-${customer.id}`}>
                      Statement Excel
                    </button>
                    <button type="button" className={customer.is_ignored ? 'accounting-secondary' : 'accounting-danger'} onClick={() => toggleIgnored(customer)} disabled={savingEmailId === customer.id}>
                      {customer.is_ignored ? 'Unignore' : 'Ignore'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
          </>
        )}
      </section>

      {selectedCustomer && (
        <div className="accounting-drawer-backdrop" role="presentation" onClick={() => setSelectedCustomer(null)}>
          <aside className="accounting-drawer" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
            <div className="accounting-panel-heading">
              <div>
                <h3>{selectedCustomer.customer_name}</h3>
                <p>{selectedCustomer.customer_code || 'No code'} - {selectedCustomer.invoice_count} invoices</p>
              </div>
              <button type="button" className="accounting-secondary" onClick={() => setSelectedCustomer(null)}>Close</button>
            </div>

            <div className="accounting-detail-actions">
              <button type="button" className="accounting-primary" onClick={() => downloadStatement(selectedCustomer)} disabled={downloading === `statement-${selectedCustomer.id}`}>
                Download Statement PDF
              </button>
              <button type="button" className="accounting-secondary" onClick={() => downloadStatementExcel(selectedCustomer)} disabled={downloading === `excel-${selectedCustomer.id}`}>
                Download Statement Excel
              </button>
            </div>

            <div className="accounting-drawer-summary">
              <div><span>Total Outstanding</span><strong>{formatMoney(selectedCustomer.total_outstanding)}</strong></div>
              <div><span>Overdue &gt; 30 Days</span><strong>{formatMoney(selectedCustomer.overdue_amount)}</strong></div>
              <div><span>Max Days</span><strong>{selectedCustomer.max_days}</strong></div>
              <div><span>Email Status</span><strong>{selectedCustomer.email ? 'Ready' : 'Missing'}</strong></div>
            </div>
            <div className="accounting-notice">
              Statement period: {statementPeriodText(selectedCustomer)}
            </div>

            <div className="accounting-edit-grid">
              <label>Email<input value={editForm.email} onChange={(event) => setEditForm((current) => ({ ...current, email: event.target.value }))} /></label>
              <label>Category<select value={editForm.category} onChange={(event) => setEditForm((current) => ({ ...current, category: event.target.value }))}>
                {categories.filter((option) => option.value).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select></label>
              <label>Notes<textarea rows="3" value={editForm.notes} onChange={(event) => setEditForm((current) => ({ ...current, notes: event.target.value }))} /></label>
              <label><span>Ignore in statement ZIP</span><select value={editForm.is_ignored ? 'true' : 'false'} onChange={(event) => setEditForm((current) => ({ ...current, is_ignored: event.target.value === 'true' }))}>
                <option value="false">No</option>
                <option value="true">Yes</option>
              </select></label>
            </div>
            <button type="button" className="accounting-primary" onClick={saveCustomer} disabled={saving}>
              {saving ? 'Saving...' : 'Save Customer Details'}
            </button>

            <h3>Email Preview</h3>
            <div className="accounting-email-preview">
              <strong>Status:</strong> {selectedCustomer.email_preview?.email_status}<br />
              <strong>Subject:</strong> {selectedCustomer.email_preview?.subject}<br />
              <strong>Attachment:</strong> {selectedCustomer.email_preview?.attachment_filename}<br /><br />
              {selectedCustomer.email_preview?.body}
            </div>

            <div className="accounting-table-wrap">
              <table className="accounting-table">
                <thead>
                  <tr>
                    <th>Invoice No.</th>
                    <th>Doc Type</th>
                    <th>LPO / Reference No.</th>
                    <th>Invoice Date</th>
                    <th>Debit</th>
                    <th>Credit</th>
                    <th>Balance</th>
                    <th>Days</th>
                  </tr>
                </thead>
                <tbody>
                  {selectedCustomer.ledger_rows?.map((invoice) => (
                    <tr key={invoice.id}>
                      <td>{invoice.invoice_number}</td>
                      <td>{invoice.doc_type}</td>
                      <td>{invoice.lpo_reference || '-'}</td>
                      <td>{invoice.invoice_date_display || invoice.invoice_date || '-'}</td>
                      <td>{formatMoney(invoice.debit)}</td>
                      <td>{formatMoney(invoice.credit)}</td>
                      <td>{formatMoney(invoice.balance)}</td>
                      <td>{invoice.days}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </aside>
        </div>
      )}
    </div>
  );
};

export default AccountingModule;
