import React, { useEffect, useMemo, useRef, useState } from 'react';
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
  const [similarCompanies, setSimilarCompanies] = useState([]);
  const [checkingSimilar, setCheckingSimilar] = useState(false);
  const [allowSimilarCreate, setAllowSimilarCreate] = useState(false);
  const [errorInfo, setErrorInfo] = useState(null);
  const [notice, setNotice] = useState(null);
  const externallyDisabledRef = useRef(disabled);
  externallyDisabledRef.current = disabled;

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

  useEffect(() => {
    if (!isCreating) return undefined;
    const name = form.name.trim();
    setAllowSimilarCreate(false);
    if (name.length < 3) {
      setSimilarCompanies([]);
      setCheckingSimilar(false);
      return undefined;
    }

    let cancelled = false;
    setCheckingSimilar(true);
    const timer = setTimeout(async () => {
      try {
        const response = await quotationAPI.companies.similar({ name, active: 'true' });
        if (!cancelled) setSimilarCompanies(response.data.suggestions || []);
      } catch (error) {
        if (!cancelled) setSimilarCompanies([]);
      } finally {
        if (!cancelled) setCheckingSimilar(false);
      }
    }, 250);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [form.name, isCreating]);

  const openCreate = () => {
    if (disabled) return;
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
    setSimilarCompanies([]);
    setAllowSimilarCreate(false);
    setErrorInfo(null);
  };

  const selectExistingCompany = (company) => {
    if (disabled) return;
    if (onChange) onChange(String(company.id), company);
    setSearch('');
    setForm(emptyCompanyForm);
    setSimilarCompanies([]);
    setAllowSimilarCreate(false);
    setIsCreating(false);
    setNotice({ type: 'success', message: `Selected existing company ${company.name}.` });
  };

  const saveCompany = async () => {
    if (saving || disabled) return;
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
        allow_similar: allowSimilarCreate,
      });
      const company = response.data;
      if (onCreated) onCreated(company);
      const canSelectCreatedCompany = !externallyDisabledRef.current;
      if (canSelectCreatedCompany && onChange) onChange(String(company.id), company);
      setSearch('');
      setForm(emptyCompanyForm);
      setSimilarCompanies([]);
      setAllowSimilarCreate(false);
      setIsCreating(false);
      setNotice({
        type: 'success',
        message: canSelectCreatedCompany
          ? (allowSimilarCreate ? 'Company created after duplicate check and selected.' : 'Company created and selected.')
          : 'Company created. Select it after the current operation finishes.',
      });
    } catch (error) {
      const backendData = error?.response?.data || {};
      const suggestions = backendData.similar_companies || [];
      if (suggestions.length) {
        setSimilarCompanies(suggestions);
        setAllowSimilarCreate(Boolean(backendData.requires_confirmation));
        setNotice({
          type: 'warning',
          message: backendData.requires_confirmation
            ? 'This looks like an existing company. Select it, or click Create anyway if this is truly different.'
            : 'This company already exists. Select the existing company below.',
        });
        setErrorInfo(null);
        return;
      }
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
          onChange={(event) => {
            if (!disabled && onChange) onChange(event.target.value);
          }}
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
              <input
                disabled={disabled || saving}
                value={form.name}
                onChange={(event) => setForm({ ...form, name: event.target.value })}
                placeholder="Company name"
              />
            </label>
            <label>
              <span className="qm-label-text">Email</span>
              <input disabled={disabled || saving} type="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} placeholder="company@example.com" />
            </label>
            <label>
              <span className="qm-label-text">Phone</span>
              <input disabled={disabled || saving} value={form.phone} onChange={(event) => setForm({ ...form, phone: event.target.value })} placeholder="+971..." />
            </label>
            <label>
              <span className="qm-label-text">TRN</span>
              <input disabled={disabled || saving} value={form.trn} onChange={(event) => setForm({ ...form, trn: event.target.value })} placeholder="TRN if available" />
            </label>
          </div>
          {(checkingSimilar || similarCompanies.length > 0) && (
            <div className="qm-duplicate-suggestions">
              <div className="qm-duplicate-suggestions-heading">
                <strong>Possible existing company</strong>
                <span>{checkingSimilar ? 'Checking...' : `${similarCompanies.length} suggestion${similarCompanies.length === 1 ? '' : 's'}`}</span>
              </div>
              {similarCompanies.map((company) => (
                <div className="qm-duplicate-suggestion" key={company.id}>
                  <div>
                    <strong>{company.name}</strong>
                    <small>{company.reason} Match score {company.score}%</small>
                    {(company.phone || company.email || company.trn) && (
                      <small>{[company.phone, company.email, company.trn && `TRN ${company.trn}`].filter(Boolean).join(' | ')}</small>
                    )}
                  </div>
                  <button type="button" className="qm-secondary small" disabled={disabled || saving} onClick={() => selectExistingCompany(company)}>
                    Select existing
                  </button>
                </div>
              ))}
            </div>
          )}
          <label>
            <span className="qm-label-text">Billing address</span>
            <textarea disabled={disabled || saving} rows="2" value={form.billing_address} onChange={(event) => setForm({ ...form, billing_address: event.target.value })} />
          </label>
          <div className="qm-action-row">
            <button type="button" className="qm-primary" disabled={disabled || saving} onClick={saveCompany}>
              {saving ? 'Creating...' : allowSimilarCreate ? 'Create anyway' : 'Create Company'}
            </button>
            <button type="button" className="qm-secondary" onClick={cancelCreate} disabled={saving}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
};

export default CompanySelectWithCreate;
