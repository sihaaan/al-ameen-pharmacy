import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import CompanySelectWithCreate from './CompanySelectWithCreate';
import QuotationErrorNotice from './QuotationErrorNotice';

const ACTIVE_ANALYSIS_STATUSES = new Set(['analyzing', 'processing', 'queued', 'running']);
const AUTO_ANALYZE_STATUSES = new Set(['claimed', 'new', 'pending', 'ready_to_analyze']);
const RECOVERABLE_ANALYSIS_STATUSES = new Set(['failed', 'paused']);
const ANALYSIS_MODE_IDS = new Set(['current_message', 'selected_messages', 'ai_thread']);

const firstDefined = (...values) => values.find((value) => value !== undefined && value !== null);
const asArray = (value) => (Array.isArray(value) ? value : []);
const asCollection = (value) => (
  Array.isArray(value)
    ? value
    : asArray(value?.results)
);
const entityId = (value) => (
  value && typeof value === 'object'
    ? firstDefined(value.id, value.pk, '')
    : firstDefined(value, '')
);

const gmailReviewUiV2Enabled = (record) => (
  record?.workflow_features?.gmail_review_ui_v2 === true
);

const gmailChainedActionsEnabled = (record) => (
  record?.workflow_features?.gmail_chained_actions === true
);

const identityReviewState = (record) => {
  const nested = firstDefined(record?.identity_review, record?.analysis?.identity_review, {}) || {};
  const approvalValues = [
    record?.identity_review_approved,
    nested.approved,
    nested.is_approved,
  ];
  const approvalValue = approvalValues.find(
    (value) => value !== undefined && value !== null
  );
  return {
    hasApproval: approvalValue !== undefined,
    approved: approvalValue === true,
    fingerprint: String(firstDefined(
      record?.identity_review_fingerprint,
      nested.fingerprint,
      ''
    )),
    suggestionApprovable: firstDefined(
      record?.identity_suggestion_approvable,
      nested.suggestion_approvable,
      false
    ) === true,
  };
};

export const gmailImportRecordFromPayload = (payload) => {
  const data = payload?.data || payload || {};
  return firstDefined(
    data.gmail_import,
    data.import_record,
    data.import,
    data.capture,
    data.result,
    data
  ) || {};
};

export const quotationIdFromGmailImportPayload = (payload) => {
  const data = payload?.data || payload || {};
  const record = gmailImportRecordFromPayload(data);
  return firstDefined(
    data.quotation_id,
    data.quote_id,
    data.quotation?.id,
    data.quote?.id,
    data.inquiry?.quotation_id,
    record.quotation_id,
    record.quote_id,
    entityId(record.quotation),
    record.quotation?.id,
    record.quote?.id,
    record.inquiry?.quotation_id,
    ''
  );
};

const importMessages = (record) => asArray(firstDefined(
  record.message_manifest,
  record.thread_messages,
  record.messages,
  record.timeline,
  record.analysis?.messages,
  record.preview?.messages
));

const importLines = (record) => asArray(firstDefined(
  record.lines,
  record.parsed_rows,
  record.analysis?.rows,
  record.analysis?.parsed_rows,
  record.analysis?.lines,
  record.analysis?.preview?.lines,
  record.preview?.lines
));

const importWarnings = (record) => asArray(firstDefined(
  record.warnings,
  record.analysis?.warnings,
  record.analysis?.preview?.warnings,
  record.preview?.warnings
));

const importMeta = (record) => firstDefined(
  record.meta,
  record.analysis?.meta,
  record.analysis?.preview?.meta,
  record.preview?.meta,
  {}
) || {};

const importSubject = (record) => firstDefined(
  record.subject,
  record.analysis?.subject,
  importMessages(record)[0]?.subject,
  'Selected Gmail conversation'
);

const importSender = (record) => firstDefined(
  record.sender,
  record.from,
  record.analysis?.sender,
  importMessages(record)[0]?.sender,
  'Unknown sender'
);

const importDate = (record) => firstDefined(
  record.received_at,
  record.sent_at,
  record.analysis?.received_at,
  importMessages(record)[0]?.received_at,
  importMessages(record)[0]?.sent_at
);

const messageIdentity = (message, index = 0) => String(firstDefined(
  message.gmail_message_id,
  message.message_id,
  message.id,
  `message-${index}`
));

const normalizedRole = (message) => {
  const explicit = String(firstDefined(
    message.usage,
    message.role,
    message.selection_status,
    message.analysis_role,
    ''
  )).toLowerCase().replaceAll(' ', '_');
  if (['used', 'selected', 'primary', 'included'].includes(explicit)) return 'used';
  if (['excluded', 'ignored', 'not_used', 'not_relevant'].includes(explicit)) return 'excluded';
  if (['context', 'supporting', 'thread_context'].includes(explicit)) return 'context';
  if (message.is_selected === true || message.selected === true || message.used === true) return 'used';
  if (message.is_excluded === true || message.excluded === true) return 'excluded';
  if (message.is_outbound === true) return 'excluded';
  return 'used';
};

const initiallySelectedMessageIds = (record) => {
  const explicit = asArray(firstDefined(
    record.selected_message_ids,
    record.analysis?.selected_message_ids,
    record.preview?.selected_message_ids
  )).map(String);
  if (explicit.length) return explicit;
  return importMessages(record)
    .map((message, index) => ({ id: messageIdentity(message, index), role: normalizedRole(message) }))
    .filter((message) => message.role !== 'excluded')
    .map((message) => message.id);
};

const initialAnalysisMode = (record) => {
  const mode = String(firstDefined(record.mode, record.analysis?.mode, 'current_message'));
  return ANALYSIS_MODE_IDS.has(mode) ? mode : 'current_message';
};

const importStatus = (record) => String(firstDefined(
  record.analysis_status,
  record.status,
  record.analysis?.status,
  ''
)).toLowerCase();

const mergeEntities = (current, incoming) => {
  const byId = new Map(asArray(current).map((entity) => [String(entity.id), entity]));
  asCollection(incoming).forEach((entity) => {
    if (entity?.id !== undefined && entity?.id !== null) byId.set(String(entity.id), entity);
  });
  return [...byId.values()].sort((left, right) => String(left.name || '').localeCompare(String(right.name || '')));
};

const companyCandidateEntities = (record) => asArray(firstDefined(
  record.candidates?.companies,
  record.company_candidates
)).map((candidate) => ({
  ...candidate,
  id: firstDefined(candidate.id, candidate.company_id),
  name: firstDefined(candidate.name, candidate.company_name, ''),
})).filter((candidate) => candidate.id && candidate.name);

const contactCandidateEntities = (record) => asArray(firstDefined(
  record.candidates?.contacts,
  record.contact_candidates
)).map((candidate) => ({
  ...candidate,
  id: firstDefined(candidate.id, candidate.contact_id),
  name: firstDefined(candidate.name, candidate.contact_name, ''),
})).filter((candidate) => candidate.id && candidate.name);

const suggestedCompany = (record) => {
  const explicit = firstDefined(
    record.company_suggestion,
    record.suggested_company,
    record.identity?.company,
    record.candidates?.company
  );
  if (explicit) return explicit;
  const candidates = companyCandidateEntities(record);
  const recommendedId = firstDefined(
    record.recommended_company_id,
    record.candidates?.recommended_company_id
  );
  return candidates.find(
    (candidate) => String(candidate.id) === String(recommendedId)
  ) || null;
};

const companySuggestionEvidenceLabel = (suggestion) => {
  const matchMethod = String(suggestion?.match_method || '')
    .trim()
    .toLowerCase()
    .replaceAll('-', '_')
    .replaceAll(' ', '_');
  const matchMethodTokens = new Set(matchMethod.split('_').filter(Boolean));
  if (
    matchMethodTokens.has('signature')
    && matchMethodTokens.has('domain')
  ) {
    return 'Suggested from email domain and signature inference';
  }
  if (matchMethodTokens.has('signature')) {
    return 'Suggested from the company name in the email signature';
  }
  if (matchMethodTokens.has('verified') && matchMethodTokens.has('domain')) {
    return 'Suggested from a verified company email domain';
  }
  if (
    matchMethodTokens.has('exact')
    && ['sender', 'contact', 'company', 'email'].some((hint) => matchMethodTokens.has(hint))
  ) {
    return 'Suggested from an exact sender email match';
  }
  if (
    matchMethodTokens.has('domain')
    && ['company', 'name', 'inferred', 'inference'].some(
      (hint) => matchMethodTokens.has(hint)
    )
  ) {
    return 'Suggested from company-name and email-domain inference';
  }
  if (matchMethodTokens.has('domain')) {
    return 'Suggested from email-domain evidence';
  }
  return 'Suggested from customer identity evidence';
};

const suggestedContact = (record) => {
  const explicit = firstDefined(
    record.contact_suggestion,
    record.suggested_contact,
    record.identity?.contact,
    record.candidates?.contact
  );
  if (explicit) return explicit;
  const candidates = contactCandidateEntities(record);
  const recommendedId = firstDefined(
    record.recommended_contact_id,
    record.candidates?.recommended_contact_id
  );
  return candidates.find(
    (candidate) => String(candidate.id) === String(recommendedId)
  ) || null;
};

