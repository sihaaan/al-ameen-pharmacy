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

export const MAILBOX_AUDIT_REQUEST_BUDGET = 100;

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

const emptyMailboxScan = {
  running: false,
  runId: null,
  processed: 0,
  relevant: 0,
  incomplete: 0,
  pages: 0,
  estimate: null,
  found: 0,
  ambiguous: 0,
  unmatched: 0,
  remaining: null,
  errors: [],
  done: false,
  inventoryDone: false,
  inventoryComplete: false,
  repairDone: false,
  repairRemaining: null,
  repairSummary: null,
  mailboxVisionAvailable: null,
  mailboxVisionReason: '',
  phase: 'idle',
  mode: 'scan',
  pauseReason: null,
};

const mailboxScanFromResponse = (payload, overrides = {}) => {
  const run = payload?.run || {};
  const matchRun = payload?.match_run || {};
  const summary = matchRun.summary || {};
  const estimate = run.result_size_estimate === null || run.result_size_estimate === undefined
    ? null
    : Number(run.result_size_estimate);
  const processed = Number(run.messages_scanned || 0);
  const inventoryDone = Boolean(payload?.inventory_done);
  // Older deployments did not expose a separate PDF-repair phase. Treat an
  // omitted marker as complete so a rolling frontend deploy can still finish
  // audits against those responses.
  const repairDone = payload?.repair_done === undefined
    ? inventoryDone
    : Boolean(payload.repair_done);
  const repairRemaining = payload?.repair_remaining === null || payload?.repair_remaining === undefined
    ? null
    : Math.max(Number(payload.repair_remaining) || 0, 0);
  const incomplete = Number(run.incomplete_messages || 0);
  const inventoryComplete = payload?.inventory_complete === undefined
    ? inventoryDone && incomplete === 0
    : Boolean(payload.inventory_complete);
  const phase = payload?.done
    ? 'complete'
    : matchRun.status === 'running'
      ? 'matching'
      : run.status === 'failed'
        ? 'failed'
        : inventoryDone && !repairDone
          ? 'repair'
          : inventoryDone
          ? 'ready_to_match'
          : run.id
            ? 'inventory'
            : 'idle';
  return {
    ...emptyMailboxScan,
    runId: run.id || null,
    processed,
    relevant: Number(run.relevant_messages || 0),
    incomplete,
    pages: Number(run.pages_scanned || 0),
    estimate,
    found: Number(summary.active_evidence || 0),
    ambiguous: Number(summary.ambiguous_messages || 0),
    unmatched: Number(summary.unmatched_messages || 0),
    remaining: estimate === null ? (inventoryDone ? 0 : null) : Math.max(estimate - processed, 0),
    errors: [...(run.errors || []), ...(matchRun.errors || [])].slice(-8),
    done: Boolean(payload?.done),
    inventoryDone,
    inventoryComplete,
    repairDone,
    repairRemaining,
    repairSummary: payload?.repair_summary || null,
    mailboxVisionAvailable: payload?.mailbox_vision_available === undefined
      ? null
      : Boolean(payload.mailbox_vision_available),
    mailboxVisionReason: payload?.mailbox_vision_reason || '',
    phase,
    ...overrides,
  };
};

