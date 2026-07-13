import React, { useCallback, useEffect, useMemo, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import QuotationErrorNotice from './QuotationErrorNotice';

const money = (value, currency = 'AED') => `${currency} ${Number(value || 0).toFixed(2)}`;

const unitMoney = (value, currency = 'AED') => `${currency} ${Number(value || 0).toLocaleString(undefined, {
  minimumFractionDigits: 2,
  maximumFractionDigits: 3,
})}`;

const percent = (value) => `${Number(value || 0).toFixed(1)}%`;

const splitEvidenceReasons = (reason) => String(reason || '')
  .split(';')
  .map((part) => part.trim())
  .filter(Boolean);

const evidenceConfidenceLabel = (confidence) => {
  const value = Number(confidence || 0);
  if (value >= 75) return 'Strong';
  if (value >= 55) return 'Review';
  return 'Weak';
};

const lineStatusLabels = {
  pending: 'Pending',
  accepted: 'Accepted',
  rejected: 'Rejected',
  unavailable_missing: 'Unavailable / missing',
  substituted: 'Substituted',
  quantity_changed: 'Quantity changed',
};

const quoteOutcomeLabels = {
  pending: 'Pending',
  won: 'Won',
  lost: 'Lost',
  partial: 'Partial',
  expired: 'Expired',
  cancelled: 'Cancelled',
};

const reasonLabels = {
  price_too_high: 'Price too high',
  not_available: 'Not available',
  customer_no_longer_required: 'Customer no longer required',
  competitor_selected: 'Competitor selected',
  alternate_brand_selected: 'Alternate brand selected',
  quantity_changed: 'Quantity changed',
  delivery_time_issue: 'Delivery time issue',
  customer_cancelled: 'Customer cancelled',
  no_response: 'No response',
  unknown: 'Unknown',
};

const methodLabels = {
  call: 'Call',
  whatsapp: 'WhatsApp',
  email: 'Email',
  visit: 'Visit',
  other: 'Other',
};

const followupStatusLabels = {
  open: 'Open',
  due: 'Due',
  overdue: 'Overdue',
  done: 'Done',
  not_required: 'Not required',
};

const draftFromLine = (line) => ({
  id: line.id,
  outcome_status: line.outcome_status || 'pending',
  accepted_quantity: line.accepted_quantity ?? '',
  accepted_unit_price: line.accepted_unit_price ?? '',
  outcome_reason: line.outcome_reason || '',
  outcome_notes: line.outcome_notes || '',
});

const QuotationOutcomeReview = ({ quoteId, onBack }) => {
  const [quote, setQuote] = useState(null);
  const [summary, setSummary] = useState(null);
  const [lineDrafts, setLineDrafts] = useState({});
  const [selectedLines, setSelectedLines] = useState([]);
  const [selectedSuggestions, setSelectedSuggestions] = useState([]);
  const [poText, setPoText] = useState('');
  const [poFile, setPoFile] = useState(null);
  const [poUseAi, setPoUseAi] = useState(true);
  const [poResult, setPoResult] = useState(null);
  const [poEvidence, setPoEvidence] = useState([]);
  const [evidenceUseAi, setEvidenceUseAi] = useState(true);
  const [manualOutcome, setManualOutcome] = useState({ outcome_status: '', outcome_notes: '' });
  const [followupDraft, setFollowupDraft] = useState({
    last_contacted_now: false,
    next_follow_up_date: '',
    follow_up_status: 'open',
    follow_up_contact_method: '',
    follow_up_notes: '',
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [poLoading, setPoLoading] = useState(false);
  const [findingEvidence, setFindingEvidence] = useState(false);
  const [parsingEvidenceId, setParsingEvidenceId] = useState(null);
  const [markingEvidenceId, setMarkingEvidenceId] = useState(null);
  const [selectedEvidenceId, setSelectedEvidenceId] = useState(null);
  const [notice, setNotice] = useState(null);
  const [errorInfo, setErrorInfo] = useState(null);

  const setLoaded = useCallback((data) => {
    setQuote(data.quotation);
    setSummary(data.summary);
    setPoEvidence(data.po_evidence || []);
    const drafts = Object.fromEntries((data.quotation.lines || []).map((line) => [line.id, draftFromLine(line)]));
    setLineDrafts(drafts);
    setManualOutcome({
      outcome_status: data.quotation.outcome_status_is_manual ? data.quotation.outcome_status : '',
      outcome_notes: data.quotation.outcome_notes || '',
    });
    setFollowupDraft({
      last_contacted_now: false,
      next_follow_up_date: data.quotation.next_follow_up_date || '',
      follow_up_status: data.quotation.follow_up_status || 'open',
      follow_up_contact_method: data.quotation.follow_up_contact_method || '',
      follow_up_notes: data.quotation.follow_up_notes || '',
    });
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.quotes.outcome(quoteId);
      setLoaded(response.data);
    } catch (error) {
      const details = await describeQuotationError(error, 'Load quotation outcome', `GET /quotations/quotes/${quoteId}/outcome/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setLoading(false);
    }
  }, [quoteId, setLoaded]);

  useEffect(() => {
    load();
  }, [load]);

  const lineIds = useMemo(() => (quote?.lines || []).map((line) => line.id), [quote]);
  const selectedActiveLines = selectedLines.filter((id) => lineIds.includes(id));
  const selectedEvidence = useMemo(
    () => poEvidence.find((item) => item.id === selectedEvidenceId) || null,
    [poEvidence, selectedEvidenceId]
  );

  const updateLineDraft = (lineId, patch) => {
    setLineDrafts((current) => ({
      ...current,
      [lineId]: { ...(current[lineId] || {}), ...patch },
    }));
  };

  const patchOutcome = async (payload, message) => {
    setSaving(true);
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.quotes.updateOutcome(quoteId, payload);
      setLoaded(response.data);
      setSelectedLines([]);
      setNotice({ type: 'success', message });
    } catch (error) {
      const details = await describeQuotationError(error, 'Save quotation outcome', `PATCH /quotations/quotes/${quoteId}/outcome/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const saveLineDrafts = () => {
    patchOutcome(
      { line_updates: Object.values(lineDrafts) },
      'Outcome lines saved.'
    );
  };

  const runBulk = (action, ids, message) => {
    if (!ids.length) return;
    patchOutcome({ bulk_action: action, line_ids: ids }, message);
  };

  const saveFollowup = () => {
    patchOutcome(followupDraft, 'Follow-up details saved.');
  };

  const saveManualOutcome = () => {
    patchOutcome(
      {
        manual_outcome: !!manualOutcome.outcome_status,
        outcome_status: manualOutcome.outcome_status || undefined,
        outcome_notes: manualOutcome.outcome_notes,
      },
      manualOutcome.outcome_status ? 'Manual outcome saved.' : 'Outcome recalculated from line statuses.'
    );
  };

  const parsePo = async () => {
    setPoLoading(true);
    setNotice(null);
    setErrorInfo(null);
    setPoResult(null);
    try {
      let response;
      if (poFile) {
        const formData = new FormData();
        formData.append('file', poFile);
        formData.append('use_ai', poUseAi ? '1' : '0');
        response = await quotationAPI.quotes.parseOutcomePO(quoteId, formData, true);
      } else {
        response = await quotationAPI.quotes.parseOutcomePO(quoteId, { text: poText, use_ai: poUseAi });
      }
      setPoResult(response.data);
      setSelectedSuggestions((response.data.suggestions || []).map((suggestion) => suggestion.quotation_line_id).filter(Boolean));
      setNotice({ type: 'success', message: 'PO suggestions parsed for review. Nothing was saved yet.' });
    } catch (error) {
      const details = await describeQuotationError(error, 'Parse outcome PO', `POST /quotations/quotes/${quoteId}/parse_outcome_po/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setPoLoading(false);
    }
  };

  const loadPOEvidence = async () => {
    try {
      const response = await quotationAPI.quotes.poEvidence(quoteId);
      setPoEvidence(response.data.results || []);
    } catch (error) {
      const details = await describeQuotationError(error, 'Load Gmail PO evidence', `GET /quotations/quotes/${quoteId}/po_evidence/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    }
  };

  const findPOEvidence = async () => {
    setFindingEvidence(true);
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.quotes.findPOEvidence(quoteId, { limit: 25 });
      setPoEvidence(response.data.results || []);
      setNotice({
        type: 'success',
        message: `Found ${response.data.count || 0} Gmail evidence candidate(s). Nothing was saved to the outcome yet.`,
      });
    } catch (error) {
      const details = await describeQuotationError(error, 'Find Gmail PO evidence', `POST /quotations/quotes/${quoteId}/find_po_evidence/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setFindingEvidence(false);
    }
  };

  const approveAndParseEvidence = async (evidenceId) => {
    setParsingEvidenceId(evidenceId);
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.quotes.parsePOEvidence(quoteId, {
        evidence_id: evidenceId,
        approve_link: true,
        use_ai: evidenceUseAi,
      });
      setPoResult(response.data);
      setSelectedSuggestions((response.data.suggestions || []).map((suggestion) => suggestion.quotation_line_id).filter(Boolean));
      await loadPOEvidence();
      setSelectedEvidenceId(null);
      setNotice({ type: 'success', message: 'Email link approved and parsed into review-only PO suggestions. No line outcome was applied.' });
    } catch (error) {
      const details = await describeQuotationError(error, 'Parse Gmail PO evidence', `POST /quotations/quotes/${quoteId}/parse_po_evidence/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setParsingEvidenceId(null);
    }
  };

  const markEvidenceNotRelevant = async (evidenceId) => {
    setMarkingEvidenceId(evidenceId);
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.quotes.markPOEvidenceNotRelevant(quoteId, { evidence_id: evidenceId });
      setPoEvidence((current) => current.map((item) => (item.id === evidenceId ? response.data : item)));
      setNotice({ type: 'success', message: 'Gmail evidence marked not relevant.' });
    } catch (error) {
      const details = await describeQuotationError(error, 'Mark Gmail evidence not relevant', `POST /quotations/quotes/${quoteId}/mark_po_evidence_not_relevant/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setMarkingEvidenceId(null);
    }
  };

  const applySelectedSuggestions = () => {
    const suggestions = (poResult?.suggestions || []).filter((suggestion) => selectedSuggestions.includes(suggestion.quotation_line_id));
    if (!suggestions.length) return;
    patchOutcome({
      line_updates: suggestions.map((suggestion) => ({
        id: suggestion.quotation_line_id,
        outcome_status: suggestion.suggested_outcome_status,
        accepted_quantity: suggestion.suggested_accepted_quantity,
        accepted_unit_price: suggestion.suggested_accepted_unit_price,
        outcome_notes: `PO suggestion applied: ${suggestion.reason}`,
      })),
    }, 'Selected PO suggestions applied. Review and save final outcome when ready.');
  };

  if (loading) return <div className="qm-loading">Loading quotation outcome...</div>;
  if (!quote) {
    return (
      <div className="qm-section">
        <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
        <div className="qm-empty">Quotation outcome not found.</div>
      </div>
    );
  }

  return (
    <div className="qm-section qm-outcome">
      <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
      <div className="qm-editor-header">
        <div>
          <button type="button" className="qm-secondary small" onClick={onBack}>Back to Quotations</button>
          <h3>Review Outcome: {quote.quotation_number}</h3>
          <p>{quote.company_name} - {quote.status_display}</p>
        </div>
        <div className="qm-action-row">
          <span className={`qm-badge status-${quote.outcome_status}`}>{quoteOutcomeLabels[quote.outcome_status] || quote.outcome_status}</span>
          <button type="button" className="qm-primary" disabled={saving} onClick={saveLineDrafts}>
            {saving ? 'Saving...' : 'Save Line Outcomes'}
          </button>
        </div>
      </div>

      {notice && <div className={`qm-feedback ${notice.type}`}>{notice.message}</div>}

      <div className="qm-stat-grid">
        <div className="qm-stat"><span>{money(summary.quoted_value, quote.currency)}</span><p>Quoted value</p></div>
        <div className="qm-stat success"><span>{money(summary.accepted_value, quote.currency)}</span><p>Accepted value</p></div>
        <div className="qm-stat warning"><span>{money(summary.lost_value, quote.currency)}</span><p>Lost value</p></div>
        <div className="qm-stat"><span>{percent(summary.value_win_rate)}</span><p>Value win rate</p></div>
        <div className="qm-stat"><span>{percent(summary.line_win_rate)}</span><p>Line win rate</p></div>
        <div className="qm-stat"><span>{summary.pending_lines}</span><p>Pending lines</p></div>
      </div>

      <div className="qm-panel qm-evidence-panel">
        <div className="qm-panel-heading">
          <div>
            <span className="qm-step-kicker">Gmail evidence</span>
            <h3>Find PO/LPO replies for this quotation</h3>
            <p>Searches the shared Gmail mailbox for this quote, customer, and post-quote PO/LPO signals. A staff member must review and approve each email link before it can be parsed.</p>
          </div>
          <div className="qm-evidence-controls">
            <label className="qm-checkbox">
              <input type="checkbox" checked={evidenceUseAi} onChange={(event) => setEvidenceUseAi(event.target.checked)} />
              AI cleanup
            </label>
            <button type="button" className="qm-primary" disabled={findingEvidence} onClick={findPOEvidence}>
              {findingEvidence ? 'Searching Gmail...' : 'Find Gmail Evidence'}
            </button>
          </div>
        </div>
        {poEvidence.length ? (
          <div className="qm-evidence-grid">
            {poEvidence.map((evidence) => {
              const evidenceStatus = evidence.status || 'candidate';
              const confidence = Math.round(Number(evidence.confidence || 0));
              const reasons = splitEvidenceReasons(evidence.matching_reason);
              return (
              <article key={evidence.id} className={`qm-evidence-card status-${evidenceStatus}`}>
                <div className="qm-evidence-card-main">
                  <div>
                    <h4>{evidence.subject || 'Untitled email'}</h4>
                    <p>{evidence.sender || 'Unknown sender'}</p>
                    <small>{evidence.sent_at ? new Date(evidence.sent_at).toLocaleString() : 'No email date'} - {evidence.attachment_count} attachment(s)</small>
                  </div>
                  <div className="qm-evidence-badges">
                    <span className={`qm-badge evidence-${evidenceConfidenceLabel(confidence).toLowerCase()}`}>{confidence}% {evidenceConfidenceLabel(confidence)}</span>
                    <span className={`qm-badge status-${evidenceStatus}`}>{evidenceStatus.replace('_', ' ')}</span>
                  </div>
                </div>
                <div className="qm-evidence-reason-list">
                  {(reasons.length ? reasons.slice(0, 3) : [evidence.snippet || 'Matched by targeted Gmail search.']).map((reason) => (
                    <span key={reason}>{reason}</span>
                  ))}
                </div>
                {evidence.error && <div className="qm-notice warning">{evidence.error}</div>}
                <div className="qm-evidence-actions">
                  <button
                    type="button"
                    className="qm-secondary small"
                    onClick={() => setSelectedEvidenceId(evidence.id)}
                  >
                    Review evidence
                  </button>
                  <button
                    type="button"
                    className="qm-secondary small"
                    disabled={markingEvidenceId === evidence.id || evidenceStatus === 'not_relevant'}
                    onClick={() => markEvidenceNotRelevant(evidence.id)}
                  >
                    {markingEvidenceId === evidence.id ? 'Saving...' : 'Not relevant'}
                  </button>
                </div>
              </article>
              );
            })}
          </div>
        ) : (
          <div className="qm-empty subtle">No Gmail evidence candidates yet. Click Find Gmail Evidence to search for related PO/LPO replies.</div>
        )}
      </div>

      {selectedEvidence && (
        <div className="qm-modal-backdrop" role="presentation" onClick={() => setSelectedEvidenceId(null)}>
          <div className="qm-modal qm-evidence-detail-modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
            <div className="qm-panel-heading">
              <div>
                <span className="qm-step-kicker">Gmail source review</span>
                <h3>{selectedEvidence.subject || 'Untitled email'}</h3>
                <p>Confirm whether this email is actually a PO/LPO response before parsing it into outcome suggestions.</p>
              </div>
              <button type="button" className="qm-secondary small" onClick={() => setSelectedEvidenceId(null)}>Close</button>
            </div>

            <div className="qm-evidence-detail-grid">
              <div>
                <span>From</span>
                <strong>{selectedEvidence.sender || '-'}</strong>
              </div>
              <div>
                <span>Shared mailbox</span>
                <strong>{selectedEvidence.mailbox_email || 'Mailbox identity not recorded'}</strong>
              </div>
              <div>
                <span>To / Cc</span>
                <strong>{selectedEvidence.recipients || '-'}</strong>
              </div>
              <div>
                <span>Date</span>
                <strong>{selectedEvidence.sent_at ? new Date(selectedEvidence.sent_at).toLocaleString() : '-'}</strong>
              </div>
              <div>
                <span>Confidence</span>
                <strong>{Math.round(Number(selectedEvidence.confidence || 0))}% {evidenceConfidenceLabel(selectedEvidence.confidence)}</strong>
              </div>
            </div>

            <div className="qm-evidence-detail-section">
              <h4>Why this was suggested</h4>
              <div className="qm-evidence-reason-list expanded">
                {(splitEvidenceReasons(selectedEvidence.matching_reason).length
                  ? splitEvidenceReasons(selectedEvidence.matching_reason)
                  : [selectedEvidence.snippet || 'Matched by targeted Gmail search.']
                ).map((reason) => <span key={reason}>{reason}</span>)}
              </div>
            </div>

            <div className="qm-evidence-detail-section">
              <h4>Email preview</h4>
              <pre className="qm-evidence-preview">{selectedEvidence.extracted_text_preview || selectedEvidence.snippet || 'No preview text available yet. Use Parse & Suggest to fetch the full review source when appropriate.'}</pre>
            </div>

            <div className="qm-evidence-detail-section">
              <h4>Attachments</h4>
              {selectedEvidence.attachments?.length ? (
                <div className="qm-evidence-attachments">
                  {selectedEvidence.attachments.map((attachment, index) => (
                    <div key={`${attachment.filename || 'attachment'}-${index}`} className="qm-evidence-attachment">
                      <strong>{attachment.filename || 'Unnamed attachment'}</strong>
                      <span>{attachment.mime_type || 'file'} - {attachment.size ? `${attachment.size} bytes` : 'size unknown'}</span>
                      {attachment.status && <span>{attachment.status}{attachment.reason ? ` - ${attachment.reason}` : ''}</span>}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="qm-empty subtle">No attachments reported on this email.</div>
              )}
            </div>

            {selectedEvidence.error && <div className="qm-notice warning">{selectedEvidence.error}</div>}
            <div className="qm-action-row">
              <button
                type="button"
                className="qm-primary"
                disabled={parsingEvidenceId === selectedEvidence.id || selectedEvidence.status === 'not_relevant'}
                onClick={() => approveAndParseEvidence(selectedEvidence.id)}
              >
                {parsingEvidenceId === selectedEvidence.id ? 'Approving & parsing...' : 'Approve this email link & parse'}
              </button>
              <button
                type="button"
                className="qm-secondary"
                disabled={markingEvidenceId === selectedEvidence.id || selectedEvidence.status === 'not_relevant'}
                onClick={() => markEvidenceNotRelevant(selectedEvidence.id)}
              >
                {markingEvidenceId === selectedEvidence.id ? 'Saving...' : 'Mark not relevant'}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="qm-grid-two">
        <div className="qm-panel">
          <div className="qm-panel-heading">
            <div>
              <h3>Follow-up</h3>
              <p>Track calls, WhatsApp, email, visits, and next action dates.</p>
            </div>
            <button type="button" className="qm-secondary" disabled={saving} onClick={saveFollowup}>Save Follow-up</button>
          </div>
          <div className="qm-outcome-form-grid">
            <label>Status
              <select value={followupDraft.follow_up_status} onChange={(event) => setFollowupDraft({ ...followupDraft, follow_up_status: event.target.value })}>
                {Object.entries(followupStatusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            <label>Method
              <select value={followupDraft.follow_up_contact_method} onChange={(event) => setFollowupDraft({ ...followupDraft, follow_up_contact_method: event.target.value })}>
                <option value="">Not set</option>
                {Object.entries(methodLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            <label>Next follow-up
              <input type="date" value={followupDraft.next_follow_up_date || ''} onChange={(event) => setFollowupDraft({ ...followupDraft, next_follow_up_date: event.target.value })} />
            </label>
            <label className="qm-checkbox">
              <input type="checkbox" checked={followupDraft.last_contacted_now} onChange={(event) => setFollowupDraft({ ...followupDraft, last_contacted_now: event.target.checked })} />
              Mark contacted now
            </label>
            <label className="span-two">Notes
              <textarea rows="3" value={followupDraft.follow_up_notes} onChange={(event) => setFollowupDraft({ ...followupDraft, follow_up_notes: event.target.value })} />
            </label>
          </div>
        </div>

        <div className="qm-panel">
          <div className="qm-panel-heading">
            <div>
              <h3>PO Assistant</h3>
              <p>Upload or paste a PO. Suggestions are review-only until applied.</p>
            </div>
            <button type="button" className="qm-secondary" disabled={poLoading || (!poText.trim() && !poFile)} onClick={parsePo}>
              {poLoading ? 'Parsing...' : 'Parse PO'}
            </button>
          </div>
          <div className="qm-outcome-form-grid">
            <label className="span-two">Paste PO text
              <textarea rows="4" value={poText} onChange={(event) => setPoText(event.target.value)} placeholder="Paste accepted PO lines here..." />
            </label>
            <label className="span-two">Or upload PO file
              <input type="file" accept=".xlsx,.xls,.xlsb,.pdf,.png,.jpg,.jpeg,.webp" onChange={(event) => setPoFile(event.target.files?.[0] || null)} />
            </label>
            <label className="qm-checkbox span-two">
              <input type="checkbox" checked={poUseAi} onChange={(event) => setPoUseAi(event.target.checked)} />
              Use AI cleanup when available
            </label>
          </div>
        </div>
      </div>

      {poResult && (
        <div className="qm-panel">
          <div className="qm-panel-heading">
            <div>
              <h3>PO Suggestions</h3>
              <p>{poResult.suggestions.length} matched suggestion(s), {poResult.unmatched_po_rows.length} unmatched PO row(s), {poResult.missing_quote_line_ids.length} quoted line(s) not found in PO.</p>
            </div>
            <button type="button" className="qm-primary" disabled={!selectedSuggestions.length || saving} onClick={applySelectedSuggestions}>Apply Selected Suggestions</button>
          </div>
          {!!poResult.warnings?.length && <div className="qm-notice">{poResult.warnings.join(' ')}</div>}
          <div className="qm-table-wrap compact">
            <table className="qm-table">
              <thead>
                <tr>
                  <th></th>
                  <th>PO item</th>
                  <th>Matched quote line</th>
                  <th>Suggested qty</th>
                  <th>Suggested price</th>
                  <th>Confidence</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {poResult.suggestions.map((suggestion) => (
                  <tr key={`${suggestion.quotation_line_id}-${suggestion.po_row_index}`}>
                    <td><input type="checkbox" checked={selectedSuggestions.includes(suggestion.quotation_line_id)} onChange={() => setSelectedSuggestions((current) => current.includes(suggestion.quotation_line_id) ? current.filter((id) => id !== suggestion.quotation_line_id) : [...current, suggestion.quotation_line_id])} /></td>
                    <td>{suggestion.po_row?.item_name || '-'}</td>
                    <td>{suggestion.quotation_line_label}</td>
                    <td>{suggestion.suggested_accepted_quantity || '-'}</td>
                    <td>{suggestion.suggested_accepted_unit_price ? unitMoney(suggestion.suggested_accepted_unit_price, quote.currency) : '-'}</td>
                    <td>{Math.round(Number(suggestion.confidence || 0))}%</td>
                    <td>{suggestion.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="qm-panel">
        <div className="qm-panel-heading">
          <div>
            <h3>Line Outcomes</h3>
            <p>Accepted lines create won value. Rejected, unavailable, substituted, and partial quantities create lost value.</p>
          </div>
          <div className="qm-action-row">
            <button type="button" className="qm-secondary small" onClick={() => setSelectedLines(lineIds)}>Select all</button>
            <button type="button" className="qm-secondary small" onClick={() => setSelectedLines([])}>Clear</button>
            <button type="button" className="qm-secondary small" disabled={!selectedActiveLines.length || saving} onClick={() => runBulk('mark_selected_accepted', selectedActiveLines, 'Selected lines marked accepted.')}>Mark accepted</button>
            <button type="button" className="qm-secondary small" disabled={!selectedActiveLines.length || saving} onClick={() => runBulk('mark_selected_rejected', selectedActiveLines, 'Selected lines marked rejected.')}>Mark rejected</button>
            <button type="button" className="qm-secondary small" disabled={saving} onClick={() => runBulk('mark_all_accepted', lineIds, 'All lines marked accepted.')}>Mark all accepted</button>
          </div>
        </div>
        <div className="qm-table-wrap">
          <table className="qm-table">
            <thead>
              <tr>
                <th></th>
                <th>#</th>
                <th>Item</th>
                <th>Quoted</th>
                <th>Outcome</th>
                <th>Accepted qty</th>
                <th>Accepted price</th>
                <th>Reason</th>
                <th>Accepted</th>
                <th>Lost</th>
              </tr>
            </thead>
            <tbody>
              {(quote.lines || []).map((line, index) => {
                const draft = lineDrafts[line.id] || draftFromLine(line);
                return (
                  <tr key={line.id}>
                    <td><input type="checkbox" checked={selectedLines.includes(line.id)} onChange={() => setSelectedLines((current) => current.includes(line.id) ? current.filter((id) => id !== line.id) : [...current, line.id])} /></td>
                    <td>{index + 1}</td>
                    <td><strong>{line.item_name_snapshot}</strong><br /><small>{line.product_name || 'No Product'} - {line.quantity} {line.unit}</small></td>
                    <td>{money(line.line_total, quote.currency)}<br /><small>{line.quantity} x {unitMoney(line.unit_price, quote.currency)}</small></td>
                    <td>
                      <select value={draft.outcome_status} onChange={(event) => updateLineDraft(line.id, { outcome_status: event.target.value })}>
                        {Object.entries(lineStatusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      </select>
                    </td>
                    <td><input type="number" min="0" step="0.001" value={draft.accepted_quantity} onChange={(event) => updateLineDraft(line.id, { accepted_quantity: event.target.value })} /></td>
                    <td><input type="number" min="0" step="0.001" value={draft.accepted_unit_price} onChange={(event) => updateLineDraft(line.id, { accepted_unit_price: event.target.value })} /></td>
                    <td>
                      <select value={draft.outcome_reason} onChange={(event) => updateLineDraft(line.id, { outcome_reason: event.target.value })}>
                        <option value="">No reason</option>
                        {Object.entries(reasonLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      </select>
                    </td>
                    <td>{money(line.accepted_total, quote.currency)}</td>
                    <td>{money(line.lost_value, quote.currency)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="qm-panel">
        <div className="qm-panel-heading">
          <div>
            <h3>Final Outcome</h3>
            <p>Leave override blank to let the system calculate pending/won/lost/partial from the line statuses.</p>
          </div>
          <button type="button" className="qm-primary" disabled={saving} onClick={saveManualOutcome}>Save Final Outcome</button>
        </div>
        <div className="qm-outcome-form-grid">
          <label>Override status
            <select value={manualOutcome.outcome_status} onChange={(event) => setManualOutcome({ ...manualOutcome, outcome_status: event.target.value })}>
              <option value="">Auto-calculate</option>
              {Object.entries(quoteOutcomeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="span-two">Outcome notes
            <textarea rows="3" value={manualOutcome.outcome_notes} onChange={(event) => setManualOutcome({ ...manualOutcome, outcome_notes: event.target.value })} placeholder="Required when overriding the calculated outcome." />
          </label>
        </div>
      </div>
    </div>
  );
};

export default QuotationOutcomeReview;
