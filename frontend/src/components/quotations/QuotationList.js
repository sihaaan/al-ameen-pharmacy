import React, { useEffect, useMemo, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import CompanySelectWithCreate from './CompanySelectWithCreate';
import QuotationErrorNotice from './QuotationErrorNotice';

const statusLabels = {
  draft: 'Draft',
  pending_review: 'Pending Review',
  approved: 'Approved',
  finalized: 'Finalized',
  sent: 'Sent',
  revised: 'Revised',
  cancelled: 'Cancelled',
};

const contactOptionLabel = (contact) => {
  const details = [contact.role, contact.department].filter(Boolean).join(', ');
  return details ? `${contact.name} - ${details}` : contact.name;
};

const emptyContactForm = {
  name: '',
  email: '',
  phone: '',
  role: '',
  department: '',
  is_primary: false,
};

const QuotationList = ({ onOpenQuote }) => {
  const [quotes, setQuotes] = useState([]);
  const [companies, setCompanies] = useState([]);
  const [form, setForm] = useState({ company: '', contact: '', notes: '' });
  const [showContactForm, setShowContactForm] = useState(false);
  const [contactForm, setContactForm] = useState(emptyContactForm);
  const [contactSaving, setContactSaving] = useState(false);
  const [statusFilter, setStatusFilter] = useState('');
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [errorInfo, setErrorInfo] = useState(null);

  const load = async () => {
    setLoading(true);
    setErrorInfo(null);
    try {
      const [quotesRes, companiesRes] = await Promise.all([
        quotationAPI.quotes.list(),
        quotationAPI.companies.list({ active: 'true' }),
      ]);
      setQuotes(quotesRes.data);
      setCompanies(companiesRes.data);
    } catch (error) {
      const details = await describeQuotationError(error, 'Load quotations', 'GET /quotations/quotes/ and GET /quotations/companies/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const filteredQuotes = useMemo(() => {
    const term = search.toLowerCase();
    return quotes.filter((quote) => {
      const statusMatch = !statusFilter || quote.status === statusFilter;
      const searchMatch = !term ||
        quote.quotation_number.toLowerCase().includes(term) ||
        quote.company_name.toLowerCase().includes(term);
      return statusMatch && searchMatch;
    });
  }, [quotes, statusFilter, search]);

  const createQuote = async (event) => {
    event.preventDefault();
    setSaving(true);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.quotes.create({
        company: form.company,
        contact: form.contact || null,
        notes: form.notes,
      });
      setForm({ company: '', contact: '', notes: '' });
      if (onOpenQuote) onOpenQuote(response.data.id);
      await load();
    } catch (error) {
      const details = await describeQuotationError(error, 'Create quotation', 'POST /quotations/quotes/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const contactsForCompany = companies.find((company) => String(company.id) === String(form.company))?.contacts || [];
  const rememberCompany = (company) => {
    setCompanies((current) => {
      const withoutDuplicate = current.filter((candidate) => candidate.id !== company.id);
      return [...withoutDuplicate, company].sort((a, b) => a.name.localeCompare(b.name));
    });
  };

  const rememberContact = (contact) => {
    setCompanies((current) => current.map((company) => {
      if (String(company.id) !== String(contact.company)) return company;
      const contacts = company.contacts || [];
      const withoutDuplicate = contacts.filter((candidate) => candidate.id !== contact.id);
      return {
        ...company,
        contacts: [...withoutDuplicate, contact].sort((a, b) => a.name.localeCompare(b.name)),
      };
    }));
  };

  const createContact = async () => {
    if (!form.company || !contactForm.name.trim()) return;
    setContactSaving(true);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.contacts.create({
        ...contactForm,
        company: form.company,
      });
      rememberContact(response.data);
      setForm((current) => ({ ...current, contact: response.data.id }));
      setContactForm(emptyContactForm);
      setShowContactForm(false);
    } catch (error) {
      const details = await describeQuotationError(error, 'Create quotation contact', 'POST /quotations/contacts/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setContactSaving(false);
    }
  };

  if (loading) return <div className="qm-loading">Loading quotations...</div>;

  return (
    <div className="qm-section">
      <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
      <div className="qm-split wide-left">
        <div className="qm-panel">
        <div className="qm-panel-heading">
          <h3>Quotations</h3>
          <div className="qm-controls">
            <input className="qm-input" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search quotes" />
            <select className="qm-input" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
              <option value="">All statuses</option>
              {Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </div>
        </div>
        <div className="qm-table-wrap">
          <table className="qm-table">
            <thead>
              <tr>
                <th>Number</th>
                <th>Company</th>
                <th>Status</th>
                <th>Version</th>
                <th>Total</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {filteredQuotes.map((quote) => (
                <tr key={quote.id} onClick={() => onOpenQuote(quote.id)}>
                  <td><strong>{quote.quotation_number}</strong></td>
                  <td>{quote.company_name}</td>
                  <td><span className={`qm-badge status-${quote.status}`}>{statusLabels[quote.status] || quote.status}</span></td>
                  <td>{quote.version}</td>
                  <td>{quote.currency} {parseFloat(quote.total || 0).toFixed(2)}</td>
                  <td>{new Date(quote.updated_at).toLocaleDateString('en-AE')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        </div>

        <div className="qm-panel">
        <h3>New Quotation</h3>
        <form onSubmit={createQuote} className="qm-form">
          <CompanySelectWithCreate
            companies={companies}
            value={form.company}
            required
            onChange={(companyId) => {
              setForm({ ...form, company: companyId, contact: '' });
              setContactForm(emptyContactForm);
              setShowContactForm(false);
            }}
            onCreated={rememberCompany}
          />
          <div className="qm-contact-control">
            <label>Contact
              <select value={form.contact} onChange={(event) => setForm({ ...form, contact: event.target.value })}>
                <option value="">No contact</option>
                {contactsForCompany.map((contact) => <option key={contact.id} value={contact.id}>{contactOptionLabel(contact)}</option>)}
              </select>
            </label>
            <button type="button" className="qm-secondary small" disabled={!form.company} onClick={() => setShowContactForm((value) => !value)}>
              {showContactForm ? 'Cancel new contact' : '+ Create contact'}
            </button>
          </div>
          {showContactForm && (
            <div className="qm-inline-card qm-contact-card">
              <label>Name<input required value={contactForm.name} onChange={(event) => setContactForm({ ...contactForm, name: event.target.value })} /></label>
              <label>Phone<input value={contactForm.phone} onChange={(event) => setContactForm({ ...contactForm, phone: event.target.value })} /></label>
              <label>Email<input type="email" value={contactForm.email} onChange={(event) => setContactForm({ ...contactForm, email: event.target.value })} /></label>
              <label>Position / Designation<input value={contactForm.role} onChange={(event) => setContactForm({ ...contactForm, role: event.target.value })} /></label>
              <label>Department<input value={contactForm.department} onChange={(event) => setContactForm({ ...contactForm, department: event.target.value })} /></label>
              <label className="qm-checkbox"><input type="checkbox" checked={contactForm.is_primary} onChange={(event) => setContactForm({ ...contactForm, is_primary: event.target.checked })} /> Primary contact</label>
              <button type="button" className="qm-primary" disabled={contactSaving || !contactForm.name.trim()} onClick={createContact}>
                {contactSaving ? 'Creating contact...' : 'Create and select contact'}
              </button>
            </div>
          )}
          <label>Notes<textarea rows="4" value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} /></label>
          <button type="submit" className="qm-primary" disabled={saving}>{saving ? 'Creating...' : 'Create Quotation'}</button>
        </form>
        </div>
      </div>
    </div>
  );
};

export default QuotationList;
