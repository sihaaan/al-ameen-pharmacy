import React, { useMemo, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import QuotationErrorNotice from './QuotationErrorNotice';

const emptyCompanyForm = {
  name: '',
  email: '',
  phone: '',
  billing_address: '',
  trn: '',
  notes: '',
};

const sortCompanies = (companies) => [...companies].sort((a, b) => a.name.localeCompare(b.name));

const normalizeCompanyName = (name) => (name || '').trim().replace(/\s+/g, ' ').toLowerCase();

const CompanySelectWithCreate = ({
  companies = [],
  value,
  onChange,
  onCreated,
  disabled = false,
  required = false,
  label = 'Company',
  placeholder = 'Select company',
  initialName = '',
  suggestedName = '',
  helperText = '',
}) => {
  const [search, setSearch] = useState('');
  const [isCreating, setIsCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState(emptyCompanyForm);
  const [errorInfo, setErrorInfo] = useState(null);
  const [notice, setNotice] = useState(null);

  const filteredCompanies = useMemo(() => {
    const term = search.trim().toLowerCase();
    const sorted = sortCompanies(companies);
    if (!term) return sorted;
    return sorted.filter((company) => (
      company.name.toLowerCase().includes(term) ||
      (company.email || '').toLowerCase().includes(term) ||
      (company.phone || '').toLowerCase().includes(term)
    ));
  }, [companies, search]);

  const selectedCompany = useMemo(
    () => companies.find((company) => String(company.id) === String(value)),
    [companies, value]
  );

  const openCreate = () => {
    setErrorInfo(null);
    setNotice(null);
    setForm((current) => ({
      ...current,
      name: current.name || search || initialName || suggestedName || '',
    }));
    setIsCreating(true);
  };

  const useSuggestedCompany = () => {
    if (!suggestedName || disabled) return;
    setErrorInfo(null);
    setNotice(null);

    const normalizedSuggestion = normalizeCompanyName(suggestedName);
    const existingCompany = companies.find((company) => normalizeCompanyName(company.name) === normalizedSuggestion);
    if (existingCompany) {
      if (onChange) onChange(String(existingCompany.id), existingCompany);
      setNotice({ type: 'success', message: `Selected existing company ${existingCompany.name}.` });
      return;
    }

    setSearch('');
    setForm((current) => ({
      ...current,
      name: suggestedName,
    }));
    setIsCreating(true);
  };

  const cancelCreate = () => {
    setIsCreating(false);
    setForm(emptyCompanyForm);
    setErrorInfo(null);
  };

  const saveCompany = async () => {
    if (saving) return;
    if (!form.name.trim()) {
      setNotice({ type: 'error', message: 'Enter a company name before saving.' });
      return;
    }
    setSaving(true);
    setErrorInfo(null);
    setNotice(null);
    try {
      const response = await quotationAPI.companies.create({
        ...form,
        name: form.name.trim(),
        is_active: true,
      });
      const company = response.data;
      if (onCreated) onCreated(company);
      if (onChange) onChange(String(company.id), company);
      setSearch('');
      setForm(emptyCompanyForm);
      setIsCreating(false);
      setNotice({ type: 'success', message: 'Company created and selected.' });
    } catch (error) {
      const details = await describeQuotationError(error, 'Create company inline', 'POST /quotations/companies/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="qm-company-picker">
      <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
      {notice && <div className={`qm-feedback ${notice.type}`}>{notice.message}</div>}
      <div className="qm-company-picker-label">
        <span className="qm-label-text">{label} {required && <span className="qm-required">*</span>}</span>
      </div>
      {suggestedName && (
        <div className="qm-company-suggestion">
          <div>
            <span>Suggested from file</span>
            <strong>{suggestedName}</strong>
          </div>
          <button type="button" className="qm-secondary small" disabled={disabled} onClick={useSuggestedCompany}>
            Use suggestion
          </button>
        </div>
      )}
      <div className={`qm-company-picker-grid${isCreating ? ' creating' : ''}`}>
        <input
          className="qm-input"
          disabled={disabled}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search companies"
        />
        <select
          required={required}
          disabled={disabled}
          value={value || ''}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="">{placeholder}</option>
          {filteredCompanies.map((company) => (
            <option key={company.id} value={company.id}>{company.name}</option>
          ))}
        </select>
        {!isCreating && (
          <button type="button" className="qm-secondary" disabled={disabled} onClick={openCreate} aria-expanded={isCreating}>
            + New Company
          </button>
        )}
      </div>
      {selectedCompany && (
        <div className="qm-selected-company">
          <span>Selected company:</span>
          <strong>{selectedCompany.name}</strong>
        </div>
      )}
      {helperText && <p className="qm-helper compact">{helperText}</p>}
      {isCreating && (
        <div className="qm-inline-create">
          <div className="qm-inline-create-heading">
            <div>
              <strong>New Company</strong>
              <p>Create this company if it does not already exist.</p>
            </div>
          </div>
          <div className="qm-grid-two">
            <label>
              <span className="qm-label-text">Company name <span className="qm-required">*</span></span>
              <input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="Company name" />
            </label>
            <label>
              <span className="qm-label-text">Email</span>
              <input type="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} placeholder="company@example.com" />
            </label>
            <label>
              <span className="qm-label-text">Phone</span>
              <input value={form.phone} onChange={(event) => setForm({ ...form, phone: event.target.value })} placeholder="+971..." />
            </label>
            <label>
              <span className="qm-label-text">TRN</span>
              <input value={form.trn} onChange={(event) => setForm({ ...form, trn: event.target.value })} placeholder="TRN if available" />
            </label>
          </div>
          <label>
            <span className="qm-label-text">Billing address</span>
            <textarea rows="2" value={form.billing_address} onChange={(event) => setForm({ ...form, billing_address: event.target.value })} />
          </label>
          <div className="qm-action-row">
            <button type="button" className="qm-primary" disabled={saving} onClick={saveCompany}>
              {saving ? 'Creating...' : 'Create Company'}
            </button>
            <button type="button" className="qm-secondary" onClick={cancelCreate} disabled={saving}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
};

export default CompanySelectWithCreate;
