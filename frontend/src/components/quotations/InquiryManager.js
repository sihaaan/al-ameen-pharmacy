import React, { useEffect, useMemo, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import QuotationErrorNotice from './QuotationErrorNotice';

const newLine = () => ({
  raw_name: '',
  quantity: '1',
  unit: '',
  notes: '',
  matched_quote_item: '',
  match_status: 'unresolved',
});

const InquiryManager = ({ onOpenQuote }) => {
  const [companies, setCompanies] = useState([]);
  const [contacts, setContacts] = useState([]);
  const [items, setItems] = useState([]);
  const [inquiries, setInquiries] = useState([]);
  const [selectedInquiry, setSelectedInquiry] = useState(null);
  const [form, setForm] = useState({
    company: '',
    contact: '',
    subject: '',
    original_text: '',
    lines: [newLine()],
  });
  const [statusFilter, setStatusFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [errorInfo, setErrorInfo] = useState(null);

  const load = async () => {
    setLoading(true);
    setErrorInfo(null);
    try {
      const [companiesRes, contactsRes, itemsRes, inquiriesRes] = await Promise.all([
        quotationAPI.companies.list({ active: 'true' }),
        quotationAPI.contacts.list({ active: 'true' }),
        quotationAPI.items.list({ active: 'true' }),
        quotationAPI.inquiries.list(),
      ]);
      setCompanies(companiesRes.data);
      setContacts(contactsRes.data);
      setItems(itemsRes.data);
      setInquiries(inquiriesRes.data);
    } catch (error) {
      const details = await describeQuotationError(
        error,
        'Load inquiries',
        'GET /quotations/companies/, /quotations/contacts/, /quotations/items/, /quotations/inquiries/'
      );
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const filteredContacts = contacts.filter((contact) => String(contact.company) === String(form.company));
  const filteredInquiries = useMemo(() => (
    statusFilter ? inquiries.filter((inquiry) => inquiry.status === statusFilter) : inquiries
  ), [inquiries, statusFilter]);

  const updateLine = (index, patch) => {
    setForm((current) => ({
      ...current,
      lines: current.lines.map((line, lineIndex) => lineIndex === index ? { ...line, ...patch } : line),
    }));
  };

  const removeLine = (index) => {
    setForm((current) => ({
      ...current,
      lines: current.lines.filter((_, lineIndex) => lineIndex !== index),
    }));
  };

  const saveInquiry = async (event) => {
    event.preventDefault();
    setSaving(true);
    setErrorInfo(null);
    const payload = {
      company: form.company,
      contact: form.contact || null,
      subject: form.subject,
      original_text: form.original_text,
      lines: form.lines
        .filter((line) => line.raw_name.trim())
        .map((line, index) => ({
          raw_name: line.raw_name,
          quantity: line.quantity || null,
          unit: line.unit,
          notes: line.notes,
          matched_quote_item: line.matched_quote_item || null,
          match_status: line.match_status,
          sort_order: index,
        })),
    };
    try {
      await quotationAPI.inquiries.create(payload);
      setForm({ company: '', contact: '', subject: '', original_text: '', lines: [newLine()] });
      await load();
    } catch (error) {
      const details = await describeQuotationError(error, 'Create inquiry', 'POST /quotations/inquiries/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const createQuote = async (inquiry) => {
    setErrorInfo(null);
    try {
      const response = await quotationAPI.inquiries.createQuote(inquiry.id);
      if (onOpenQuote) onOpenQuote(response.data.id);
      await load();
    } catch (error) {
      const details = await describeQuotationError(error, 'Create quote from inquiry', `POST /quotations/inquiries/${inquiry.id}/create_quote/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    }
  };

  return (
    <div className="qm-section">
      <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
      <div className="qm-split wide-left">
        <div className="qm-panel">
        <div className="qm-panel-heading">
          <h3>Inquiries</h3>
          <select className="qm-input" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">All statuses</option>
            <option value="draft">Draft</option>
            <option value="quoted">Quoted</option>
            <option value="cancelled">Cancelled</option>
          </select>
        </div>
        {loading ? (
          <div className="qm-loading">Loading inquiries...</div>
        ) : (
          <div className="qm-table-wrap">
            <table className="qm-table">
              <thead>
                <tr>
                  <th>Subject</th>
                  <th>Company</th>
                  <th>Lines</th>
                  <th>Status</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {filteredInquiries.map((inquiry) => (
                  <tr key={inquiry.id} className={selectedInquiry?.id === inquiry.id ? 'selected' : ''} onClick={() => setSelectedInquiry(inquiry)}>
                    <td>{inquiry.subject || `Inquiry #${inquiry.id}`}</td>
                    <td>{inquiry.company_name}</td>
                    <td>{inquiry.lines?.length || 0}</td>
                    <td><span className="qm-badge muted">{inquiry.status}</span></td>
                    <td>
                      <button type="button" className="qm-secondary small" onClick={(event) => { event.stopPropagation(); createQuote(inquiry); }}>
                        Create Quote
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {selectedInquiry && (
          <div className="qm-subpanel">
            <h4>{selectedInquiry.subject || `Inquiry #${selectedInquiry.id}`}</h4>
            <p>{selectedInquiry.original_text || 'No source text entered.'}</p>
            <div className="qm-table-wrap compact">
              <table className="qm-table">
                <thead><tr><th>Requested Item</th><th>Qty</th><th>Match</th><th>Status</th></tr></thead>
                <tbody>
                  {(selectedInquiry.lines || []).map((line) => (
                    <tr key={line.id}>
                      <td>{line.raw_name}</td>
                      <td>{line.quantity || '-'}</td>
                      <td>{line.matched_quote_item_name || '-'}</td>
                      <td>{line.match_status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        </div>

        <div className="qm-panel">
        <h3>Manual Inquiry</h3>
        <form onSubmit={saveInquiry} className="qm-form">
          <label>Company
            <select required value={form.company} onChange={(event) => setForm({ ...form, company: event.target.value, contact: '' })}>
              <option value="">Select company</option>
              {companies.map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}
            </select>
          </label>
          <label>Contact
            <select value={form.contact} onChange={(event) => setForm({ ...form, contact: event.target.value })}>
              <option value="">No contact</option>
              {filteredContacts.map((contact) => <option key={contact.id} value={contact.id}>{contact.name}</option>)}
            </select>
          </label>
          <label>Subject<input value={form.subject} onChange={(event) => setForm({ ...form, subject: event.target.value })} /></label>
          <label>Original Inquiry Text<textarea rows="4" value={form.original_text} onChange={(event) => setForm({ ...form, original_text: event.target.value })} /></label>

          <div className="qm-line-editor">
            <div className="qm-panel-heading">
              <h4>Requested Lines</h4>
              <button type="button" className="qm-secondary small" onClick={() => setForm({ ...form, lines: [...form.lines, newLine()] })}>Add Line</button>
            </div>
            {form.lines.map((line, index) => (
              <div key={index} className="qm-line-form">
                <input placeholder="Requested item name" required value={line.raw_name} onChange={(event) => updateLine(index, { raw_name: event.target.value })} />
                <input type="number" min="0" step="0.001" placeholder="Qty" value={line.quantity} onChange={(event) => updateLine(index, { quantity: event.target.value })} />
                <input placeholder="Unit" value={line.unit} onChange={(event) => updateLine(index, { unit: event.target.value })} />
                <select value={line.matched_quote_item} onChange={(event) => {
                  const matched = event.target.value;
                  updateLine(index, { matched_quote_item: matched, match_status: matched ? 'confirmed' : 'unresolved' });
                }}>
                  <option value="">Unmatched</option>
                  {items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
                <button type="button" className="qm-icon danger" onClick={() => removeLine(index)} disabled={form.lines.length === 1}>Delete</button>
              </div>
            ))}
          </div>

          <button type="submit" className="qm-primary" disabled={saving}>{saving ? 'Saving...' : 'Create Inquiry'}</button>
        </form>
        </div>
      </div>
    </div>
  );
};

export default InquiryManager;
