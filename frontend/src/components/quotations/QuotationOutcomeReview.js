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

const firstDefined = (...values) => values.find((value) => value !== undefined && value !== null && value !== '');

const humanizeEvidenceLabel = (value) => String(value || '')
  .replaceAll('_', ' ')
  .replace(/\b\w/g, (character) => character.toUpperCase());

const formatFileSize = (value) => {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return 'Size unknown';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB'];
  let size = bytes / 1024;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size >= 10 ? size.toFixed(0) : size.toFixed(1)} ${units[unitIndex]}`;
};

const attachmentIdentifier = (attachment) => firstDefined(
  attachment?.attachment_id,
  attachment?.source_gmail_attachment_id,
  attachment?.part_id,
  attachment?.id
);

const SAFE_INLINE_ATTACHMENT_TYPES = new Set([
  'application/pdf',
  'image/gif',
  'image/jpeg',
  'image/png',
  'image/webp',
  'text/plain',
]);

const normalizeMimeType = (value) => String(value || '')
  .split(';', 1)[0]
  .trim()
  .toLowerCase();

const attachmentMimeType = (attachment) => normalizeMimeType(firstDefined(
  attachment?.mime_type,
  attachment?.source_mime_type,
  attachment?.content_type
));

const canInlinePreviewAttachment = (attachment) => SAFE_INLINE_ATTACHMENT_TYPES.has(attachmentMimeType(attachment));

const responseContentType = (response, attachment) => {
  const headers = response?.headers;
  const headerValue = typeof headers?.get === 'function'
    ? headers.get('content-type')
    : headers?.['content-type'];
  return normalizeMimeType(firstDefined(headerValue, response?.data?.type, attachmentMimeType(attachment)))
    || 'application/octet-stream';
};

const safeAttachmentFilename = (attachment) => Array.from(
  String(attachment?.filename || 'gmail-attachment')
).map((character) => {
  const codePoint = character.codePointAt(0);
  return character === '\\' || character === '/' || codePoint < 32 || codePoint === 127
    ? '_'
    : character;
}).join('').slice(0, 240) || 'gmail-attachment';

const attachmentTypeLabel = (attachment) => {
  const mimeType = firstDefined(attachment?.mime_type, attachment?.source_mime_type, attachment?.content_type);
  const filename = String(attachment?.filename || '');
  const extension = filename.includes('.') ? filename.split('.').pop().toUpperCase() : '';
  if (extension && mimeType) return `${extension} · ${mimeType}`;
  return extension || mimeType || 'File';
};

const evidenceBodyPreview = (evidence) => {
  const fields = [
    ['Selected source text', evidence?.extracted_text, evidence?.extracted_text_truncated],
    ['Email body', evidence?.email_body_text, evidence?.email_body_text_truncated],
    ['Email body', evidence?.email_body_preview],
    ['Email body', evidence?.body_preview],
    ['Parsed email body', evidence?.extracted_text_preview],
    ['Email body', evidence?.email_body],
    ['Email body', evidence?.body_text],
    ['Gmail snippet', evidence?.snippet],
  ];
  const selected = fields.find(([, value]) => value !== undefined && value !== null && String(value).trim());
  if (!selected) return { label: 'Email preview', text: '', truncated: false };
  const text = String(selected[1]).trim();
  return {
    label: selected[0],
    text: text.slice(0, 5000),
    truncated: Boolean(selected[2]) || text.length > 5000,
  };
};

const quoteReferenceInfo = (evidence) => {
  const nested = firstDefined(
    evidence?.quote_reference,
    evidence?.match_signals?.quote_reference,
    evidence?.matching_signals?.quote_reference
  );
  const nestedObject = nested && typeof nested === 'object' && !Array.isArray(nested) ? nested : {};
  const status = firstDefined(evidence?.quote_reference_status, nestedObject.status);
  const explicitFlag = firstDefined(
    evidence?.quote_reference_present,
    evidence?.quote_reference_match,
    evidence?.has_quote_reference,
    nestedObject.present,
    nestedObject.matched,
    nestedObject.found
  );
  const reference = firstDefined(
    evidence?.matched_quote_reference,
    evidence?.quote_reference_value,
    nestedObject.reference,
    nestedObject.value,
    nestedObject.label,
    typeof nested === 'string' && !['present', 'matched', 'missing', 'not_found'].includes(nested.toLowerCase()) ? nested : undefined,
    Array.isArray(evidence?.quote_references) ? evidence.quote_references.join(', ') : evidence?.quote_references
  );

  if (explicitFlag === undefined && status === undefined && reference === undefined) return null;
  const normalizedStatus = String(status || '').toLowerCase();
  const present = typeof explicitFlag === 'boolean'
    ? explicitFlag
    : explicitFlag !== undefined
      ? !['false', 'missing', 'not_found', 'none', 'no'].includes(String(explicitFlag).toLowerCase())
      : status !== undefined
        ? !['missing', 'not_found', 'none', 'no_match', 'false'].includes(normalizedStatus)
        : Boolean(reference);
  return {
    present,
    label: present ? 'Quote reference present' : 'Quote reference missing',
    detail: reference || (status ? humanizeEvidenceLabel(status) : ''),
  };
};

const selectedAttachmentIdentity = (evidence) => {
  const selected = firstDefined(evidence?.selected_source, evidence?.selected_attachment);
  const selectedObject = selected && typeof selected === 'object' ? selected : {};
  return {
    id: firstDefined(
      evidence?.selected_attachment_id,
      evidence?.selected_source_attachment_id,
      selectedObject.attachment_id,
      selectedObject.part_id,
      selectedObject.id
    ),
    filename: firstDefined(
      evidence?.selected_attachment_filename,
      evidence?.selected_source_filename,
      evidence?.source_filename,
      selectedObject.filename,
      typeof selected === 'string' ? selected : undefined
    ),
  };
};

const isSelectedAttachment = (evidence, attachment) => {
  if ([attachment?.is_selected, attachment?.selected, attachment?.is_primary, attachment?.selected_source].some((value) => value === true)) {
    return true;
  }
  const selected = selectedAttachmentIdentity(evidence);
  const identifier = attachmentIdentifier(attachment);
  if (selected.id !== undefined && identifier !== undefined && String(selected.id) === String(identifier)) return true;
  return Boolean(selected.filename && attachment?.filename && selected.filename === attachment.filename);
};

const signalValueText = (value) => {
  if (value === undefined || value === null || value === '') return '';
  if (typeof value === 'boolean') return value ? 'Matched' : 'Not matched';
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  if (Array.isArray(value)) return value.map((item) => signalValueText(item)).filter(Boolean).join(', ');
  const preferred = firstDefined(value.description, value.detail, value.reason, value.value, value.status, value.summary);
  if (preferred !== undefined) return signalValueText(preferred);
  return Object.entries(value)
    .map(([key, item]) => `${humanizeEvidenceLabel(key)}: ${signalValueText(item)}`)
    .filter((item) => !item.endsWith(': '))
    .join(' · ');
};

const normalizeSignalEntries = (value) => {
  if (value === undefined || value === null || value === '') return [];
  if (Array.isArray(value)) {
    return value.map((item, index) => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) {
        return { label: `Signal ${index + 1}`, value: signalValueText(item), matched: undefined };
      }
      return {
        label: firstDefined(item.label, item.name, item.item_name, item.item, item.type, `Signal ${index + 1}`),
        value: signalValueText(firstDefined(item.description, item.detail, item.reason, item.value, item.status, item)),
        matched: firstDefined(item.matched, item.present, item.found),
      };
    }).filter((entry) => entry.value || entry.label);
  }
  if (typeof value !== 'object') return [{ label: 'Signal', value: signalValueText(value), matched: undefined }];
  const looksLikeSingleSignal = ['label', 'name', 'description', 'detail', 'reason', 'value', 'status', 'matched']
    .some((key) => Object.prototype.hasOwnProperty.call(value, key));
  if (looksLikeSingleSignal) {
    return [{
      label: firstDefined(value.label, value.name, 'Signal'),
      value: signalValueText(firstDefined(value.description, value.detail, value.reason, value.value, value.status, value)),
      matched: firstDefined(value.matched, value.present, value.found),
    }];
  }
  return Object.entries(value).map(([key, item]) => ({
    label: humanizeEvidenceLabel(key),
    value: signalValueText(item),
    matched: item && typeof item === 'object' ? firstDefined(item.matched, item.present, item.found) : undefined,
  })).filter((entry) => entry.value);
};

const EvidenceSignalSection = ({ title, value }) => {
  const entries = normalizeSignalEntries(value);
  if (!entries.length) return null;
  return (
    <div className="qm-evidence-detail-section">
      <h4>{title}</h4>
      <div className="qm-evidence-signal-grid">
        {entries.map((entry, index) => (
          <div
            key={`${entry.label}-${index}`}
            className={`qm-evidence-signal ${entry.matched === true ? 'matched' : entry.matched === false ? 'not-matched' : ''}`}
          >
            <strong>{entry.label}</strong>
            <span>{entry.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
};

const evidenceConfidenceLabel = (confidence) => {
  const value = Number(confidence || 0);
  if (value >= 75) return 'Strong';
  if (value >= 55) return 'Review';
  return 'Weak';
};

const archivedEvidenceStatuses = new Set(['superseded', 'not_relevant']);

const evidenceStatusDetails = {
  candidate: {
    label: 'Candidate',
    description: 'Ready for staff review before this email is linked and parsed.',
  },
  ambiguous: {
    label: 'Needs assignment',
    description: 'Ambiguous match: this email may belong to more than one quotation. Assign it here only after checking the quotation reference and attachments.',
  },
  parsed: {
    label: 'Parsed',
    description: 'This email link was approved and parsed into review-only suggestions.',
  },
  failed: {
    label: 'Parse failed',
    description: 'The last parse attempt failed. Review the source before retrying.',
  },
  superseded: {
    label: 'Superseded (archived)',
    description: 'A newer scan or explicit assignment replaced this match. It cannot be parsed for this quotation.',
  },
  not_relevant: {
    label: 'Not relevant (archived)',
    description: 'Staff rejected this match. It is retained only as audit history and cannot be parsed.',
  },
};

const evidenceStatus = (evidence) => evidence?.status || 'candidate';
const evidenceStatusInfo = (evidence) => evidenceStatusDetails[evidenceStatus(evidence)] || {
  label: evidenceStatus(evidence).replaceAll('_', ' '),
  description: 'Review this email source before taking any action.',
};
const isEvidenceArchived = (evidence) => archivedEvidenceStatuses.has(evidenceStatus(evidence));

const GmailEvidenceCard = ({ evidence, markingEvidenceId, onReview, onMarkNotRelevant }) => {
  const status = evidenceStatus(evidence);
  const statusInfo = evidenceStatusInfo(evidence);
  const archived = isEvidenceArchived(evidence);
  const confidence = Math.round(Number(evidence.confidence || 0));
  const reasons = splitEvidenceReasons(evidence.matching_reason);
  const referenceInfo = quoteReferenceInfo(evidence);
  const attachmentCount = firstDefined(evidence.attachment_count, evidence.attachments?.length, 0);
  const showStatusExplanation = ['ambiguous', 'superseded', 'not_relevant'].includes(status);

  return (
    <article className={`qm-evidence-card status-${status}`}>
      <div className="qm-evidence-card-main">
        <div>
          <h4>{evidence.subject || 'Untitled email'}</h4>
          <p>{evidence.sender || 'Unknown sender'}</p>
          <small>{evidence.sent_at ? new Date(evidence.sent_at).toLocaleString() : 'No email date'} - {attachmentCount} attachment(s)</small>
        </div>
        <div className="qm-evidence-badges">
          <span className={`qm-badge evidence-${evidenceConfidenceLabel(confidence).toLowerCase()}`}>{confidence}% {evidenceConfidenceLabel(confidence)}</span>
          <span className={`qm-badge status-${status}`}>{statusInfo.label}</span>
          {referenceInfo && (
            <span className={`qm-badge evidence-reference-${referenceInfo.present ? 'present' : 'missing'}`}>
              {referenceInfo.label}
            </span>
          )}
        </div>
      </div>
      <div className="qm-evidence-reason-list">
        {(reasons.length ? reasons.slice(0, 3) : [evidence.snippet || 'Matched by targeted Gmail search.']).map((reason) => (
          <span key={reason}>{reason}</span>
        ))}
      </div>
      {showStatusExplanation && <div className={`qm-notice ${status === 'ambiguous' ? 'warning' : ''}`}>{statusInfo.description}</div>}
      {evidence.error && evidence.error !== statusInfo.description && <div className="qm-notice warning">{evidence.error}</div>}
      <div className="qm-evidence-actions">
        <button
          type="button"
          className="qm-secondary small"
          onClick={() => onReview(evidence.id)}
        >
          {archived ? 'View archived evidence' : 'Review evidence'}
        </button>
        <button
          type="button"
          className="qm-secondary small"
          disabled={archived || markingEvidenceId === evidence.id}
          onClick={() => onMarkNotRelevant(evidence.id)}
        >
          {markingEvidenceId === evidence.id ? 'Saving...' : 'Not relevant'}
        </button>
      </div>
    </article>
  );
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
  const [poEvidencePagination, setPoEvidencePagination] = useState(null);
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
  const [selectedEvidenceSource, setSelectedEvidenceSource] = useState(null);
  const [loadingEvidenceSource, setLoadingEvidenceSource] = useState(false);
  const [evidenceSourceError, setEvidenceSourceError] = useState(null);
  const [viewingAttachmentKey, setViewingAttachmentKey] = useState(null);
  const [attachmentError, setAttachmentError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [errorInfo, setErrorInfo] = useState(null);

  const setLoaded = useCallback((data) => {
    setQuote(data.quotation);
    setSummary(data.summary);
    setPoEvidence(data.po_evidence || []);
    setPoEvidencePagination(data.po_evidence_pagination || null);
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
  const selectedEvidenceForReview = useMemo(
    () => selectedEvidence
      ? { ...selectedEvidence, ...(selectedEvidenceSource?.id === selectedEvidence.id ? selectedEvidenceSource : {}) }
      : null,
    [selectedEvidence, selectedEvidenceSource]
  );
  const selectedEvidencePreview = useMemo(
    () => evidenceBodyPreview(selectedEvidenceForReview),
    [selectedEvidenceForReview]
  );
  const selectedEvidenceReference = useMemo(
    () => quoteReferenceInfo(selectedEvidence),
    [selectedEvidence]
  );
  const selectedEvidenceSignalSections = useMemo(() => {
    if (!selectedEvidence) return [];
    const grouped = selectedEvidence.match_signal_sections || selectedEvidence.items_quantity_time || {};
    return [
      {
        title: 'Match signals',
        value: firstDefined(
          selectedEvidence.match_signals,
          selectedEvidence.matching_signals,
          selectedEvidence.match_signal_summary,
          selectedEvidence.signals,
          grouped.match,
          grouped.general
        ),
      },
      {
        title: 'Items',
        value: firstDefined(
          selectedEvidence.item_match_signals,
          selectedEvidence.item_signals,
          selectedEvidence.matched_items,
          selectedEvidence.item_matches,
          grouped.items,
          grouped.item
        ),
      },
      {
        title: 'Quantity',
        value: firstDefined(
          selectedEvidence.quantity_match_signals,
          selectedEvidence.quantity_signals,
          selectedEvidence.quantity_comparison,
          selectedEvidence.quantity_matches,
          grouped.quantity,
          grouped.quantities
        ),
      },
      {
        title: 'Timing',
        value: firstDefined(
          selectedEvidence.time_match_signals,
          selectedEvidence.time_signals,
          selectedEvidence.time_comparison,
          selectedEvidence.timeline_signals,
          grouped.time,
          grouped.timing,
          grouped.timeline
        ),
      },
    ].filter((section) => section.value !== undefined && section.value !== null && section.value !== '');
  }, [selectedEvidence]);
  const activeEvidence = useMemo(
    () => poEvidence.filter((item) => !isEvidenceArchived(item)),
    [poEvidence]
  );
  const archivedEvidence = useMemo(
    () => poEvidence.filter((item) => isEvidenceArchived(item)),
    [poEvidence]
  );

  useEffect(() => {
    setAttachmentError(null);
    setViewingAttachmentKey(null);
  }, [selectedEvidenceId]);

  useEffect(() => {
    let cancelled = false;
    setSelectedEvidenceSource(null);
    setEvidenceSourceError(null);
    if (!selectedEvidenceId) {
      setLoadingEvidenceSource(false);
      return () => { cancelled = true; };
    }
    setLoadingEvidenceSource(true);
    quotationAPI.quotes.poEvidenceSource(selectedEvidenceId)
      .then((response) => {
        if (!cancelled) setSelectedEvidenceSource(response.data || null);
      })
      .catch((error) => {
        if (!cancelled) {
          setEvidenceSourceError(error?.response?.data?.detail || error?.message || 'Could not load the source text.');
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingEvidenceSource(false);
      });
    return () => { cancelled = true; };
  }, [selectedEvidenceId]);

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

  const loadPOEvidence = async ({ archivedOffset, append = false } = {}) => {
    try {
      const params = archivedOffset === undefined ? undefined : { archived_offset: archivedOffset };
      const response = params
        ? await quotationAPI.quotes.poEvidence(quoteId, params)
        : await quotationAPI.quotes.poEvidence(quoteId);
      const incoming = response.data.results || [];
      setPoEvidence((current) => {
        if (!append) return incoming;
        const byId = new Map(current.map((item) => [item.id, item]));
        incoming.forEach((item) => byId.set(item.id, item));
        return Array.from(byId.values());
      });
      setPoEvidencePagination(response.data.pagination || null);
      return true;
    } catch (error) {
      const details = await describeQuotationError(error, 'Load Gmail PO evidence', `GET /quotations/quotes/${quoteId}/po_evidence/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
      return false;
    }
  };

  const loadMoreArchivedEvidence = async () => {
    const nextOffset = poEvidencePagination?.archived_next_offset;
    if (nextOffset === undefined || nextOffset === null) return;
    setFindingEvidence(true);
    await loadPOEvidence({ archivedOffset: nextOffset, append: true });
    setFindingEvidence(false);
  };

  const findPOEvidence = async () => {
    setFindingEvidence(true);
    setNotice(null);
    setErrorInfo(null);
    try {
      const historyRefreshed = await loadPOEvidence();
      setNotice({
        type: historyRefreshed ? 'success' : 'warning',
        message: historyRefreshed
          ? 'Mailbox-wide evidence refreshed. Nothing was saved to the outcome.'
          : 'The evidence history could not be refreshed, so the existing review list was preserved.',
      });
    } catch (error) {
      const details = await describeQuotationError(error, 'Refresh Gmail PO evidence', `GET /quotations/quotes/${quoteId}/po_evidence/`);
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

  const viewEvidenceAttachment = async (evidence, attachment, index) => {
    const attachmentId = attachmentIdentifier(attachment);
    if (attachmentId === undefined) {
      setAttachmentError('This attachment does not include an attachment ID, so it cannot be opened from the current API response.');
      return;
    }

    const key = `${evidence.id}:${attachmentId || index}`;
    const endpoint = `GET /quotations/po-evidence/${evidence.id}/attachment/?attachment_id=${encodeURIComponent(attachmentId)}`;
    let previewWindow = null;
    if (canInlinePreviewAttachment(attachment)) {
      try {
        previewWindow = window.open('about:blank', '_blank');
        if (previewWindow) previewWindow.opener = null;
      } catch {
        previewWindow = null;
      }
    }

    setViewingAttachmentKey(key);
    setAttachmentError(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.quotes.poEvidenceAttachment(evidence.id, attachmentId);
      const responseType = responseContentType(response, attachment);
      const inlineSafe = SAFE_INLINE_ATTACHMENT_TYPES.has(responseType);
      // Active formats must never receive an HTML/SVG-capable blob URL in the
      // application origin. Force them to an inert download MIME type even if
      // the server or Gmail metadata reports a browser-renderable type.
      const blob = new Blob([response.data], {
        type: inlineSafe ? responseType : 'application/octet-stream',
      });
      if (!window.URL?.createObjectURL) throw new Error('This browser cannot open downloaded attachment blobs.');
      const objectUrl = window.URL.createObjectURL(blob);

      if (inlineSafe) {
        if (previewWindow) {
          previewWindow.location.href = objectUrl;
        } else {
          const link = document.createElement('a');
          link.href = objectUrl;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
          link.click();
        }
      } else {
        if (previewWindow?.close) previewWindow.close();
        const link = document.createElement('a');
        link.href = objectUrl;
        link.download = safeAttachmentFilename(attachment);
        link.rel = 'noopener';
        document.body.appendChild(link);
        link.click();
        link.remove();
      }
      window.setTimeout(() => window.URL.revokeObjectURL(objectUrl), 60000);
    } catch (error) {
      if (previewWindow?.close) previewWindow.close();
      const details = await describeQuotationError(error, 'View Gmail evidence attachment', endpoint);
      setAttachmentError(details.detail || 'Could not open this attachment.');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setViewingAttachmentKey(null);
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
            <h3>Review mailbox-wide PO/LPO evidence</h3>
            <p>The mailbox audit compares source attachments or the newest email body with quotation items, quantities, prices/totals, customer and timing. A staff member must still inspect and approve each link before parsing.</p>
          </div>
          <div className="qm-evidence-controls">
            <label className="qm-checkbox">
              <input type="checkbox" checked={evidenceUseAi} onChange={(event) => setEvidenceUseAi(event.target.checked)} />
              AI cleanup
            </label>
            <button type="button" className="qm-primary" disabled={findingEvidence} onClick={findPOEvidence}>
              {findingEvidence ? 'Refreshing...' : 'Refresh Evidence'}
            </button>
          </div>
        </div>
        {activeEvidence.length ? (
          <>
            <div className="qm-evidence-section-heading">
              <strong>Active evidence</strong>
              <span>{activeEvidence.length} {activeEvidence.length === 1 ? 'match' : 'matches'} awaiting review or already parsed</span>
            </div>
            <div className="qm-evidence-grid">
              {activeEvidence.map((evidence) => (
                <GmailEvidenceCard
                  key={evidence.id}
                  evidence={evidence}
                  markingEvidenceId={markingEvidenceId}
                  onReview={setSelectedEvidenceId}
                  onMarkNotRelevant={markEvidenceNotRelevant}
                />
              ))}
            </div>
          </>
        ) : (
          <div className="qm-empty subtle">
            {archivedEvidence.length
              ? 'No active Gmail evidence. Archived scan history is available below.'
              : 'No Gmail evidence candidates yet. Run the mailbox-wide audit from the quotations list, then refresh this review.'}
          </div>
        )}
        {archivedEvidence.length > 0 && (
          <details className="qm-evidence-archive">
            <summary>Archived evidence ({poEvidencePagination?.archived_count ?? archivedEvidence.length})</summary>
            <p>Superseded and rejected matches are retained for audit only. They cannot be parsed for this quotation.</p>
            <div className="qm-evidence-grid">
              {archivedEvidence.map((evidence) => (
                <GmailEvidenceCard
                  key={evidence.id}
                  evidence={evidence}
                  markingEvidenceId={markingEvidenceId}
                  onReview={setSelectedEvidenceId}
                  onMarkNotRelevant={markEvidenceNotRelevant}
                />
              ))}
            </div>
            {poEvidencePagination?.archived_has_more && (
              <button
                type="button"
                className="qm-secondary small"
                disabled={findingEvidence}
                onClick={loadMoreArchivedEvidence}
              >
                {findingEvidence ? 'Loading archive...' : 'Load more archived evidence'}
              </button>
            )}
          </details>
        )}
      </div>

      {selectedEvidence && (
        <div className="qm-modal-backdrop" role="presentation" onClick={() => setSelectedEvidenceId(null)}>
          <div className="qm-modal qm-evidence-detail-modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
            <div className="qm-panel-heading">
              <div>
                <span className="qm-step-kicker">Gmail source review</span>
                <h3>{selectedEvidence.subject || 'Untitled email'}</h3>
                <p>{evidenceStatusInfo(selectedEvidence).description}</p>
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
              <div>
                <span>Assignment status</span>
                <strong>{evidenceStatusInfo(selectedEvidence).label}</strong>
              </div>
              {selectedEvidenceReference && (
                <div className={`qm-evidence-reference ${selectedEvidenceReference.present ? 'present' : 'missing'}`}>
                  <span>Quotation reference</span>
                  <strong>
                    {selectedEvidenceReference.label}
                    {selectedEvidenceReference.detail ? ` · ${selectedEvidenceReference.detail}` : ''}
                  </strong>
                </div>
              )}
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
              <h4>{selectedEvidencePreview.label}</h4>
              <pre className="qm-evidence-preview">
                {selectedEvidencePreview.text
                  || (loadingEvidenceSource
                    ? 'Loading the selected source text...'
                    : 'No source text is available for this evidence. You can still inspect its attachment below.')}
              </pre>
              {selectedEvidencePreview.truncated && <small className="qm-evidence-preview-note">Preview limited to the first 5,000 characters.</small>}
              {evidenceSourceError && <div className="qm-notice warning">{evidenceSourceError}</div>}
            </div>

            {selectedEvidenceSignalSections.map((section) => (
              <EvidenceSignalSection key={section.title} title={section.title} value={section.value} />
            ))}

            <div className="qm-evidence-detail-section">
              <h4>Attachments</h4>
              {selectedEvidence.attachments?.length ? (
                <div className="qm-evidence-attachments">
                  {selectedEvidence.attachments.map((attachment, index) => {
                    const identifier = attachmentIdentifier(attachment);
                    const attachmentKey = `${selectedEvidence.id}:${identifier || index}`;
                    const selectedSource = isSelectedAttachment(selectedEvidence, attachment);
                    const status = attachment.status || 'available';
                    const size = firstDefined(attachment.size, attachment.source_file_size);
                    const inlinePreview = canInlinePreviewAttachment(attachment);
                    return (
                      <article
                        key={`${attachment.filename || 'attachment'}-${identifier || index}`}
                        className={`qm-evidence-attachment ${selectedSource ? 'is-selected' : ''} status-${status}`}
                      >
                        <div className="qm-evidence-attachment-heading">
                          <div>
                            <strong>{attachment.filename || 'Unnamed attachment'}</strong>
                            <span>{attachmentTypeLabel(attachment)}</span>
                          </div>
                          <div className="qm-evidence-attachment-badges">
                            {selectedSource && <span className="qm-evidence-source-badge">Selected source</span>}
                            <span className={`qm-evidence-attachment-status status-${status}`}>{humanizeEvidenceLabel(status)}</span>
                          </div>
                        </div>
                        <div className="qm-evidence-attachment-meta">
                          <span>{formatFileSize(size)}</span>
                          {attachment.line_count !== undefined && <span>{attachment.line_count} parsed row(s)</span>}
                          {identifier !== undefined && <span>Attachment ID recorded</span>}
                        </div>
                        {attachment.reason && <p>{attachment.reason}</p>}
                        <button
                          type="button"
                          className="qm-secondary small"
                          disabled={identifier === undefined || viewingAttachmentKey === attachmentKey}
                          title={identifier === undefined
                            ? 'The API response did not include an attachment ID.'
                            : inlinePreview
                              ? `Open ${attachment.filename || 'attachment'} in a new tab`
                              : `Download ${attachment.filename || 'attachment'} without opening active content`}
                          onClick={() => viewEvidenceAttachment(selectedEvidence, attachment, index)}
                        >
                          {viewingAttachmentKey === attachmentKey
                            ? 'Opening attachment...'
                            : identifier === undefined
                              ? 'View unavailable'
                              : inlinePreview
                                ? 'View attachment'
                                : 'Download attachment'}
                        </button>
                      </article>
                    );
                  })}
                </div>
              ) : (
                <div className="qm-empty subtle">No attachments reported on this email.</div>
              )}
              {attachmentError && <div className="qm-notice warning">{attachmentError}</div>}
            </div>

            {selectedEvidence.error && <div className="qm-notice warning">{selectedEvidence.error}</div>}
            <div className="qm-action-row">
              <button
                type="button"
                className="qm-primary"
                disabled={parsingEvidenceId === selectedEvidence.id || isEvidenceArchived(selectedEvidence)}
                onClick={() => approveAndParseEvidence(selectedEvidence.id)}
              >
                {parsingEvidenceId === selectedEvidence.id
                  ? 'Approving & parsing...'
                  : isEvidenceArchived(selectedEvidence)
                    ? 'Archived - cannot parse'
                    : evidenceStatus(selectedEvidence) === 'ambiguous'
                      ? 'Assign to this quotation & parse'
                      : evidenceStatus(selectedEvidence) === 'parsed'
                        ? 'Reparse approved email'
                        : 'Approve this email link & parse'}
              </button>
              <button
                type="button"
                className="qm-secondary"
                disabled={markingEvidenceId === selectedEvidence.id || isEvidenceArchived(selectedEvidence)}
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
