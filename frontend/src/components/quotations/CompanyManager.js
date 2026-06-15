import React, { useEffect, useMemo, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import QuotationErrorNotice from './QuotationErrorNotice';

const emptyCompany = {
  name: '',
  email: '',
  phone: '',
  billing_address: '',
  trn: '',
  notes: '',
  is_active: true,
};

const emptyContact = {
  name: '',
  email: '',
  phone: '',
  role: '',
  department: '',
  is_primary: false,
  is_active: true,
};

const CompanyManager = () => {
  const [companies, setCompanies] = useState([]);
  const [selectedCompany, setSelectedCompany] = useState(null);
  const [form, setForm] = useState(emptyCompany);
  const [contactForm, setContactForm] = useState(emptyContact);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState(null);
  const [errorInfo, setErrorInfo] = useState(null);
  const [similarCompanies, setSimilarCompanies] = useState([]);
  const [checkingSimilar, setCheckingSimilar] = useState(false);
  const [allowSimilarCreate, setAllowSimilarCreate] = useState(false);

  const loadCompanies = async () => {
    setLoading(true);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.companies.list();
      setCompanies(response.data);
    } catch (error) {
      const details = await describeQuotationError(error, 'Load companies', 'GET /quotations/companies/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadCompanies();
  }, []);

  useEffect(() => {
    if (selectedCompany) return undefined;
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
  }, [form.name, selectedCompany]);

  const filteredCompanies = useMemo(() => {
    const term = search.toLowerCase();
    return companies.filter((company) =>
      company.name.toLowerCase().includes(term) ||
      (company.email || '').toLowerCase().includes(term) ||
      (company.phone || '').toLowerCase().includes(term)
    );
  }, [companies, search]);

  const reset = () => {
    setSelectedCompany(null);
    setForm(emptyCompany);
    setContactForm(emptyContact);
    setSimilarCompanies([]);
    setCheckingSimilar(false);
    setAllowSimilarCreate(false);
  };

  const applyCompanyToForm = (company) => {
    setSelectedCompany(company);
    setForm({
      name: company.name || '',
      email: company.email || '',
      phone: company.phone || '',
      billing_address: company.billing_address || '',
      trn: company.trn || '',
      notes: company.notes || '',
      is_active: company.is_active,
    });
    setContactForm(emptyContact);
    setSimilarCompanies([]);
    setCheckingSimilar(false);
    setAllowSimilarCreate(false);
  };

  const editCompany = async (company) => {
    applyCompanyToForm({ ...company, contacts: company.contacts || [] });
    setErrorInfo(null);
    try {
      const response = await quotationAPI.companies.retrieve(company.id);
      applyCompanyToForm(response.data);
    } catch (error) {
      const details = await describeQuotationError(error, 'Load company details', `GET /quotations/companies/${company.id}/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    }
  };

  const saveCompany = async (event) => {
    event.preventDefault();
    setSaving(true);
    setNotice(null);
    setErrorInfo(null);
    try {
      if (selectedCompany) {
        await quotationAPI.companies.update(selectedCompany.id, form);
      } else {
        await quotationAPI.companies.create({ ...form, allow_similar: allowSimilarCreate });
      }
      setNotice({
        type: 'success',
        message: selectedCompany
          ? 'Company updated.'
          : allowSimilarCreate
            ? 'Company created after duplicate check.'
            : 'Company created.',
      });
      reset();
      await loadCompanies();
    } catch (error) {
      const backendData = error?.response?.data || {};
      const suggestions = backendData.similar_companies || [];
      if (!selectedCompany && suggestions.length) {
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
      const details = await describeQuotationError(
        error,
        selectedCompany ? 'Update company' : 'Create company',
        selectedCompany ? `PATCH /quotations/companies/${selectedCompany.id}/` : 'POST /quotations/companies/'
      );
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const deleteOrDeactivateCompany = async () => {
    if (!selectedCompany || saving) return;
    if (!window.confirm(`Delete or deactivate "${selectedCompany.name}"? Companies with quotation history are deactivated instead of deleted.`)) return;
    setSaving(true);
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.companies.delete(selectedCompany.id);
      setNotice({
        type: 'success',
        message: response.status === 200 ? 'Company was deactivated because it has history.' : 'Unused company was deleted.',
      });
      reset();
      await loadCompanies();
    } catch (error) {
      const details = await describeQuotationError(error, 'Delete/deactivate company', `DELETE /quotations/companies/${selectedCompany.id}/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const saveContact = async (event) => {
    event.preventDefault();
    if (!selectedCompany) return;
    setSaving(true);
    setErrorInfo(null);
    try {
      await quotationAPI.contacts.create({
        ...contactForm,
        company: selectedCompany.id,
      });
      const updated = await quotationAPI.companies.retrieve(selectedCompany.id);
      applyCompanyToForm(updated.data);
    } catch (error) {
      const details = await describeQuotationError(error, 'Create company contact', 'POST /quotations/contacts/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="qm-section">
      <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
      {notice && <div className={`qm-feedback ${notice.type}`}>{notice.message}</div>}
      <div className="qm-split">
        <div className="qm-panel">
        <div className="qm-panel-heading">
          <h3>Companies</h3>
          <input
            className="qm-input"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search companies"
          />
        </div>
        {loading ? (
          <div className="qm-loading">Loading companies...</div>
        ) : (
          <div className="qm-table-wrap">
            <table className="qm-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Email</th>
                  <th>Phone</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {filteredCompanies.map((company) => (
                  <tr
                    key={company.id}
                    className={selectedCompany?.id === company.id ? 'selected' : ''}
                    onClick={() => editCompany(company)}
                  >
                    <td>{company.name}</td>
                    <td>{company.email || '-'}</td>
                    <td>{company.phone || '-'}</td>
                    <td><span className={`qm-badge ${company.is_active ? 'success' : 'muted'}`}>{company.is_active ? 'Active' : 'Inactive'}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        </div>

        <div className="qm-panel">
        <div className="qm-panel-heading">
          <h3>{selectedCompany ? 'Edit Company' : 'New Company'}</h3>
          {selectedCompany && <button type="button" className="qm-secondary" onClick={reset}>New</button>}
        </div>
        <form onSubmit={saveCompany} className="qm-form">
          <label>Name<input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
          {!selectedCompany && (checkingSimilar || similarCompanies.length > 0) && (
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
                  <button type="button" className="qm-secondary small" onClick={() => editCompany(company)}>
                    Select existing
                  </button>
                </div>
              ))}
            </div>
          )}
          <label>Email<input type="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })} /></label>
          <label>Phone<input value={form.phone} onChange={(event) => setForm({ ...form, phone: event.target.value })} /></label>
          <label>TRN<input value={form.trn} onChange={(event) => setForm({ ...form, trn: event.target.value })} /></label>
          <label>Billing Address<textarea rows="3" value={form.billing_address} onChange={(event) => setForm({ ...form, billing_address: event.target.value })} /></label>
          <label>Notes<textarea rows="2" value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} /></label>
          <label className="qm-checkbox"><input type="checkbox" checked={form.is_active} onChange={(event) => setForm({ ...form, is_active: event.target.checked })} /> Active</label>
          <div className="qm-action-row">
            <button type="submit" className="qm-primary" disabled={saving}>{saving ? 'Saving...' : allowSimilarCreate && !selectedCompany ? 'Create anyway' : 'Save Company'}</button>
            {selectedCompany && (
              <button type="button" className="qm-secondary danger" disabled={saving} onClick={deleteOrDeactivateCompany}>
                Delete / Deactivate
              </button>
            )}
          </div>
        </form>

        {selectedCompany && (
          <div className="qm-subpanel">
            <h4>Contacts</h4>
            <div className="qm-contact-list">
              {(selectedCompany.contacts || []).map((contact) => (
                <div key={contact.id} className="qm-contact-row">
                  <strong>{contact.name}</strong>
                  <span>
                    {[contact.role, contact.department].filter(Boolean).join(' - ') || 'Purchaser contact'}
                    {(contact.phone || contact.email) ? ` | ${contact.phone || contact.email}` : ''}
                  </span>
                  {contact.is_primary && <span className="qm-badge success">Primary</span>}
                </div>
              ))}
            </div>
            <form onSubmit={saveContact} className="qm-form compact">
              <label>Name<input required value={contactForm.name} onChange={(event) => setContactForm({ ...contactForm, name: event.target.value })} /></label>
              <label>Email<input type="email" value={contactForm.email} onChange={(event) => setContactForm({ ...contactForm, email: event.target.value })} /></label>
              <label>Phone<input value={contactForm.phone} onChange={(event) => setContactForm({ ...contactForm, phone: event.target.value })} /></label>
              <label>Position / Designation<input value={contactForm.role} onChange={(event) => setContactForm({ ...contactForm, role: event.target.value })} /></label>
              <label>Department<input value={contactForm.department} onChange={(event) => setContactForm({ ...contactForm, department: event.target.value })} /></label>
              <label className="qm-checkbox"><input type="checkbox" checked={contactForm.is_primary} onChange={(event) => setContactForm({ ...contactForm, is_primary: event.target.checked })} /> Primary</label>
              <button type="submit" className="qm-secondary" disabled={saving}>Add Contact</button>
            </form>
          </div>
        )}
        </div>
      </div>
    </div>
  );
};

export default CompanyManager;