const poEvidenceBadges = (quote) => {
  const active = Number(quote.po_evidence_candidate_count || 0);
  const parsed = Number(quote.po_evidence_parsed_count || 0);
  const candidates = Math.max(0, active - parsed);
  const ambiguous = Number(quote.po_evidence_ambiguous_count || 0);
  const badges = [];

  if (candidates > 0) {
    badges.push({ key: 'candidate', label: `${candidates} candidate${candidates === 1 ? '' : 's'}`, className: 'status-sent' });
  }
  if (ambiguous > 0) {
    badges.push({ key: 'ambiguous', label: `${ambiguous} need${ambiguous === 1 ? 's' : ''} assignment`, className: 'status-needs_review' });
  }
  if (parsed > 0) {
    badges.push({ key: 'parsed', label: `${parsed} parsed`, className: 'status-ready' });
  }
  if (badges.length) return badges;
  if (quote.po_evidence_last_scanned_at) {
    return [{
      key: 'checked',
      label: quote.po_evidence_last_scan_error ? 'Scan issue' : 'Checked',
      className: quote.po_evidence_last_scan_error ? 'status-cancelled' : 'status-pending',
    }];
  }
  return [{ key: 'unchecked', label: 'Not checked', className: 'status-pending' }];
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
  const [poScan, setPoScan] = useState(emptyMailboxScan);
  const stopPoScanRef = useRef(false);

  const load = async () => {
    setLoading(true);
    setErrorInfo(null);
    try {
      const [quotesRes, companiesRes, latestAuditRes] = await Promise.all([
        quotationAPI.quotes.list(),
        quotationAPI.companies.list({ active: 'true' }),
        quotationAPI.mailboxPOAudits.latest().catch(() => null),
      ]);
      setQuotes(quotesRes.data);
      setCompanies(companiesRes.data);
      if (latestAuditRes?.data?.run) {
        setPoScan((current) => mailboxScanFromResponse(latestAuditRes.data, {
          running: current.running,
          mode: current.mode,
        }));
      }
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

  const runPOEvidenceScan = async ({ rescan = false } = {}) => {
    if (poScan.running) return;
    stopPoScanRef.current = false;
    setErrorInfo(null);
    setPoScan((current) => ({
      ...(rescan ? emptyMailboxScan : current),
      running: true,
      done: false,
      mode: rescan ? 'rescan' : 'scan',
      phase: 'inventory',
      pauseReason: null,
    }));

    try {
      let response = await quotationAPI.mailboxPOAudits.start({ restart: rescan });
      let requestsUsed = 1;
      let pauseReason = null;
      while (true) {
        const data = response.data || {};
        const run = data.run || {};
        setPoScan(mailboxScanFromResponse(data, {
          running: !data.done && run.status !== 'failed' && !stopPoScanRef.current,
          mode: rescan ? 'rescan' : 'scan',
        }));
        if (data.done || run.status === 'failed' || stopPoScanRef.current) break;
        if (requestsUsed >= MAILBOX_AUDIT_REQUEST_BUDGET) {
          pauseReason = 'request_budget';
          break;
        }
        const repairDone = data.repair_done === undefined
          ? Boolean(data.inventory_done)
          : Boolean(data.repair_done);
        if (data.inventory_done && !repairDone) {
          response = await quotationAPI.mailboxPOAudits.repairPage(run.id);
        } else if (data.inventory_done) {
          response = await quotationAPI.mailboxPOAudits.reconcile(run.id);
        } else {
          response = await quotationAPI.mailboxPOAudits.scanPage(run.id, { page_size: 25 });
        }
        requestsUsed += 1;
      }
      if (stopPoScanRef.current) pauseReason = 'stopped';
      await load();
      setPoScan((current) => ({
        ...current,
        running: false,
        mode: rescan ? 'rescan' : 'scan',
        ...(pauseReason ? { done: false, phase: 'paused', pauseReason } : { pauseReason: null }),
      }));
    } catch (error) {
      const details = await describeQuotationError(error, 'Audit Gmail for PO/LPO evidence', 'POST /quotations/mailbox-po-audits/');
      setErrorInfo(details);
      setPoScan((current) => ({ ...current, running: false }));
      console.error(formatQuotationError(details), error);
    }
  };

  const stopPOEvidenceScan = () => {
    stopPoScanRef.current = true;
    setPoScan((current) => ({
      ...current,
      running: false,
      done: false,
      phase: 'paused',
      pauseReason: 'stopped',
    }));
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
            <strong>Mailbox-wide PO/LPO audit</strong>
            <p>Inventories every incoming Gmail message since the first quotation (including Spam/Trash for completeness), reads the newest email body, and checks likely documents against quotation items, quantities, prices/totals, customer and timing. Matches remain review-only.</p>
            <p className="qm-inline-warning">
              Privacy: when mailbox PDF vision is explicitly enabled, bounded page images from unreadable PDFs are sent to the configured OpenAI vision model with API storage disabled. AI-read or partially rendered documents always require staff to inspect the exact Gmail attachment.
            </p>
            {(poScan.runId || poScan.processed > 0 || poScan.remaining !== null) && (
              <div className="qm-po-scan-meta">
                <span>{poScan.processed}{poScan.estimate !== null ? ` / ~${poScan.estimate}` : ''} emails inventoried</span>
                <span>{poScan.relevant} possible PO/LPO emails</span>
                <span>{poScan.pages} Gmail page{poScan.pages === 1 ? '' : 's'}</span>
                {poScan.inventoryComplete && <span>Mailbox inventory complete</span>}
                {poScan.inventoryDone && !poScan.inventoryComplete && (
                  <span>{poScan.incomplete} email{poScan.incomplete === 1 ? '' : 's'} could not be read after three attempts</span>
                )}
                {poScan.inventoryDone && !poScan.repairDone && (
                  <span>
                    {poScan.repairRemaining === null
                      ? 'Checking unreadable PDF attachments'
                      : `${poScan.repairRemaining} PDF attachment${poScan.repairRemaining === 1 ? '' : 's'} remaining for bounded review repair`}
                  </span>
                )}
                {poScan.inventoryDone && poScan.repairDone && poScan.repairSummary && (
                  <span>Unreadable PDF review repair complete</span>
                )}
                {poScan.inventoryDone && poScan.mailboxVisionAvailable === false && poScan.mailboxVisionReason && (
                  <span>PDF vision unavailable: {poScan.mailboxVisionReason}</span>
                )}
                {poScan.done && <span>{poScan.found} active review {poScan.found === 1 ? 'match' : 'matches'}</span>}
                {poScan.done && <span>{poScan.ambiguous} {poScan.ambiguous === 1 ? 'email needs' : 'emails need'} assignment</span>}
                {poScan.done && <span>{poScan.unmatched} possible {poScan.unmatched === 1 ? 'email had' : 'emails had'} no safe quote match</span>}
                {!poScan.inventoryDone && <span>{poScan.remaining === null ? 'Remaining count loading' : `${poScan.remaining} estimated remaining`}</span>}
                {poScan.mode === 'rescan' && <span>Rescan mode</span>}
                {poScan.phase === 'failed' && <span>Paused after a Gmail error; Resume retries the saved page</span>}
                {poScan.phase === 'paused' && poScan.pauseReason === 'request_budget' && (
                  <span>Paused after {MAILBOX_AUDIT_REQUEST_BUDGET} audit requests in this browser action; progress is saved. Select Resume Mailbox Audit to continue.</span>
                )}
                {poScan.phase === 'paused' && poScan.pauseReason === 'stopped' && (
                  <span>Paused by staff after the current request; progress is saved.</span>
                )}
              </div>
            )}
            {poScan.errors.length > 0 && (
              <div className="qm-inline-warning">
                {poScan.errors.length} audit issue(s). Latest: {poScan.errors[poScan.errors.length - 1].gmail_message_id ? `${poScan.errors[poScan.errors.length - 1].gmail_message_id}: ` : ''}{poScan.errors[poScan.errors.length - 1].error || poScan.errors[poScan.errors.length - 1].detail}
              </div>
            )}
          </div>
          <div className="qm-po-scan-actions">
            <button type="button" className="qm-primary" disabled={poScan.running} onClick={() => runPOEvidenceScan({ rescan: false })}>
              {poScan.running
                ? (poScan.phase === 'repair'
                  ? 'Reviewing unreadable PDF...'
                  : poScan.inventoryDone
                    ? 'Matching...'
                    : 'Reading Gmail...')
                : (poScan.phase === 'failed' || poScan.phase === 'paused' || (poScan.runId && !poScan.done))
                  ? 'Resume Mailbox Audit'
                  : 'Audit New Mailbox Run'}
            </button>
            <button type="button" className="qm-secondary" disabled={poScan.running} onClick={() => runPOEvidenceScan({ rescan: true })}>
              Start Full Rescan
            </button>
            {poScan.running && (
              <button type="button" className="qm-secondary" onClick={stopPOEvidenceScan}>
                Stop after this Gmail page
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
                    <div className="qm-evidence-summary">
                      {poEvidenceBadges(quote).map((badge) => (
                        <span key={badge.key} className={`qm-badge ${badge.className}`}>{badge.label}</span>
                      ))}
                    </div>
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