const evidenceForLine = (line) => {
  const evidence = asArray(firstDefined(line.evidence, line.sources, line.provenance));
  if (evidence.length) {
    return evidence.map((source) => ({
      ...source,
      page: firstDefined(
        source.page,
        source.page_number,
        source.source_page,
        line.source_page,
        line.page_number
      ),
      raw_text: firstDefined(
        source.raw_text,
        source.extracted_text,
        line.raw_line,
        line.raw_source_line
      ),
    }));
  }
  const sourceKeys = asArray(firstDefined(line._source_keys, line.source_keys));
  if (sourceKeys.length) return sourceKeys.map((key) => ({
    source_key: key,
    page: firstDefined(line.source_page, line.page_number),
    raw_text: firstDefined(line.raw_line, line.raw_source_line),
  }));
  const hasFallback = [
    line.source_filename,
    line.attachment_filename,
    line.source_subject,
    line.gmail_message_id,
    line.source_page,
  ].some(Boolean);
  if (!hasFallback) return [];
  return [{
    filename: firstDefined(line.source_filename, line.attachment_filename),
    subject: line.source_subject,
    gmail_message_id: line.gmail_message_id,
    page: firstDefined(line.source_page, line.page_number),
    raw_text: firstDefined(line.raw_line, line.raw_source_line),
  }];
};

const sourceKeysForLine = (line) => [
  ...asArray(firstDefined(line._source_keys, line.source_keys)),
  ...evidenceForLine(line).map((source) => sourceKey(source)),
]
  .map(String)
  .filter((value, index, values) => value && values.indexOf(value) === index);

const lineOperation = (line) => {
  const operation = String(firstDefined(
    line.operation,
    line.change_type,
    line.change,
    line.diff_status,
    line.row_operation,
    'unchanged'
  )).toLowerCase().replaceAll(' ', '_');
  const aliases = {
    add: 'added',
    new: 'added',
    update: 'changed',
    updated: 'changed',
    delete: 'removed',
    deleted: 'removed',
    excluded: 'removed',
    ambiguous: 'uncertain',
    same: 'unchanged',
  };
  const normalized = aliases[operation] || operation;
  return ['added', 'changed', 'removed', 'duplicate', 'uncertain', 'unchanged'].includes(normalized)
    ? normalized
    : 'uncertain';
};

const lineCustomerCommercialEvidence = (line) => ({
  unitPrice: firstDefined(
    line.customer_unit_price,
    line.customer_price,
    line.requested_unit_price,
    line.budget_unit_price,
    line.budget_price,
    line.evidence_unit_price
  ),
  total: firstDefined(
    line.customer_total,
    line.customer_line_total,
    line.requested_total,
    line.budget_total,
    line.evidence_total
  ),
  vat: firstDefined(line.customer_vat, line.requested_vat, line.evidence_vat),
  currency: firstDefined(line.customer_currency, line.currency, ''),
});

const confidencePercent = (line) => {
  const raw = Number(firstDefined(line.match_confidence, line.parse_confidence, line.confidence));
  if (!Number.isFinite(raw)) return null;
  return Math.max(0, Math.min(100, Math.round(raw <= 1 ? raw * 100 : raw)));
};

const formatCommercialAmount = (value, currency = '') => {
  if (value === undefined || value === null || value === '') return '';
  const numeric = Number(value);
  const amount = Number.isFinite(numeric) ? numeric.toFixed(2) : String(value);
  return currency ? `${currency} ${amount}` : `${amount} (currency not stated)`;
};

const reviewRowKey = (line) => String(firstDefined(
  line.row_key,
  line.review_key,
  line.source_row_key,
  ''
));

const reviewLineIncluded = (line) => (
  line.included !== false
  && !['removed', 'duplicate'].includes(lineOperation(line))
  && !['excluded', 'ignored', 'removed'].includes(
    String(firstDefined(line.status, line.parse_status, '')).toLowerCase()
  )
);

const normalizeReviewLines = (record) => importLines(record).map((line) => ({
  ...line,
  raw_name: firstDefined(line.raw_name, line.item_name, line.requested_item_name, ''),
  quantity: firstDefined(line.quantity, ''),
  unit: firstDefined(line.unit, ''),
  included: reviewLineIncluded(line),
  staff_reviewed: Boolean(firstDefined(
    line.staff_reviewed,
    line.reviewed_by_user,
    line.reviewed,
    false
  )),
}));

const reviewLineInvalid = (line) => {
  if (!line.included) return false;
  const quantity = Number(line.quantity);
  return (
    !String(line.raw_name || '').trim()
    || !String(line.unit || '').trim()
    || !Number.isFinite(quantity)
    || quantity <= 0
  );
};

const reviewLineUncertain = (line) => {
  if (!line.included || line.staff_reviewed) return false;
  const status = String(firstDefined(line.status, line.parse_status, '')).toLowerCase();
  return (
    lineOperation(line) === 'uncertain'
    || ['needs_review', 'uncertain', 'unparsed', 'invalid'].includes(status)
  );
};

const sourceKey = (source) => String(firstDefined(
  source.source_key,
  source.key,
  source.evidence_key,
  ''
));

const sourceIdentity = (source, index = 0) => sourceKey(source) || String(firstDefined(
  source.attachment_id,
  source.part_id,
  source.gmail_message_id && `message:${source.gmail_message_id}`,
  source.filename,
  `source-${index}`
));

const evidenceLabel = (evidence) => {
  const name = firstDefined(
    evidence.filename,
    evidence.attachment_filename,
    evidence.subject,
    evidence.message_subject,
    evidence.source_label,
    String(evidence.kind || '').toLowerCase() === 'ai_thread_analysis' ? 'AI thread analysis' : null,
    String(evidence.kind || '').toLowerCase() === 'email_body' ? 'Email body' : null,
    'Email evidence'
  );
  const page = firstDefined(evidence.page, evidence.page_number, evidence.source_page);
  const sheet = firstDefined(evidence.sheet_name, evidence.sheet);
  const cells = firstDefined(evidence.cell_range, evidence.cells);
  return [
    String(name),
    page ? `page ${page}` : null,
    sheet ? `sheet ${sheet}` : null,
    cells ? `cells ${cells}` : null,
  ].filter(Boolean).join(' | ');
};

const importAttachments = (record) => {
  const messages = importMessages(record);
  const candidates = [
    ...asArray(firstDefined(record.attachments, record.attachment_manifest, record.preview?.attachments)),
    ...messages.flatMap((message) => asArray(firstDefined(
      message.attachments,
      message.attachment_manifest
    )).map((attachment) => ({
      ...attachment,
      source_message_id: messageIdentity(message),
      source_subject: message.subject,
    }))),
  ];
  const byIdentity = new Map();
  candidates.forEach((attachment, index) => {
    const identity = [
      firstDefined(attachment.source_message_id, attachment.gmail_message_id, ''),
      firstDefined(attachment.attachment_id, attachment.part_id, ''),
      firstDefined(attachment.filename, ''),
      index,
    ].join('::');
    byIdentity.set(identity, attachment);
  });
  return [...byIdentity.values()];
};

const formatDateTime = (value) => {
  if (!value) return 'Date unavailable';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
};

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

const responseContentType = (response, attachment) => {
  const headers = response?.headers;
  const headerValue = typeof headers?.get === 'function'
    ? headers.get('content-type')
    : headers?.['content-type'];
  return normalizeMimeType(firstDefined(headerValue, response?.data?.type, attachmentMimeType(attachment)))
    || 'application/octet-stream';
};

const safeAttachmentFilename = (attachment) => Array.from(
  String(attachment?.filename || 'gmail-inquiry-attachment')
).map((character) => {
  const codePoint = character.codePointAt(0);
  return character === '\\' || character === '/' || codePoint < 32 || codePoint === 127
    ? '_'
    : character;
}).join('').slice(0, 240) || 'gmail-inquiry-attachment';

