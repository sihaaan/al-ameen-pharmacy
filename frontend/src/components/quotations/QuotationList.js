import React, { useEffect, useMemo, useRef, useState } from 'react';
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

const outcomeLabels = {
  pending: 'Pending',
  won: 'Won',
  lost: 'Lost',
  partial: 'Partial',
  expired: 'Expired',
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

const poEvidenceLabel = (quote) => {
  const candidates = Number(quote.po_evidence_candidate_count || 0);
  const parsed = Number(quote.po_evidence_parsed_count || 0);
  if (parsed > 0) return `${parsed} parsed`;
  if (candidates > 0) return `${candidates} candidate${candidates === 1 ? '' : 's'}`;
  if (quote.po_evidence_last_scanned_at) {
    return quote.po_evidence_last_scan_error ? 'Scan issue' : 'Checked';
  }
  return 'Not checked';
};

const QuotationList = ({ onOpenQuote, onReviewOutcome }) => {
  const [quotes, setQuotes] = useState([]);
  const [companies, setCompanies] = useState([]);
  const [contacts, setContacts] = useState([]);
  const [form, setForm] = useState({ company: '', contact: '', notes: '' });
  const [showContactForm, setShowContactForm] = useState(false);
  const [contactForm, setContactForm] = useState(emptyContactForm);
  const [contactSaving, setContactSaving] = useState(false);
  const [statusFilter, setStatusFilter] = useState('');
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadingContacts, setLoadingContacts] = useState(false);
  const [saving, setSaving] = useState(false);
  const [errorInfo, setErrorInfo] = useState(null);
  const [poScan, setPoScan] = useState({
    running: false,
    processed: 0,
    found: 0,
    remaining: null,
    errors: [],
    lastQuotes: [],
    done: false,
  });
  const stopPoScanRef = useRef(false);

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

  const loadContactsForCompany = async (companyId) => {
    if (!companyId) {
      setContacts([]);
      return;
    }
    setLoadingContacts(true);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.contacts.list({ company: companyId, active: 'true' });
      setContacts(response.data);
    } catch (error) {
      const details = await describeQuotationError(error, 'Load company contacts', `GET /quotations/contacts/?company=${companyId}`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setLoadingContacts(false);
    }
  };

  const filteredQuotes = useMemo(() => {
    const term = search.toLowerCase();
    return quotes.filter((quote) => {
      const statusMatch = !statusFilter || quote.status === statusFilter;
      const searchMatch = !term ||
        quote.quotation_number.toLowerCase().includes(term) ||
        quote.company_name.toLowerCase().includes(term) ||
        (quote.created_by_username || '').toLowerCase().includes(term);
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
      setContacts([]);
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

  const runPOEvidenceScan = async () => {
    if (poScan.running) return;
    stopPoScanRef.current = false;
    setErrorInfo(null);
    setPoScan({
      running: true,
      processed: 0,
      found: 0,
      remaining: null,
      errors: [],
      lastQuotes: [],
      done: false,
    });

    const totals = {
      processed: 0,
      found: 0,
      errors: [],
      lastQuotes: [],
      remaining: null,
      done: false,
    };

    try {
      for (let pass = 0; pass < 200; pass += 1) {
        if (stopPoScanRef.current) break;
        const response = await quotationAPI.quotes.scanPOEvidence({ quote_limit: 5, message_limit: 10 });
        const data = response.data || {};
        totals.processed += Number(data.processed || 0);
        totals.found += Number(data.candidates_found || 0);
        totals.remaining = Number(data.remaining || 0);
        totals.done = Boolean(data.done);
        totals.errors = [...totals.errors, ...(data.errors || [])].slice(-8);
        totals.lastQuotes = data.quotes || [];
        setPoScan({
          running: !totals.done,
          processed: totals.processed,
          found: totals.found,
          remaining: totals.remaining,
          errors: totals.errors,
          lastQuotes: totals.lastQuotes,
          done: totals.done,
        });
        if (totals.done || Number(data.processed || 0) === 0) break;
      }
      await load();
      setPoScan((current) => ({ ...current, running: false, done: current.remaining === 0 }));
    } catch (error) {
      const details = await describeQuotationError(error, 'Scan quotations for PO/LPO evidence', 'POST /quotations/quotes/scan_po_evidence/');
      setErrorInfo(details);
      setPoScan((current) => ({ ...current, running: false }));
      console.error(formatQuotationError(details), error);
    }
  };

  const stopPOEvidenceScan = () => {
    stopPoScanRef.current = true;
    setPoScan((current) => ({ ...current, running: false }));
  };

  const rememberCompany = (company) => {
    setCompanies((current) => {
      const withoutDuplicate = current.filter((candidate) => candidate.id !== company.id);
      return [...withoutDuplicate, company].sort((a, b) => a.name.localeCompare(b.name));
    });
  };

  const rememberContact = (contact) => {
    setContacts((current) => {
      const withoutDuplicate = current.filter((candidate) => candidate.id !== contact.id);
      return [...withoutDuplicate, contact].sort((a, b) => a.name.localeCompare(b.name));
    });
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
        <div className="qm-po-scan-card">
          <div>
            <strong>PO/LPO evidence scan</strong>
            <p>Automatically checks finalized and sent quotations against Gmail, then flags likely PO/LPO replies for outcome review.</p>
            {(poScan.processed > 0 || poScan.remaining !== null) && (
              <div className="qm-po-scan-meta">
                <span>{poScan.processed} quotation(s) checked</span>
                <span>{poScan.found} candidate email(s) found</span>
                <span>{poScan.remaining ?? 0} remaining</span>
              </div>
            )}
            {poScan.errors.length > 0 && (
              <div className="qm-inline-warning">
                {poScan.errors.length} scan issue(s). Latest: {poScan.errors[poScan.errors.length - 1].quotation_number}: {poScan.errors[poScan.errors.length - 1].detail}
              </div>
            )}
          </div>
          <div className="qm-po-scan-actions">
            <button type="button" className="qm-primary" disabled={poScan.running} onClick={runPOEvidenceScan}>
              {poScan.running ? 'Scanning...' : poScan.done ? 'Scan Missing Quotes' : 'Scan Sent Quotes'}
            </button>
            {poScan.running && (
              <button type="button" className="qm-secondary" onClick={stopPOEvidenceScan}>
                Stop after this quote
              </button>
            )}
          </div>
        </div>
        <div className="qm-table-wrap">
          <table className="qm-table">
            <thead>
              <tr>
                <th>Number</th>
                <th>Company</th>
                <th>Prepared By</th>
                <th>Status</th>
                <th>Outcome</th>
                <th>PO/LPO</th>
                <th>Version</th>
                <th>Total</th>
                <th>Updated</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {filteredQuotes.map((quote) => (
                <tr key={quote.id} onClick={() => onOpenQuote(quote.id)}>
                  <td><strong>{quote.quotation_number}</strong></td>
                  <td>{quote.company_name}</td>
                  <td>{quote.created_by_username || '-'}</td>
                  <td><span className={`qm-badge status-${quote.status}`}>{statusLabels[quote.status] || quote.status}</span></td>
                  <td><span className={`qm-badge status-${quote.outcome_status || 'pending'}`}>{outcomeLabels[quote.outcome_status] || quote.outcome_status || 'Pending'}</span></td>
                  <td>
                    <span className={`qm-badge ${quote.po_evidence_candidate_count ? 'status-sent' : 'status-pending'}`}>
                      {poEvidenceLabel(quote)}
                    </span>
                  </td>
                  <td>{quote.version}</td>
                  <td>{quote.currency} {parseFloat(quote.total || 0).toFixed(2)}</td>
                  <td>{new Date(quote.updated_at).toLocaleDateString('en-AE')}</td>
                  <td>
                    {['finalized', 'sent'].includes(quote.status) ? (
                      <button
                        type="button"
                        className="qm-secondary small"
                        onClick={(event) => {
                          event.stopPropagation();
                          onReviewOutcome(quote.id);
                        }}
                      >
                        Review Outcome
                      </button>
                    ) : '-'}
                  </td>
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
              loadContactsForCompany(companyId);
            }}
            onCreated={rememberCompany}
          />
          <div className="qm-contact-control">
            <label>Contact
              <select value={form.contact} onChange={(event) => setForm({ ...form, contact: event.target.value })}>
                <option value="">{loadingContacts ? 'Loading contacts...' : 'No contact'}</option>
                {contacts.map((contact) => <option key={contact.id} value={contact.id}>{contactOptionLabel(contact)}</option>)}
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
