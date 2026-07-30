import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import CompanySelectWithCreate from './CompanySelectWithCreate';
import QuotationErrorNotice from './QuotationErrorNotice';

const ACTIVE_ANALYSIS_STATUSES = new Set(['analyzing', 'processing', 'queued', 'running']);
const AUTO_ANALYZE_STATUSES = new Set(['claimed', 'new', 'pending', 'ready_to_analyze']);
const ANALYSIS_MODES = [
  {
    id: 'current_message',
    label: 'Open message only',
    description: 'Use only the email that was open when the Gmail action was clicked.',
  },
  {
    id: 'selected_messages',
    label: 'Chosen messages',
    description: 'Use only the thread messages checked below.',
  },
  {
    id: 'ai_thread',
    label: 'AI-assisted thread',
    description: 'Let the analyzer choose relevant inquiry evidence from the conversation.',
  },
];
const ANALYSIS_MODE_IDS = new Set(ANALYSIS_MODES.map((mode) => mode.id));

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
  return (
    candidates.find((candidate) => String(candidate.id) === String(recommendedId))
    || (candidates.length === 1 ? candidates[0] : null)
  );
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
  return (
    candidates.find((candidate) => String(candidate.id) === String(recommendedId))
    || (candidates.length === 1 ? candidates[0] : null)
  );
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

const initiallySelectedSourceKeys = (record) => {
  const explicit = asArray(firstDefined(
    record.selected_source_keys,
    record.analysis?.selected_source_keys,
    record.analysis?.recommended_source_keys
  )).map(String);
  const evidence = asArray(record.evidence);
  const available = new Set(evidence.map(sourceKey).filter(Boolean));
  if (explicit.length) return explicit.filter((key) => available.has(key));
  return evidence
    .filter((source) => (
      source.selected !== false
      && source.included !== false
      && !['excluded', 'failed', 'skipped'].includes(String(firstDefined(source.status, source.role, '')).toLowerCase())
    ))
    .map(sourceKey)
    .filter((value, index, values) => value && values.indexOf(value) === index);
};

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
  return page ? `${name} | page ${page}` : String(name);
};

const importAttachments = (record) => {
  const messages = importMessages(record);
  const candidates = [
    ...asArray(firstDefined(record.attachments, record.attachment_manifest, record.preview?.attachments)),
    ...messages.flatMap((message) => asArray(firstDefined(message.attachments, message.attachment_manifest)).map((attachment) => ({
      ...attachment,
      source_message_id: messageIdentity(message),
      source_subject: message.subject,
    }))),
  ];
  const byKey = new Map();
  candidates.forEach((attachment, index) => {
    const key = [
      firstDefined(attachment.source_message_id, attachment.gmail_message_id, ''),
      firstDefined(attachment.attachment_id, attachment.part_id, ''),
      firstDefined(attachment.filename, ''),
      index,
    ].join('::');
    byKey.set(key, attachment);
  });
  return [...byKey.values()];
};

const formatDateTime = (value) => {
  if (!value) return 'Date unavailable';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
};