const GmailInquiryReview = ({
  token = '',
  importId = '',
  onClaimed,
  onOpenQuote,
  onBack,
}) => {
  const [record, setRecord] = useState(null);
  const [companies, setCompanies] = useState([]);
  const [contacts, setContacts] = useState([]);
  const [companyId, setCompanyId] = useState('');
  const [contactId, setContactId] = useState('');
  const [analysisMode, setAnalysisMode] = useState('current_message');
  const [selectedMessageIds, setSelectedMessageIds] = useState([]);
  const [reviewLines, setReviewLines] = useState([]);
  const [reviewDirty, setReviewDirty] = useState(false);
  const [dirtyReviewRowKeys, setDirtyReviewRowKeys] = useState(() => new Set());
  const [identityConfirmed, setIdentityConfirmed] = useState(false);
  const [identityReviewApproved, setIdentityReviewApproved] = useState(false);
  const [loading, setLoading] = useState(true);
  const [companiesLoading, setCompaniesLoading] = useState(true);
  const [contactsLoading, setContactsLoading] = useState(false);
  const [busyAction, setBusyAction] = useState('');
  const [errorInfo, setErrorInfo] = useState(null);
  const [notice, setNotice] = useState(null);
  const [attachmentError, setAttachmentError] = useState('');
  const recordRef = useRef(null);
  const mountedRef = useRef(false);
  const requestGenerationRef = useRef(0);
  const actionGenerationRef = useRef(0);
  const companyPatchGenerationRef = useRef(0);
  const autoAnalyzeImportRef = useRef('');
  const chainedActionLockRef = useRef(false);

  const applyPayload = useCallback((payload, { preserveSelection = false } = {}) => {
    const incoming = gmailImportRecordFromPayload(payload);
    const previous = recordRef.current;
    const next = previous ? { ...previous, ...incoming } : incoming;
    recordRef.current = next;
    setRecord(next);
    const company = firstDefined(next.selected_company, next.company, next.company_id, '');
    const contact = firstDefined(next.selected_contact, next.contact, next.contact_id, '');
    const previousCompany = previous
      ? firstDefined(previous.selected_company, previous.company, previous.company_id, '')
      : null;
    const previousContact = previous
      ? firstDefined(previous.selected_contact, previous.contact, previous.contact_id, '')
      : null;
    if (previous) {
      const identityChanged = (
        String(entityId(previousCompany) || '') !== String(entityId(company) || '')
        || String(entityId(previousContact) || '') !== String(entityId(contact) || '')
      );
      const analysisChanged = (
        String(previous.analysis_attempts ?? '') !== String(next.analysis_attempts ?? '')
        || String(previous.analyzed_at ?? '') !== String(next.analyzed_at ?? '')
        || String(previous.source_fingerprint ?? '') !== String(next.source_fingerprint ?? '')
      );
      if (identityChanged || analysisChanged) {
        setIdentityConfirmed(false);
      }
      if (gmailReviewUiV2Enabled(next)) {
        const incomingApproval = identityReviewState(incoming);
        if (incomingApproval.hasApproval) {
          setIdentityReviewApproved(incomingApproval.approved);
        } else if (identityChanged || analysisChanged) {
          setIdentityReviewApproved(false);
        }
      }
    } else if (gmailReviewUiV2Enabled(next)) {
      setIdentityReviewApproved(identityReviewState(next).approved);
    }
    setCompanyId(String(entityId(company) || ''));
    setContactId(String(entityId(contact) || ''));
    if (!preserveSelection) {
      setAnalysisMode(initialAnalysisMode(next));
      setSelectedMessageIds(initiallySelectedMessageIds(next));
      setReviewLines(normalizeReviewLines(next));
      setReviewDirty(false);
      setDirtyReviewRowKeys(new Set());
    }
    return incoming;
  }, []);

  const handleError = useCallback(async (error, action, endpoint) => {
    const details = await describeQuotationError(error, action, endpoint);
    if (!mountedRef.current) return;
    setErrorInfo(details);
    console.error(formatQuotationError(details), error);
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      actionGenerationRef.current += 1;
      companyPatchGenerationRef.current += 1;
      chainedActionLockRef.current = false;
    };
  }, []);

  const actionIsCurrent = useCallback(
    (generation) => (
      mountedRef.current
      && actionGenerationRef.current === generation
    ),
    []
  );

  const loadCompanies = useCallback(async (search = '') => {
    setCompaniesLoading(true);
    try {
      const response = await quotationAPI.companies.list({
        active: 'true',
        limit: 100,
        ...(String(search || '').trim() ? { search: String(search).trim() } : {}),
      });
      setCompanies((current) => mergeEntities(current, response.data));
    } catch (error) {
      await handleError(error, 'Load companies for Gmail inquiry', 'GET /quotations/companies/');
    } finally {
      setCompaniesLoading(false);
    }
  }, [handleError]);

  useEffect(() => {
    loadCompanies();
  }, [loadCompanies]);

  useEffect(() => {
    const candidates = companyCandidateEntities(record || {});
    const suggestion = suggestedCompany(record || {});
    setCompanies((current) => mergeEntities(
      current,
      [
        ...candidates,
        ...(suggestion && typeof suggestion === 'object' && suggestion.id ? [suggestion] : []),
      ]
    ));
  }, [record]);

  const contactSuggestion = suggestedContact(record || {});
  const contactSuggestionId = entityId(contactSuggestion);
  const contactCandidates = contactCandidateEntities(record || {});
  const contactCandidatesRef = useRef(contactCandidates);
  contactCandidatesRef.current = contactCandidates;

  useEffect(() => {
    if (!companyId) return;
    const matchingCandidates = contactCandidatesRef.current.filter(
      (candidate) => !candidate.company_id || String(candidate.company_id) === String(companyId)
    );
    if (matchingCandidates.length) {
      setContacts((current) => mergeEntities(current, matchingCandidates));
    }
  }, [companyId, record]);

  useEffect(() => {
    let cancelled = false;
    if (!companyId) {
      setContacts([]);
      return undefined;
    }
    setContactsLoading(true);
    quotationAPI.contacts.list({ active: 'true', company: companyId })
      .then((response) => {
        if (!cancelled) {
          const matchingCandidates = contactCandidatesRef.current.filter(
            (candidate) => !candidate.company_id || String(candidate.company_id) === String(companyId)
          );
          setContacts(mergeEntities(
            asCollection(response.data),
            matchingCandidates
          ));
        }
      })
      .catch((error) => {
        if (!cancelled) handleError(error, 'Load Gmail inquiry contacts', 'GET /quotations/contacts/');
      })
      .finally(() => {
        if (!cancelled) setContactsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [companyId, contactSuggestionId, handleError]);

  const runAnalysis = useCallback(async (targetId, {
    reanalyze = false,
    selectedIds = selectedMessageIds,
    mode = analysisMode,
  } = {}) => {
    if (!targetId || busyAction) return null;
    const normalizedMode = ANALYSIS_MODE_IDS.has(mode) ? mode : 'current_message';
    const normalizedSelectedIds = normalizedMode === 'selected_messages'
      ? [...new Set(asArray(selectedIds).map(String).filter(Boolean))]
      : [];
    if (normalizedMode === 'selected_messages' && normalizedSelectedIds.length === 0) {
      setNotice({
        type: 'warning',
        message: 'This Gmail import has no usable source messages. Return to Gmail and import the relevant email again.',
      });
      return null;
    }
    const actionGeneration = ++actionGenerationRef.current;
    setBusyAction(reanalyze ? 'reanalyze' : 'analyze');
    setErrorInfo(null);
    setNotice(null);
    setIdentityConfirmed(false);
    setIdentityReviewApproved(false);
    let replacementImportId = '';
    let analysisImportId = targetId;
    let analysisRequested = false;
    try {
      const selectionResponse = await quotationAPI.gmailInquiryImports.update(targetId, {
        mode: normalizedMode,
        selected_message_ids: normalizedSelectedIds,
      });
      if (!actionIsCurrent(actionGeneration)) return null;
      const currentRecord = recordRef.current || {};
      const incomingSelectionRecord = gmailImportRecordFromPayload(selectionResponse.data);
      const nextSelectionRecord = {
        ...currentRecord,
        ...incomingSelectionRecord,
      };
      const selectionContextChanged = Boolean(
        String(entityId(currentRecord) || '') !== String(entityId(nextSelectionRecord) || '')
        || initialAnalysisMode(currentRecord) !== initialAnalysisMode(nextSelectionRecord)
        || String(currentRecord.source_fingerprint || '') !== String(nextSelectionRecord.source_fingerprint || '')
      );
      const selectionRecord = applyPayload(selectionResponse.data, {
        preserveSelection: !selectionContextChanged,
      });
      if (selectionContextChanged) {
        // The rows on screen belong to the previous import configuration.
        // Never display them beneath a replacement import while its new
        // message selection is being analyzed.
        setReviewLines([]);
        setReviewDirty(false);
        setDirtyReviewRowKeys(new Set());
      }
      const effectiveImportId = entityId(selectionRecord) || targetId;
      analysisImportId = effectiveImportId;
      autoAnalyzeImportRef.current = String(effectiveImportId);
      if (String(effectiveImportId) !== String(targetId)) {
        replacementImportId = effectiveImportId;
      }
      analysisRequested = true;
      const analysisRequest = quotationAPI.gmailInquiryImports.analyze(effectiveImportId, {
        force: Boolean(reanalyze),
      });
      if (replacementImportId && actionIsCurrent(actionGeneration)) {
        // Persist the durable deduplicated import ID before the potentially
        // long analysis request finishes, so refresh/back cannot resume the
        // superseded record.
        onClaimed?.(replacementImportId);
      }
      const response = await analysisRequest;
      if (!actionIsCurrent(actionGeneration)) return response.data;
      applyPayload(response.data);
      setNotice({
        type: 'success',
        message: reanalyze
          ? 'The Gmail inquiry was analyzed again. Review the updated evidence below.'
          : 'Gmail inquiry analysis is ready for review.',
      });
      return response.data;
    } catch (error) {
      if (!actionIsCurrent(actionGeneration)) return null;
      const recoverableRequestFailure = Boolean(
        error?.code === 'ECONNABORTED'
        || !error?.response
        || error?.response?.status === 409
        || Number(error?.response?.status) >= 500
      );
      const shouldRefreshAnalysisState = Boolean(
        analysisRequested
        || error?.response?.status === 409
      );
      if (shouldRefreshAnalysisState) {
        try {
          const recoveryResponse = await quotationAPI.gmailInquiryImports.retrieve(analysisImportId);
          if (!actionIsCurrent(actionGeneration)) return null;
          const recoveryRecord = applyPayload(recoveryResponse.data);
          const recoveryStatus = importStatus(recoveryRecord);
          if (
            recoverableRequestFailure
            && ACTIVE_ANALYSIS_STATUSES.has(recoveryStatus)
          ) {
            setErrorInfo(null);
            setNotice({
              type: 'info',
              message: 'Gmail analysis is still processing. This page will keep checking for the result.',
            });
            return recoveryResponse.data;
          }
          if (
            recoverableRequestFailure
            && ['ready', 'review_required', 'confirmed'].includes(recoveryStatus)
            && (importLines(recoveryRecord).length || quotationIdFromGmailImportPayload(recoveryRecord))
          ) {
            setErrorInfo(null);
            setNotice({
              type: 'success',
              message: 'Gmail analysis finished while the browser was reconnecting. Review the recovered result below.',
            });
            return recoveryResponse.data;
          }
        } catch {
          // Preserve the original analyze error when status recovery also fails.
        }
      }
      await handleError(
        error,
        reanalyze ? 'Reanalyze Gmail inquiry' : 'Analyze Gmail inquiry',
        `POST /quotations/gmail-inquiry-imports/${analysisImportId}/analyze/`
      );
      return null;
    } finally {
      if (actionIsCurrent(actionGeneration)) setBusyAction('');
    }
  }, [
    actionIsCurrent,
    analysisMode,
    applyPayload,
    busyAction,
    handleError,
    onClaimed,
    selectedMessageIds,
  ]);

  useEffect(() => {
    const generation = ++requestGenerationRef.current;
    actionGenerationRef.current += 1;
    companyPatchGenerationRef.current += 1;
    let cancelled = false;
    const loadImport = async () => {
      setLoading(true);
      setErrorInfo(null);
      try {
        const response = token
          ? await quotationAPI.gmailInquiryImports.claim(token)
          : await quotationAPI.gmailInquiryImports.retrieve(importId);
        if (cancelled || requestGenerationRef.current !== generation) return;
        const incoming = applyPayload(response.data);
        const claimedId = entityId(incoming);
        if (claimedId && token) onClaimed?.(claimedId);
      } catch (error) {
        if (!cancelled && requestGenerationRef.current === generation) {
          await handleError(
            error,
            token ? 'Claim Gmail inquiry link' : 'Resume Gmail inquiry review',
            token
              ? 'POST /quotations/gmail-inquiry-imports/claim/'
              : `GET /quotations/gmail-inquiry-imports/${importId}/`
          );
        }
      } finally {
        if (!cancelled && requestGenerationRef.current === generation) setLoading(false);
      }
    };

    if (token || importId) loadImport();
    else {
      setLoading(false);
      setErrorInfo({
        action: 'Open Gmail inquiry',
        endpoint: 'Gmail quotation link',
        status: 'Invalid link',
        detail: 'This Gmail quotation link does not contain an import token or resumable import ID.',
      });
    }
    return () => {
      cancelled = true;
      requestGenerationRef.current += 1;
    };
  }, [applyPayload, handleError, importId, onClaimed, token]);

  const recordId = entityId(record);
  const status = importStatus(record || {});
  const gmailReviewUiV2 = gmailReviewUiV2Enabled(record || {});
  const gmailChainedActions = gmailChainedActionsEnabled(record || {});
  const identityReview = identityReviewState(record || {});
  const messages = useMemo(() => importMessages(record || {}), [record]);
  const messagesById = useMemo(
    () => new Map(messages.map((message, index) => [messageIdentity(message, index), message])),
    [messages]
  );
  const lines = useMemo(() => importLines(record || {}), [record]);
  const attachments = useMemo(() => importAttachments(record || {}), [record]);
  const warnings = useMemo(() => importWarnings(record || {}), [record]);
  const analysisMeta = useMemo(() => importMeta(record || {}), [record]);
  const evidenceSources = useMemo(() => {
    const candidates = [
      ...asArray(record?.evidence),
      ...reviewLines.flatMap(evidenceForLine),
    ];
    const byIdentity = new Map();
    candidates.forEach((source, index) => {
      const identity = sourceIdentity(source, index);
      if (!byIdentity.has(identity)) byIdentity.set(identity, source);
    });
    return [...byIdentity.values()];
  }, [record, reviewLines]);
  const enrichedEvidenceSources = useMemo(
    () => evidenceSources.map((source) => {
      const sourceMessageId = String(firstDefined(
        source.gmail_message_id,
        source.source_message_id,
        ''
      ));
      const isAttachment = (
        String(source.kind || '').toLowerCase() === 'attachment'
        || sourceKey(source).startsWith('attachment:')
      );
      const attachment = isAttachment
        ? attachments.find((candidate) => {
          const candidateMessageId = String(firstDefined(
            candidate.gmail_message_id,
            candidate.source_message_id,
            ''
          ));
          return (
            String(candidate.filename || '') === String(source.filename || '')
            && (
              !sourceMessageId
              || !candidateMessageId
              || sourceMessageId === candidateMessageId
            )
          );
        })
        : null;
      return {
        ...(attachment || {}),
        ...source,
        gmail_message_id: firstDefined(
          source.gmail_message_id,
          source.source_message_id,
          attachment?.gmail_message_id,
          attachment?.source_message_id
        ),
      };
    }),
    [attachments, evidenceSources]
  );
  const evidenceBySourceKey = useMemo(
    () => new Map(
      enrichedEvidenceSources
        .filter((source) => sourceKey(source))
        .map((source) => [sourceKey(source), source])
    ),
    [enrichedEvidenceSources]
  );
  const quoteId = quotationIdFromGmailImportPayload(record || {});
  const analysisActive = ACTIVE_ANALYSIS_STATUSES.has(status);
  const analysisRequestPending = ['analyze', 'reanalyze'].includes(busyAction);
  const analysisUiActive = analysisActive || analysisRequestPending;
  const readOnlyImport = Boolean(quoteId || status === 'confirmed');
  const analysisNeedsRecovery = Boolean(
    recordId
    && !readOnlyImport
    && !analysisUiActive
    && (
      RECOVERABLE_ANALYSIS_STATUSES.has(status)
      || (
        reviewLines.length === 0
        && (!AUTO_ANALYZE_STATUSES.has(status) || Boolean(errorInfo))
      )
    )
  );

  useEffect(() => {
    if (
      !recordId
      || quoteId
      || lines.length
      || !AUTO_ANALYZE_STATUSES.has(status)
      || autoAnalyzeImportRef.current === String(recordId)
      || busyAction
    ) return;
    autoAnalyzeImportRef.current = String(recordId);
    runAnalysis(recordId, {
      mode: initialAnalysisMode(record || {}),
      selectedIds: initiallySelectedMessageIds(record || {}),
    });
  }, [busyAction, lines.length, quoteId, record, recordId, runAnalysis, status]);

  useEffect(() => {
    if (!recordId || !analysisActive) return undefined;
    let cancelled = false;
    let timer = null;
    const schedulePoll = () => {
      timer = setTimeout(async () => {
        try {
          const response = await quotationAPI.gmailInquiryImports.retrieve(recordId);
          if (!cancelled) applyPayload(response.data);
        } catch (error) {
          if (!cancelled) {
            await handleError(
              error,
              'Refresh Gmail inquiry analysis',
              `GET /quotations/gmail-inquiry-imports/${recordId}/`
            );
          }
        } finally {
          // A transient network/server failure must not strand the review in
          // its locked "analyzing" state. Successful payloads cause this
          // effect to re-evaluate; failures keep polling the same import.
          if (!cancelled) schedulePoll();
        }
      }, 1800);
    };
    schedulePoll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [analysisActive, applyPayload, handleError, recordId]);

  const patchIdentity = async (patch) => {
    if (!recordId) return;
    const generation = ++companyPatchGenerationRef.current;
    const previousRecord = recordRef.current;
    setBusyAction('identity');
    setErrorInfo(null);
    try {
      const response = await quotationAPI.gmailInquiryImports.update(recordId, patch);
      if (mountedRef.current && generation === companyPatchGenerationRef.current) {
        applyPayload(response.data, { preserveSelection: true });
      }
    } catch (error) {
      if (mountedRef.current && generation === companyPatchGenerationRef.current) {
        try {
          const recoveryResponse = await quotationAPI.gmailInquiryImports.retrieve(recordId);
          if (mountedRef.current && generation === companyPatchGenerationRef.current) {
            applyPayload(recoveryResponse.data);
          }
        } catch {
          if (mountedRef.current && generation === companyPatchGenerationRef.current) {
            const previousCompany = firstDefined(
              previousRecord?.selected_company,
              previousRecord?.company,
              previousRecord?.company_id,
              ''
            );
            const previousContact = firstDefined(
              previousRecord?.selected_contact,
              previousRecord?.contact,
              previousRecord?.contact_id,
              ''
            );
            const previousCompanyId = String(entityId(previousCompany) || '');
            const previousContactId = String(entityId(previousContact) || '');
            setCompanyId(previousCompanyId);
            setContactId(previousContactId);
            setIdentityReviewApproved(identityReviewState(previousRecord || {}).approved);
          }
        }
        await handleError(
          error,
          'Save Gmail inquiry customer selection',
          `PATCH /quotations/gmail-inquiry-imports/${recordId}/`
        );
      }
    } finally {
      if (mountedRef.current && generation === companyPatchGenerationRef.current) {
        setBusyAction('');
      }
    }
  };

  const selectCompany = (value, company) => {
    const normalized = String(value || '');
    setCompanyId(normalized);
    setContactId('');
    setIdentityConfirmed(false);
    setIdentityReviewApproved(false);
    if (company) setCompanies((current) => mergeEntities(current, [company]));
    patchIdentity({ company: normalized || null, contact: null });
  };

  const selectContact = (value) => {
    const normalized = String(value || '');
    setContactId(normalized);
    setIdentityConfirmed(false);
    setIdentityReviewApproved(false);
    patchIdentity({ company: companyId || null, contact: normalized || null });
  };

  const updateReviewLine = (index, field, value) => {
    const rowKey = reviewRowKey(reviewLines[index] || {});
    const reviewUiV2 = gmailReviewUiV2Enabled(recordRef.current || {});
    setReviewLines((current) => current.map((line, candidateIndex) => (
      candidateIndex === index
        ? {
          ...line,
          [field]: value,
          // Correcting a substantive field is itself an explicit staff
          // review. An unchanged uncertain row still needs the separate
          // "Mark reviewed" action below.
          staff_reviewed: field === 'included' ? line.staff_reviewed : true,
        }
        : line
    )));
    const dirtyKey = rowKey || `__missing-row-${index}`;
    setDirtyReviewRowKeys((current) => new Set([...current, dirtyKey]));
    setReviewDirty(true);
    if (!reviewUiV2) setIdentityConfirmed(false);
  };

  const markReviewLineReviewed = (index) => {
    const line = reviewLines[index] || {};
    const rowKey = reviewRowKey(line);
    if (!rowKey || reviewLineInvalid(line)) return;
    setReviewLines((current) => current.map((candidate, candidateIndex) => (
      candidateIndex === index
        ? { ...candidate, staff_reviewed: true }
        : candidate
    )));
    setDirtyReviewRowKeys((current) => new Set([...current, rowKey]));
    setReviewDirty(true);
  };

  const workflowConcurrencyPayload = (sourceRecord) => ({
    expected_source_fingerprint: String(sourceRecord?.source_fingerprint || ''),
    expected_analysis_attempt: firstDefined(sourceRecord?.analysis_attempts, null),
    identity_review_fingerprint: identityReviewState(sourceRecord || {}).fingerprint,
    expected_review_rows_fingerprint: String(sourceRecord?.review_rows_fingerprint || ''),
  });

  const completeWorkflowConcurrencyPayload = (payload) => Boolean(
    payload.expected_source_fingerprint
    && payload.expected_analysis_attempt !== null
    && payload.expected_analysis_attempt !== undefined
    && payload.identity_review_fingerprint
    && payload.expected_review_rows_fingerprint
  );

  const persistDirtyReviewLines = async ({ actionGeneration, announce = true }) => {
    if (!reviewDirty) return recordRef.current;
    if (invalidIncludedLines.length) {
      setNotice({
        type: 'warning',
        message: 'Every included row needs an item name, a quantity above zero, and a unit before it can be saved.',
      });
      return null;
    }
    const sourceRecord = recordRef.current || {};
    const reviewUiV2 = gmailReviewUiV2Enabled(sourceRecord);
    const chainedActions = gmailChainedActionsEnabled(sourceRecord);
    const linesToSave = reviewUiV2
      ? reviewLines.filter((line, index) => (
        dirtyReviewRowKeys.has(reviewRowKey(line))
        || dirtyReviewRowKeys.has(`__missing-row-${index}`)
      ))
      : reviewLines;
    const missingStableKeys = linesToSave.some((line) => !reviewRowKey(line));
    if (missingStableKeys) {
      setNotice({
        type: 'error',
        message: 'These rows do not yet have stable review keys. Import the email again from Gmail, then try again.',
      });
      return null;
    }
    const payload = {
      review_lines: linesToSave.map((line) => ({
        row_key: reviewRowKey(line),
        raw_name: String(line.raw_name || '').trim(),
        quantity: line.quantity === '' || line.quantity === null ? null : line.quantity,
        unit: String(line.unit || '').trim(),
        included: Boolean(line.included),
        ...(reviewUiV2 ? { reviewed: Boolean(line.staff_reviewed) } : {}),
      })),
    };
    if (chainedActions) {
      const concurrency = workflowConcurrencyPayload(sourceRecord);
      if (!completeWorkflowConcurrencyPayload(concurrency)) {
        setNotice({
          type: 'error',
          message: 'This review is missing current safety fingerprints. Refresh the Gmail inquiry before continuing.',
        });
        return null;
      }
      Object.assign(payload, concurrency);
    }
    const response = await quotationAPI.gmailInquiryImports.update(recordId, payload);
    if (!actionIsCurrent(actionGeneration)) return null;
    const incoming = applyPayload(response.data, { preserveSelection: true });
    const effectiveImportId = entityId(incoming) || recordId;
    const authoritativeRecord = recordRef.current || incoming;
    setReviewLines(normalizeReviewLines(authoritativeRecord));
    setReviewDirty(false);
    setDirtyReviewRowKeys(new Set());
    if (String(effectiveImportId) !== String(recordId)) onClaimed?.(effectiveImportId);
    if (announce) {
      setNotice({
        type: 'success',
        message: 'Reviewed Gmail inquiry rows were saved.',
      });
    }
    return authoritativeRecord;
  };

  const saveReviewLines = async () => {
    if (!recordId || !reviewDirty || busyAction || chainedActionLockRef.current) return;
    const useSynchronousLock = gmailChainedActionsEnabled(recordRef.current || {});
    if (useSynchronousLock) chainedActionLockRef.current = true;
    const actionGeneration = ++actionGenerationRef.current;
    setBusyAction('review-lines');
    setErrorInfo(null);
    setNotice(null);
    try {
      await persistDirtyReviewLines({ actionGeneration });
    } catch (error) {
      if (actionIsCurrent(actionGeneration)) {
        await handleError(
          error,
          'Save reviewed Gmail inquiry rows',
          `PATCH /quotations/gmail-inquiry-imports/${recordId}/`
        );
      }
    } finally {
      if (useSynchronousLock) chainedActionLockRef.current = false;
      if (actionIsCurrent(actionGeneration)) setBusyAction('');
    }
  };

  const approveCompany = async ({ company, contact = '', suggested = false }) => {
    const normalizedCompanyId = String(
      entityId(company)
      || (company && typeof company === 'object' ? company.company_id : '')
      || company
      || ''
    );
    const normalizedContactId = String(entityId(contact) || contact || '');
    const currentRecord = recordRef.current || {};
    const fingerprint = identityReviewState(currentRecord).fingerprint;
    if (
      !recordId
      || !normalizedCompanyId
      || !fingerprint
      || busyAction
      || analysisActive
    ) return;
    const actionGeneration = ++actionGenerationRef.current;
    setBusyAction('approve-company');
    setErrorInfo(null);
    setNotice(null);
    setIdentityReviewApproved(false);
    setCompanyId(normalizedCompanyId);
    setContactId(normalizedContactId);
    if (company && typeof company === 'object') {
      setCompanies((current) => mergeEntities(current, [company]));
    }
    try {
      const response = await quotationAPI.gmailInquiryImports.approveCompany(recordId, {
        company: normalizedCompanyId,
        contact: normalizedContactId || null,
        suggested: Boolean(suggested),
        identity_review_fingerprint: fingerprint,
      });
      if (!actionIsCurrent(actionGeneration)) return;
      applyPayload(response.data, { preserveSelection: true });
      setNotice({
        type: 'success',
        message: 'Customer company approved for this Gmail evidence.',
      });
    } catch (error) {
      if (actionIsCurrent(actionGeneration)) {
        await handleError(
          error,
          'Approve Gmail inquiry company',
          `POST /quotations/gmail-inquiry-imports/${recordId}/approve_company/`
        );
      }
    } finally {
      if (actionIsCurrent(actionGeneration)) setBusyAction('');
    }
  };

  const viewAttachment = async (attachment, source) => {
    const key = sourceKey(source || {});
    if (!recordId || !key || busyAction) return;
    const actionGeneration = ++actionGenerationRef.current;
    const endpoint = `GET /quotations/gmail-inquiry-imports/${recordId}/attachment/?source_key=<opaque>`;
    const inlineRequested = SAFE_INLINE_ATTACHMENT_TYPES.has(attachmentMimeType(attachment));
    let previewWindow = null;
    if (inlineRequested) {
      try {
        previewWindow = window.open('about:blank', '_blank');
        if (previewWindow) previewWindow.opener = null;
      } catch {
        previewWindow = null;
      }
    }

    setBusyAction(`attachment:${key}`);
    setAttachmentError('');
    setErrorInfo(null);
    try {
      const response = await quotationAPI.gmailInquiryImports.attachment(recordId, key);
      if (!actionIsCurrent(actionGeneration)) {
        if (previewWindow?.close) previewWindow.close();
        return;
      }
      const contentType = responseContentType(response, attachment);
      const inlineSafe = SAFE_INLINE_ATTACHMENT_TYPES.has(contentType);
      const blob = new Blob([response.data], {
        type: inlineSafe ? contentType : 'application/octet-stream',
      });
      if (!window.URL?.createObjectURL) {
        throw new Error('This browser cannot open downloaded attachment files.');
      }
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
      if (actionIsCurrent(actionGeneration)) {
        const details = await describeQuotationError(error, 'Open Gmail inquiry attachment', endpoint);
        if (actionIsCurrent(actionGeneration)) {
          setAttachmentError(details.detail || 'Could not open this Gmail attachment.');
          setErrorInfo(details);
          console.error(formatQuotationError(details), error);
        }
      }
    } finally {
      if (actionIsCurrent(actionGeneration)) setBusyAction('');
    }
  };

  const confirmImport = async () => {
    const identityApproved = gmailReviewUiV2 ? identityReviewApproved : identityConfirmed;
    if (!recordId || !companyId || !identityApproved || busyAction || analysisActive) return;
    const actionGeneration = ++actionGenerationRef.current;
    setBusyAction('confirm');
    setErrorInfo(null);
    setNotice(null);
    try {
      const payload = {
        company: companyId,
        contact: contactId || null,
      };
      if (gmailReviewUiV2) {
        payload.identity_review_fingerprint = identityReview.fingerprint;
      }
      const includedSourceKeys = reviewLines
        .filter((line) => line.included)
        .flatMap(sourceKeysForLine)
        .filter((key, index, values) => key && values.indexOf(key) === index);
      if (includedSourceKeys.length) {
        payload.selected_source_keys = includedSourceKeys;
      }
      const response = await quotationAPI.gmailInquiryImports.confirm(recordId, payload);
      if (!actionIsCurrent(actionGeneration)) return;
      applyPayload(response.data);
      const exactQuoteId = quotationIdFromGmailImportPayload(response.data);
      if (!exactQuoteId) {
        throw new Error('The Gmail inquiry was confirmed, but the backend did not return its quotation ID.');
      }
      onOpenQuote?.(exactQuoteId);
    } catch (error) {
      if (actionIsCurrent(actionGeneration)) {
        await handleError(
          error,
          'Confirm Gmail inquiry and open quotation',
          `POST /quotations/gmail-inquiry-imports/${recordId}/confirm/`
        );
      }
    } finally {
      if (actionIsCurrent(actionGeneration)) setBusyAction('');
    }
  };

  const confirmAuthoritativeImport = async (authoritativeRecord, actionGeneration) => {
    if (!authoritativeRecord || !actionIsCurrent(actionGeneration)) return null;
    const authoritativeImportId = entityId(authoritativeRecord) || recordId;
    const authoritativeCompanyId = String(entityId(firstDefined(
      authoritativeRecord.selected_company,
      authoritativeRecord.company,
      authoritativeRecord.company_id,
      ''
    )) || '');
    const authoritativeContactId = String(entityId(firstDefined(
      authoritativeRecord.selected_contact,
      authoritativeRecord.contact,
      authoritativeRecord.contact_id,
      ''
    )) || '');
    const concurrency = workflowConcurrencyPayload(authoritativeRecord);
    if (
      !authoritativeImportId
      || !authoritativeCompanyId
      || !completeWorkflowConcurrencyPayload(concurrency)
      || (
        gmailReviewUiV2Enabled(authoritativeRecord)
        && !identityReviewState(authoritativeRecord).approved
      )
    ) {
      setNotice({
        type: 'error',
        message: 'The saved review is no longer current. Refresh it and approve the customer company again.',
      });
      return null;
    }
    const authoritativeLines = normalizeReviewLines(authoritativeRecord);
    const includedSourceKeys = authoritativeLines
      .filter((line) => line.included)
      .flatMap(sourceKeysForLine)
      .filter((key, index, values) => key && values.indexOf(key) === index);
    const payload = {
      company: authoritativeCompanyId,
      contact: authoritativeContactId || null,
      ...concurrency,
    };
    if (includedSourceKeys.length) payload.selected_source_keys = includedSourceKeys;
    if (!actionIsCurrent(actionGeneration)) return null;
    const response = await quotationAPI.gmailInquiryImports.confirm(
      authoritativeImportId,
      payload
    );
    if (!actionIsCurrent(actionGeneration)) return null;
    applyPayload(response.data);
    const exactQuoteId = quotationIdFromGmailImportPayload(response.data);
    if (!exactQuoteId) {
      throw new Error('The Gmail inquiry was confirmed, but the backend did not return its quotation ID.');
    }
    if (!actionIsCurrent(actionGeneration)) return null;
    onOpenQuote?.(exactQuoteId);
    return response.data;
  };

  const saveReviewAndCreateQuotation = async () => {
    if (
      !recordId
      || busyAction
      || analysisActive
      || chainedActionLockRef.current
    ) return;
    chainedActionLockRef.current = true;
    const actionGeneration = ++actionGenerationRef.current;
    let endpoint = `POST /quotations/gmail-inquiry-imports/${recordId}/confirm/`;
    setBusyAction('save-create');
    setErrorInfo(null);
    setNotice(null);
    try {
      let authoritativeRecord = recordRef.current;
      if (reviewDirty) {
        endpoint = `PATCH /quotations/gmail-inquiry-imports/${recordId}/`;
        authoritativeRecord = await persistDirtyReviewLines({
          actionGeneration,
          announce: false,
        });
      }
      if (!authoritativeRecord || !actionIsCurrent(actionGeneration)) return;
      endpoint = `POST /quotations/gmail-inquiry-imports/${entityId(authoritativeRecord) || recordId}/confirm/`;
      await confirmAuthoritativeImport(authoritativeRecord, actionGeneration);
    } catch (error) {
      if (actionIsCurrent(actionGeneration)) {
        await handleError(
          error,
          reviewDirty
            ? 'Save Gmail review and create quotation'
            : 'Create quotation from Gmail review',
          endpoint
        );
      }
    } finally {
      chainedActionLockRef.current = false;
      if (actionIsCurrent(actionGeneration)) setBusyAction('');
    }
  };

  const usableLines = reviewLines.filter((line) => line.included);
  const invalidIncludedLines = reviewLines.filter(reviewLineInvalid);
  const uncertainIncludedLines = reviewLines.filter(reviewLineUncertain);
  const includedLinesWithoutEvidence = usableLines.filter(
    (line) => sourceKeysForLine(line).length === 0
  );
  const selectedCompany = companies.find((company) => String(company.id) === companyId);
  const selectedContact = contacts.find((contact) => String(contact.id) === contactId);
  const aiIdentity = record?.candidates?.ai_identity || {};
  const aiIdentityCompany = String(aiIdentity.company_name || '').trim();
  const aiIdentityContact = String(aiIdentity.contact_name || '').trim();
  const aiIdentityEmail = String(aiIdentity.contact_email || '').trim();
  const aiIdentityReason = String(aiIdentity.reason || '').trim();
  const aiIdentityConfidence = confidencePercent(aiIdentity);
  const identityReanalysisRequired = Boolean(
    record?.candidates?.identity_reanalysis_required
  );
  const aiIdentityFromForward = Boolean(
    record?.candidates?.ai_identity_unverified_forwarded
  );
  const aiIdentitySourceLabel = firstDefined(
    identityReanalysisRequired
      ? 'Previous analysis — reanalyze before relying on it'
      : null,
    aiIdentityFromForward ? 'Read from unverified forwarded content' : null,
    'Read from the customer email and signature'
  );
  const hasAiIdentity = Boolean(
    aiIdentityCompany
    || aiIdentityContact
    || aiIdentityEmail
  );
  const companySuggestion = suggestedCompany(record || {});
  const companySuggestionId = String(firstDefined(
    entityId(companySuggestion),
    companySuggestion?.company_id,
    ''
  ));
  const selectedCompanyIsSuggestion = Boolean(
    companySuggestionId && companySuggestionId === String(companyId)
  );
  const selectedSuggestionCanUseOneClickApproval = Boolean(
    identityReview.suggestionApprovable
    && selectedCompanyIsSuggestion
    && !contactId
  );
  const companySuggestionEvidence = companySuggestion
    ? companySuggestionEvidenceLabel(companySuggestion)
    : '';
  const anchorMessage = messages.find(
    (message, index) => messageIdentity(message, index) === String(record?.anchor_message_id || '')
  ) || messages[0];
  const detectedSender = firstDefined(
    record?.sender_email,
    record?.identity?.sender_email,
    asArray(record?.candidates?.sender_emails)[0],
    anchorMessage?.sender,
    anchorMessage?.from,
    'Unknown'
  );
  const claimedBy = firstDefined(
    record?.claimed_by_username,
    record?.claimed_by?.username,
    record?.confirmed_by_username,
    'the signed-in staff user'
  );
  const messageSelectionValid = (
    analysisMode !== 'selected_messages'
    || selectedMessageIds.length > 0
  );
  const confirmDisabled = Boolean(
    busyAction
    || analysisActive
    || (reviewDirty && !gmailChainedActions)
    || !companyId
    || !(gmailReviewUiV2 ? identityReviewApproved : identityConfirmed)
    || !messageSelectionValid
    || invalidIncludedLines.length > 0
    || uncertainIncludedLines.length > 0
    || includedLinesWithoutEvidence.length > 0
    || usableLines.length === 0
  );

  if (loading && !record) {
    return (
      <div className="qm-panel qm-gmail-import-loading" role="status">
        <span className="qm-spinner" aria-hidden="true" />
        <div>
          <h3>Opening Gmail inquiry</h3>
          <p>Claiming the secure link after login and loading the selected email.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="qm-section qm-gmail-import">
      <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />

      <div className="qm-panel qm-gmail-import-hero">
        <div className="qm-panel-heading">
          <div>
            <span className="qm-eyebrow">Gmail inquiry review</span>
            <h3>{importSubject(record || {})}</h3>
            <p>
              {importSender(record || {})}
              {' | '}
              {formatDateTime(importDate(record || {}))}
            </p>
          </div>
          <div className="qm-gmail-import-heading-actions">
            <span className={`qm-gmail-import-status status-${analysisUiActive ? 'analyzing' : status || 'pending'}`}>
              {analysisUiActive ? 'Analyzing' : quoteId ? 'Quotation created' : status.replaceAll('_', ' ') || 'Ready for review'}
            </span>
            {onBack && <button type="button" className="qm-secondary" onClick={onBack}>Back to inquiries</button>}
          </div>
        </div>
        <div className="qm-helper warning">
          Nothing is created automatically. Check the customer identity, extracted rows, and source evidence before confirming.
        </div>
        {analysisUiActive && (
          <div className="qm-feedback info" role="status" aria-live="polite">
            Analyzing the Gmail inquiry and supported documents. This usually takes 15–30 seconds; larger documents can take longer.
          </div>
        )}
        {analysisNeedsRecovery && (
          <div className="qm-feedback warning">
            <span>
              {RECOVERABLE_ANALYSIS_STATUSES.has(status) || errorInfo
                ? 'The analysis did not finish. You can safely retry it without creating a duplicate.'
                : 'No request items were found. Continue analysis to try the same Gmail import again.'}
            </span>
            <button
              type="button"
              className="qm-secondary"
              disabled={!messageSelectionValid || Boolean(busyAction)}
              onClick={() => runAnalysis(recordId, { reanalyze: true })}
            >
              {RECOVERABLE_ANALYSIS_STATUSES.has(status) || errorInfo ? 'Retry analysis' : 'Continue analysis'}
            </button>
          </div>
        )}
        {identityReanalysisRequired && !analysisUiActive && !readOnlyImport && (
          <div className="qm-feedback warning" role="status">
            <span>
              Customer identity matching was upgraded. The previous suggestion was cleared; reanalyze before relying on it.
            </span>
            <button
              type="button"
              className="qm-secondary"
              disabled={!messageSelectionValid || Boolean(busyAction)}
              onClick={() => runAnalysis(recordId, { reanalyze: true })}
            >
              Reanalyze Gmail inquiry
            </button>
          </div>
        )}
        {warnings.length > 0 && (
          <div className="qm-gmail-analysis-warnings" role="status">
            <strong>Review warnings</strong>
            <ul>
              {warnings.map((warning, index) => <li key={`${String(warning)}-${index}`}>{String(warning)}</li>)}
            </ul>
          </div>
        )}
        {(analysisMeta.ai_used || analysisMeta.multiple_distinct_sources || analysisMeta.obvious_order) && (
          <div className="qm-gmail-analysis-flags">
            {analysisMeta.ai_used && <span>AI-assisted extraction</span>}
            {analysisMeta.multiple_distinct_sources && <span>Different item sets detected</span>}
            {analysisMeta.obvious_order && <span>May be an order/LPO</span>}
          </div>
        )}
        {notice && <div className={`qm-feedback ${notice.type}`}>{notice.message}</div>}
        {quoteId && (
          <div className="qm-gmail-existing-quote">
            <div>
              <strong>This email has already been converted to a quotation.</strong>
              <span>
                Created by {claimedBy}. Open the exact linked quotation; this intake will never create or revise it again.
                If the customer later changes the request, use the quotation's manual revision action.
              </span>
            </div>
            <button type="button" className="qm-primary" onClick={() => onOpenQuote?.(quoteId)}>
              Open linked quotation
            </button>
          </div>
        )}
      </div>

      <div className="qm-panel qm-gmail-identity-panel">
        <div className="qm-panel-heading">
          <div>
            <h3>1. Confirm customer identity</h3>
            <p>Sender matching is a suggestion. Confirm the company and purchaser yourself before creating the quotation.</p>
          </div>
        </div>
        {companySuggestion && !companyId && (
          <div className="qm-gmail-company-suggestion">
            <div>
              <span>{companySuggestionEvidence}</span>
              <strong>{companySuggestion.name}</strong>
              <small>Staff confirmation is required before creating the quotation.</small>
            </div>
            <button
              type="button"
              className={
                gmailReviewUiV2 && identityReview.suggestionApprovable
                  ? 'qm-primary'
                  : 'qm-secondary'
              }
              disabled={
                readOnlyImport
                || analysisActive
                || Boolean(busyAction)
                || (
                  gmailReviewUiV2
                  && identityReview.suggestionApprovable
                  && !identityReview.fingerprint
                )
              }
              onClick={() => (
                gmailReviewUiV2 && identityReview.suggestionApprovable
                  ? approveCompany({ company: companySuggestion, contact: '', suggested: true })
                  : selectCompany(companySuggestionId, companySuggestion)
              )}
            >
              {busyAction === 'approve-company'
                ? 'Approving...'
                : gmailReviewUiV2 && identityReview.suggestionApprovable
                  ? 'Approve suggested company'
                  : gmailReviewUiV2
                    ? 'Select company for manual approval'
                  : 'Use suggested company'}
            </button>
          </div>
        )}
        <div className="qm-gmail-identity-grid">
          <CompanySelectWithCreate
            companies={companies}
            value={companyId}
            required
            loading={companiesLoading}
            disabled={readOnlyImport || analysisActive || Boolean(busyAction)}
            onSearch={loadCompanies}
            maxRenderedCompanies={Math.max(companies.length, 100)}
            suggestedName=""
            helperText={firstDefined(
              record?.company_match_reason,
              record?.identity?.company_reason,
              companySuggestionEvidence,
              ''
            )}
            onChange={selectCompany}
            onCreated={(company) => {
              setCompanies((current) => mergeEntities(current, [company]));
            }}
          />
          <label className="qm-gmail-contact-picker">
            <span className="qm-label-text">Contact / Purchaser</span>
            <select
              disabled={readOnlyImport || !companyId || contactsLoading || analysisActive || Boolean(busyAction)}
              value={contactId}
              onChange={(event) => selectContact(event.target.value)}
            >
              <option value="">{contactsLoading ? 'Loading contacts...' : 'No contact selected'}</option>
              {contacts.map((contact) => (
                <option key={contact.id} value={contact.id}>
                  {contact.name}{contact.email ? ` | ${contact.email}` : ''}
                </option>
              ))}
            </select>
          </label>
        </div>
        {hasAiIdentity && (
          <section className="qm-gmail-ai-identity" aria-label="AI-detected customer identity">
            <div className="qm-gmail-ai-identity-heading">
              <div>
                <span>AI-detected identity</span>
                <small>{aiIdentitySourceLabel}</small>
              </div>
              {aiIdentityConfidence !== null && (
                <strong>{aiIdentityConfidence}% confidence</strong>
              )}
            </div>
            <div className="qm-gmail-ai-identity-details">
              {aiIdentityCompany && (
                <div>
                  <span>Company stated in email</span>
                  <strong>{aiIdentityCompany}</strong>
                </div>
              )}
              {(aiIdentityContact || aiIdentityEmail) && (
                <div>
                  <span>Purchaser stated in email</span>
                  <strong>{aiIdentityContact || 'Name not stated'}</strong>
                  {aiIdentityEmail && <small>{aiIdentityEmail}</small>}
                </div>
              )}
            </div>
            <p>
              Evidence only — compare this with the selected saved company and purchaser.
              {aiIdentityReason && ` ${aiIdentityReason}`}
            </p>
          </section>
        )}
        <div className="qm-gmail-identity-evidence">
          <div>
            <span>Detected sender</span>
            <strong>{detectedSender}</strong>
          </div>
          <div>
            <span>Selected company</span>
            <strong>{selectedCompany?.name || 'Not selected'}</strong>
          </div>
          <div>
            <span>Selected purchaser</span>
            <strong>{selectedContact?.name || 'No contact selected'}</strong>
          </div>
          <div>
            <span>Match explanation</span>
            <strong>{firstDefined(
              identityReanalysisRequired
                ? 'Reanalysis required before using the previous suggestion'
                : null,
              aiIdentityFromForward
                ? 'Suggested from unverified forwarded content; confirm manually'
                : null,
              record?.company_match_reason,
              record?.identity?.company_reason,
              companySuggestionEvidence,
              'Manual confirmation required'
            )}</strong>
          </div>
        </div>
        {gmailReviewUiV2 ? (
          <div
            className={`qm-gmail-identity-approval${identityReviewApproved ? ' is-approved' : ''}`}
          >
            <div role="status" aria-live="polite">
              <strong>
                {identityReviewApproved
                  ? 'Company approved for this evidence'
                  : 'Company approval required'}
              </strong>
              <span>
                {identityReviewApproved
                  ? `Approved as ${selectedCompany?.name || 'the selected company'}. Row-only edits will not remove this approval.`
                  : companyId
                    ? 'Review the sender and evidence, then approve the selected company. A purchaser is never selected automatically.'
                    : 'Choose a company, or use the suggested-company approval above. A purchaser is optional and never selected automatically.'}
              </span>
            </div>
            {!identityReviewApproved && companyId && (
              <button
                type="button"
                className="qm-primary"
                disabled={
                  readOnlyImport
                  || analysisActive
                  || Boolean(busyAction)
                  || !identityReview.fingerprint
                }
                onClick={() => approveCompany({
                  company: selectedCompany || companyId,
                  contact: contactId,
                  suggested: selectedSuggestionCanUseOneClickApproval,
                })}
              >
                {busyAction === 'approve-company'
                  ? 'Approving...'
                  : selectedSuggestionCanUseOneClickApproval
                    ? 'Approve suggested company'
                    : 'Approve selected company'}
              </button>
            )}
            {!identityReview.fingerprint && !readOnlyImport && (
              <small>Reanalyze this Gmail inquiry to create a current identity-review fingerprint.</small>
            )}
          </div>
        ) : (
          <label className="qm-checkbox qm-gmail-identity-confirmation">
            <input
              type="checkbox"
              checked={identityConfirmed}
              disabled={readOnlyImport || !companyId || analysisActive || Boolean(busyAction)}
              onChange={(event) => setIdentityConfirmed(event.target.checked)}
            />
            I checked the sender and evidence and confirm that this inquiry belongs to the selected company.
          </label>
        )}
      </div>

      <div className="qm-panel qm-gmail-lines-panel">
        <div className="qm-panel-heading">
          <div>
            <h3>2. Review extracted request lines</h3>
            <p>Edit the request details or exclude a row before confirming. Customer prices are evidence only; your quotation price remains blank.</p>
          </div>
          <div className={`qm-gmail-lines-actions${reviewDirty ? ' is-dirty' : ''}`}>
            <div className="qm-gmail-lines-save-state">
              <span className="qm-heading-count">{usableLines.length} included row{usableLines.length === 1 ? '' : 's'}</span>
              {reviewDirty && (
                <span className="qm-gmail-unsaved-label" role="status" aria-live="polite">
                  Unsaved row changes — save them to unlock confirmation.
                </span>
              )}
            </div>
            <button
              type="button"
              className={reviewDirty ? 'qm-primary qm-gmail-save-review-button' : 'qm-secondary'}
              disabled={readOnlyImport || !reviewDirty || Boolean(busyAction) || analysisActive}
              onClick={saveReviewLines}
            >
              {busyAction === 'review-lines' ? 'Saving rows...' : 'Save reviewed rows'}
            </button>
          </div>
        </div>
        {(
          invalidIncludedLines.length > 0
          || uncertainIncludedLines.length > 0
          || includedLinesWithoutEvidence.length > 0
        ) && (
          <div className="qm-feedback warning qm-gmail-line-review-warning">
            {invalidIncludedLines.length > 0
              ? `${invalidIncludedLines.length} included row(s) need a valid item name, quantity, and unit.`
              : uncertainIncludedLines.length > 0
                ? `${uncertainIncludedLines.length} included row(s) still need staff review. Correct them or exclude them.`
                : `${includedLinesWithoutEvidence.length} included row(s) have no source evidence. Retry analysis above or exclude them.`}
          </div>
        )}
        {attachmentError && <div className="qm-feedback error" role="alert">{attachmentError}</div>}
        <div className="qm-table-wrap">
          <table className="qm-table qm-gmail-lines-table">
            <thead>
              <tr>
                <th>Use</th>
                <th>#</th>
                <th>Change</th>
                <th>Requested Item</th>
                <th>Matched Product</th>
                <th>Qty</th>
                <th>Unit</th>
                <th>Customer Price / Budget Evidence</th>
                <th>Confidence</th>
                <th>Status</th>
                <th>Source Evidence</th>
              </tr>
            </thead>
            <tbody>
              {reviewLines.map((line, index) => {
                const lineStatus = String(firstDefined(line.status, line.parse_status, 'needs_review')).toLowerCase();
                const operation = lineOperation(line);
                const commercial = lineCustomerCommercialEvidence(line);
                const confidence = confidencePercent(line);
                const evidence = evidenceForLine(line).map((source) => ({
                  ...(evidenceBySourceKey.get(sourceKey(source)) || {}),
                  ...source,
                }));
                return (
                  <tr
                    key={firstDefined(reviewRowKey(line), line.id, line.row_id, `${line.raw_name || 'line'}-${index}`)}
                    className={`status-${lineStatus} operation-${operation}${line.included ? '' : ' is-excluded'}`}
                  >
                    <td data-label="Use row">
                      <label className="qm-gmail-row-include">
                        <input
                          type="checkbox"
                          checked={Boolean(line.included)}
                          disabled={
                            readOnlyImport
                            || ['removed', 'duplicate'].includes(operation)
                            || analysisActive
                            || Boolean(busyAction)
                          }
                          onChange={(event) => updateReviewLine(index, 'included', event.target.checked)}
                        />
                        <span>{line.included ? 'Included' : 'Excluded'}</span>
                      </label>
                    </td>
                    <td data-label="Row">{index + 1}</td>
                    <td data-label="Change">
                      <span className={`qm-gmail-operation operation-${operation}`}>
                        {operation.replaceAll('_', ' ')}
                      </span>
                    </td>
                    <td data-label="Requested item">
                      <input
                        className="qm-input"
                        value={line.raw_name}
                        disabled={readOnlyImport || !line.included || analysisActive || Boolean(busyAction)}
                        aria-label={`Requested item row ${index + 1}`}
                        onChange={(event) => updateReviewLine(index, 'raw_name', event.target.value)}
                      />
                      {firstDefined(line.raw_line, line.raw_source_line) && <small>{firstDefined(line.raw_line, line.raw_source_line)}</small>}
                    </td>
                    <td data-label="Matched product">
                      {firstDefined(
                        line.matched_product_name,
                        line.matched_quote_item_name,
                        line.product_name,
                        'Unmatched'
                      )}
                      {line.match_reason && <small>{line.match_reason}</small>}
                    </td>
                    <td data-label="Quantity">
                      <input
                        className="qm-input compact"
                        type="text"
                        inputMode="decimal"
                        value={line.quantity}
                        disabled={readOnlyImport || !line.included || analysisActive || Boolean(busyAction)}
                        aria-label={`Quantity row ${index + 1}`}
                        onChange={(event) => updateReviewLine(index, 'quantity', event.target.value)}
                      />
                    </td>
                    <td data-label="Unit">
                      <input
                        className="qm-input compact"
                        value={line.unit}
                        disabled={readOnlyImport || !line.included || analysisActive || Boolean(busyAction)}
                        aria-label={`Unit row ${index + 1}`}
                        onChange={(event) => updateReviewLine(index, 'unit', event.target.value)}
                      />
                    </td>
                    <td data-label="Customer price / budget evidence">
                      <div className="qm-gmail-customer-commercial">
                        {commercial.unitPrice !== undefined && commercial.unitPrice !== null && commercial.unitPrice !== '' && (
                          <span><small>Customer unit evidence</small>{formatCommercialAmount(commercial.unitPrice, commercial.currency)}</span>
                        )}
                        {commercial.total !== undefined && commercial.total !== null && commercial.total !== '' && (
                          <span><small>Customer total evidence</small>{formatCommercialAmount(commercial.total, commercial.currency)}</span>
                        )}
                        {commercial.vat !== undefined && commercial.vat !== null && commercial.vat !== '' && (
                          <span><small>Customer VAT evidence</small>{String(commercial.vat)}</span>
                        )}
                        {(commercial.unitPrice === undefined || commercial.unitPrice === null || commercial.unitPrice === '')
                          && (commercial.total === undefined || commercial.total === null || commercial.total === '')
                          && (commercial.vat === undefined || commercial.vat === null || commercial.vat === '')
                          && <em>Not stated</em>}
                        <small className="qm-gmail-not-our-price">Evidence only - not our quotation price</small>
                      </div>
                    </td>
                    <td data-label="Confidence">
                      {confidence === null
                        ? <span className="qm-gmail-confidence unknown">Review</span>
                        : <span className={`qm-gmail-confidence ${confidence >= 85 ? 'high' : confidence >= 65 ? 'medium' : 'low'}`}>{confidence}%</span>}
                    </td>
                    <td data-label="Status">
                      <div className="qm-gmail-row-review-state">
                        <span className={`qm-gmail-line-status status-${line.staff_reviewed ? 'reviewed' : lineStatus}`}>
                          {line.staff_reviewed ? 'staff reviewed' : lineStatus.replaceAll('_', ' ')}
                        </span>
                        {gmailReviewUiV2 && reviewLineUncertain(line) && !line.staff_reviewed && (
                          <button
                            type="button"
                            className="qm-secondary small qm-gmail-mark-reviewed"
                            disabled={
                              readOnlyImport
                              || analysisActive
                              || Boolean(busyAction)
                              || reviewLineInvalid(line)
                            }
                            onClick={() => markReviewLineReviewed(index)}
                          >
                            Mark reviewed
                          </button>
                        )}
                      </div>
                    </td>
                    <td data-label="Source evidence">
                      <div className="qm-gmail-row-evidence">
                        {evidence.map((source, evidenceIndex) => {
                          const message = messagesById.get(String(source.gmail_message_id || ''));
                          const enrichedSource = {
                            ...source,
                            subject: firstDefined(source.subject, message?.subject),
                          };
                          const sourceDetail = [
                            firstDefined(message?.sender, message?.from),
                            firstDefined(message?.received_at, message?.sent_at)
                              ? formatDateTime(firstDefined(message?.received_at, message?.sent_at))
                              : null,
                            firstDefined(source.sheet_name, source.sheet)
                              ? `Sheet ${firstDefined(source.sheet_name, source.sheet)}`
                              : null,
                            firstDefined(source.cell_range, source.cells)
                              ? `Cells ${firstDefined(source.cell_range, source.cells)}`
                              : null,
                            firstDefined(source.raw_text, source.extracted_text),
                          ].filter(Boolean).join(' | ');
                          const key = sourceKey(enrichedSource);
                          const label = evidenceLabel(enrichedSource);
                          const isAttachment = (
                            String(enrichedSource.kind || '').toLowerCase() === 'attachment'
                            || key.startsWith('attachment:')
                          );
                          return isAttachment && key ? (
                            <button
                              type="button"
                              className="qm-gmail-row-evidence-link"
                              key={`${sourceIdentity(source, evidenceIndex)}-${evidenceIndex}`}
                              title={sourceDetail}
                              aria-label={`Open source ${label}`}
                              disabled={Boolean(busyAction)}
                              onClick={() => viewAttachment(enrichedSource, enrichedSource)}
                            >
                              {busyAction === `attachment:${key}` ? 'Opening...' : label}
                            </button>
                          ) : (
                            <span
                              key={`${sourceIdentity(source, evidenceIndex)}-${evidenceIndex}`}
                              title={sourceDetail}
                            >
                              {label}
                            </span>
                          );
                        })}
                        {!evidence.length && <em>Evidence link unavailable</em>}
                      </div>
                    </td>
                  </tr>
                );
              })}
              {!reviewLines.length && (
                <tr>
                  <td colSpan="11">
                    {analysisUiActive ? (
                      <div className="qm-empty" role="status">
                        <strong>Analyzing the request...</strong>
                        <span>Extracted item rows will appear here automatically when analysis finishes.</span>
                      </div>
                    ) : (
                      <div className="qm-empty">
                        No inquiry rows were extracted. Use the analysis action above to try again, or import the relevant email again from Gmail.
                      </div>
                    )}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {!quoteId && (
        <div className="qm-panel qm-gmail-confirm-panel">
          <div>
            <h3>Ready to create the draft quotation?</h3>
            <p>The reviewed company, quantities, product matches, and source evidence will be saved. You will be taken to the exact new quotation to enter your prices.</p>
            <span className="qm-gmail-confirm-actor">Confirming as {claimedBy}</span>
          </div>
          <button
            type="button"
            className="qm-primary"
            disabled={confirmDisabled}
            onClick={gmailChainedActions ? saveReviewAndCreateQuotation : confirmImport}
          >
            {busyAction === 'save-create'
              ? 'Saving review & creating quotation...'
              : busyAction === 'confirm'
                ? 'Creating quotation...'
                : gmailChainedActions && reviewDirty
                  ? 'Save Review & Create Quotation'
                  : gmailChainedActions
                    ? 'Create Quotation'
                    : 'Confirm & Open Quotation'}
          </button>
          {confirmDisabled && (
            <small>
              {reviewDirty && !gmailChainedActions
                ? 'Save the reviewed rows before confirming.'
                  : invalidIncludedLines.length
                    ? 'Correct or exclude every invalid row first.'
                    : uncertainIncludedLines.length
                      ? 'Correct or exclude every uncertain row first.'
                      : includedLinesWithoutEvidence.length
                        ? 'Retry analysis above or exclude every row without source evidence.'
                        : !companyId
                          ? 'Select the customer company first.'
                          : !(gmailReviewUiV2 ? identityReviewApproved : identityConfirmed)
                            ? 'Confirm the customer identity first.'
                            : !messageSelectionValid
                              ? 'This Gmail import has no usable source messages. Return to Gmail and import it again.'
                              : !usableLines.length
                                ? 'Analysis must return at least one usable row.'
                                : 'Wait for the current operation to finish.'}
            </small>
          )}
        </div>
      )}
    </div>
  );
};

export default GmailInquiryReview;
