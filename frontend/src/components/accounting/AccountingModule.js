import React, { useEffect, useMemo, useState } from 'react';
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
];

const formatMoney = (value) => `AED ${Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

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
  const [customers, setCustomers] = useState([]);
  const [selectedCustomer, setSelectedCustomer] = useState(null);
  const [uploadFile, setUploadFile] = useState(null);
  const [categoryFile, setCategoryFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [downloading, setDownloading] = useState('');
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [filters, setFilters] = useState({ search: '', status: 'due', category: '', emailMissing: false });
  const [editForm, setEditForm] = useState({ email: '', category: 'unknown', is_ignored: false, notes: '' });

  const selectedImportId = selectedImport?.id;

  const loadImports = async () => {
    const [dashboardRes, importsRes] = await Promise.all([
      accountingAPI.dashboard.retrieve(),
      accountingAPI.imports.list(),
    ]);
    setDashboard(dashboardRes.data);
    setImports(importsRes.data);
    if (!selectedImport && importsRes.data.length) {
      setSelectedImport(importsRes.data[0]);
    }
  };

  const loadCustomers = async () => {
    if (!selectedImportId) {
      setCustomers([]);
      return;
    }
    setLoading(true);
    setError('');
    try {
      const params = { import_id: selectedImportId };
      if (filters.search) params.search = filters.search;
      if (filters.status === 'due') params.due_only = true;
      if (filters.status && filters.status !== 'due') params.status = filters.status;
      if (filters.category) params.category = filters.category;
      if (filters.emailMissing) params.email_missing = true;
      const response = await accountingAPI.importCustomers.list(params);
      setCustomers(response.data);
    } catch (err) {
      setError((await describeAccountingError(err, 'Load accounting customers', 'GET /accounting/import-customers/')).detail);
    } finally {
      setLoading(false);
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
  }, [selectedImportId, filters.status, filters.category, filters.emailMissing]);

  useEffect(() => {
    if (!selectedCustomer) return;
    setEditForm({
      email: selectedCustomer.email || '',
      category: selectedCustomer.category || 'unknown',
      is_ignored: !!selectedCustomer.is_ignored,
      notes: '',
    });
  }, [selectedCustomer]);

  const filteredImport = useMemo(() => selectedImport || imports[0] || null, [selectedImport, imports]);

  const uploadImport = async (event) => {
    event.preventDefault();
    if (!uploadFile) {
      setError('Choose the monthly outstanding CSV/XLSX file first.');
      return;
    }
    setUploading(true);
    setNotice('');
    setError('');
    const formData = new FormData();
    formData.append('file', uploadFile);
    if (categoryFile) formData.append('category_file', categoryFile);
    try {
      const response = await accountingAPI.imports.upload(formData);
      setNotice(response.data.duplicate ? response.data.duplicate_message : 'Accounting import parsed successfully.');
      setSelectedImport(response.data);
      setUploadFile(null);
      setCategoryFile(null);
      await loadImports();
    } catch (err) {
      const info = await describeAccountingError(err, 'Upload accounting import', 'POST /accounting/imports/upload/');
      setError(info.detail);
    } finally {
      setUploading(false);
    }
  };

  const openCustomer = async (customer) => {
    setError('');
    try {
      const response = await accountingAPI.importCustomers.retrieve(customer.id);
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
      setSelectedCustomer(response.data);
      setNotice('Customer accounting details saved.');
      await loadCustomers();
      await loadImports();
    } catch (err) {
      setError((await describeAccountingError(err, 'Save accounting customer', `PATCH /accounting/import-customers/${selectedCustomer.id}/`)).detail);
    } finally {
      setSaving(false);
    }
  };

  const downloadStatement = async (customer = selectedCustomer) => {
    if (!customer) return;
    setDownloading(`pdf-${customer.id}`);
    setError('');
    try {
      const response = await accountingAPI.importCustomers.statementPdf(customer.id);
      const filename = customer.email_preview?.attachment_filename || `${customer.customer_name || 'statement'}.pdf`;
      saveBlob(response.data, filename);
    } catch (err) {
      setError((await describeAccountingError(err, 'Download statement PDF', `GET /accounting/import-customers/${customer.id}/statement_pdf/`)).detail);
    } finally {
      setDownloading('');
    }
  };

  const downloadZip = async () => {
    if (!filteredImport) return;
    setDownloading('zip');
    setError('');
    try {
      const response = await accountingAPI.imports.statementsZip(filteredImport.id);
      saveBlob(response.data, `accounting-statements-${filteredImport.id}.zip`);
    } catch (err) {
      setError((await describeAccountingError(err, 'Download accounting statements ZIP', `GET /accounting/imports/${filteredImport.id}/statements_zip/`)).detail);
    } finally {
      setDownloading('');
    }
  };

  return (
    <div className="accounting-module">
      <div className="accounting-header">
        <div>
          <h2>Accounting</h2>
          <p>Upload agewise outstanding exports, review due customers, and prepare statement files.</p>
        </div>
        {filteredImport && (
          <button type="button" className="accounting-primary" onClick={downloadZip} disabled={downloading === 'zip'}>
            {downloading === 'zip' ? 'Preparing ZIP...' : 'Download Due Statements ZIP'}
          </button>
        )}
      </div>

      <div className="accounting-notice">
        This does not send emails yet. It only prepares statement files and email previews for review and download.
      </div>
      {notice && <div className="accounting-notice">{notice}</div>}
      {error && <div className="accounting-error">{error}</div>}

      <div className="accounting-grid">
        <section className="accounting-panel">
          <h3>Upload Monthly Outstanding</h3>
          <p>Upload the POS agewise outstanding CSV/XLSX export. Source files are parsed and discarded.</p>
          <form className="accounting-form" onSubmit={uploadImport}>
            <label>
              Outstanding export *
              <input type="file" accept=".csv,.xlsx" onChange={(event) => setUploadFile(event.target.files?.[0] || null)} />
            </label>
            <label>
              Customer category workbook
              <input type="file" accept=".xlsx" onChange={(event) => setCategoryFile(event.target.files?.[0] || null)} />
            </label>
            <button type="submit" className="accounting-primary" disabled={uploading}>
              {uploading ? 'Parsing...' : 'Upload and Parse'}
            </button>
          </form>
        </section>

        <section className="accounting-panel">
          <div className="accounting-panel-heading">
            <div>
              <h3>Import Summary</h3>
              <p>{filteredImport ? filteredImport.source_filename : 'No accounting imports yet.'}</p>
            </div>
            <button type="button" className="accounting-secondary" onClick={() => loadImports()}>
              Refresh
            </button>
          </div>
          <div className="accounting-stat-grid">
            <div className="accounting-stat"><span>{filteredImport?.parsed_row_count || 0}</span><p>Parsed rows</p></div>
            <div className="accounting-stat"><span>{filteredImport?.customer_count || 0}</span><p>Customers</p></div>
            <div className="accounting-stat"><span>{filteredImport?.due_customer_count || 0}</span><p>Due</p></div>
            <div className="accounting-stat"><span>{dashboard?.email_missing_count || 0}</span><p>Email missing</p></div>
          </div>
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
                <tr><th>File</th><th>Date</th><th>Rows</th><th>Due</th></tr>
              </thead>
              <tbody>
                {imports.map((item) => (
                  <tr key={item.id} className={filteredImport?.id === item.id ? 'active-row' : ''} onClick={() => setSelectedImport(item)}>
                    <td>{item.source_filename}</td>
                    <td>{item.report_date || '-'}</td>
                    <td>{item.parsed_row_count}</td>
                    <td>{item.due_customer_count}</td>
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
            <p>Default view shows customers with invoices older than 30 days or overdue ageing buckets.</p>
          </div>
        </div>
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
          <label className="accounting-muted">
            <input
              type="checkbox"
              checked={filters.emailMissing}
              onChange={(event) => setFilters((current) => ({ ...current, emailMissing: event.target.checked }))}
            /> Email missing
          </label>
        </div>
        <div className="accounting-table-wrap">
          <table className="accounting-table">
            <thead>
              <tr>
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
              {loading && <tr><td colSpan="10">Loading customers...</td></tr>}
              {!loading && customers.length === 0 && <tr><td colSpan="10">No customers match this filter.</td></tr>}
              {customers.map((customer) => (
                <tr key={customer.id} className={selectedCustomer?.id === customer.id ? 'active-row' : ''}>
                  <td>{customer.customer_code || '-'}</td>
                  <td>{customer.customer_name}</td>
                  <td>{customer.category}</td>
                  <td>{customer.email || <span className="accounting-badge warning">Missing</span>}</td>
                  <td>{formatMoney(customer.total_outstanding)}</td>
                  <td>{formatMoney(customer.overdue_amount)}</td>
                  <td>{customer.max_days}</td>
                  <td>{customer.invoice_count}</td>
                  <td><span className={`accounting-badge ${customer.is_due ? 'due' : 'ready'}`}>{customer.status}</span></td>
                  <td className="accounting-actions">
                    <button type="button" className="accounting-secondary" onClick={() => openCustomer(customer)}>View</button>
                    <button type="button" className="accounting-secondary" onClick={() => downloadStatement(customer)} disabled={downloading === `pdf-${customer.id}`}>
                      PDF
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {selectedCustomer && (
        <section className="accounting-detail">
          <div className="accounting-panel-heading">
            <div>
              <h3>{selectedCustomer.customer_name}</h3>
              <p>{selectedCustomer.customer_code || 'No code'} · {selectedCustomer.invoice_count} invoices</p>
            </div>
            <button type="button" className="accounting-primary" onClick={() => downloadStatement()} disabled={downloading === `pdf-${selectedCustomer.id}`}>
              {downloading === `pdf-${selectedCustomer.id}` ? 'Downloading...' : 'Download Statement PDF'}
            </button>
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
                <tr><th>Bill No.</th><th>Date</th><th>Amount</th><th>0-30</th><th>30-60</th><th>60-90</th><th>Over 90</th><th>Total</th><th>Days</th></tr>
              </thead>
              <tbody>
                {selectedCustomer.invoice_rows?.map((invoice) => (
                  <tr key={invoice.id}>
                    <td>{invoice.bill_number}</td>
                    <td>{invoice.invoice_date || '-'}</td>
                    <td>{formatMoney(invoice.amount)}</td>
                    <td>{formatMoney(invoice.bucket_0_30)}</td>
                    <td>{formatMoney(invoice.bucket_30_60)}</td>
                    <td>{formatMoney(invoice.bucket_60_90)}</td>
                    <td>{formatMoney(invoice.bucket_over_90)}</td>
                    <td>{formatMoney(invoice.total)}</td>
                    <td>{invoice.days}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
};

export default AccountingModule;