const formatBytes = (value) => {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
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
  const [selectedSourceKeys, setSelectedSourceKeys] = useState([]);
  const [reviewLines, setReviewLines] = useState([]);
  const [reviewDirty, setReviewDirty] = useState(false);
  const [selectionDirty, setSelectionDirty] = useState(false);
  const [identityConfirmed, setIdentityConfirmed] = useState(false);
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
      if (identityChanged || analysisChanged) setIdentityConfirmed(false);
    }
    setCompanyId(String(entityId(company) || ''));
    setContactId(String(entityId(contact) || ''));
    if (!preserveSelection) {
      setAnalysisMode(initialAnalysisMode(next));
      setSelectedMessageIds(initiallySelectedMessageIds(next));
      setSelectedSourceKeys(initiallySelectedSourceKeys(next));
      setReviewLines(normalizeReviewLines(next));
      setReviewDirty(false);
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
        message: 'Choose at least one thread message before analyzing selected messages.',
      });
      return null;
    }
    const actionGeneration = ++actionGenerationRef.current;
    setBusyAction(reanalyze ? 'reanalyze' : 'analyze');
    setErrorInfo(null);
    setNotice(null);
    setIdentityConfirmed(false);
    let replacementImportId = '';
    let analysisImportId = targetId;
    let analysisRequested = false;
    try {
      const selectionResponse = await quotationAPI.gmailInquiryImports.update(targetId, {
        mode: normalizedMode,
        selected_message_ids: normalizedSelectedIds,
      });
      if (!actionIsCurrent(actionGeneration)) return null;
      const selectionRecord = applyPayload(selectionResponse.data, { preserveSelection: true });
      const effectiveImportId = entityId(selectionRecord) || targetId;
      analysisImportId = effectiveImportId;
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
      setSelectionDirty(false);
      setNotice({
        type: 'success',
        message: reanalyze
          ? 'The selected Gmail messages were analyzed again. Review the updated evidence below.'
          : 'Gmail inquiry analysis is ready for review.',
      });
      return response.data;
    } catch (error) {
      if (!actionIsCurrent(actionGeneration)) return null;
      const recoverableRequestFailure = Boolean(
        analysisRequested
        && (
          error?.code === 'ECONNABORTED'
          || !error?.response
          || error?.response?.status === 409
          || Number(error?.response?.status) >= 500
        )
      );
      if (recoverableRequestFailure) {
        try {
          const recoveryResponse = await quotationAPI.gmailInquiryImports.retrieve(analysisImportId);
          if (!actionIsCurrent(actionGeneration)) return null;
          const recoveryRecord = applyPayload(recoveryResponse.data);
          const recoveryStatus = importStatus(recoveryRecord);
          if (ACTIVE_ANALYSIS_STATUSES.has(recoveryStatus)) {
            setSelectionDirty(false);
            setErrorInfo(null);
            setNotice({
              type: 'info',
              message: 'The browser stopped waiting, but Gmail analysis is still processing. This page will keep checking for the result.',
            });
            return recoveryResponse.data;
          }
          if (
            ['ready', 'review_required', 'confirmed'].includes(recoveryStatus)
            && (importLines(recoveryRecord).length || quotationIdFromGmailImportPayload(recoveryRecord))
          ) {
            setSelectionDirty(false);
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
  const selectableSourceKeys = useMemo(
    () => asArray(record?.evidence)
      .map(sourceKey)
      .filter((key, index, values) => key && values.indexOf(key) === index),
    [record]
  );
  const evidenceBySourceKey = useMemo(
    () => new Map(
      evidenceSources
        .filter((source) => sourceKey(source))
        .map((source) => [sourceKey(source), source])
    ),
    [evidenceSources]
  );
  const attachmentEvidence = useMemo(() => attachments.map((attachment) => {
    const matchedSource = evidenceSources.find((source) => (
      String(source.kind || '').toLowerCase() === 'attachment'
      && String(source.filename || '') === String(attachment.filename || '')
      && (
        !source.gmail_message_id
        || !attachment.gmail_message_id
        || String(source.gmail_message_id) === String(attachment.gmail_message_id)
      )
    ));
    return {
      attachment,
      source: matchedSource || null,
    };
  }), [attachments, evidenceSources]);
  const quoteId = quotationIdFromGmailImportPayload(record || {});
  const analysisActive = ACTIVE_ANALYSIS_STATUSES.has(status);
  const readOnlyImport = Boolean(quoteId || status === 'confirmed');

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
    setBusyAction('identity');
    setErrorInfo(null);
    try {
      const response = await quotationAPI.gmailInquiryImports.update(recordId, patch);
      if (mountedRef.current && generation === companyPatchGenerationRef.current) {
        applyPayload(response.data, { preserveSelection: true });
      }
    } catch (error) {
      if (mountedRef.current && generation === companyPatchGenerationRef.current) {
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
    if (company) setCompanies((current) => mergeEntities(current, [company]));
    patchIdentity({ company: normalized || null, contact: null });
  };

  const selectContact = (value) => {
    const normalized = String(value || '');
    setContactId(normalized);
    setIdentityConfirmed(false);
    patchIdentity({ company: companyId || null, contact: normalized || null });
  };

  const toggleMessage = (messageId) => {
    setSelectedMessageIds((current) => (
      current.includes(messageId)
        ? current.filter((candidate) => candidate !== messageId)
        : [...current, messageId]
    ));
    setSelectionDirty(true);
    setIdentityConfirmed(false);
  };

  const changeAnalysisMode = (mode) => {
    if (!ANALYSIS_MODE_IDS.has(mode)) return;
    setAnalysisMode(mode);
    setSelectionDirty(true);
    setIdentityConfirmed(false);
  };

  const toggleSource = (key) => {
    if (!key) return;
    setSelectedSourceKeys((current) => (
      current.includes(key)
        ? current.filter((candidate) => candidate !== key)
        : [...current, key]
    ));
    setIdentityConfirmed(false);
  };

  const updateReviewLine = (index, field, value) => {
    setReviewLines((current) => current.map((line, candidateIndex) => (
      candidateIndex === index
        ? {
          ...line,
          [field]: value,
          staff_reviewed: field === 'included' ? line.staff_reviewed : true,
        }
        : line
    )));
    setReviewDirty(true);
    setIdentityConfirmed(false);
  };

  const saveReviewLines = async () => {
    if (!recordId || !reviewDirty || busyAction) return;
    if (invalidIncludedLines.length) {
      setNotice({
        type: 'warning',
        message: 'Every included row needs an item name, a quantity above zero, and a unit before it can be saved.',
      });
      return;
    }
    const missingStableKeys = reviewLines.some((line) => !reviewRowKey(line));
    if (missingStableKeys) {
      setNotice({
        type: 'error',
        message: 'These rows do not yet have stable review keys. Reanalyze the Gmail inquiry, then try again.',
      });
      return;
    }
    const actionGeneration = ++actionGenerationRef.current;
    setBusyAction('review-lines');
    setErrorInfo(null);
    setNotice(null);
    try {
      const response = await quotationAPI.gmailInquiryImports.update(recordId, {
        review_lines: reviewLines.map((line) => ({
          row_key: reviewRowKey(line),
          raw_name: String(line.raw_name || '').trim(),
          quantity: line.quantity === '' || line.quantity === null ? null : line.quantity,
          unit: String(line.unit || '').trim(),
          included: Boolean(line.included),
        })),
      });
      if (!actionIsCurrent(actionGeneration)) return;
      const incoming = applyPayload(response.data, { preserveSelection: true });
      const effectiveImportId = entityId(incoming) || recordId;
      setReviewLines(normalizeReviewLines(recordRef.current || incoming));
      setReviewDirty(false);
      if (String(effectiveImportId) !== String(recordId)) onClaimed?.(effectiveImportId);
      setNotice({
        type: 'success',
        message: 'Reviewed Gmail inquiry rows were saved.',
      });
    } catch (error) {
      if (actionIsCurrent(actionGeneration)) {
        await handleError(
          error,
          'Save reviewed Gmail inquiry rows',
          `PATCH /quotations/gmail-inquiry-imports/${recordId}/`
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
    if (!recordId || !companyId || !identityConfirmed || busyAction || analysisActive) return;
    const actionGeneration = ++actionGenerationRef.current;
    setBusyAction('confirm');
    setErrorInfo(null);
    setNotice(null);
    try {
      const payload = {
        company: companyId,
        contact: contactId || null,
      };
      if (selectableSourceKeys.length) {
        payload.selected_source_keys = selectedSourceKeys.filter((key) => selectableSourceKeys.includes(key));
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

  const progressCurrent = Number(firstDefined(
    record?.analysis_progress?.current,
    record?.analysis?.processed_messages,
    record?.processed_messages,
    0
  ));
  const progressTotal = Number(firstDefined(
    record?.analysis_progress?.total,
    record?.analysis?.total_messages,
    record?.total_messages,
    messages.length,
    0
  ));
  const progressPercent = progressTotal > 0
    ? Math.max(0, Math.min(100, Math.round((progressCurrent / progressTotal) * 100)))
    : 0;
  const canResumeAnalysis = Boolean(
    record?.can_resume
    || record?.analysis_has_more
    || record?.analysis?.has_more
    || ['failed', 'paused', 'partial'].includes(status)
  );
  const usableLines = reviewLines.filter((line) => line.included);
  const invalidIncludedLines = reviewLines.filter(reviewLineInvalid);
  const uncertainIncludedLines = reviewLines.filter(reviewLineUncertain);
  const selectedSourceKeySet = new Set(selectedSourceKeys);
  const sourceSelectionConflicts = selectableSourceKeys.length
    ? reviewLines.flatMap((line, index) => {
      if (!line.included) return [];
      const rowSourceKeys = sourceKeysForLine(line);
      const missingSourceKeys = rowSourceKeys.length
        ? rowSourceKeys.filter((key) => !selectedSourceKeySet.has(key))
        : ['missing-provenance'];
      return missingSourceKeys.length
        ? [{
          index,
          name: String(line.raw_name || `Row ${index + 1}`),
          missingSourceKeys,
        }]
        : [];
    })
    : [];
  const sourceConflictIndexes = new Set(
    sourceSelectionConflicts.map((conflict) => conflict.index)
  );
  const selectedCompany = companies.find((company) => String(company.id) === companyId);
  const selectedContact = contacts.find((contact) => String(contact.id) === contactId);
  const companySuggestion = suggestedCompany(record || {});
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
  const hasSelectedSource = (
    selectableSourceKeys.length === 0
    || selectedSourceKeys.some((key) => selectableSourceKeys.includes(key))
  );
  const sourceSelectionValid = (
    hasSelectedSource
    && sourceSelectionConflicts.length === 0
  );
  const confirmDisabled = Boolean(
    busyAction
    || analysisActive
    || selectionDirty
    || reviewDirty
    || !companyId
    || !identityConfirmed
    || !messageSelectionValid
    || !sourceSelectionValid
    || invalidIncludedLines.length > 0
    || uncertainIncludedLines.length > 0
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
            <span className={`qm-gmail-import-status status-${status || 'pending'}`}>
              {analysisActive ? 'Analyzing' : quoteId ? 'Quotation created' : status.replaceAll('_', ' ') || 'Ready for review'}
            </span>
            {onBack && <button type="button" className="qm-secondary" onClick={onBack}>Back to inquiries</button>}
          </div>
        </div>
        <div className="qm-helper warning">
          Nothing is created automatically. Check the customer identity, selected messages, attachments, and extracted rows before confirming.
        </div>
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

      <div className="qm-panel qm-gmail-analysis-panel">
        <div className="qm-panel-heading">
          <div>
            <h3>1. Thread and analysis</h3>
            <p>Select the messages that belong to this request. Reanalyzing replaces the rows below with evidence from that selection.</p>
          </div>
          <button
            type="button"
            className="qm-secondary"
            disabled={
              !recordId
              || readOnlyImport
              || analysisActive
              || Boolean(busyAction)
              || (analysisMode === 'selected_messages' && !selectedMessageIds.length)
            }
            onClick={() => runAnalysis(recordId, { reanalyze: true })}
          >
            {busyAction === 'reanalyze' ? 'Reanalyzing...' : canResumeAnalysis ? 'Continue analysis' : 'Reanalyze selection'}
          </button>
        </div>
        <fieldset className="qm-gmail-analysis-modes" disabled={readOnlyImport || analysisActive || Boolean(busyAction)}>
          <legend>Messages to analyze</legend>
          {ANALYSIS_MODES.map((mode) => (
            <label key={mode.id} className={analysisMode === mode.id ? 'selected' : ''}>
              <input
                type="radio"
                name="gmail-analysis-mode"
                value={mode.id}
                checked={analysisMode === mode.id}
                onChange={(event) => changeAnalysisMode(event.target.value)}
              />
              <span>
                <strong>{mode.label}</strong>
                <small>{mode.description}</small>
              </span>
            </label>
          ))}
        </fieldset>
        {(analysisActive || progressTotal > 0) && (
          <div className="qm-gmail-analysis-progress" role={analysisActive ? 'status' : undefined}>
            <div>
              <strong>{analysisActive ? 'Reading selected email evidence...' : 'Analysis progress'}</strong>
              <span>{progressTotal ? `${progressCurrent} of ${progressTotal} messages` : 'Preparing analysis'}</span>
            </div>
            <div className="qm-gmail-progress-track" aria-label={`Analysis ${progressPercent}% complete`}>
              <span style={{ width: `${progressPercent}%` }} />
            </div>
          </div>
        )}
        {selectionDirty && (
          <div className="qm-feedback warning">
            Message selection changed. Reanalyze the selection before confirming so every row remains tied to the correct evidence.
          </div>
        )}
        <div className="qm-gmail-thread-timeline">
          {messages.map((message, index) => {
            const id = messageIdentity(message, index);
            const role = normalizedRole(message);
            const isAnchor = String(id) === String(record?.anchor_message_id || '');
            const classification = String(firstDefined(message.classification, message.analysis_classification, ''))
              .replaceAll('_', ' ');
            const analysisReason = firstDefined(message.analysis_reason, message.reason, '');
            const analysisConfidence = Number(firstDefined(message.analysis_confidence, message.confidence));
            const analysisConfidenceLabel = Number.isFinite(analysisConfidence)
              ? `${Math.round(analysisConfidence <= 1 ? analysisConfidence * 100 : analysisConfidence)}% confidence`
              : '';
            return (
              <article key={id} className={`qm-gmail-thread-message role-${role}`}>
                <div className="qm-gmail-thread-marker" aria-hidden="true" />
                <div className="qm-gmail-thread-card">
                  <div className="qm-gmail-thread-heading">
                    <label>
                      <input
                        type="checkbox"
                        checked={selectedMessageIds.includes(id)}
                        disabled={
                          analysisMode !== 'selected_messages'
                          || readOnlyImport
                          || analysisActive
                          || Boolean(busyAction)
                        }
                        onChange={() => toggleMessage(id)}
                      />
                      Use when "Chosen messages" is selected
                    </label>
                    <div className="qm-gmail-message-badges">
                      {isAnchor && <span className="qm-gmail-anchor-badge">Open email / Anchor</span>}
                      <span className={`qm-gmail-message-role role-${role}`}>
                        {role === 'used' ? 'Used' : role === 'excluded' ? 'Excluded' : 'Context'}
                      </span>
                    </div>
                  </div>
                  <strong>{message.subject || '(No subject)'}</strong>
                  <span>{firstDefined(message.sender, message.from, 'Unknown sender')} | {formatDateTime(firstDefined(message.received_at, message.sent_at))}</span>
                  <p>{firstDefined(message.snippet, message.body_preview, 'No preview available.')}</p>
                  {(classification || analysisReason || analysisConfidenceLabel) && (
                    <small className="qm-gmail-message-explanation">
                      {[classification, analysisReason, analysisConfidenceLabel].filter(Boolean).join(' | ')}
                    </small>
                  )}
                </div>
              </article>
            );
          })}
          {!messages.length && <div className="qm-empty">No Gmail thread messages are available yet.</div>}
        </div>
      </div>

      <div className="qm-panel qm-gmail-identity-panel">
        <div className="qm-panel-heading">
          <div>
            <h3>2. Confirm customer identity</h3>
            <p>Sender matching is a suggestion. Confirm the company and purchaser yourself before creating the quotation.</p>
          </div>
        </div>
        {companySuggestion && !companyId && (
          <div className="qm-gmail-company-suggestion">
            <div>
              <span>Suggested from exact sender evidence</span>
              <strong>{companySuggestion.name}</strong>
              {companySuggestion.match_method && (
                <small>{String(companySuggestion.match_method).replaceAll('_', ' ')}</small>
              )}
            </div>
            <button
              type="button"
              className="qm-secondary"
              disabled={readOnlyImport || analysisActive || Boolean(busyAction)}
              onClick={() => selectCompany(companySuggestion.id, companySuggestion)}
            >
              Use suggested company
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
              suggestedCompany(record || {})?.match_method,
              ''
            )}
            onChange={selectCompany}
            onCreated={(company) => {
              setCompanies((current) => mergeEntities(current, [company]));
            }}
          />
          <label>
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
              record?.company_match_reason,
              record?.identity?.company_reason,
              suggestedCompany(record || {})?.match_method,
              'Manual confirmation required'
            )}</strong>
          </div>
        </div>
        <label className="qm-checkbox qm-gmail-identity-confirmation">
          <input
            type="checkbox"
            checked={identityConfirmed}
            disabled={readOnlyImport || !companyId || analysisActive || Boolean(busyAction)}
            onChange={(event) => setIdentityConfirmed(event.target.checked)}
          />
          I checked the sender and evidence and confirm that this inquiry belongs to the selected company.
        </label>
      </div>

      <div className="qm-panel qm-gmail-attachments-panel">
        <div className="qm-panel-heading">
          <div>
            <h3>3. Attachments and source evidence</h3>
            <p>Every extracted row below shows where it came from. Failed or excluded files are never silently used.</p>
          </div>
          <span className="qm-heading-count">{attachments.length} attachment{attachments.length === 1 ? '' : 's'}</span>
        </div>
        <div className="qm-evidence-attachments qm-gmail-attachments">
          {attachmentEvidence.map(({ attachment, source }, index) => {
            const viewSource = source || attachment;
            const key = sourceKey(viewSource || {});
            const attachmentStatus = String(firstDefined(
              source?.status,
              source?.parse_status,
              attachment.status,
              attachment.parse_status,
              source ? 'available' : key ? 'unparsed' : 'unavailable'
            )).toLowerCase();
            const attachmentReason = firstDefined(
              source?.reason,
              source?.parse_reason,
              attachment.reason,
              attachment.parse_reason,
              asArray(source?.warnings)[0],
              !source
                ? warnings.find((warning) => String(warning).includes(String(attachment.filename || '')))
                : null,
              !source ? 'This file was not used for row extraction. You can still inspect the original attachment.' : null
            );
            return (
              <article key={`${firstDefined(attachment.attachment_id, attachment.part_id, attachment.filename, index)}-${index}`} className={`qm-evidence-attachment status-${attachmentStatus}`}>
                <div className="qm-evidence-attachment-heading">
                  <div>
                    <strong>{attachment.filename || 'Unnamed attachment'}</strong>
                    <span>{attachment.source_subject || attachment.mime_type || 'Gmail attachment'}</span>
                  </div>
                  <span className={`qm-evidence-attachment-status status-${attachmentStatus}`}>{attachmentStatus.replaceAll('_', ' ')}</span>
                </div>
                <div className="qm-evidence-attachment-meta">
                  {attachment.mime_type && <span>{attachment.mime_type}</span>}
                  {formatBytes(attachment.size) && <span>{formatBytes(attachment.size)}</span>}
                  {firstDefined(source?.line_count, attachment.line_count) !== undefined && (
                    <span>{firstDefined(source?.line_count, attachment.line_count)} extracted rows</span>
                  )}
                </div>
                {attachmentReason && <p>{String(attachmentReason)}</p>}
                {key && (
                  <button
                    type="button"
                    className="qm-secondary small"
                    disabled={Boolean(busyAction)}
                    onClick={() => viewAttachment(attachment, viewSource)}
                  >
                    {busyAction === `attachment:${key}` ? 'Opening...' : 'View / Open'}
                  </button>
                )}
              </article>
            );
          })}
          {!attachments.length && <div className="qm-empty compact">This inquiry uses the email body and has no file attachments.</div>}
        </div>
        {attachmentError && <div className="qm-feedback error">{attachmentError}</div>}
        {selectableSourceKeys.length > 0 && (
          <div className="qm-gmail-source-selection">
            <div>
              <strong>Evidence to carry into the quotation</strong>
              <span>Uncheck anything that does not belong to the customer's request.</span>
            </div>
            <div className="qm-gmail-source-options">
              {evidenceSources.filter(
                (source) => selectableSourceKeys.includes(sourceKey(source))
              ).map((source, index) => {
                const key = sourceKey(source);
                return (
                  <label key={key || sourceIdentity(source, index)}>
                    <input
                      type="checkbox"
                      checked={selectedSourceKeys.includes(key)}
                      disabled={readOnlyImport || analysisActive || Boolean(busyAction)}
                      onChange={() => toggleSource(key)}
                    />
                    <span>
                      <strong>{evidenceLabel(source)}</strong>
                      <small>{firstDefined(source.reason, source.source_type, source.mime_type, 'Parsed source evidence')}</small>
                    </span>
                  </label>
                );
              })}
            </div>
            {sourceSelectionConflicts.length > 0 && (
              <div className="qm-feedback warning" role="alert">
                <strong>
                  Confirmation is blocked: {sourceSelectionConflicts.length} included row(s)
                  still depend on unchecked evidence.
                </strong>
                <span>
                  {' '}
                  Re-select every cited source, or explicitly exclude the affected row:
                  {' '}
                  {sourceSelectionConflicts
                    .map((conflict) => `#${conflict.index + 1} ${conflict.name}`)
                    .join(', ')}.
                </span>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="qm-panel qm-gmail-lines-panel">
        <div className="qm-panel-heading">
          <div>
            <h3>4. Review extracted request lines</h3>
            <p>Edit the request details or exclude a row before confirming. Customer prices are evidence only; your quotation price remains blank.</p>
          </div>
          <div className="qm-gmail-lines-actions">
            <span className="qm-heading-count">{usableLines.length} included row{usableLines.length === 1 ? '' : 's'}</span>
            <button
              type="button"
              className="qm-secondary"
              disabled={readOnlyImport || !reviewDirty || Boolean(busyAction) || analysisActive}
              onClick={saveReviewLines}
            >
              {busyAction === 'review-lines' ? 'Saving rows...' : 'Save reviewed rows'}
            </button>
          </div>
        </div>
        {(invalidIncludedLines.length > 0 || uncertainIncludedLines.length > 0) && (
          <div className="qm-feedback warning qm-gmail-line-review-warning">
            {invalidIncludedLines.length > 0
              ? `${invalidIncludedLines.length} included row(s) need a valid item name, quantity, and unit.`
              : `${uncertainIncludedLines.length} included row(s) still need staff review. Correct them or exclude them.`}
          </div>
        )}
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
                const hasSourceConflict = sourceConflictIndexes.has(index);
                const evidence = evidenceForLine(line).map((source) => ({
                  ...(evidenceBySourceKey.get(sourceKey(source)) || {}),
                  ...source,
                }));
                return (
                  <tr
                    key={firstDefined(reviewRowKey(line), line.id, line.row_id, `${line.raw_name || 'line'}-${index}`)}
                    className={`status-${lineStatus} operation-${operation}${line.included ? '' : ' is-excluded'}${hasSourceConflict ? ' has-source-conflict' : ''}`}
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
                      <span className={`qm-gmail-line-status status-${line.staff_reviewed ? 'reviewed' : lineStatus}`}>
                        {line.staff_reviewed ? 'staff reviewed' : lineStatus.replaceAll('_', ' ')}
                      </span>
                    </td>
                    <td data-label="Source evidence">
                      <div className="qm-gmail-row-evidence">
                        {hasSourceConflict && (
                          <strong className="qm-gmail-source-conflict">
                            Blocked: re-select this row's evidence or exclude the row
                          </strong>
                        )}
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
                            firstDefined(source.raw_text, source.extracted_text),
                          ].filter(Boolean).join(' | ');
                          return (
                            <span
                              key={`${sourceIdentity(source, evidenceIndex)}-${evidenceIndex}`}
                              className={
                                sourceKey(source) && !selectedSourceKeys.includes(sourceKey(source))
                                  ? 'excluded'
                                  : ''
                              }
                              title={sourceDetail}
                            >
                              {evidenceLabel(enrichedSource)}
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
                  <td colSpan="11"><div className="qm-empty">No inquiry rows have been extracted. Resume or retry analysis before confirming.</div></td>
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
            <p>The reviewed company, message selection, quantities, product matches, and source evidence will be saved. You will be taken to the exact new quotation to enter your prices.</p>
            <span className="qm-gmail-confirm-actor">Confirming as {claimedBy}</span>
          </div>
          <button type="button" className="qm-primary" disabled={confirmDisabled} onClick={confirmImport}>
            {busyAction === 'confirm' ? 'Creating quotation...' : 'Confirm & Open Quotation'}
          </button>
          {confirmDisabled && (
            <small>
              {selectionDirty
                ? 'Reanalyze the changed message selection first.'
                : reviewDirty
                  ? 'Save the reviewed rows before confirming.'
                  : invalidIncludedLines.length
                    ? 'Correct or exclude every invalid row first.'
                    : uncertainIncludedLines.length
                      ? 'Correct or exclude every uncertain row first.'
                      : !companyId
                        ? 'Select the customer company first.'
                        : !identityConfirmed
                          ? 'Confirm the customer identity first.'
                            : !messageSelectionValid
                              ? 'Select at least one Gmail message.'
                            : !hasSelectedSource
                              ? 'Keep at least one source of evidence selected.'
                              : sourceSelectionConflicts.length
                                ? 'Re-select every source cited by included rows, or explicitly exclude the affected rows.'
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
