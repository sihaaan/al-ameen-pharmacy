import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import { releaseNumberWheelFocus } from '../../utils/numberInput';
import ProductPriceHistoryDialog from './ProductPriceHistoryDialog';
import AuditLogPanel from './AuditLogPanel';
import QuotationErrorNotice from './QuotationErrorNotice';
import CompanySelectWithCreate from './CompanySelectWithCreate';
import QuotationEmailPreviewDialog from './QuotationEmailPreviewDialog';
import CompanyPriceField, { historyPricePatch, isBlankPrice } from './CompanyPriceField';
import ProductSelect, { buildProductCatalogue } from './ProductSelect';

const editableStatuses = new Set(['draft', 'pending_review', 'approved']);
const UNSAVED_LINES_FINALIZE_ISSUE = 'Save all line changes before finalizing.';
const UNSAVED_DISCOUNT_FINALIZE_ISSUE = 'Save the final discount before finalizing.';

const gmailChainedActionsEnabled = (quote = {}) => (
  quote?.workflow_features?.gmail_chained_actions === true
);

const quotationEditorProgressiveLoadEnabled = (quote = {}) => (
  quote?.workflow_features?.quotation_editor_progressive_load === true
);

const supportingDatasetLabels = {
  items: 'Product catalogue and images',
  companyItems: 'Customer Product history',
  companies: 'Company directory',
  contacts: 'Company contacts',
  lpos: 'LPO records',
  priceHistory: 'Price history',
};
const statusSteps = [
  { id: 'draft', label: 'Draft' },
  { id: 'pending_review', label: 'Pending Review' },
  { id: 'approved', label: 'Approved' },
  { id: 'finalized', label: 'Finalized' },
  { id: 'sent', label: 'Sent' },
];

const paymentTermOptions = [
  { value: 'credit_30_days', label: 'Credit 30 days' },
  { value: 'credit_60_days', label: 'Credit 60 days' },
  { value: 'advance_100', label: '100% advance' },
  { value: 'pdc_30_days', label: 'PDC 30 days' },
  { value: 'cash', label: 'Cash' },
  { value: 'pdc_60_days', label: 'PDC 60 days' },
  { value: 'as_per_agreement', label: 'As per agreement' },
];

const unitSuggestions = [
  'each',
  'pcs',
  'nos',
  'no',
  'set',
  'box',
  'boxes',
  'pack',
  'pkt',
  'carton',
  'bottle',
  'tube',
  'roll',
  'pair',
  'bag',
  'vial',
  'ampoule',
  'strip',
  'sachet',
  'jar',
  'can',
  'tin',
  'case',
];

const sanitizeUnitText = (value) => String(value || '')
  .replace(/[0-9]/g, '')
  .replace(/\s+/g, ' ')
  .trimStart()
  .slice(0, 50);

const preventUnitNumberKey = (event) => {
  if (/^[0-9]$/.test(event.key)) {
    event.preventDefault();
  }
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

const emptyLine = {
  product: '',
  item_name_snapshot: '',
  brand_name_snapshot: '',
  description: '',
  quantity: '1',
  unit: '',
  unit_price: '',
  vat_rate: '0',
  match_status: 'unresolved',
  include_product_image: false,
  product_image: '',
  product_image_url: '',
  has_product_image: false,
  notes: '',
};

const normalizeVatRate = (value) => {
  if (value === null || value === undefined || value === '') return '0';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return numeric === 5 ? '5' : '0';
};

const normalizeDraft = (draft = {}) => ({
  product: String(draft.product || ''),
  item_name_snapshot: String(draft.item_name_snapshot || ''),
  brand_name_snapshot: String(draft.brand_name_snapshot || ''),
  description: String(draft.description || ''),
  quantity: String(draft.quantity || ''),
  unit: String(draft.unit || ''),
  unit_price: String(draft.unit_price ?? ''),
  price_source_history: draft.price_source_history || null,
  price_original_history: draft.price_original_history || null,
  price_reviewed: !!draft.price_reviewed,
  price_context_changed: !!draft.price_context_changed,
  price_feedback: draft.price_feedback || '',
  vat_rate: normalizeVatRate(draft.vat_rate),
  match_status: String(draft.match_status || 'unresolved'),
  include_product_image: !!draft.include_product_image,
  product_image: String(draft.product_image || ''),
  notes: String(draft.notes || ''),
});

const draftsMatch = (left, right) => JSON.stringify(normalizeDraft(left)) === JSON.stringify(normalizeDraft(right));

const mergeUnsavedLineDraft = (authoritativeDraft, currentDraft, savedDraft) => {
  if (!currentDraft || !savedDraft) return authoritativeDraft;
  const currentNormalized = normalizeDraft(currentDraft);
  const savedNormalized = normalizeDraft(savedDraft);
  const merged = { ...authoritativeDraft };
  Object.keys(currentNormalized).forEach((field) => {
    if (currentNormalized[field] !== savedNormalized[field]) {
      merged[field] = currentDraft[field];
    }
  });
  return merged;
};

const snapshotLineDrafts = (sourceQuote = {}, drafts = {}) => Object.fromEntries(
  (sourceQuote.lines || []).map((line) => [line.id, normalizeDraft(drafts[line.id])])
);

const lineDraftSnapshotMatches = (snapshot = {}, drafts = {}) => (
  Object.entries(snapshot).every(([lineId, expected]) => draftsMatch(drafts[lineId], expected))
);

const draftFromLine = (line) => ({
  product: line.product || '',
  item_name_snapshot: line.item_name_snapshot || '',
  brand_name_snapshot: line.brand_name_snapshot || '',
  description: line.description || '',
  quantity: line.quantity || '1',
  unit: line.unit || '',
  unit_price: line.unit_price ?? '',
  price_provenance: line.price_provenance || {},
  price_review_required: !!line.price_review_required,
  vat_rate: normalizeVatRate(line.vat_rate),
  match_status: line.match_status || 'unresolved',
  include_product_image: !!line.include_product_image,
  product_image: line.product_image || '',
  product_image_url: line.product_image_url || '',
  has_product_image: !!line.has_product_image,
  notes: line.notes || '',
});

const termsDraftFromQuote = (quote = {}) => ({
  payment_terms: quote.payment_terms || 'as_per_agreement',
  valid_until: quote.valid_until || '',
  show_brand_column: !!quote.show_brand_column,
});

const discountDraftFromQuote = (quote = {}) => String(quote.discount_amount ?? '0.00');

const discountDraftsMatch = (left, right) => {
  const leftText = String(left ?? '').trim();
  const rightText = String(right ?? '').trim();
  const leftNumber = leftText === '' ? 0 : Number(leftText);
  const rightNumber = rightText === '' ? 0 : Number(rightText);
  if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) {
    return leftNumber === rightNumber;
  }
  return leftText === rightText;
};

const BIGINT_ZERO = window.BigInt(0);
const BIGINT_ONE = window.BigInt(1);
const BIGINT_TWO = window.BigInt(2);
const BIGINT_TEN = window.BigInt(10);
const BIGINT_ONE_HUNDRED = window.BigInt(100);

const decimalParts = (value) => {
  const match = /^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:[eE]([+-]?\d+))?$/.exec(
    String(value ?? '').trim(),
  );
  if (!match) return null;
  const exponent = Number(match[5] || 0);
  // Model fields permit only modest fixed precision. This bound accepts every
  // representable value while avoiding unbounded powers from malformed input.
  if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > 100) return null;
  const whole = match[2] || '0';
  const fraction = match[2] === undefined ? (match[4] || '') : (match[3] || '');
  let coefficient = window.BigInt(`${whole}${fraction}` || '0')
    * (match[1] === '-' ? -BIGINT_ONE : BIGINT_ONE);
  let scale = fraction.length - exponent;
  if (scale < 0) {
    coefficient *= decimalPower(-scale);
    scale = 0;
  }
  return { coefficient, scale };
};

const decimalPower = (exponent) => BIGINT_TEN ** window.BigInt(exponent);

// Django Decimal.quantize uses ROUND_HALF_EVEN. Keep the editor preview on
// the same fixed-point rule so cent boundaries cannot disagree with saves.
const divideRoundHalfEven = (numerator, denominator) => {
  if (denominator <= BIGINT_ZERO) return BIGINT_ZERO;
  const negative = numerator < BIGINT_ZERO;
  const absolute = negative ? -numerator : numerator;
  let quotient = absolute / denominator;
  const remainder = absolute % denominator;
  const twiceRemainder = remainder * BIGINT_TWO;
  if (
    twiceRemainder > denominator
    || (twiceRemainder === denominator && quotient % BIGINT_TWO === BIGINT_ONE)
  ) {
    quotient += BIGINT_ONE;
  }
  return negative ? -quotient : quotient;
};

const quantizedDecimalProduct = (values, targetScale, extraDivisorScale = 0) => {
  const parsedValues = values.map(decimalParts);
  if (parsedValues.some((value) => value === null)) return BIGINT_ZERO;
  const coefficient = parsedValues.reduce(
    (product, value) => product * value.coefficient,
    BIGINT_ONE,
  );
  const sourceScale = parsedValues.reduce((sum, value) => sum + value.scale, 0)
    + extraDivisorScale;
  const scaleDifference = sourceScale - targetScale;
  if (scaleDifference <= 0) return coefficient * decimalPower(-scaleDifference);
  return divideRoundHalfEven(coefficient, decimalPower(scaleDifference));
};

const decimalToScaledInteger = (value, targetScale) => (
  quantizedDecimalProduct([value], targetScale)
);

const formatMoneyCents = (value) => {
  const cents = typeof value === 'bigint' ? value : window.BigInt(value || 0);
  const negative = cents < BIGINT_ZERO;
  const absolute = negative ? -cents : cents;
  const whole = absolute / BIGINT_ONE_HUNDRED;
  const fraction = String(absolute % BIGINT_ONE_HUNDRED).padStart(2, '0');
  return `${negative ? '-' : ''}${whole}.${fraction}`;
};

const partyDraftFromQuote = (quote = {}) => ({
  company: quote.company || '',
  contact: quote.contact || '',
});

const quotationReviewDisplaySignature = (quote = {}) => {
  const displayed = { ...(quote || {}) };
  delete displayed.quotation_review_fingerprint;
  return JSON.stringify(displayed);
};

const termsDraftsMatch = (left = {}, right = {}) => (
  String(left.payment_terms || '') === String(right.payment_terms || '') &&
  String(left.valid_until || '') === String(right.valid_until || '') &&
  !!left.show_brand_column === !!right.show_brand_column
);

const partyDraftsMatch = (left = {}, right = {}) => (
  String(left.company || '') === String(right.company || '') &&
  String(left.contact || '') === String(right.contact || '')
);

const safeDownloadNamePart = (value) => {
  const cleaned = String(value || '')
    .toUpperCase()
    .replace(/[^A-Z0-9-]+/g, '_')
    .replace(/^[_-]+|[_-]+$/g, '');
  return cleaned.slice(0, 80);
};

const quotationDownloadFilename = (quote, extension) => {
  const companyPart = safeDownloadNamePart(quote?.company_name);
  const quotePart = safeDownloadNamePart(quote?.quotation_number) || 'QUOTATION';
  return `${companyPart ? `${companyPart}-` : ''}${quotePart}.${extension}`;
};

const proformaDownloadFilename = (quote) => {
  const companyPart = safeDownloadNamePart(quote?.company_name);
  const quotePart = safeDownloadNamePart(quote?.quotation_number) || 'QUOTATION';
  return `${companyPart ? `${companyPart}-` : ''}PROFORMA-${quotePart}.pdf`;
};

const lpoDraftFromRecord = (lpo = null) => {
  const parsedMeta = lpo?.parsed_meta || {};
  const hasAppliedMapping = Object.prototype.hasOwnProperty.call(parsedMeta, 'applied_outcome_line_ids');
  const suggestedIds = (parsedMeta.outcome_suggestions || [])
    .map((suggestion) => Number(suggestion?.quotation_line_id))
    .filter(Number.isInteger);
  const appliedIds = (parsedMeta.applied_outcome_line_ids || [])
    .map(Number)
    .filter(Number.isInteger);
  return {
    lpo_number: lpo?.lpo_number || '',
    lpo_date: lpo?.lpo_date || '',
    notes: lpo?.notes || '',
    status: lpo?.status || 'parsed',
    applied_outcome_line_ids: hasAppliedMapping ? appliedIds : suggestedIds,
  };
};

const transientLoadStatuses = new Set([408, 500, 502, 503, 504]);

const MATERIAL_LPO_WARNING_PATTERNS = [
  /stopp(?:ed|ing)?\s+reading/i,
  /truncat/i,
  /\blimit(?:ed|s)?\b/i,
  /\bfallback\b/i,
  /\bpartial(?:ly)?\b/i,
  /no cached result/i,
  /\bnot (?:parsed|imported|refreshed)\b/i,
  /\bcould not\b/i,
  /cannot be fully inspected/i,
  /may appear blank|missing values/i,
  /not used for row extraction/i,
  /active[- ]content|embedded|macro|vba|external links?|highly compressed/i,
];

const isMaterialLpoWarning = (warning) => MATERIAL_LPO_WARNING_PATTERNS.some(
  (pattern) => pattern.test(String(warning)),
);

const LpoWarningReview = ({ warnings = [] }) => {
  const reviewWarnings = Array.isArray(warnings)
    ? warnings.filter((warning) => String(warning || '').trim())
    : [];
  if (!reviewWarnings.length) return null;
  const alwaysVisibleIndexes = new Set(
    reviewWarnings.reduce((indexes, warning, index) => {
      if (index < 3 || isMaterialLpoWarning(warning)) indexes.push(index);
      return indexes;
    }, []),
  );
  const alwaysVisible = reviewWarnings.filter((warning, index) => alwaysVisibleIndexes.has(index));
  const remaining = reviewWarnings.filter((warning, index) => !alwaysVisibleIndexes.has(index));
  return (
    <div className="qm-lpo-warning" role="alert" aria-label="LPO attachment warnings">
      <strong>Review attachment warnings</strong>
      {alwaysVisible.map((warning, index) => (
        <p key={`lpo-warning-${index}`}>{warning}</p>
      ))}
      {remaining.length > 0 && (
        <details>
          <summary>
            Show {remaining.length} more {remaining.length === 1 ? 'warning' : 'warnings'}
          </summary>
          {remaining.map((warning, index) => (
            <p key={`lpo-warning-more-${index}`}>{warning}</p>
          ))}
        </details>
      )}
    </div>
  );
};

export const shouldRetryQuotationGet = (error) => {
  if (error?.code === 'ERR_CANCELED') return false;
  if (!error?.response) return true;
  return transientLoadStatuses.has(Number(error.response.status));
};

export const retryTransientQuotationGet = async (request, delayMs = 150) => {
  try {
    return await request();
  } catch (error) {
    if (!shouldRetryQuotationGet(error)) throw error;
    if (delayMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
    return request();
  }
};

const QuotationEditor = ({
  quoteId,
  onClose,
  onOpenQuote,
  onReviewOutcome,
  onOpenGmailImport,
  gmailEvidenceVisible = false,
  initialEmailReviewFingerprint = '',
  onInitialEmailReviewHandled,
}) => {
  const [quote, setQuote] = useState(null);
  const [companies, setCompanies] = useState([]);
  const [contacts, setContacts] = useState([]);
  const [loadingContacts, setLoadingContacts] = useState(false);
  const [quotePartyDraft, setQuotePartyDraft] = useState(partyDraftFromQuote());
  const [savedQuotePartyDraft, setSavedQuotePartyDraft] = useState(partyDraftFromQuote());
  const [quoteTermsDraft, setQuoteTermsDraft] = useState(termsDraftFromQuote());
  const [savedQuoteTermsDraft, setSavedQuoteTermsDraft] = useState(termsDraftFromQuote());
  const [discountDraft, setDiscountDraft] = useState(discountDraftFromQuote());
  const [savedDiscountDraft, setSavedDiscountDraft] = useState(discountDraftFromQuote());
  const [items, setItems] = useState([]);
  const [companyItems, setCompanyItems] = useState([]);
  const [lineForm, setLineForm] = useState(emptyLine);
  const [lineDrafts, setLineDrafts] = useState({});
  const [savedLineDrafts, setSavedLineDrafts] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [actionInFlight, setActionInFlight] = useState('');
  const [downloadLoading, setDownloadLoading] = useState(false);
  const [excelDownloadLoading, setExcelDownloadLoading] = useState(false);
  const [proformaDownloadLoading, setProformaDownloadLoading] = useState(false);
  const [lineFeedback, setLineFeedback] = useState(null);
  const [linePriceHints, setLinePriceHints] = useState({});
  const [priceContexts, setPriceContexts] = useState({});
  const [priceContextError, setPriceContextError] = useState(null);
  const [priceHistoryDialog, setPriceHistoryDialog] = useState(null);
  const [errorInfo, setErrorInfo] = useState(null);
  const [referenceLoadFailures, setReferenceLoadFailures] = useState([]);
  const [supportingDatasetStates, setSupportingDatasetStates] = useState({});
  const [referenceRetrying, setReferenceRetrying] = useState(false);
  const [selectedLineIds, setSelectedLineIds] = useState([]);
  const [lineFilter, setLineFilter] = useState('active');
  const [productCreateModal, setProductCreateModal] = useState(null);
  const [productCreateError, setProductCreateError] = useState(null);
  const [showContactForm, setShowContactForm] = useState(false);
  const [contactForm, setContactForm] = useState(emptyContactForm);
  const [contactSaving, setContactSaving] = useState(false);
  const [lpos, setLpos] = useState([]);
  const [lpoDraft, setLpoDraft] = useState(lpoDraftFromRecord());
  const [lpoFile, setLpoFile] = useState(null);
  const [lpoText, setLpoText] = useState('');
  const [lpoUseAi, setLpoUseAi] = useState(true);
  const [lpoUploading, setLpoUploading] = useState(false);
  const [lpoSaving, setLpoSaving] = useState(false);
  const [lpoFeedback, setLpoFeedback] = useState(null);
  const [emailPreviewOpen, setEmailPreviewOpen] = useState(false);
  const [emailPreviewLoading, setEmailPreviewLoading] = useState(false);
  const [emailPreview, setEmailPreview] = useState(null);
  const [emailPreviewError, setEmailPreviewError] = useState('');
  const [emailSendError, setEmailSendError] = useState(null);
  const [emailSending, setEmailSending] = useState(false);
  const [emailQuoteFinalized, setEmailQuoteFinalized] = useState(false);
  const [emailThreadCandidates, setEmailThreadCandidates] = useState([]);
  const [emailThreadCandidatesLoading, setEmailThreadCandidatesLoading] = useState(false);
  const [emailThreadCandidatesError, setEmailThreadCandidatesError] = useState('');
  const [emailThreadSearchCompleted, setEmailThreadSearchCompleted] = useState(false);
  const [emailGmailReconnectError, setEmailGmailReconnectError] = useState('');
  const [emailReconciling, setEmailReconciling] = useState(false);
  const [emailReconcileFeedback, setEmailReconcileFeedback] = useState(null);
  const linePriceVersionRef = useRef({});
  const lineSelectedProductRef = useRef({});
  const lineFormPriceVersionRef = useRef(0);
  const lineFormSelectedProductRef = useRef('');
  const priceContextGenerationRef = useRef(0);
  const loadGenerationRef = useRef(0);
  const referenceLoadGenerationRef = useRef(0);
  const supportingDatasetGenerationRef = useRef({});
  const contactLoadGenerationRef = useRef(0);
  const emailPreviewGenerationRef = useRef(0);
  const emailThreadSearchGenerationRef = useRef(0);
  const emailReconcileGenerationRef = useRef(0);
  const reviewEmailGenerationRef = useRef(0);
  const lineSaveGenerationRef = useRef(0);
  const reviewEmailInFlightRef = useRef(false);
  const initialEmailReviewRequestRef = useRef('');
  const loadEmailPreviewRef = useRef(null);
  const priceInputRefs = useRef(new Map());
  const initialPriceFocusQuoteRef = useRef('');
  const quoteRef = useRef(null);
  const quotePartyDraftRef = useRef(quotePartyDraft);
  const savedQuotePartyDraftRef = useRef(savedQuotePartyDraft);
  const quoteTermsDraftRef = useRef(quoteTermsDraft);
  const savedQuoteTermsDraftRef = useRef(savedQuoteTermsDraft);
  const discountDraftRef = useRef(discountDraft);
  const savedDiscountDraftRef = useRef(savedDiscountDraft);
  const lineDraftsRef = useRef(lineDrafts);
  const savedLineDraftsRef = useRef(savedLineDrafts);
  quotePartyDraftRef.current = quotePartyDraft;
  savedQuotePartyDraftRef.current = savedQuotePartyDraft;
  quoteTermsDraftRef.current = quoteTermsDraft;
  savedQuoteTermsDraftRef.current = savedQuoteTermsDraft;
  discountDraftRef.current = discountDraft;
  savedDiscountDraftRef.current = savedDiscountDraft;
  lineDraftsRef.current = lineDrafts;
  savedLineDraftsRef.current = savedLineDrafts;

  const setLoadedQuote = useCallback((quoteData) => {
    const isSameQuote = String(quoteRef.current?.id || '') === String(quoteData?.id || '');
    const currentPartyDraft = quotePartyDraftRef.current;
    const previousSavedPartyDraft = savedQuotePartyDraftRef.current;
    const currentTermsDraft = quoteTermsDraftRef.current;
    const previousSavedTermsDraft = savedQuoteTermsDraftRef.current;
    const preserveDiscountDraft = isSameQuote && !discountDraftsMatch(
      discountDraftRef.current,
      savedDiscountDraftRef.current,
    );
    quoteRef.current = quoteData;
    setQuote(quoteData);
    const nextPartyDraft = partyDraftFromQuote(quoteData);
    const displayedPartyDraft = isSameQuote
      ? {
        company: String(currentPartyDraft.company || '') !== String(previousSavedPartyDraft.company || '')
          ? currentPartyDraft.company
          : nextPartyDraft.company,
        contact: String(currentPartyDraft.contact || '') !== String(previousSavedPartyDraft.contact || '')
          ? currentPartyDraft.contact
          : nextPartyDraft.contact,
      }
      : nextPartyDraft;
    quotePartyDraftRef.current = displayedPartyDraft;
    savedQuotePartyDraftRef.current = nextPartyDraft;
    setQuotePartyDraft(displayedPartyDraft);
    setSavedQuotePartyDraft(nextPartyDraft);
    const nextTermsDraft = termsDraftFromQuote(quoteData);
    const displayedTermsDraft = isSameQuote
      ? {
        payment_terms: String(currentTermsDraft.payment_terms || '') !== String(previousSavedTermsDraft.payment_terms || '')
          ? currentTermsDraft.payment_terms
          : nextTermsDraft.payment_terms,
        valid_until: String(currentTermsDraft.valid_until || '') !== String(previousSavedTermsDraft.valid_until || '')
          ? currentTermsDraft.valid_until
          : nextTermsDraft.valid_until,
        show_brand_column: !!currentTermsDraft.show_brand_column !== !!previousSavedTermsDraft.show_brand_column
          ? currentTermsDraft.show_brand_column
          : nextTermsDraft.show_brand_column,
      }
      : nextTermsDraft;
    quoteTermsDraftRef.current = displayedTermsDraft;
    savedQuoteTermsDraftRef.current = nextTermsDraft;
    setQuoteTermsDraft(displayedTermsDraft);
    setSavedQuoteTermsDraft(nextTermsDraft);
    const nextDiscountDraft = discountDraftFromQuote(quoteData);
    const displayedDiscountDraft = preserveDiscountDraft
      ? discountDraftRef.current
      : nextDiscountDraft;
    discountDraftRef.current = displayedDiscountDraft;
    savedDiscountDraftRef.current = nextDiscountDraft;
    setDiscountDraft(displayedDiscountDraft);
    setSavedDiscountDraft(nextDiscountDraft);
    const savedDrafts = Object.fromEntries((quoteData.lines || []).map((line) => [line.id, draftFromLine(line)]));
    const displayedDrafts = Object.fromEntries((quoteData.lines || []).map((line) => {
      const currentDraft = lineDraftsRef.current[line.id];
      const previousSavedDraft = savedLineDraftsRef.current[line.id];
      const preserveCurrentDraft = isSameQuote
        && currentDraft
        && previousSavedDraft
        && !draftsMatch(currentDraft, previousSavedDraft);
      return [
        line.id,
        preserveCurrentDraft
          ? mergeUnsavedLineDraft(savedDrafts[line.id], currentDraft, previousSavedDraft)
          : savedDrafts[line.id],
      ];
    }));
    lineDraftsRef.current = displayedDrafts;
    savedLineDraftsRef.current = savedDrafts;
    setLineDrafts(displayedDrafts);
    setSavedLineDrafts(savedDrafts);
    setLinePriceHints({});
    setPriceContexts({});
    setPriceContextError(null);
    setPriceHistoryDialog(null);
    priceContextGenerationRef.current += 1;
    linePriceVersionRef.current = {};
    lineSelectedProductRef.current = Object.fromEntries(
      (quoteData.lines || []).map((line) => [line.id, String(line.product || '')])
    );
    lineFormPriceVersionRef.current += 1;
    lineFormSelectedProductRef.current = '';
    setSelectedLineIds((current) => current.filter((id) => (quoteData.lines || []).some((line) => line.id === id)));
  }, []);

  const syncLpos = useCallback((records) => {
    const nextRecords = records || [];
    setLpos(nextRecords);
    setLpoDraft(lpoDraftFromRecord(nextRecords[0] || null));
  }, []);

  const load = useCallback(async ({
    refreshQuote = true,
    refreshReferences = true,
    referenceKeys = null,
  } = {}) => {
    const loadGeneration = refreshQuote
      ? ++loadGenerationRef.current
      : loadGenerationRef.current;
    const referenceLoadGeneration = refreshReferences
      ? ++referenceLoadGenerationRef.current
      : referenceLoadGenerationRef.current;
    const quoteLoadIsCurrent = () => loadGenerationRef.current === loadGeneration;
    const referenceLoadIsCurrent = () => (
      quoteLoadIsCurrent()
      && referenceLoadGenerationRef.current === referenceLoadGeneration
    );
    if (refreshQuote) {
      contactLoadGenerationRef.current += 1;
      setLoading(true);
      setLoadingContacts(false);
      setReferenceRetrying(false);
      setQuote((current) => {
        if (current && String(current.id) !== String(quoteId)) {
          quoteRef.current = null;
          return null;
        }
        return current;
      });
      setErrorInfo(null);
      if (refreshReferences) setReferenceLoadFailures([]);
      setPriceContextError(null);
    } else {
      setReferenceRetrying(true);
    }
    try {
      let quoteData = quoteRef.current;
      if (
        refreshQuote
        || !quoteData
        || String(quoteData.id) !== String(quoteId)
      ) {
        const quoteRes = await retryTransientQuotationGet(
          () => quotationAPI.quotes.retrieve(quoteId)
        );
        if (!quoteLoadIsCurrent()) return;
        quoteData = quoteRes.data;
        setLoadedQuote(quoteData);
      }

      const productIds = Array.from(new Set(
        (quoteData.lines || []).map((line) => line.product).filter(Boolean).map(String)
      ));
      const progressiveLoad = quotationEditorProgressiveLoadEnabled(quoteData);
      const setReferenceDatasetState = (key, status, details = null) => {
        if (!progressiveLoad || !quoteLoadIsCurrent()) return;
        setSupportingDatasetStates((current) => ({
          ...current,
          [key]: { status, details },
        }));
      };
      const setPriceHistoryDatasetState = (status, details = null) => {
        if (
          !progressiveLoad
          || !quoteLoadIsCurrent()
          || priceContextGenerationRef.current !== priceHistoryRequestGeneration
        ) return;
        setSupportingDatasetStates((current) => ({
          ...current,
          priceHistory: { status, details },
        }));
      };
      const priceHistoryRequestGeneration = priceContextGenerationRef.current;
      const startPriceHistoryPreviewLoad = () => {
        if (!refreshQuote) return;
        if (!productIds.length) {
          setPriceHistoryDatasetState('ready');
          return;
        }
        setPriceHistoryDatasetState('loading');
        const requestedLinePriceStates = Object.fromEntries(
          (quoteData.lines || []).map((line) => [line.id, {
            product: String(line.product || ''),
            version: linePriceVersionRef.current[line.id] || 0,
          }])
        );
        void (async () => {
          const contextRequests = [];
          for (let index = 0; index < productIds.length; index += 100) {
            contextRequests.push(quotationAPI.quotes.productPrices(quoteId, {
              products: productIds.slice(index, index + 100).join(','),
              history_limit: 10,
            }));
          }
          const results = await Promise.allSettled(contextRequests);
          if (
            priceContextGenerationRef.current !== priceHistoryRequestGeneration
            || !quoteLoadIsCurrent()
          ) return;

          const successfulResponses = results
            .filter((result) => result.status === 'fulfilled')
            .map((result) => result.value);
          const nextPriceContexts = Object.assign(
            {},
            ...successfulResponses.map((response) => response.data?.results || {})
          );
          if (progressiveLoad) {
            // Per-Product lookups triggered by an employee are newer than this
            // initial batch. Preserve them, and add a line hint only while the
            // line still has the exact Product/version captured for the batch.
            setPriceContexts((current) => ({ ...nextPriceContexts, ...current }));
            setLinePriceHints((current) => {
              const next = { ...current };
              (quoteData.lines || []).forEach((line) => {
                const requestedState = requestedLinePriceStates[line.id];
                const context = nextPriceContexts[String(line.product)];
                if (
                  !line.product
                  || !context
                  || !requestedState
                  || lineSelectedProductRef.current[line.id] !== requestedState.product
                  || (linePriceVersionRef.current[line.id] || 0) !== requestedState.version
                ) return;
                next[line.id] = {
                  ...context,
                  mode: context.latest_quoted ? 'history_found' : 'no_history',
                };
              });
              return next;
            });
          } else {
            setPriceContexts(nextPriceContexts);
            setLinePriceHints(Object.fromEntries(
              (quoteData.lines || [])
                .filter((line) => line.product && nextPriceContexts[String(line.product)])
                .map((line) => {
                  const context = nextPriceContexts[String(line.product)];
                  return [line.id, { ...context, mode: context.latest_quoted ? 'history_found' : 'no_history' }];
                })
            ));
          }

          if (quoteData.workflow_features?.company_price_autofill === true && editableStatuses.has(quoteData.status)) {
            setLineDrafts((current) => {
              const next = { ...current };
              (quoteData.lines || []).forEach((line) => {
                const draft = current[line.id];
                const state = requestedLinePriceStates[line.id];
                const recommendation = nextPriceContexts[String(line.product)]?.line_recommendations?.[line.id] || nextPriceContexts[String(line.product)]?.recommendations_by_unit?.[draft?.unit];
                if (draft?.price_provenance?.kind === 'history' && state && recommendation
                    && (linePriceVersionRef.current[line.id] || 0) === state.version
                    && (!recommendation.eligible || recommendation.history_id !== draft.price_provenance.history_id)) {
                  next[line.id] = { ...draft, price_review_required: true, price_reviewed: false };
                }
                if (draft && state && draft.match_status === 'confirmed' && isBlankPrice(draft.unit_price)
                    && !draft.price_review_required && recommendation?.eligible
                    && (linePriceVersionRef.current[line.id] || 0) === state.version
                    && String(draft.product) === state.product) {
                  next[line.id] = { ...draft, ...historyPricePatch(recommendation) };
                }
              });
              return next;
            });
          }
          const failedResult = results.find((result) => result.status === 'rejected');
          if (failedResult) {
            const details = await describeQuotationError(
              failedResult.reason,
              'Load price history previews',
              `GET /quotations/quotes/${quoteId}/product_prices/`
            );
            if (
              priceContextGenerationRef.current !== priceHistoryRequestGeneration
              || !quoteLoadIsCurrent()
            ) return;
            setPriceContextError(details);
            setPriceHistoryDatasetState('error', details);
            console.error(formatQuotationError(details), failedResult.reason);
          } else {
            setPriceHistoryDatasetState('ready');
          }
        })();
      };

      if (refreshQuote && !progressiveLoad) setSupportingDatasetStates({});
      if (progressiveLoad) startPriceHistoryPreviewLoad();
      if (refreshReferences) {
        const referenceCompany = refreshQuote
          ? quoteData.company
          : (quotePartyDraftRef.current.company || quoteData.company);
        const requestedReferenceKeys = Array.isArray(referenceKeys)
          ? new Set(referenceKeys)
          : null;
        if (refreshQuote) {
          setCompanyItems([]);
          setContacts([]);
          syncLpos([]);
        }
        const referenceRequests = [
          {
            key: 'items',
            action: 'Load quotation Product catalogue',
            endpoint: 'GET /quotations/items/?active=true',
            request: () => quotationAPI.items.list({ active: 'true' }),
            apply: (response) => setItems(response.data || []),
          },
          {
            key: 'companyItems',
            action: 'Load customer Product history',
            endpoint: `GET /quotations/items/?active=true&company_used=${referenceCompany}`,
            request: () => quotationAPI.items.list({ active: 'true', company_used: referenceCompany }),
            apply: (response) => setCompanyItems(response.data || []),
          },
          {
            key: 'companies',
            action: 'Load company directory',
            endpoint: 'GET /quotations/companies/?active=true',
            request: () => quotationAPI.companies.list({ active: 'true' }),
            apply: (response) => setCompanies(response.data || []),
          },
          {
            key: 'contacts',
            action: 'Load company contacts',
            endpoint: `GET /quotations/contacts/?company=${referenceCompany}&active=true`,
            request: () => (
              referenceCompany
                ? quotationAPI.contacts.list({ company: referenceCompany, active: 'true' })
                : Promise.resolve({ data: [] })
            ),
            apply: (response) => {
              if (
                String(quotePartyDraftRef.current.company || quoteData.company)
                === String(referenceCompany)
              ) {
                setContacts(response.data || []);
              }
            },
          },
          {
            key: 'lpos',
            action: 'Load quotation LPO records',
            endpoint: `GET /quotations/quotes/${quoteId}/lpos/`,
            request: () => quotationAPI.quotes.lpos(quoteId),
            apply: (response) => syncLpos(response.data || []),
          },
        ];
        const selectedReferenceRequests = requestedReferenceKeys
          ? referenceRequests.filter((request) => requestedReferenceKeys.has(request.key))
          : referenceRequests;
        if (progressiveLoad) {
          const datasetRequestVersions = {};
          selectedReferenceRequests.forEach((request) => {
            const nextVersion = (supportingDatasetGenerationRef.current[request.key] || 0) + 1;
            supportingDatasetGenerationRef.current[request.key] = nextVersion;
            datasetRequestVersions[request.key] = nextVersion;
            setReferenceDatasetState(request.key, 'loading');
          });
          const datasetRequestIsCurrent = (key) => (
            quoteLoadIsCurrent()
            && supportingDatasetGenerationRef.current[key] === datasetRequestVersions[key]
          );
          if (refreshQuote) setLoading(false);
          await Promise.all(selectedReferenceRequests.map(async (referenceRequest) => {
            try {
              const response = await retryTransientQuotationGet(referenceRequest.request);
              if (!datasetRequestIsCurrent(referenceRequest.key)) return;
              referenceRequest.apply(response);
              setReferenceDatasetState(referenceRequest.key, 'ready');
              setReferenceLoadFailures((current) => (
                current.filter((failure) => failure.key !== referenceRequest.key)
              ));
            } catch (error) {
              const details = await describeQuotationError(
                error,
                referenceRequest.action,
                referenceRequest.endpoint
              );
              if (!datasetRequestIsCurrent(referenceRequest.key)) return;
              setReferenceDatasetState(referenceRequest.key, 'error', details);
              setReferenceLoadFailures((current) => [
                ...current.filter((failure) => failure.key !== referenceRequest.key),
                { key: referenceRequest.key, details },
              ]);
            }
          }));
        } else {
          const referenceResults = await Promise.all(selectedReferenceRequests.map(async (referenceRequest) => {
            try {
              const response = await retryTransientQuotationGet(referenceRequest.request);
              return { ...referenceRequest, response };
            } catch (error) {
              return { ...referenceRequest, error };
            }
          }));
          if (!referenceLoadIsCurrent()) return;

          referenceResults
            .filter((result) => result.response)
            .forEach((result) => result.apply(result.response));
          const failedReferences = await Promise.all(
            referenceResults
              .filter((result) => result.error)
              .map(async (result) => ({
                key: result.key,
                details: await describeQuotationError(result.error, result.action, result.endpoint),
              }))
          );
          if (!referenceLoadIsCurrent()) return;
          setReferenceLoadFailures(failedReferences);
        }
      }

      // Price history is useful context, but it is not required to edit a quote.
      // Load it after the core editor data so a transient batch failure cannot
      // turn an otherwise healthy quotation into a blank/error screen.
      if (!progressiveLoad) startPriceHistoryPreviewLoad();
      if (progressiveLoad && refreshQuote && !refreshReferences) setLoading(false);
    } catch (error) {
      if (refreshQuote ? !quoteLoadIsCurrent() : !referenceLoadIsCurrent()) return;
      const details = await describeQuotationError(
        error,
        refreshQuote ? 'Load quotation' : 'Retry quotation supporting data',
        refreshQuote
          ? `GET /quotations/quotes/${quoteId}/`
          : 'GET quotation supporting endpoints'
      );
      if (refreshQuote ? !quoteLoadIsCurrent() : !referenceLoadIsCurrent()) return;
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      const requestIsCurrent = refreshQuote
        ? quoteLoadIsCurrent()
        : referenceLoadIsCurrent();
      if (requestIsCurrent) {
        if (refreshQuote) setLoading(false);
        else setReferenceRetrying(false);
      }
    }
  }, [quoteId, setLoadedQuote, syncLpos]);

  useEffect(() => {
    const mountedPriceInputs = priceInputRefs.current;
    lineSaveGenerationRef.current += 1;
    setSaving(false);
    setEmailPreviewOpen(false);
    setEmailPreviewLoading(false);
    setEmailPreview(null);
    setEmailPreviewError('');
    setEmailSendError(null);
    setEmailSending(false);
    setEmailQuoteFinalized(false);
    setEmailThreadCandidates([]);
    setEmailThreadCandidatesLoading(false);
    setEmailThreadCandidatesError('');
    setEmailThreadSearchCompleted(false);
    setEmailGmailReconnectError('');
    setEmailReconciling(false);
    setEmailReconcileFeedback(null);
    emailPreviewGenerationRef.current += 1;
    emailThreadSearchGenerationRef.current += 1;
    emailReconcileGenerationRef.current += 1;
    load();
    return () => {
      loadGenerationRef.current += 1;
      referenceLoadGenerationRef.current += 1;
      contactLoadGenerationRef.current += 1;
      emailPreviewGenerationRef.current += 1;
      emailThreadSearchGenerationRef.current += 1;
      emailReconcileGenerationRef.current += 1;
      reviewEmailGenerationRef.current += 1;
      lineSaveGenerationRef.current += 1;
      reviewEmailInFlightRef.current = false;
      supportingDatasetGenerationRef.current = {};
      mountedPriceInputs.clear();
    };
  }, [load]);

  const isEditable = quote && editableStatuses.has(quote.status);
  const chainedActionsEnabled = gmailChainedActionsEnabled(quote);
  const progressiveLoadEnabled = quotationEditorProgressiveLoadEnabled(quote);
  const emailPreviewRefreshRequired = emailSendError?.refreshPreview === true || [
    'stale_email_preview',
    'email_preview_required',
  ].includes(String(emailSendError?.code || ''));
  const activeLines = quote?.lines || [];
  const changedLineIds = quote ? (quote.lines || [])
    .filter((line) => !draftsMatch(lineDrafts[line.id], savedLineDrafts[line.id]))
    .map((line) => line.id) : [];
  const hasUnsavedLines = changedLineIds.length > 0;
  const hasUnsavedQuoteParty = !partyDraftsMatch(quotePartyDraft, savedQuotePartyDraft);
  const hasUnsavedQuoteTerms = !termsDraftsMatch(quoteTermsDraft, savedQuoteTermsDraft);
  const discountCurrencySupported = String(quote?.currency || '').toUpperCase() === 'AED';
  const hasUnsavedDiscount = discountCurrencySupported
    && !discountDraftsMatch(discountDraft, savedDiscountDraft);
  const hasUnsavedLineOrDiscount = hasUnsavedLines || hasUnsavedDiscount;
  const hasUnsavedCustomerDocument = hasUnsavedLines
    || hasUnsavedQuoteParty
    || hasUnsavedQuoteTerms
    || hasUnsavedDiscount;
  const datasetStatus = (key) => supportingDatasetStates[key]?.status || 'idle';
  const productCatalogueLoading = progressiveLoadEnabled && datasetStatus('items') === 'loading';
  const companyDirectoryLoading = progressiveLoadEnabled && datasetStatus('companies') === 'loading';
  const companyContactsLoading = progressiveLoadEnabled && datasetStatus('contacts') === 'loading';
  const lpoRecordsLoading = progressiveLoadEnabled && datasetStatus('lpos') === 'loading';
  const priceHistoryLoading = progressiveLoadEnabled && datasetStatus('priceHistory') === 'loading';
  const currentCompanyFallback = quote?.company ? {
    id: quote.company,
    name: quote.company_name || `Company ${quote.company}`,
  } : null;
  const companiesForQuotePicker = progressiveLoadEnabled
    && currentCompanyFallback
    && !companies.some((company) => String(company.id) === String(currentCompanyFallback.id))
    ? [currentCompanyFallback, ...companies]
    : companies;
  const currentContactFallback = quote?.contact
    && String(quotePartyDraft.company || '') === String(quote.company || '')
    && String(quotePartyDraft.contact || '') === String(quote.contact || '')
    ? {
      id: quote.contact,
      company: quote.company,
      name: quote.contact_name || `Contact ${quote.contact}`,
      role: quote.contact_role || '',
      department: quote.contact_department || '',
    }
    : null;
  const contactsForQuoteCompany = progressiveLoadEnabled
    && currentContactFallback
    && !contacts.some((contact) => String(contact.id) === String(currentContactFallback.id))
    ? [currentContactFallback, ...contacts]
    : contacts;
  const referenceFailureKeys = new Set(referenceLoadFailures.map((failure) => failure.key));
  const productCatalogueUnavailable = referenceFailureKeys.has('items');
  const companyDirectoryUnavailable = referenceFailureKeys.has('companies');
  const companyContactsUnavailable = referenceFailureKeys.has('contacts');
  const partyDataUnavailable = companyDirectoryUnavailable
    || companyContactsUnavailable
    || (progressiveLoadEnabled && (companyDirectoryLoading || companyContactsLoading || loadingContacts));
  const lpoRecordsUnavailable = referenceFailureKeys.has('lpos');
  const productCatalogueBlocked = productCatalogueUnavailable || productCatalogueLoading;
  const companyDirectoryBlocked = companyDirectoryUnavailable || companyDirectoryLoading;
  const companyContactsBlocked = companyContactsUnavailable
    || companyContactsLoading
    || (progressiveLoadEnabled && loadingContacts);
  const companyControlBlocked = progressiveLoadEnabled
    ? companyDirectoryBlocked
    : (referenceRetrying || companyDirectoryUnavailable);
  const contactControlBlocked = progressiveLoadEnabled
    ? companyContactsBlocked
    : (referenceRetrying || companyContactsUnavailable);
  const partyControlsBlocked = progressiveLoadEnabled
    ? partyDataUnavailable
    : (referenceRetrying || partyDataUnavailable);

  const loadContactsForCompany = async (companyId) => {
    const requestGeneration = ++contactLoadGenerationRef.current;
    const datasetGeneration = progressiveLoadEnabled
      ? (supportingDatasetGenerationRef.current.contacts || 0) + 1
      : null;
    if (progressiveLoadEnabled) {
      supportingDatasetGenerationRef.current.contacts = datasetGeneration;
    }
    const datasetRequestIsCurrent = () => (
      !progressiveLoadEnabled
      || supportingDatasetGenerationRef.current.contacts === datasetGeneration
    );
    const normalizedCompanyId = String(companyId || '');
    if (!companyId) {
      setContacts([]);
      setLoadingContacts(false);
      if (progressiveLoadEnabled) {
        setSupportingDatasetStates((current) => ({
          ...current,
          contacts: { status: 'ready', details: null },
        }));
        setReferenceLoadFailures((current) => current.filter((failure) => failure.key !== 'contacts'));
      }
      return;
    }
    setLoadingContacts(true);
    if (progressiveLoadEnabled) {
      setSupportingDatasetStates((current) => ({
        ...current,
        contacts: { status: 'loading', details: null },
      }));
      setReferenceLoadFailures((current) => current.filter((failure) => failure.key !== 'contacts'));
    }
    setErrorInfo(null);
    try {
      const response = await quotationAPI.contacts.list({ company: companyId, active: 'true' });
      if (
        contactLoadGenerationRef.current !== requestGeneration
        || !datasetRequestIsCurrent()
        || String(quotePartyDraftRef.current.company || '') !== normalizedCompanyId
      ) return;
      setContacts(response.data);
      if (progressiveLoadEnabled) {
        setSupportingDatasetStates((current) => ({
          ...current,
          contacts: { status: 'ready', details: null },
        }));
      }
    } catch (error) {
      if (contactLoadGenerationRef.current !== requestGeneration || !datasetRequestIsCurrent()) return;
      const details = await describeQuotationError(error, 'Load company contacts', `GET /quotations/contacts/?company=${companyId}`);
      if (contactLoadGenerationRef.current !== requestGeneration || !datasetRequestIsCurrent()) return;
      if (progressiveLoadEnabled) {
        setSupportingDatasetStates((current) => ({
          ...current,
          contacts: { status: 'error', details },
        }));
        setReferenceLoadFailures((current) => [
          ...current.filter((failure) => failure.key !== 'contacts'),
          { key: 'contacts', details },
        ]);
      }
      if (!progressiveLoadEnabled) setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      if (contactLoadGenerationRef.current === requestGeneration) {
        setLoadingContacts(false);
      }
    }
  };

  const lineLabel = (line, draft = {}) => draft.item_name_snapshot || line.inquiry_line_raw_name || line.item_name_snapshot || `Line ${line.sort_order + 1}`;

  const productCatalogue = useMemo(() => buildProductCatalogue(items, companyItems), [items, companyItems]);

  const lineMoneyForDraft = (draft = {}) => {
    if (draft.match_status === 'ignored') {
      return { subtotalCents: BIGINT_ZERO, vatCents: BIGINT_ZERO, totalCents: BIGINT_ZERO };
    }
    const quantity = draft.quantity || '0';
    const unitPrice = draft.unit_price || '0';
    const vatRate = draft.vat_rate || '0';
    const subtotalCents = quantizedDecimalProduct([quantity, unitPrice], 2);
    // The backend quantizes VAT from the raw quantity x unit-price product,
    // independently of the already-quantized line subtotal.
    const vatCents = quantizedDecimalProduct([quantity, unitPrice, vatRate], 2, 2);
    return {
      subtotalCents,
      vatCents,
      totalCents: subtotalCents + vatCents,
    };
  };
  const liveLineDraftFor = (line) => ({ ...line, ...(lineDrafts[line.id] || {}) });
  const liveQuoteSubtotalCents = activeLines.reduce(
    (sum, line) => sum + lineMoneyForDraft(liveLineDraftFor(line)).subtotalCents,
    BIGINT_ZERO,
  );
  const liveQuoteVatCents = activeLines.reduce(
    (sum, line) => sum + lineMoneyForDraft(liveLineDraftFor(line)).vatCents,
    BIGINT_ZERO,
  );
  const liveTotalBeforeDiscountCents = liveQuoteSubtotalCents + liveQuoteVatCents;
  const discountText = String(discountDraft ?? '').trim();
  const parsedDiscount = discountText === '' ? 0 : Number(discountText);
  const discountFormatInvalid = discountText !== '' && !/^\d+(?:\.\d{0,2})?$/.test(discountText);
  const parsedDiscountCents = discountFormatInvalid
    ? BIGINT_ZERO
    : decimalToScaledInteger(discountText || '0', 2);
  const discountError = !discountCurrencySupported
    ? ''
    : !Number.isFinite(parsedDiscount)
      ? 'Enter a valid final discount amount.'
      : parsedDiscount < 0
        ? 'Final discount cannot be negative.'
        : discountFormatInvalid
          ? 'Final discount can have no more than two decimal places.'
          : parsedDiscountCents > liveTotalBeforeDiscountCents
            ? `Final discount cannot exceed ${quote?.currency || 'AED'} ${formatMoneyCents(liveTotalBeforeDiscountCents)}.`
            : '';
  const liveDiscountCents = discountError ? BIGINT_ZERO : parsedDiscountCents;
  const liveFinalTotalCents = liveDiscountCents >= liveTotalBeforeDiscountCents
    ? BIGINT_ZERO
    : liveTotalBeforeDiscountCents - liveDiscountCents;

  const derivedLineStatus = (line) => {
    const draft = lineDrafts[line.id] || {};
    if (draft.match_status === 'ignored') return { id: 'skipped', label: 'Skipped' };
    if (!draft.product) return { id: 'unmatched', label: 'Unmatched' };
    if ((draft.price_review_required && !draft.price_reviewed) || !draft.quantity || Number(draft.quantity) <= 0 || !draft.unit_price || Number(draft.unit_price) <= 0) {
      return { id: 'needs_review', label: 'Needs review' };
    }
    return { id: 'ready', label: 'Ready' };
  };

  const filteredLines = activeLines.filter((line) => {
    const status = derivedLineStatus(line).id;
    if (lineFilter === 'all') return true;
    if (lineFilter === 'active') return status !== 'skipped';
    return status === lineFilter;
  });

  const visiblePriceLineIds = filteredLines.map((line) => String(line.id));
  const visiblePriceLineOrder = visiblePriceLineIds.join('|');
  const priceFocusQuoteId = quote?.id || '';

  const assignPriceInputRef = (lineId, node) => {
    const key = String(lineId);
    if (node) priceInputRefs.current.set(key, node);
    else priceInputRefs.current.delete(key);
  };

  const moveToNextBlankPrice = (event, lineId) => {
    if (!progressiveLoadEnabled || event.shiftKey || !['Enter', 'Tab'].includes(event.key)) return;
    const currentIndex = visiblePriceLineIds.indexOf(String(lineId));
    if (currentIndex < 0) return;
    const nextInput = visiblePriceLineIds
      .slice(currentIndex + 1)
      .map((id) => priceInputRefs.current.get(id))
      .find((input) => input && !input.disabled && String(input.value || '').trim() === '');
    if (!nextInput) return;
    event.preventDefault();
    nextInput.focus();
  };

  useEffect(() => {
    if (!progressiveLoadEnabled || !isEditable || !priceFocusQuoteId) return;
    const quoteKey = String(priceFocusQuoteId);
    if (initialPriceFocusQuoteRef.current === quoteKey) return;
    const firstBlankInput = (visiblePriceLineOrder ? visiblePriceLineOrder.split('|') : [])
      .map((id) => priceInputRefs.current.get(id))
      .find((input) => input && !input.disabled && String(input.value || '').trim() === '');
    if (firstBlankInput) {
      firstBlankInput.focus();
      initialPriceFocusQuoteRef.current = quoteKey;
    }
  }, [isEditable, priceFocusQuoteId, progressiveLoadEnabled, visiblePriceLineOrder]);

  const selectedLines = activeLines.filter((line) => selectedLineIds.includes(line.id));
  const selectedUnmatchedLines = selectedLines.filter((line) => derivedLineStatus(line).id === 'unmatched');

  const companyPricingEnabled = quote?.workflow_features?.company_price_autofill === true;
  const companyPricingVisible = companyPricingEnabled || (quote?.lines || []).some((line) => line.price_review_required || line.price_provenance?.kind);

  const finalizeIssues = (() => {
    if (!quote || !['draft', 'pending_review', 'approved'].includes(quote.status)) return [];
    const issues = [];
    if (!quote.lines?.length) issues.push('Add at least one quotation line.');
    if (hasUnsavedQuoteParty) issues.push('Save customer/contact before finalizing.');
    if (hasUnsavedQuoteTerms) issues.push('Save quotation terms and layout before finalizing.');
    if (hasUnsavedLines) issues.push(UNSAVED_LINES_FINALIZE_ISSUE);
    if (discountError) issues.push(discountError);
    else if (hasUnsavedDiscount) issues.push(UNSAVED_DISCOUNT_FINALIZE_ISSUE);
    (quote.lines || []).forEach((line, index) => {
      const draft = lineDrafts[line.id] || {};
      const name = draft.item_name_snapshot || `Line ${index + 1}`;
      if (draft.match_status !== 'ignored') {
        if (draft.price_review_required && !draft.price_reviewed) issues.push(`${name}: review the product and price.`);
        if (!draft.product) issues.push(`${name}: select or create a Product.`);
        if (!draft.quantity || Number(draft.quantity) <= 0) issues.push(`${name}: enter a valid quantity.`);
        if (!draft.unit_price || Number(draft.unit_price) <= 0) issues.push(`${name}: enter a valid unit price.`);
      }
    });
    return issues;
  })();
  const reviewEmailIssues = chainedActionsEnabled
    ? finalizeIssues.filter((issue) => ![
        UNSAVED_LINES_FINALIZE_ISSUE,
        UNSAVED_DISCOUNT_FINALIZE_ISSUE,
      ].includes(issue))
    : finalizeIssues;
  const primaryEmailActionIssues = chainedActionsEnabled
    ? reviewEmailIssues
    : finalizeIssues;
  const directFinalizeIssues = !isEditable || quote?.quotation_review_fingerprint
    ? finalizeIssues
    : [...finalizeIssues, 'Reload the quotation before finalizing.'];

  const updateLineDraft = (lineId, patch) => {
    setLineFeedback(null);
    if (companyPricingEnabled && Object.prototype.hasOwnProperty.call(patch, 'item_name_snapshot')
        && !Object.prototype.hasOwnProperty.call(patch, 'product')) {
      patch = { ...patch, price_source_history: null, price_original_history: null, price_review_required: true, price_reviewed: false };
      linePriceVersionRef.current[lineId] = (linePriceVersionRef.current[lineId] || 0) + 1;
    }
    const affectsPriceRequest = Object.prototype.hasOwnProperty.call(patch, 'unit_price') || Object.prototype.hasOwnProperty.call(patch, 'product') || Object.prototype.hasOwnProperty.call(patch, 'unit');
    if (affectsPriceRequest) {
      linePriceVersionRef.current[lineId] = (linePriceVersionRef.current[lineId] || 0) + 1;
      setLinePriceHints((current) => {
        const next = { ...current };
        delete next[lineId];
        return next;
      });
    }
    if (Object.prototype.hasOwnProperty.call(patch, 'product')) {
      lineSelectedProductRef.current[lineId] = String(patch.product || '');
    }
    setLineDrafts((current) => ({
      ...current,
      [lineId]: { ...current[lineId], ...patch },
    }));
    return linePriceVersionRef.current[lineId] || 0;
  };

  const updateQuoteTermDraft = (patch) => {
    setLineFeedback(null);
    setQuoteTermsDraft((current) => {
      const next = { ...current, ...patch };
      quoteTermsDraftRef.current = next;
      return next;
    });
  };

  const updateDiscountDraft = (value) => {
    setLineFeedback(null);
    discountDraftRef.current = value;
    setDiscountDraft(value);
  };

  const updateQuotePartyDraft = (patch) => {
    setLineFeedback(null);
    if (companyPricingEnabled && patch.company && String(patch.company) !== String(quotePartyDraftRef.current.company)) {
      priceContextGenerationRef.current += 1;
      setPriceContexts({}); setLinePriceHints({});
      lineFormPriceVersionRef.current += 1;
      setLineForm((current) => ({ ...current,
        unit_price: current.price_provenance?.kind === 'history' ? '' : current.unit_price,
        price_provenance: {}, price_source_history: null, price_original_history: null, price_reviewed: false,
        price_context_changed: !isBlankPrice(current.unit_price) && current.price_provenance?.kind !== 'history',
        price_review_required: !isBlankPrice(current.unit_price) && current.price_provenance?.kind !== 'history',
      }));
      setLineDrafts((current) => Object.fromEntries(Object.entries(current).map(([id, draft]) => [id, {
        ...draft, unit_price: draft.price_provenance?.kind === 'history' ? '' : draft.unit_price,
        price_provenance: {}, price_source_history: null, price_original_history: null, price_reviewed: false,
        price_review_required: !isBlankPrice(draft.unit_price) && draft.price_provenance?.kind !== 'history',
      }])));
    }
    setQuotePartyDraft((current) => {
      const next = { ...current, ...patch };
      quotePartyDraftRef.current = next;
      return next;
    });
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

  const productPatch = (draft, productId) => {
    const item = items.find((candidate) => String(candidate.id) === String(productId));
    const hasSnapshotName = String(draft.item_name_snapshot || '').trim().length > 0;
    const changed = String(draft.product || '') !== String(productId || '');
    return {
      ...(changed && companyPricingVisible ? {
        unit_price: draft.price_provenance?.kind === 'history' ? '' : draft.unit_price,
        price_provenance: draft.price_provenance?.kind === 'history' ? {} : draft.price_provenance,
        price_source_history: null, price_original_history: null,
        price_review_required: !isBlankPrice(draft.unit_price) && draft.price_provenance?.kind !== 'history',
        price_context_changed: !isBlankPrice(draft.unit_price) && draft.price_provenance?.kind !== 'history',
        price_reviewed: false,
      } : {}),
      product: productId,
      item_name_snapshot: hasSnapshotName ? draft.item_name_snapshot : (item?.name || ''),
      brand_name_snapshot: productId ? (item?.brand_name || '') : '',
      unit: draft.unit || sanitizeUnitText(item?.unit || ''),
      match_status: productId ? 'confirmed' : 'unresolved',
      product_image: '',
      product_image_url: item?.primary_image_url || '',
      has_product_image: !!item?.primary_image_url,
      include_product_image: false,
    };
  };

  const priceShouldAutofill = (draft) => isBlankPrice(draft.unit_price) && !draft.price_review_required;

  const setPriceHintForLine = (lineId, suggestion, mode) => {
    setLinePriceHints((current) => ({
      ...current,
      [lineId]: {
        ...suggestion,
        mode,
      },
    }));
  };

  const maybeFetchProductPrice = async (productId, unit = '', wording = '', force = false) => {
    if (!quote?.id || !productId) return null;
    const expectedQuoteId = String(quote.id);
    const expectedContextGeneration = priceContextGenerationRef.current;
    const requestIsCurrent = () => (
      String(quoteRef.current?.id || '') === expectedQuoteId
      && priceContextGenerationRef.current === expectedContextGeneration
    );
    const cacheKey = companyPricingEnabled ? `${productId}:${unit}:${wording}` : String(productId);
    const cached = priceContexts[cacheKey];
    if (cached && !force) return cached;
    try {
      const response = await quotationAPI.quotes.productPrice(quote.id, { product: productId, ...(companyPricingEnabled ? { unit, wording } : {}) });
      if (!requestIsCurrent()) return null;
      setPriceContexts((current) => ({ ...current, [cacheKey]: response.data }));
      return response.data;
    } catch (error) {
      if (!requestIsCurrent()) return null;
      const details = await describeQuotationError(error, 'Load company Product price', `GET /quotations/quotes/${quote.id}/product_price/?product=${productId}`);
      if (!requestIsCurrent()) return null;
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
      return null;
    }
  };

  const priceHintText = (hint) => {
    if (!hint) return '';
    const quoted = hint.latest_quoted;
    const accepted = hint.latest_accepted;
    const parts = [];
    if (quoted?.quoted_unit_price) parts.push(`Last quoted ${quoted.currency || 'AED'} ${quoted.quoted_unit_price}`);
    if (accepted?.accepted_unit_price) {
      parts.push(`Last accepted ${accepted.currency || 'AED'} ${accepted.accepted_unit_price}${accepted.lpo_number ? ` (LPO ${accepted.lpo_number})` : ''}`);
    }
    if (!parts.length) return 'No previous customer price';
    const prefix = hint.mode === 'autofilled' ? 'Applied · ' : hint.mode === 'current_kept' ? 'Current price kept · ' : '';
    return `${prefix}${parts.join(' · ')}`;
  };

  const handleLineProductChange = async (line, productId) => {
    if (productId === '__create__') {
      createProductForLine(line.id);
      return;
    }
    const currentDraft = lineDrafts[line.id] || {};
    const patch = productPatch(currentDraft, productId);
    const requestVersion = updateLineDraft(line.id, patch);
    const requestGeneration = priceContextGenerationRef.current;
    if (!productId) return;

    const suggestion = await maybeFetchProductPrice(productId, patch.unit, currentDraft.item_name_snapshot);
    if (!suggestion) return;
    if (priceContextGenerationRef.current !== requestGeneration || lineSelectedProductRef.current[line.id] !== String(productId)) return;
    if (companyPricingEnabled) {
      setPriceHintForLine(line.id, suggestion, 'history_found');
      if (linePriceVersionRef.current[line.id] === requestVersion && suggestion.recommendation?.eligible && priceShouldAutofill({ ...currentDraft, ...patch })) {
        updateLineDraft(line.id, historyPricePatch(suggestion.recommendation));
      }
      return;
    }
    if (progressiveLoadEnabled) {
      setPriceHintForLine(
        line.id,
        suggestion,
        suggestion.source === 'company_price_history' ? 'history_found' : 'no_history'
      );
      return;
    }
    setPriceHistoryDialog({
      productId,
      productName: suggestion.product_name || items.find((item) => String(item.id) === String(productId))?.name || '',
      context: suggestion,
    });
    if (suggestion.source !== 'company_price_history') {
      setPriceHintForLine(line.id, suggestion, 'no_history');
      return;
    }
    if (!suggestion?.unit_price) return;
    const pricePatch = {};
    if (linePriceVersionRef.current[line.id] === requestVersion && priceShouldAutofill(currentDraft)) {
      pricePatch.unit_price = suggestion.unit_price;
    }
    if (Object.keys(pricePatch).length) {
      updateLineDraft(line.id, pricePatch);
      setPriceHintForLine(line.id, suggestion, pricePatch.unit_price ? 'autofilled' : 'history_found');
    } else {
      setPriceHintForLine(line.id, suggestion, 'current_kept');
    }
  };

  const handleLineFormProductChange = async (productId) => {
    const patch = productPatch(lineForm, productId);
    lineFormPriceVersionRef.current += 1;
    const requestVersion = lineFormPriceVersionRef.current;
    const requestGeneration = priceContextGenerationRef.current;
    lineFormSelectedProductRef.current = String(productId || '');
    setLineForm((current) => ({ ...current, ...patch }));
    if (!productId) return;
    if (progressiveLoadEnabled && !companyPricingEnabled) return;
    const suggestion = await maybeFetchProductPrice(productId, patch.unit, lineForm.item_name_snapshot);
    if (!suggestion || priceContextGenerationRef.current !== requestGeneration || lineFormSelectedProductRef.current !== String(productId)) return;
    if (companyPricingEnabled) {
      setLineForm((current) => ({ ...current, price_recommendation: suggestion.recommendation,
        ...(lineFormPriceVersionRef.current === requestVersion && priceShouldAutofill(current) && suggestion.recommendation?.eligible
          ? historyPricePatch(suggestion.recommendation) : {}) }));
      return;
    }
    setPriceHistoryDialog({
      productId,
      productName: suggestion.product_name || items.find((item) => String(item.id) === String(productId))?.name || '',
      context: suggestion,
    });
    if (!suggestion?.unit_price || suggestion.source !== 'company_price_history') return;
    setLineForm((current) => ({
      ...current,
      unit_price: lineFormPriceVersionRef.current === requestVersion && priceShouldAutofill(current)
        ? suggestion.unit_price
        : current.unit_price,
    }));
  };

  const refreshLinePrice = async (line, draftOverride) => {
    const draft = draftOverride || lineDraftsRef.current[line.id];
    if (!draft?.product || hasUnsavedQuoteParty) return;
    const version = linePriceVersionRef.current[line.id] || 0;
    const generation = priceContextGenerationRef.current;
    setPriceHintForLine(line.id, {}, 'loading');
    const context = await maybeFetchProductPrice(draft.product, draft.unit, draft.item_name_snapshot, true);
    if (generation !== priceContextGenerationRef.current || version !== (linePriceVersionRef.current[line.id] || 0)) return;
    setPriceHintForLine(line.id, context || {}, context ? 'history_found' : 'error');
    if (context?.recommendation?.eligible && priceShouldAutofill(draft)) updateLineDraft(line.id, historyPricePatch(context.recommendation));
  };

  const changeLineUnit = (line, unit) => {
    const draft = lineDraftsRef.current[line.id];
    updateLineDraft(line.id, { unit, ...(companyPricingEnabled ? {
      unit_price: draft.price_provenance?.kind === 'history' ? '' : draft.unit_price,
      price_source_history: null, price_original_history: null, price_provenance: draft.price_provenance?.kind === 'history' ? {} : draft.price_provenance,
      price_review_required: !isBlankPrice(draft.unit_price) && draft.price_provenance?.kind !== 'history', price_reviewed: false,
    } : {}) });
  };

  const wrongProduct = (line, draft) => updateLineDraft(line.id, {
    ...productPatch(draft, ''), price_feedback: 'wrong_product', price_reviewed: false,
    unit_price: draft.price_provenance?.kind === 'history' ? '' : draft.unit_price,
    price_source_history: null, price_original_history: null, price_review_required: true,
  });

  const payloadForLine = (draft) => ({
    ...draft,
    product: draft.product || null,
    product_image: draft.product_image || null,
    include_product_image: !!draft.include_product_image,
    unit_price: isBlankPrice(draft.unit_price) ? null : draft.unit_price,
    price_source_history: draft.price_source_history || null,
    match_status: draft.product && draft.match_status === 'unresolved' ? 'confirmed' : draft.match_status,
  });

  const mergeSavedQuote = (quoteData, savedIds = [], preserveCurrentDrafts = false) => {
    const savedSet = new Set(savedIds);
    const preserveEveryCurrentDraft = preserveCurrentDrafts === true;
    const preservedDraftIds = new Set(
      Array.isArray(preserveCurrentDrafts) ? preserveCurrentDrafts.map(String) : []
    );
    const currentDrafts = lineDraftsRef.current || {};
    const currentSavedDrafts = savedLineDraftsRef.current || {};
    const nextDrafts = {};
    const nextSavedDrafts = {};
    (quoteData.lines || []).forEach((line) => {
      const savedDraft = draftFromLine(line);
      if (preserveEveryCurrentDraft || preservedDraftIds.has(String(line.id))) {
        nextDrafts[line.id] = currentDrafts[line.id] || savedDraft;
      } else if (savedSet.has(line.id)) {
        nextDrafts[line.id] = savedDraft;
      } else {
        nextDrafts[line.id] = currentDrafts[line.id] || savedDraft;
      }
      nextSavedDrafts[line.id] = savedSet.has(line.id)
        ? savedDraft
        : (currentSavedDrafts[line.id] || savedDraft);
    });
    // A chained preview must use the exact authoritative quote returned by
    // the successful save, not the pre-save render captured by React.
    quoteRef.current = quoteData;
    lineDraftsRef.current = nextDrafts;
    savedLineDraftsRef.current = nextSavedDrafts;
    setQuote(quoteData);
    setLineDrafts(nextDrafts);
    setSavedLineDrafts(nextSavedDrafts);
    return quoteData;
  };

  const mergeSavedDiscount = (quoteData, preserveCurrentDraft = false) => {
    const nextSavedDiscount = discountDraftFromQuote(quoteData);
    const nextDisplayedDiscount = preserveCurrentDraft
      ? discountDraftRef.current
      : nextSavedDiscount;
    discountDraftRef.current = nextDisplayedDiscount;
    savedDiscountDraftRef.current = nextSavedDiscount;
    setDiscountDraft(nextDisplayedDiscount);
    setSavedDiscountDraft(nextSavedDiscount);
  };

  const discountAmountForPayload = () => {
    const value = String(discountDraftRef.current ?? '').trim();
    return value === '' ? '0.00' : value;
  };

  const saveLine = async (lineId) => {
    if (saving || actionInFlight || discountError) return;
    const currentQuote = quoteRef.current || quote;
    const expectedQuoteId = String(currentQuote?.id || '');
    const expectedLoadGeneration = loadGenerationRef.current;
    const requestGeneration = ++lineSaveGenerationRef.current;
    const requestIsCurrent = () => (
      lineSaveGenerationRef.current === requestGeneration
      && loadGenerationRef.current === expectedLoadGeneration
      && String(quoteRef.current?.id || '') === expectedQuoteId
    );
    const draftsAtSaveStart = snapshotLineDrafts(currentQuote, lineDraftsRef.current);
    const discountAtSaveStart = discountDraftRef.current;
    const payloadDraft = { ...(lineDraftsRef.current[lineId] || {}) };
    setSaving(true);
    setLineFeedback(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.quotes.bulkUpdateLines(quote.id, {
        lines: [{ id: lineId, ...payloadForLine(payloadDraft) }],
        discount_amount: discountAmountForPayload(),
        quotation_review_fingerprint: currentQuote?.quotation_review_fingerprint || '',
      });
      if (!requestIsCurrent()) return;
      const draftsChangedDuringSave = Object.entries(draftsAtSaveStart)
        .filter(([savedLineId, expected]) => !draftsMatch(lineDraftsRef.current[savedLineId], expected))
        .map(([savedLineId]) => savedLineId);
      const newerEditsRemain = draftsChangedDuringSave.length > 0;
      const newerDiscountEditRemains = !discountDraftsMatch(
        discountDraftRef.current,
        discountAtSaveStart,
      );
      mergeSavedQuote(
        response.data.quotation,
        [lineId],
        draftsChangedDuringSave,
      );
      mergeSavedDiscount(response.data.quotation, newerDiscountEditRemains);
      setLineFeedback(newerEditsRemain || newerDiscountEditRemains
        ? { type: 'warning', message: 'Saved the submitted changes; newer edits remain unsaved.' }
        : { type: 'success', message: 'Line saved.' });
    } catch (error) {
      if (!requestIsCurrent()) return;
      if (replaceStaleQuotationReview(error)) return;
      const details = await describeQuotationError(error, 'Save quote line', `PATCH /quotations/quote-lines/${lineId}/`);
      if (!requestIsCurrent()) return;
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      if (requestIsCurrent()) setSaving(false);
    }
  };

  const saveAllLines = async () => {
    if (saving || actionInFlight || !hasUnsavedLineOrDiscount || discountError) return;
    const currentQuote = quoteRef.current || quote;
    const expectedQuoteId = String(currentQuote?.id || '');
    const expectedLoadGeneration = loadGenerationRef.current;
    const requestGeneration = ++lineSaveGenerationRef.current;
    const requestIsCurrent = () => (
      lineSaveGenerationRef.current === requestGeneration
      && loadGenerationRef.current === expectedLoadGeneration
      && String(quoteRef.current?.id || '') === expectedQuoteId
    );
    const lineIdsToSave = [...changedLineIds];
    const draftsAtSaveStart = snapshotLineDrafts(currentQuote, lineDraftsRef.current);
    const discountAtSaveStart = discountDraftRef.current;
    const payloadDrafts = Object.fromEntries(
      lineIdsToSave.map((lineId) => [lineId, { ...(lineDraftsRef.current[lineId] || {}) }])
    );
    setSaving(true);
    setLineFeedback(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.quotes.bulkUpdateLines(quote.id, {
        lines: lineIdsToSave.map((lineId) => ({ id: lineId, ...payloadForLine(payloadDrafts[lineId]) })),
        discount_amount: discountAmountForPayload(),
        quotation_review_fingerprint: currentQuote?.quotation_review_fingerprint || '',
      });
      if (!requestIsCurrent()) return;
      const draftsChangedDuringSave = Object.entries(draftsAtSaveStart)
        .filter(([lineId, expected]) => !draftsMatch(lineDraftsRef.current[lineId], expected))
        .map(([lineId]) => lineId);
      const newerEditsRemain = draftsChangedDuringSave.length > 0;
      const newerDiscountEditRemains = !discountDraftsMatch(
        discountDraftRef.current,
        discountAtSaveStart,
      );
      mergeSavedQuote(
        response.data.quotation,
        lineIdsToSave,
        draftsChangedDuringSave,
      );
      mergeSavedDiscount(response.data.quotation, newerDiscountEditRemains);
      const savedMessage = lineIdsToSave.length && hasUnsavedDiscount
        ? `Saved ${lineIdsToSave.length} line${lineIdsToSave.length === 1 ? '' : 's'} and the final discount.`
        : lineIdsToSave.length
          ? `Saved ${lineIdsToSave.length} line${lineIdsToSave.length === 1 ? '' : 's'}.`
          : 'Final discount saved.';
      setLineFeedback(newerEditsRemain || newerDiscountEditRemains
        ? { type: 'warning', message: 'Saved the submitted changes; newer edits remain unsaved.' }
        : { type: 'success', message: savedMessage });
    } catch (error) {
      if (!requestIsCurrent()) return;
      if (replaceStaleQuotationReview(error)) return;
      const details = await describeQuotationError(error, 'Save all quote lines', 'PATCH /quotations/quote-lines/{id}/');
      if (!requestIsCurrent()) return;
      setErrorInfo(details);
      setLineFeedback({ type: 'error', message: 'The quotation changes could not be saved.' });
      console.error(formatQuotationError(details), error);
    } finally {
      if (requestIsCurrent()) setSaving(false);
    }
  };

  const saveQuoteTerms = async () => {
    if (saving || actionInFlight || !hasUnsavedQuoteTerms) return;
    const currentQuote = quoteRef.current || quote;
    const termsAtSaveStart = { ...quoteTermsDraftRef.current };
    setSaving(true);
    setLineFeedback(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.quotes.update(quote.id, {
        payment_terms: termsAtSaveStart.payment_terms || 'as_per_agreement',
        valid_until: termsAtSaveStart.valid_until || null,
        show_brand_column: !!termsAtSaveStart.show_brand_column,
        quotation_review_fingerprint: currentQuote?.quotation_review_fingerprint || '',
      });
      const newerTermsRemain = !termsDraftsMatch(
        quoteTermsDraftRef.current,
        termsAtSaveStart,
      );
      setLoadedQuote(response.data);
      setLineFeedback(newerTermsRemain
        ? { type: 'warning', message: 'Quotation terms and layout saved; newer edits remain unsaved.' }
        : { type: 'success', message: 'Quotation terms and layout saved.' });
    } catch (error) {
      if (replaceStaleQuotationReview(error)) return;
      const details = await describeQuotationError(error, 'Save quotation terms and layout', `PATCH /quotations/quotes/${quote.id}/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const saveQuoteParty = async () => {
    if (saving || actionInFlight || !hasUnsavedQuoteParty) return;
    setSaving(true);
    setLineFeedback(null);
    setErrorInfo(null);
    try {
      await quotationAPI.quotes.update(quote.id, {
        company: quotePartyDraft.company,
        contact: quotePartyDraft.contact || null,
      });
      await load();
      setLineFeedback({ type: 'success', message: 'Customer and contact saved.' });
    } catch (error) {
      const details = await describeQuotationError(error, 'Save quotation customer/contact', `PATCH /quotations/quotes/${quote.id}/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const createQuoteContact = async () => {
    if (!quotePartyDraft.company || !contactForm.name.trim() || saving || actionInFlight) return;
    setContactSaving(true);
    setErrorInfo(null);
    setLineFeedback(null);
    try {
      const response = await quotationAPI.contacts.create({
        ...contactForm,
        company: quotePartyDraft.company,
      });
      rememberContact(response.data);
      updateQuotePartyDraft({ contact: response.data.id });
      setContactForm(emptyContactForm);
      setShowContactForm(false);
      setLineFeedback({ type: 'success', message: 'Contact created and selected. Save customer/contact to apply it to this quotation.' });
    } catch (error) {
      const details = await describeQuotationError(error, 'Create quotation contact', 'POST /quotations/contacts/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setContactSaving(false);
    }
  };

  const addLine = async (event) => {
    event.preventDefault();
    if (saving || actionInFlight) return;
    setSaving(true);
    setLineFeedback(null);
    setErrorInfo(null);
    try {
      await quotationAPI.lines.create({
        ...payloadForLine(lineForm),
        quotation: quote.id,
        sort_order: quote.lines.length,
      });
      lineFormPriceVersionRef.current += 1;
      lineFormSelectedProductRef.current = '';
      setLineForm(emptyLine);
      await load({ refreshReferences: false });
      setLineFeedback({ type: 'success', message: 'Line added.' });
    } catch (error) {
      const details = await describeQuotationError(error, 'Add quote line', 'POST /quotations/quote-lines/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const deleteLine = async (lineId) => {
    if (saving || actionInFlight) return;
    if (!window.confirm('Delete this quotation line?')) return;
    setSaving(true);
    setLineFeedback(null);
    setErrorInfo(null);
    try {
      await quotationAPI.lines.delete(lineId);
      await load({ refreshReferences: false });
      setLineFeedback({ type: 'success', message: 'Line deleted.' });
    } catch (error) {
      const details = await describeQuotationError(error, 'Delete quote line', `DELETE /quotations/quote-lines/${lineId}/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const toggleLineSelection = (lineId) => {
    setSelectedLineIds((current) => (
      current.includes(lineId)
        ? current.filter((id) => id !== lineId)
        : [...current, lineId]
    ));
  };

  const selectVisibleUnmatched = () => {
    setSelectedLineIds(filteredLines.filter((line) => derivedLineStatus(line).id === 'unmatched').map((line) => line.id));
  };

  const openCreateProductModal = (lineIds) => {
    const ids = lineIds.filter((lineId) => {
      const line = activeLines.find((candidate) => candidate.id === lineId);
      return line && derivedLineStatus(line).id === 'unmatched';
    });
    if (!ids.length) {
      setLineFeedback({ type: 'warning', message: 'Select unmatched rows before creating Products.' });
      return;
    }
    const names = Object.fromEntries(ids.map((lineId) => {
      const line = activeLines.find((candidate) => candidate.id === lineId);
      return [lineId, lineLabel(line, lineDrafts[lineId])];
    }));
    setProductCreateError(null);
    setProductCreateModal({ lineIds: ids, names, confirmations: {} });
  };

  const closeCreateProductModal = () => {
    setProductCreateError(null);
    setProductCreateModal(null);
  };

  const confirmCreateProducts = async (forceCreate = false) => {
    if (!productCreateModal || saving || actionInFlight) return;
    const currentQuote = quoteRef.current || quote;
    setSaving(true);
    setErrorInfo(null);
    setProductCreateError(null);
    setLineFeedback(null);
    try {
      const names = { ...productCreateModal.names };
      if (!forceCreate && companyPricingEnabled) {
        // Small batches keep AI preflight outside the quotation's save lock.
        for (let offset = 0; offset < productCreateModal.lineIds.length; offset += 3) {
          await Promise.all(productCreateModal.lineIds.slice(offset, offset + 3).map(async (id) => {
            try {
              const preview = (await quotationAPI.items.identityPreview({ name: names[id], company: quote.company })).data;
              names[id] = preview.standard_name || names[id];
            } catch { /* Original wording still passes deterministic matching. */ }
          }));
        }
        setProductCreateModal((current) => current ? { ...current, names } : current);
      }
      const response = await quotationAPI.quotes.bulkCreateProductsForLines(quote.id, {
        line_ids: productCreateModal.lineIds,
        names,
        confirm_create_line_ids: forceCreate
          ? productCreateModal.lineIds.filter((lineId) => {
            const warning = productCreateModal.confirmations?.[lineId];
            return warning && !warning.creation_blocked && productCreateModal.reviewed?.[lineId];
          })
          : [],
        quotation_review_fingerprint: currentQuote?.quotation_review_fingerprint || '',
      });
      const updatedLines = response.data.updated_lines || [];
      const confirmationRequired = response.data.confirmation_required || [];
      applyUpdatedLines(updatedLines, response.data.quotation);
      setItems((current) => {
        const additions = updatedLines
          .filter((line) => line.product && line.product_name)
          .map((line) => ({ id: line.product, name: line.product_name, unit: line.unit || '', pack_size: line.unit || '', status: 'draft', show_price: false }));
        const byId = new Map(current.map((item) => [String(item.id), item]));
        additions.forEach((item) => byId.set(String(item.id), { ...(byId.get(String(item.id)) || {}), ...item }));
        return Array.from(byId.values()).sort((a, b) => a.name.localeCompare(b.name));
      });
      const updatedIds = updatedLines.map((line) => line.id);
      setSelectedLineIds((current) => current.filter((id) => !updatedIds.includes(id)));
      if (confirmationRequired.length > 0) {
        const pendingIds = confirmationRequired.map((entry) => entry.line_id);
        setProductCreateModal((current) => (
          current
            ? {
              ...current,
              lineIds: pendingIds,
              confirmations: Object.fromEntries(confirmationRequired.map((entry) => [entry.line_id, entry])),
            }
            : null
        ));
        setLineFeedback({
          type: 'warning',
          message: `${confirmationRequired.length} row${confirmationRequired.length === 1 ? '' : 's'} look like existing Products. Review the matches before creating anything new.`,
        });
      } else {
        closeCreateProductModal();
        setLineFeedback({ type: 'success', message: response.data.message || 'Products created/linked.' });
      }
    } catch (error) {
      if (replaceStaleQuotationReview(error)) {
        closeCreateProductModal();
        return;
      }
      const details = await describeQuotationError(error, 'Create Products from quote lines', `POST /quotations/quotes/${quote.id}/bulk_create_products_for_lines/`);
      setProductCreateError(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const linkCandidateFromCreateModal = async (lineId, candidate) => {
    if (!candidate?.product_id || saving || actionInFlight || discountError) return;
    const currentQuote = quoteRef.current || quote;
    const discountAtSaveStart = discountDraftRef.current;
    const currentDraft = lineDrafts[lineId] || {};
    const hasSnapshotName = String(currentDraft.item_name_snapshot || '').trim().length > 0;
    const selectedProduct = items.find((item) => String(item.id) === String(candidate.product_id));
    const candidateBrand = candidate.brand_name ?? selectedProduct?.brand_name;
    const linkedDraft = {
      ...currentDraft,
      product: String(candidate.product_id),
      item_name_snapshot: hasSnapshotName ? currentDraft.item_name_snapshot : (candidate.product_name || ''),
      brand_name_snapshot: candidateBrand || '',
      match_status: 'confirmed',
      product_image: '',
      product_image_url: '',
      has_product_image: false,
      include_product_image: false,
    };
    setSaving(true);
    setErrorInfo(null);
    setProductCreateError(null);
    setLineFeedback(null);
    try {
      const linePayload = { id: lineId, ...payloadForLine(linkedDraft) };
      if (candidateBrand === null || candidateBrand === undefined) {
        delete linePayload.brand_name_snapshot;
      }
      const response = await quotationAPI.quotes.bulkUpdateLines(quote.id, {
        lines: [linePayload],
        discount_amount: discountAmountForPayload(),
        quotation_review_fingerprint: currentQuote?.quotation_review_fingerprint || '',
      });
      mergeSavedQuote(response.data.quotation, [lineId]);
      mergeSavedDiscount(
        response.data.quotation,
        !discountDraftsMatch(discountDraftRef.current, discountAtSaveStart),
      );
      rememberProductsInList([{
        id: candidate.product_id,
        name: candidate.product_name,
        sku: candidate.sku || '',
        barcode: candidate.barcode || '',
        dosage: candidate.dosage || '',
        pack_size: candidate.pack_size || '',
        status: candidate.status || 'draft',
      }]);
      setProductCreateModal((current) => {
        if (!current) return null;
        const remainingIds = current.lineIds.filter((id) => id !== lineId);
        if (!remainingIds.length) return null;
        const confirmations = { ...current.confirmations };
        delete confirmations[lineId];
        return { ...current, lineIds: remainingIds, confirmations };
      });
      setSelectedLineIds((current) => current.filter((id) => id !== lineId));
      setLineFeedback({ type: 'success', message: `Linked the row to existing Product '${candidate.product_name}'.` });
    } catch (error) {
      if (replaceStaleQuotationReview(error)) {
        closeCreateProductModal();
        return;
      }
      const details = await describeQuotationError(error, 'Link existing Product to quote line', `POST /quotations/quotes/${quote.id}/bulk_update_lines/`);
      setProductCreateError(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const applyUpdatedLines = (updatedLines = [], authoritativeQuote = null) => {
    if (authoritativeQuote?.id) {
      setLoadedQuote(authoritativeQuote);
      return;
    }
    const updatedById = Object.fromEntries(updatedLines.map((line) => [line.id, line]));
    const currentQuote = quoteRef.current || quote || {};
    const currentDrafts = lineDraftsRef.current || {};
    const currentSavedDrafts = savedLineDraftsRef.current || {};
    const nextDrafts = { ...currentDrafts };
    const nextSavedDrafts = { ...currentSavedDrafts };
    updatedLines.forEach((line) => {
      const nextDraft = draftFromLine(line);
      const currentDraft = currentDrafts[line.id] || {};
      const savedDraft = currentSavedDrafts[line.id] || {};
      nextDrafts[line.id] = mergeUnsavedLineDraft(nextDraft, currentDraft, savedDraft);
      nextSavedDrafts[line.id] = nextDraft;
    });
    const nextQuote = {
      ...currentQuote,
      // A partial legacy response cannot provide a current review token.
      // Fail closed until the editor is reloaded instead of submitting a
      // fingerprint that predates this line mutation.
      quotation_review_fingerprint: '',
      lines: (currentQuote.lines || []).map((line) => updatedById[line.id] || line),
    };
    quoteRef.current = nextQuote;
    lineDraftsRef.current = nextDrafts;
    savedLineDraftsRef.current = nextSavedDrafts;
    setQuote(nextQuote);
    setLineDrafts(nextDrafts);
    setSavedLineDrafts(nextSavedDrafts);
  };

  const rememberProductsInList = (products = []) => {
    setItems((current) => {
      const byId = new Map(current.map((item) => [String(item.id), item]));
      products
        .filter((product) => product?.id)
        .forEach((product) => byId.set(String(product.id), { ...(byId.get(String(product.id)) || {}), ...product }));
      return Array.from(byId.values()).sort((a, b) => a.name.localeCompare(b.name));
    });
  };

  const createProductForLine = async (lineId) => {
    if (saving || actionInFlight) return;
    setSaving(true);
    setActionInFlight(`create-product-${lineId}`);
    setErrorInfo(null);
    setLineFeedback(null);
    try {
      const draft = lineDrafts[lineId] || {};
      const response = await quotationAPI.lines.createProduct(lineId, {
        product_name: draft.item_name_snapshot || '',
        quotation_review_fingerprint: quoteRef.current?.quotation_review_fingerprint || '',
      });
      applyUpdatedLines([response.data.line], response.data.quotation);
      rememberProductsInList([response.data.product]);
      setSelectedLineIds((current) => current.filter((id) => id !== lineId));
      setLineFeedback({ type: 'success', message: response.data.message || 'Created Product and linked row.' });
    } catch (error) {
      if (replaceStaleQuotationReview(error)) return;
      const warning = error?.response?.data;
      if (error?.response?.status === 409 && warning?.requires_confirmation) {
        const draft = lineDrafts[lineId] || {};
        setProductCreateError(null);
        setProductCreateModal({
          lineIds: [lineId],
          names: { [lineId]: draft.item_name_snapshot || '' },
          confirmations: { [lineId]: { line_id: lineId, ...warning } },
        });
        setLineFeedback({
          type: 'warning',
          message: warning.creation_blocked
            ? 'This Product conflicts with an existing identifier. Select the existing Product or correct the name/details.'
            : 'A similar Product already exists. Review it before choosing to create a new one.',
        });
        return;
      }
      const details = await describeQuotationError(error, 'Create Product from quote line', `POST /quotations/quote-lines/${lineId}/create_product/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
      setActionInFlight('');
    }
  };

  const uploadImageForLine = async (lineId, file) => {
    if (!file || saving || actionInFlight) return;
    setSaving(true);
    setActionInFlight(`image-${lineId}`);
    setErrorInfo(null);
    setLineFeedback(null);
    const formData = new FormData();
    formData.append('image', file);
    formData.append(
      'quotation_review_fingerprint',
      quoteRef.current?.quotation_review_fingerprint || '',
    );
    try {
      const response = await quotationAPI.lines.uploadProductImage(lineId, formData);
      applyUpdatedLines([response.data.line], response.data.quotation);
      setLineFeedback({ type: 'success', message: response.data.message || 'Image saved for this Product.' });
    } catch (error) {
      if (replaceStaleQuotationReview(error)) return;
      const details = await describeQuotationError(error, 'Upload quotation line image', `POST /quotations/quote-lines/${lineId}/upload_product_image/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
      setActionInFlight('');
    }
  };

  const actionEndpoint = (label) => {
    const endpointNames = {
      Finalize: 'finalize',
      'Mark Sent': 'mark_sent',
      'Create Revision': 'revise',
      Cancel: 'cancel',
    };
    return `POST /quotations/quotes/${quote.id}/${endpointNames[label] || label.toLowerCase()}/`;
  };

  const downloadPdfFile = async (quoteForFilename = quote) => {
    const response = await quotationAPI.quotes.pdf(quote.id);
    const url = window.URL.createObjectURL(new Blob([response.data], { type: 'application/pdf' }));
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', quotationDownloadFilename(quoteForFilename, 'pdf'));
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
  };

  const emailPreviewParams = (sourceQuote = quoteRef.current, extra = {}) => ({
    ...extra,
    ...(sourceQuote?.quotation_review_fingerprint
      ? { quotation_review_fingerprint: sourceQuote.quotation_review_fingerprint }
      : {}),
  });

  const currentQuoteForEmailReview = async (requestGeneration) => {
    const displayedQuote = quoteRef.current || {};
    const latestResponse = await quotationAPI.quotes.retrieve(quote.id);
    if (emailPreviewGenerationRef.current !== requestGeneration) return null;
    const latestQuote = latestResponse.data || {};
    const displayedFingerprint = String(
      displayedQuote.quotation_review_fingerprint || ''
    );
    const latestFingerprint = String(
      latestQuote.quotation_review_fingerprint || ''
    );
    const displayedPayloadChanged = (
      quotationReviewDisplaySignature(latestQuote)
      !== quotationReviewDisplaySignature(displayedQuote)
    );
    if (!latestFingerprint || latestFingerprint !== displayedFingerprint || displayedPayloadChanged) {
      if (latestQuote?.id) setLoadedQuote(latestQuote);
      setEmailPreviewOpen(false);
      setEmailPreviewLoading(false);
      setEmailPreview(null);
      setEmailPreviewError('');
      setEmailSendError(null);
      setLineFeedback({
        type: 'warning',
        message: latestFingerprint
          ? `The quotation changed since this editor loaded. Review the refreshed quotation, then click ${gmailChainedActionsEnabled(latestQuote) ? 'Review Email' : 'Finalize or Email Quotation'} again.`
          : 'The current quotation review token is unavailable. Reload the editor before preparing an email.',
      });
      return null;
    }
    return latestQuote;
  };

  const replaceStaleQuotationReview = (error) => {
    const responseData = error?.response?.data || {};
    if (String(responseData.code || '') !== 'stale_quotation_review') return false;
    if (responseData.quote?.id) setLoadedQuote(responseData.quote);
    emailPreviewGenerationRef.current += 1;
    setEmailPreviewOpen(false);
    setEmailPreviewLoading(false);
    setEmailPreview(null);
    setEmailPreviewError('');
    setEmailSendError(null);
    setLineFeedback({
      type: 'warning',
      message: responseData.detail || 'The quotation changed in another session. Review the refreshed quotation before opening the email preview again.',
    });
    return true;
  };

  const requestEmailPreview = async (authoritativeQuote = null, isStillCurrent = null) => {
    const requestGeneration = ++emailPreviewGenerationRef.current;
    const sourceQuote = authoritativeQuote || quoteRef.current || quote;
    setEmailPreviewOpen(true);
    setEmailPreviewLoading(true);
    setEmailPreview(null);
    setEmailPreviewError('');
    setEmailSendError(null);
    setEmailQuoteFinalized(['finalized', 'sent'].includes(sourceQuote?.status));
    setEmailThreadCandidates([]);
    setEmailThreadCandidatesError('');
    setEmailThreadSearchCompleted(false);
    setEmailGmailReconnectError('');
    setEmailReconcileFeedback(null);
    emailThreadSearchGenerationRef.current += 1;
    emailReconcileGenerationRef.current += 1;
    const stopForNewLocalEdits = () => {
      if (!isStillCurrent || isStillCurrent()) return false;
      setEmailPreviewOpen(false);
      setEmailPreviewLoading(false);
      setEmailPreview(null);
      setEmailPreviewError('');
      setEmailSendError(null);
      setLineFeedback({
        type: 'warning',
        message: 'The quotation changed while the email review was being prepared. Review the changes, then click Review Email again.',
      });
      return true;
    };
    try {
      const latestQuote = authoritativeQuote
        || await currentQuoteForEmailReview(requestGeneration);
      if (!latestQuote) return;
      if (stopForNewLocalEdits()) return;
      const response = await quotationAPI.quotes.emailPreview(
        quote.id,
        emailPreviewParams(latestQuote),
      );
      if (emailPreviewGenerationRef.current !== requestGeneration) return;
      if (stopForNewLocalEdits()) return;
      setEmailPreview(response.data || {});
    } catch (error) {
      if (emailPreviewGenerationRef.current !== requestGeneration) return;
      if (replaceStaleQuotationReview(error)) return;
      const details = await describeQuotationError(
        error,
        'Prepare quotation email preview',
        `GET /quotations/quotes/${quote.id}/email_preview/`
      );
      if (emailPreviewGenerationRef.current !== requestGeneration) return;
      setEmailPreviewError(details.detail || 'The server could not prepare the email preview.');
      console.error(formatQuotationError(details), error);
    } finally {
      if (emailPreviewGenerationRef.current === requestGeneration) {
        setEmailPreviewLoading(false);
      }
    }
  };

  const loadEmailPreview = async (isStillCurrent = null) => {
    if (emailSending || emailReconciling || saving || actionInFlight) return;
    if (editableStatuses.has(quote.status) && finalizeIssues.length > 0) return;
    await requestEmailPreview(
      null,
      typeof isStillCurrent === 'function' ? isStillCurrent : null
    );
  };
  loadEmailPreviewRef.current = loadEmailPreview;

  useEffect(() => {
    const expectedFingerprint = String(initialEmailReviewFingerprint || '');
    if (!/^[0-9a-f]{64}$/.test(expectedFingerprint) || loading) return;
    const requestKey = `${String(quoteId)}:${expectedFingerprint}`;
    if (initialEmailReviewRequestRef.current === requestKey) return;
    initialEmailReviewRequestRef.current = requestKey;
    onInitialEmailReviewHandled?.();

    const loadedQuote = quoteRef.current;
    if (!loadedQuote || String(loadedQuote.id) !== String(quoteId)) {
      setLineFeedback({
        type: 'warning',
        message: 'The prepared quotation could not be loaded. Open it again before reviewing the email.',
      });
      return;
    }
    if (String(loadedQuote.quotation_review_fingerprint || '') !== expectedFingerprint) {
      setLineFeedback({
        type: 'warning',
        message: 'The quotation changed before the email review opened. Review the current quotation, then click Review Email again.',
      });
      return;
    }
    if (editableStatuses.has(loadedQuote.status) && finalizeIssues.length > 0) {
      setLineFeedback({
        type: 'warning',
        message: 'The prepared quotation needs review before its email preview can be opened.',
      });
      return;
    }

    const localStateAtHandoff = JSON.stringify({
      quote: quotationReviewDisplaySignature(loadedQuote),
      party: quotePartyDraftRef.current,
      terms: quoteTermsDraftRef.current,
      discount: discountDraftRef.current,
      lines: snapshotLineDrafts(loadedQuote, lineDraftsRef.current),
    });
    const isStillCurrent = () => {
      const currentQuote = quoteRef.current;
      return Boolean(
        currentQuote
        && String(currentQuote.id) === String(quoteId)
        && String(currentQuote.quotation_review_fingerprint || '') === expectedFingerprint
        && JSON.stringify({
          quote: quotationReviewDisplaySignature(currentQuote),
          party: quotePartyDraftRef.current,
          terms: quoteTermsDraftRef.current,
          discount: discountDraftRef.current,
          lines: snapshotLineDrafts(currentQuote, lineDraftsRef.current),
        }) === localStateAtHandoff
      );
    };
    loadEmailPreviewRef.current?.(isStillCurrent);
  }, [
    finalizeIssues.length,
    initialEmailReviewFingerprint,
    loading,
    onInitialEmailReviewHandled,
    quoteId,
  ]);

  const reviewEmail = async () => {
    if (!chainedActionsEnabled) {
      await loadEmailPreview();
      return;
    }
    if (
      reviewEmailInFlightRef.current
      || emailSending
      || emailReconciling
      || saving
      || actionInFlight
      || reviewEmailIssues.length > 0
      || discountError
    ) return;

    const currentQuote = quoteRef.current || quote;
    const currentFingerprint = String(
      currentQuote?.quotation_review_fingerprint || ''
    );
    const lineIdsToSave = [...changedLineIds];
    const draftsAtActionStart = snapshotLineDrafts(currentQuote, lineDraftsRef.current);
    const discountAtActionStart = discountDraftRef.current;
    const shouldSaveBeforeReview = lineIdsToSave.length > 0 || hasUnsavedDiscount;
    if (shouldSaveBeforeReview && !currentFingerprint) {
      setLineFeedback({
        type: 'warning',
        message: 'Reload the quotation before saving and reviewing its email.',
      });
      return;
    }

    reviewEmailInFlightRef.current = true;
    const actionGeneration = ++reviewEmailGenerationRef.current;
    setActionInFlight('Review Email');
    if (shouldSaveBeforeReview) setSaving(true);
    setLineFeedback(null);
    setErrorInfo(null);

    try {
      let savedQuote = currentQuote;
      if (shouldSaveBeforeReview) {
        const response = await quotationAPI.quotes.bulkUpdateLines(quote.id, {
          quotation_review_fingerprint: currentFingerprint,
          discount_amount: discountAmountForPayload(),
          lines: lineIdsToSave.map((lineId) => ({
            id: lineId,
            ...payloadForLine(lineDraftsRef.current[lineId] || {}),
          })),
        });
        if (reviewEmailGenerationRef.current !== actionGeneration) return;
        savedQuote = response.data?.quotation;
        if (
          !savedQuote?.id
          || String(savedQuote.id) !== String(quote.id)
          || !savedQuote.quotation_review_fingerprint
        ) {
          throw new Error(
            'The quotation was saved, but the server did not return a current review fingerprint.'
          );
        }
        const lineDraftsChangedDuringSave = !lineDraftSnapshotMatches(
          draftsAtActionStart,
          lineDraftsRef.current,
        );
        const discountChangedDuringSave = !discountDraftsMatch(
          discountDraftRef.current,
          discountAtActionStart,
        );
        if (lineDraftsChangedDuringSave || discountChangedDuringSave) {
          mergeSavedQuote(savedQuote, lineIdsToSave, true);
          mergeSavedDiscount(savedQuote, discountChangedDuringSave);
          setLineFeedback({
            type: 'warning',
            message: 'The quotation changed while saving. The saved response was applied without discarding your newer edits; click Review Email again after checking them.',
          });
          return;
        }
        mergeSavedQuote(savedQuote, lineIdsToSave);
        mergeSavedDiscount(savedQuote);
        setLineFeedback({
          type: 'success',
          message: lineIdsToSave.length
            ? `Saved ${lineIdsToSave.length} line${lineIdsToSave.length === 1 ? '' : 's'}${hasUnsavedDiscount ? ' and the final discount' : ''}. Opening the email review...`
            : 'Saved the final discount. Opening the email review...',
        });
      }

      if (reviewEmailGenerationRef.current !== actionGeneration) return;
      // A save response is an authoritative locked server snapshot. With no
      // changes, retain the existing retrieve-and-compare gate before preview.
      const draftsBeforePreview = snapshotLineDrafts(savedQuote, lineDraftsRef.current);
      const discountBeforePreview = discountDraftRef.current;
      await requestEmailPreview(
        shouldSaveBeforeReview ? savedQuote : null,
        () => lineDraftSnapshotMatches(draftsBeforePreview, lineDraftsRef.current)
          && discountDraftsMatch(discountDraftRef.current, discountBeforePreview),
      );
    } catch (error) {
      if (reviewEmailGenerationRef.current !== actionGeneration) return;
      if (replaceStaleQuotationReview(error)) return;
      const details = await describeQuotationError(
        error,
        'Save quotation and review email',
        `POST /quotations/quotes/${quote.id}/bulk_update_lines/`
      );
      if (reviewEmailGenerationRef.current !== actionGeneration) return;
      setErrorInfo(details);
      setLineFeedback({
        type: 'error',
        message: details.detail || 'The quotation could not be saved. The email preview was not opened.',
      });
      console.error(formatQuotationError(details), error);
    } finally {
      if (reviewEmailGenerationRef.current === actionGeneration) {
        reviewEmailInFlightRef.current = false;
        setSaving(false);
        setActionInFlight('');
      }
    }
  };

  const refreshEmailPreview = async () => {
    if (emailSending || emailReconciling || saving || actionInFlight) return;
    if (editableStatuses.has(quoteRef.current?.status) && finalizeIssues.length > 0) {
      setEmailPreviewError('The quotation changed and must be corrected before a new email preview can be prepared.');
      return;
    }
    const requestGeneration = ++emailPreviewGenerationRef.current;
    const threadSelectionToken = String(emailPreview?.thread_selection_token || '');
    setEmailPreviewLoading(true);
    setEmailPreviewError('');
    try {
      const latestQuote = await currentQuoteForEmailReview(requestGeneration);
      if (!latestQuote) return;
      const response = threadSelectionToken
        ? await quotationAPI.quotes.emailPreview(quote.id, {
            ...emailPreviewParams(latestQuote),
            thread_selection_token: threadSelectionToken,
          })
        : await quotationAPI.quotes.emailPreview(
            quote.id,
            emailPreviewParams(latestQuote),
          );
      if (emailPreviewGenerationRef.current !== requestGeneration) return;
      setEmailPreview({
        ...(response.data || {}),
        ...(threadSelectionToken ? { thread_selection_token: threadSelectionToken } : {}),
      });
      setEmailSendError(null);
      setEmailQuoteFinalized(['finalized', 'sent'].includes(latestQuote.status));
      setEmailReconcileFeedback(null);
    } catch (error) {
      if (emailPreviewGenerationRef.current !== requestGeneration) return;
      if (replaceStaleQuotationReview(error)) return;
      const details = await describeQuotationError(
        error,
        'Refresh quotation email preview',
        `GET /quotations/quotes/${quote.id}/email_preview/`
      );
      if (emailPreviewGenerationRef.current !== requestGeneration) return;
      setEmailPreviewError(details.detail || 'The latest email preview could not be prepared.');
      console.error(formatQuotationError(details), error);
    } finally {
      if (emailPreviewGenerationRef.current === requestGeneration) {
        setEmailPreviewLoading(false);
      }
    }
  };

  const closeEmailPreview = () => {
    if (emailSending || emailReconciling) return;
    emailPreviewGenerationRef.current += 1;
    setEmailPreviewOpen(false);
    setEmailPreviewLoading(false);
    setEmailPreview(null);
    setEmailPreviewError('');
    setEmailSendError(null);
    setEmailThreadCandidates([]);
    setEmailThreadCandidatesLoading(false);
    setEmailThreadCandidatesError('');
    setEmailThreadSearchCompleted(false);
    setEmailGmailReconnectError('');
    setEmailReconciling(false);
    setEmailReconcileFeedback(null);
    emailThreadSearchGenerationRef.current += 1;
    emailReconcileGenerationRef.current += 1;
  };

  const clearEmailThreadCandidates = () => {
    emailThreadSearchGenerationRef.current += 1;
    setEmailThreadCandidates([]);
    setEmailThreadCandidatesLoading(false);
    setEmailThreadCandidatesError('');
    setEmailThreadSearchCompleted(false);
  };

  const findEmailThreadCandidates = async (recipient) => {
    if (!recipient || emailThreadCandidatesLoading || emailSending) return;
    const requestGeneration = ++emailThreadSearchGenerationRef.current;
    setEmailThreadCandidatesLoading(true);
    setEmailThreadCandidatesError('');
    setEmailThreadSearchCompleted(false);
    try {
      const response = await quotationAPI.quotes.emailThreadCandidates(quote.id, recipient, 10);
      if (emailThreadSearchGenerationRef.current !== requestGeneration) return;
      setEmailThreadCandidates(response.data?.candidates || []);
      setEmailThreadSearchCompleted(true);
    } catch (error) {
      const details = await describeQuotationError(
        error,
        'Find original Gmail thread',
        `GET /quotations/quotes/${quote.id}/email_thread_candidates/`
      );
      if (emailThreadSearchGenerationRef.current !== requestGeneration) return;
      setEmailThreadCandidates([]);
      setEmailThreadCandidatesError(details.detail || 'The shared mailbox could not be searched.');
      console.error(formatQuotationError(details), error);
    } finally {
      if (emailThreadSearchGenerationRef.current === requestGeneration) {
        setEmailThreadCandidatesLoading(false);
      }
    }
  };

  const selectEmailThreadCandidate = async (candidate) => {
    if (!candidate?.selection_token || emailSending) return;
    const requestGeneration = ++emailPreviewGenerationRef.current;
    setEmailPreviewLoading(true);
    setEmailThreadCandidatesError('');
    setEmailReconcileFeedback(null);
    emailThreadSearchGenerationRef.current += 1;
    try {
      const response = await quotationAPI.quotes.emailPreview(quote.id, {
        ...emailPreviewParams(),
        thread_selection_token: candidate.selection_token,
      });
      if (emailPreviewGenerationRef.current !== requestGeneration) return;
      setEmailPreview({
        ...(response.data || {}),
        thread_selection_token: candidate.selection_token,
      });
      setEmailThreadCandidates([]);
      setEmailThreadSearchCompleted(false);
    } catch (error) {
      if (emailPreviewGenerationRef.current !== requestGeneration) return;
      if (replaceStaleQuotationReview(error)) return;
      const details = await describeQuotationError(
        error,
        'Open selected Gmail thread',
        `GET /quotations/quotes/${quote.id}/email_preview/`
      );
      if (emailPreviewGenerationRef.current !== requestGeneration) return;
      setEmailThreadCandidatesError(details.detail || 'That Gmail message could not be verified. Search again and choose another message.');
      console.error(formatQuotationError(details), error);
    } finally {
      if (emailPreviewGenerationRef.current === requestGeneration) {
        setEmailPreviewLoading(false);
      }
    }
  };

  const reconnectGmailForSending = async () => {
    if (actionInFlight || emailSending) return;
    setActionInFlight('Reconnect Gmail');
    setEmailGmailReconnectError('');
    try {
      const response = await quotationAPI.gmail.connectUrl(
        `/admin?quotation_tab=quotes&quote_id=${encodeURIComponent(quote.id)}`
      );
      if (response.data?.auth_url) window.location.href = response.data.auth_url;
      else setEmailGmailReconnectError('Google authorization could not be opened. Use Gmail settings to reconnect the shared mailbox.');
    } catch (error) {
      const details = await describeQuotationError(error, 'Reconnect Gmail', 'POST /quotations/gmail/connection/');
      setEmailGmailReconnectError(details.detail || 'Gmail reconnect could not be started.');
      console.error(formatQuotationError(details), error);
    } finally {
      setActionInFlight('');
    }
  };

  const clearCorrectableEmailError = () => {
    setEmailSendError((current) => (
      current?.code === 'email_delivery_error' ? null : current
    ));
  };

  const finalizeWithoutEmail = (id) => quotationAPI.quotes.finalize(id, {
    quotation_review_fingerprint: quoteRef.current?.quotation_review_fingerprint || '',
  });

  const reconcileQuotationEmail = async () => {
    if (emailReconciling || emailSending) return;
    const requestGeneration = ++emailReconcileGenerationRef.current;
    setEmailReconciling(true);
    setEmailReconcileFeedback(null);
    try {
      const response = await quotationAPI.quotes.reconcileEmail(quote.id);
      if (emailReconcileGenerationRef.current !== requestGeneration) return;
      const responseQuote = response.data?.quote;
      const delivery = response.data?.delivery || {};
      const reconciled = response.data?.reconciled === true || delivery.status === 'sent';
      if (responseQuote?.id) setLoadedQuote(responseQuote);
      if (reconciled) {
        setEmailPreviewOpen(false);
        setLineFeedback({
          type: 'success',
          message: response.data?.detail || 'Gmail confirmed that the quotation email was sent.',
        });
        if (!responseQuote?.id) await load({ refreshReferences: false });
      } else {
        setEmailPreview((current) => ({
          ...(current || {}),
          status: delivery.status || current?.status || 'unknown',
          ...(typeof delivery.can_reconcile === 'boolean'
            ? { can_reconcile: delivery.can_reconcile }
            : {}),
        }));
        setEmailReconcileFeedback({
          type: 'warning',
          title: 'No confirmed Gmail delivery was found yet.',
          detail: response.data?.detail || 'The delivery remains unknown. Check the Sent mailbox before trying any other action.',
        });
      }
    } catch (error) {
      const responseData = error?.response?.data || {};
      const details = await describeQuotationError(
        error,
        'Check Gmail delivery status',
        `POST /quotations/quotes/${quote.id}/reconcile_email/`
      );
      if (emailReconcileGenerationRef.current !== requestGeneration) return;
      if (responseData.quote?.id) setLoadedQuote(responseData.quote);
      setEmailPreview((current) => ({
        ...(current || {}),
        status: responseData.delivery?.status || current?.status || 'unknown',
        ...(typeof responseData.delivery?.can_reconcile === 'boolean'
          ? { can_reconcile: responseData.delivery.can_reconcile }
          : {}),
      }));
      setEmailReconcileFeedback({
        type: 'error',
        title: 'Gmail status could not be checked.',
        detail: details.detail || 'The delivery remains blocked. Check the shared mailbox before taking another action.',
      });
      console.error(formatQuotationError(details), error);
    } finally {
      if (emailReconcileGenerationRef.current === requestGeneration) {
        setEmailReconciling(false);
      }
    }
  };

  const finalizeOnlyFromPreview = async () => {
    if (emailSending || saving || actionInFlight || directFinalizeIssues.length > 0) return;
    setEmailSending(true);
    setSaving(true);
    setActionInFlight('Finalize');
    setEmailSendError(null);
    setErrorInfo(null);
    try {
      const response = await finalizeWithoutEmail(quote.id);
      setEmailQuoteFinalized(true);
      try {
        setDownloadLoading(true);
        await downloadPdfFile(response.data || quote);
      } catch (downloadError) {
        const details = await describeQuotationError(
          downloadError,
          'Download finalized quotation PDF',
          `GET /quotations/quotes/${quote.id}/pdf/`
        );
        setErrorInfo(details);
        console.error(formatQuotationError(details), downloadError);
      } finally {
        setDownloadLoading(false);
      }
      setEmailPreviewOpen(false);
      setLineFeedback({ type: 'success', message: 'Quotation finalized. No email was sent.' });
      await load({ refreshReferences: false });
    } catch (error) {
      if (replaceStaleQuotationReview(error)) return;
      const details = await describeQuotationError(
        error,
        'Finalize quotation',
        `POST /quotations/quotes/${quote.id}/finalize/`
      );
      setEmailSendError({
        kind: 'finalize',
        detail: details.detail || 'The quotation could not be finalized.',
        quoteFinalized: false,
        retryable: true,
        deliveryStatus: 'not_sent',
      });
      console.error(formatQuotationError(details), error);
    } finally {
      setEmailSending(false);
      setSaving(false);
      setActionInFlight('');
    }
  };

  const sendQuotationEmail = async (payload) => {
    if (emailSending || saving || actionInFlight) return;
    const alreadyFinalized = emailQuoteFinalized || ['finalized', 'sent'].includes(quoteRef.current?.status);
    const action = alreadyFinalized
      ? quotationAPI.quotes.sendEmail
      : quotationAPI.quotes.finalizeAndSend;
    const endpoint = alreadyFinalized
      ? `POST /quotations/quotes/${quote.id}/send_email/`
      : `POST /quotations/quotes/${quote.id}/finalize_and_send/`;
    setEmailSending(true);
    setSaving(true);
    setActionInFlight(alreadyFinalized ? 'Send Quotation' : 'Finalize & Send');
    setEmailSendError(null);
    setErrorInfo(null);
    try {
      const response = await action(quote.id, payload);
      const responseQuote = response.data?.quote || response.data?.quotation;
      if (responseQuote?.id) setLoadedQuote(responseQuote);
      setEmailPreviewOpen(false);
      setLineFeedback({
        type: 'success',
        message: response.data?.detail || response.data?.message || 'Quotation finalized and emailed successfully.',
      });
      await load({ refreshReferences: false });
    } catch (error) {
      const responseData = error?.response?.data || {};
      const deliveryStatus = responseData.delivery_status || responseData.delivery?.status || 'not_sent';
      const errorCode = String(responseData.code || '');
      const quoteWasFinalized = Boolean(
        responseData.quote_finalized
        || responseData.quotation_finalized
        || responseData.finalized
        || ['finalized', 'sent'].includes(responseData.quote?.status)
        || alreadyFinalized
      );
      const details = await describeQuotationError(error, alreadyFinalized ? 'Send quotation email' : 'Finalize and send quotation', endpoint);
      setEmailQuoteFinalized(quoteWasFinalized);
      setEmailSendError({
        code: errorCode,
        detail: details.detail || 'The email could not be sent.',
        quoteFinalized: quoteWasFinalized,
        retryable: responseData.retryable === true,
        deliveryStatus,
        refreshPreview: responseData.refresh_preview === true,
      });
      if (responseData.delivery?.outbound_snapshot_frozen === true) {
        setEmailPreview((current) => ({
          ...(current || {}),
          ...responseData.delivery,
        }));
      }
      if (responseData.quote?.id) setLoadedQuote(responseData.quote);
      else if (quoteWasFinalized || deliveryStatus === 'unknown') await load({ refreshReferences: false });
      console.error(formatQuotationError(details), error);
    } finally {
      setEmailSending(false);
      setSaving(false);
      setActionInFlight('');
    }
  };

  const runAction = async (label, action) => {
    if (saving || actionInFlight) return;
    if (label === 'Finalize' && finalizeIssues.length > 0) return;
    if (label === 'Cancel' && !window.confirm('Cancel this quotation?')) return;
    setSaving(true);
    setActionInFlight(label);
    setErrorInfo(null);
    try {
      const response = await action(quote.id);
      if (label === 'Create Revision' && response.data?.id) {
        if (onOpenQuote) {
          onOpenQuote(response.data.id);
        } else {
          setLineFeedback({
            type: 'success',
            message: `Created ${response.data.quotation_number || 'a draft revision'}. Return to the quotation list to open it.`,
          });
          await load({ refreshReferences: false });
        }
        return;
      }
      if (label === 'Finalize') {
        setDownloadLoading(true);
        try {
          await downloadPdfFile(response.data || quote);
        } catch (downloadError) {
          const details = await describeQuotationError(downloadError, 'Download finalized quotation PDF', `GET /quotations/quotes/${quote.id}/pdf/`);
          setErrorInfo(details);
          console.error(formatQuotationError(details), downloadError);
        } finally {
          setDownloadLoading(false);
        }
      }
      await load({ refreshReferences: false });
    } catch (error) {
      if (label === 'Finalize' && replaceStaleQuotationReview(error)) return;
      const details = await describeQuotationError(error, label, actionEndpoint(label));
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
      setActionInFlight('');
    }
  };

  const downloadPdf = async () => {
    if (downloadLoading || actionInFlight || hasUnsavedCustomerDocument) return;
    setDownloadLoading(true);
    setErrorInfo(null);
    try {
      await downloadPdfFile(quote);
    } catch (error) {
      const details = await describeQuotationError(error, 'Download quotation PDF', `GET /quotations/quotes/${quote.id}/pdf/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setDownloadLoading(false);
    }
  };

  const downloadExcel = async () => {
    if (excelDownloadLoading || actionInFlight || hasUnsavedCustomerDocument) return;
    setExcelDownloadLoading(true);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.quotes.excel(quote.id);
      const url = window.URL.createObjectURL(new Blob([response.data], {
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      }));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', quotationDownloadFilename(quote, 'xlsx'));
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (error) {
      const details = await describeQuotationError(error, 'Download quotation Excel', `GET /quotations/quotes/${quote.id}/excel/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setExcelDownloadLoading(false);
    }
  };

  const uploadLpo = async () => {
    if (lpoUploading || actionInFlight) return;
    if (!lpoFile && !lpoText.trim()) {
      setLpoFeedback({ type: 'warning', message: 'Upload an LPO file or paste LPO text first.' });
      return;
    }
    setLpoUploading(true);
    setLpoFeedback(null);
    setErrorInfo(null);
    try {
      let response;
      if (lpoFile) {
        const formData = new FormData();
        formData.append('file', lpoFile);
        formData.append('use_ai', lpoUseAi ? 'true' : 'false');
        response = await quotationAPI.quotes.uploadLpo(quote.id, formData, true);
      } else {
        response = await quotationAPI.quotes.uploadLpo(quote.id, {
          text: lpoText,
          use_ai: lpoUseAi,
        });
      }
      const nextLpo = response.data.lpo;
      const existing = lpos.filter((item) => item.id !== nextLpo.id);
      syncLpos([nextLpo, ...existing]);
      setLpoFile(null);
      setLpoText('');
      setLpoFeedback({
        type: 'success',
        message: response.data.message || 'LPO recorded. Review details and download the Proforma Tax Invoice.',
      });
    } catch (error) {
      const details = await describeQuotationError(error, 'Upload LPO', `POST /quotations/quotes/${quote.id}/upload_lpo/`);
      setErrorInfo(details);
      setLpoFeedback({ type: 'error', message: details.detail || 'LPO upload failed.' });
      console.error(formatQuotationError(details), error);
    } finally {
      setLpoUploading(false);
    }
  };

  const saveLpoDetails = async () => {
    const currentLpo = lpos[0];
    if (!currentLpo || lpoSaving) return;
    setLpoSaving(true);
    setLpoFeedback(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.lpos.update(currentLpo.id, {
        lpo_number: lpoDraft.lpo_number,
        lpo_date: lpoDraft.lpo_date || null,
        notes: lpoDraft.notes,
        status: lpoDraft.status || currentLpo.status,
        applied_outcome_line_ids: lpoDraft.applied_outcome_line_ids || [],
      });
      syncLpos([response.data, ...lpos.filter((item) => item.id !== response.data.id)]);
      setLpoFeedback({ type: 'success', message: 'LPO details saved.' });
    } catch (error) {
      const details = await describeQuotationError(error, 'Save LPO details', `PATCH /quotations/lpos/${currentLpo.id}/`);
      setErrorInfo(details);
      setLpoFeedback({ type: 'error', message: details.detail || 'Could not save LPO details.' });
      console.error(formatQuotationError(details), error);
    } finally {
      setLpoSaving(false);
    }
  };

  const downloadProforma = async () => {
    const currentLpo = lpos[0];
    if (!currentLpo || proformaDownloadLoading || actionInFlight) return;
    setProformaDownloadLoading(true);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.quotes.proformaPdf(quote.id, { lpo: currentLpo.id });
      const url = window.URL.createObjectURL(new Blob([response.data], { type: 'application/pdf' }));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', proformaDownloadFilename(quote));
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      setLpoFeedback({ type: 'success', message: 'Proforma Tax Invoice downloaded.' });
    } catch (error) {
      const details = await describeQuotationError(error, 'Download Proforma Tax Invoice', `GET /quotations/quotes/${quote.id}/proforma_pdf/`);
      setErrorInfo(details);
      setLpoFeedback({ type: 'error', message: details.detail || 'Could not download Proforma Tax Invoice.' });
      console.error(formatQuotationError(details), error);
    } finally {
      setProformaDownloadLoading(false);
    }
  };

  if (loading) return <div className="qm-loading">Loading quotation...</div>;
  if (!quote) {
    return (
      <div className="qm-section">
        <QuotationErrorNotice
          error={errorInfo}
          onRetry={() => load()}
          retrying={loading}
          retryLabel="Retry quotation"
        />
        <div className="qm-empty">The quotation could not be loaded. Retry here without closing the editor.</div>
      </div>
    );
  }

  const latestLpo = lpos[0] || null;
  const lpoWorkflowEligible = ['approved', 'finalized', 'sent'].includes(quote.status);
  const canUseLpoWorkflow = (
    lpoWorkflowEligible
    && !lpoRecordsUnavailable
    && !lpoRecordsLoading
  );
  const visibleSupportingDatasetStates = progressiveLoadEnabled
    ? Object.entries(supportingDatasetLabels)
      .map(([key, label]) => ({ key, label, ...(supportingDatasetStates[key] || {}) }))
      .filter((dataset) => ['loading', 'error'].includes(dataset.status))
    : [];
  const productCreationWarnings = productCreateModal ? Object.values(productCreateModal.confirmations || {}) : [];
  const hasProductCreationWarnings = productCreationWarnings.length > 0;
  const canOverrideProductCreationWarning = productCreationWarnings.some((warning) => !warning.creation_blocked) && productCreateModal?.lineIds?.every((id) => productCreateModal.confirmations?.[id]?.creation_blocked || productCreateModal.reviewed?.[id]);
  const gmailSource = quote.gmail_source && typeof quote.gmail_source === 'object'
    ? quote.gmail_source
    : null;
  const gmailImportId = gmailSource?.import_id ?? gmailSource?.id ?? null;
  const gmailSourceSubject = gmailSource?.subject || gmailSource?.inquiry_subject || '';
  const gmailSourceCompany = (
    gmailSource?.confirmed_company_name
    || gmailSource?.company_name
    || quote.company_name
    || 'Not recorded'
  );
  const gmailSourceContact = (
    gmailSource?.confirmed_contact_name
    || gmailSource?.contact_name
    || quote.contact_name
    || 'No contact selected'
  );
  const hasUnsavedLineForm = Object.keys(emptyLine).some(
    (key) => lineForm[key] !== emptyLine[key]
  );
  const hasUnsavedContactForm = showContactForm && Object.keys(emptyContactForm).some(
    (key) => contactForm[key] !== emptyContactForm[key]
  );
  const gmailEvidenceNavigationBlocked = Boolean(
    hasUnsavedCustomerDocument
    || hasUnsavedLineForm
    || hasUnsavedContactForm
    || saving
    || contactSaving
    || actionInFlight
  );
  const gmailEvidenceActionBlocked = Boolean(
    !gmailEvidenceVisible && gmailEvidenceNavigationBlocked
  );
  const gmailEvidenceNavigationHintId = `gmail-evidence-navigation-hint-${quote.id}`;
  const renderDraftCompletionActions = () => {
    if (!['draft', 'pending_review', 'approved'].includes(quote.status)) return null;

    return (
      <>
        {chainedActionsEnabled && (
          <button
            type="button"
            className="qm-secondary"
            disabled={saving || Boolean(actionInFlight) || directFinalizeIssues.length > 0}
            title={directFinalizeIssues.length > 0 ? directFinalizeIssues[0] : 'Finalize without sending an email.'}
            onClick={() => runAction('Finalize', finalizeWithoutEmail)}
          >
            {actionInFlight === 'Finalize' ? 'Finalizing...' : 'Finalize'}
          </button>
        )}
        <button
          type="button"
          className="qm-primary"
          disabled={saving || Boolean(actionInFlight) || primaryEmailActionIssues.length > 0}
          onClick={chainedActionsEnabled ? reviewEmail : loadEmailPreview}
        >
          {chainedActionsEnabled
            ? (actionInFlight === 'Review Email' ? 'Opening Email Review...' : 'Review Email')
            : 'Finalize'}
        </button>
      </>
    );
  };

  return (
    <div className="qm-editor">
      <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
      {visibleSupportingDatasetStates.length > 0 && (
        <div
          className="qm-supporting-data-status"
          role="status"
          aria-live="polite"
          aria-label="Supporting quotation data status"
        >
          <strong>Supporting data</strong>
          <div>
            {visibleSupportingDatasetStates.map((dataset) => (
              <span key={dataset.key} className={`qm-supporting-data-item ${dataset.status}`}>
                {dataset.label}: {dataset.status === 'loading' ? 'Loading' : 'Unavailable'}
              </span>
            ))}
          </div>
        </div>
      )}
      {referenceLoadFailures.length > 0 && (
        <div className="qm-feedback warning qm-reference-load-warning" role="alert">
          <div>
            <strong>Quotation loaded, but some supporting data is temporarily unavailable.</strong>
            <p>
              Safe GET requests were retried automatically. Controls that depend on the missing data are disabled until retry succeeds.
            </p>
            <ul>
              {referenceLoadFailures.map((failure) => (
                <li key={failure.key}>
                  {failure.details.action}: status {failure.details.status} — <code>{failure.details.endpoint}</code>
                </li>
              ))}
            </ul>
          </div>
          <button
            type="button"
            className="qm-primary small"
            disabled={referenceRetrying}
            onClick={() => load({
              refreshQuote: false,
              referenceKeys: referenceLoadFailures.map((failure) => failure.key),
            })}
          >
            {referenceRetrying ? 'Retrying missing data...' : 'Retry missing data'}
          </button>
        </div>
      )}
      <div className="qm-editor-header">
        <div>
          <button type="button" className="qm-secondary small" onClick={onClose}>Back to List</button>
          <h3>{quote.quotation_number}</h3>
          <p>{quote.company_name} - {quote.status_display} - Version {quote.version}</p>
          {quote.contact_name && (
            <p className="qm-muted-line">
              Attention: {quote.contact_name}
              {quote.contact_role ? ` - ${quote.contact_role}` : ''}
              {quote.contact_department ? `, ${quote.contact_department}` : ''}
            </p>
          )}
        </div>
        <div className="qm-action-row">
          {renderDraftCompletionActions()}
          {quote.status === 'finalized' && <button type="button" className="qm-primary" disabled={saving || Boolean(actionInFlight)} onClick={loadEmailPreview}>Email Quotation</button>}
          {quote.status === 'finalized' && <button type="button" className="qm-secondary" disabled={saving || Boolean(actionInFlight)} onClick={() => runAction('Mark Sent', quotationAPI.quotes.markSent)}>{actionInFlight === 'Mark Sent' ? 'Saving...' : 'Mark Sent'}</button>}
          {['finalized', 'sent'].includes(quote.status) && <button type="button" className="qm-primary" disabled={saving || Boolean(actionInFlight)} onClick={() => onReviewOutcome && onReviewOutcome(quote.id)}>Review Outcome</button>}
          {['finalized', 'sent'].includes(quote.status) && <button type="button" className="qm-secondary" disabled={saving || Boolean(actionInFlight)} onClick={() => runAction('Create Revision', quotationAPI.quotes.revise)}>{actionInFlight === 'Create Revision' ? 'Creating...' : 'Create Revision'}</button>}
          {!['revised', 'cancelled'].includes(quote.status) && <button type="button" className="qm-secondary danger" disabled={saving || Boolean(actionInFlight)} onClick={() => runAction('Cancel', quotationAPI.quotes.cancel)}>{actionInFlight === 'Cancel' ? 'Cancelling...' : 'Cancel'}</button>}
          <button
            type="button"
            className="qm-secondary"
            disabled={downloadLoading || saving || Boolean(actionInFlight) || hasUnsavedCustomerDocument}
            title={hasUnsavedCustomerDocument ? 'Save customer, terms and layout, and line changes, including the final discount, before downloading.' : ''}
            onClick={downloadPdf}
          >
            {downloadLoading ? 'Preparing PDF...' : quote.status === 'draft' ? 'Download Draft PDF' : ['finalized', 'sent'].includes(quote.status) ? 'Download Final PDF' : 'Download PDF'}
          </button>
          <button
            type="button"
            className="qm-secondary"
            disabled={excelDownloadLoading || saving || Boolean(actionInFlight) || hasUnsavedCustomerDocument}
            title={hasUnsavedCustomerDocument ? 'Save customer, terms and layout, and line changes, including the final discount, before downloading.' : ''}
            onClick={downloadExcel}
          >
            {excelDownloadLoading ? 'Preparing Excel...' : 'Download Excel'}
          </button>
        </div>
      </div>

      {gmailSource && (
        <section className="qm-gmail-source-banner" aria-label="Gmail inquiry source">
          <div className="qm-gmail-source-content">
            <span className="qm-gmail-source-kicker">Imported from Gmail</span>
            <strong>{gmailSourceSubject || 'Gmail inquiry'}</strong>
            <div className="qm-gmail-source-details">
              <span><b>Confirmed company</b>{gmailSourceCompany}</span>
              <span><b>Confirmed contact</b>{gmailSourceContact}</span>
            </div>
            <p>Source messages, attachments, and row evidence are retained with this quotation.</p>
          </div>
          {gmailImportId && typeof onOpenGmailImport === 'function' && (
            <div className="qm-gmail-source-actions">
              <button
                type="button"
                className="qm-secondary"
                disabled={gmailEvidenceActionBlocked}
                aria-describedby={gmailEvidenceActionBlocked
                  ? gmailEvidenceNavigationHintId
                  : undefined}
                onClick={() => onOpenGmailImport(gmailImportId)}
              >
                {gmailEvidenceVisible ? 'Hide Gmail evidence' : 'View Gmail evidence'}
              </button>
              {gmailEvidenceActionBlocked && (
                <small id={gmailEvidenceNavigationHintId} role="status">
                  Finish the current action, or save or clear unfinished quotation changes before opening Gmail evidence.
                </small>
              )}
            </div>
          )}
        </section>
      )}

      <div className="qm-status-progress" aria-label="Quotation status progress">
        {statusSteps.map((step, index) => {
          const currentIndex = statusSteps.findIndex((candidate) => candidate.id === quote.status);
          const isComplete = currentIndex >= index && currentIndex !== -1;
          const isActive = quote.status === step.id;
          return (
            <div key={step.id} className={`qm-status-step ${isComplete ? 'complete' : ''} ${isActive ? 'active' : ''}`}>
              <span>{index + 1}</span>
              <p>{step.label}</p>
            </div>
          );
        })}
      </div>

      {!isEditable && (
        <div className="qm-notice">This quotation is locked. Create a revision to make changes.</div>
      )}
      <div className="qm-helper">PDF and Excel use the latest saved customer, terms, layout, final discount, and line data. Save any changes before downloading or finalizing.</div>
      {priceContextError && (
        <div className="qm-feedback warning" role="status">
          <div>
            <strong>Price history previews are temporarily unavailable.</strong>
            <p>
              The quotation is still available to edit. Use View price history beside an item to retry that Product directly.
              {priceContextError.detail ? ` ${priceContextError.detail}` : ''}
            </p>
          </div>
          <button type="button" className="qm-secondary small" onClick={() => setPriceContextError(null)}>Dismiss</button>
        </div>
      )}
      {progressiveLoadEnabled && lpoWorkflowEligible && lpoRecordsLoading && (
        <div className="qm-panel qm-lpo-workflow qm-supporting-panel-loading" role="status">
          <strong>Loading LPO records...</strong>
          <p>The quotation and pricing controls are ready while purchase-order history loads.</p>
        </div>
      )}
      {canUseLpoWorkflow && (
        <div className="qm-panel qm-lpo-workflow">
          <div className="qm-panel-heading">
            <div>
              <h3>LPO & Proforma Tax Invoice</h3>
              <p>Record the customer LPO, verify the detected details, then download a Proforma Tax Invoice for advance-payment processing.</p>
            </div>
            <div className="qm-lpo-status-pill">{latestLpo ? `LPO ${latestLpo.status_display || latestLpo.status}` : 'No LPO recorded'}</div>
          </div>
          {lpoFeedback && <div className={`qm-feedback ${lpoFeedback.type}`}>{lpoFeedback.message}</div>}
          <div className="qm-lpo-steps">
            <div className="qm-lpo-card">
              <span className="qm-step-kicker">Step 1</span>
              <h4>Upload or paste LPO</h4>
              <p>Use a PDF/Excel LPO or paste the purchase order text. Source files stay private.</p>
              <label className="qm-file-control">
                <span className="qm-label-text">LPO file</span>
                <input type="file" accept=".pdf,.xlsx,.xls,.xlsb" onChange={(event) => setLpoFile(event.target.files?.[0] || null)} />
              </label>
              <label>
                <span className="qm-label-text">Or paste LPO text</span>
                <textarea rows="4" value={lpoText} onChange={(event) => setLpoText(event.target.value)} placeholder="Paste LPO / purchase order details here..." />
              </label>
              <label className="qm-checkbox">
                <input type="checkbox" checked={lpoUseAi} onChange={(event) => setLpoUseAi(event.target.checked)} />
                Use AI cleanup when available
              </label>
              <button type="button" className="qm-primary" disabled={lpoUploading || (!lpoFile && !lpoText.trim())} onClick={uploadLpo}>
                {lpoUploading ? 'Recording LPO...' : latestLpo ? 'Upload another LPO' : 'Record LPO'}
              </button>
            </div>
            <div className="qm-lpo-card featured">
              <span className="qm-step-kicker">Step 2</span>
              <h4>Review detected details</h4>
              {latestLpo ? (
                <>
                  <div className="qm-lpo-metadata">
                    <span><strong>Source</strong>{latestLpo.source_filename || latestLpo.source_type_display}</span>
                    <span><strong>Rows parsed</strong>{latestLpo.parsed_row_count}</span>
                    <span><strong>Received</strong>{new Date(latestLpo.received_at).toLocaleDateString()}</span>
                  </div>
                  <div className="qm-lpo-detail-grid">
                    <label>
                      <span className="qm-label-text">LPO number</span>
                      <input value={lpoDraft.lpo_number} onChange={(event) => setLpoDraft({ ...lpoDraft, lpo_number: event.target.value })} placeholder="Enter LPO number if missing" />
                    </label>
                    <label>
                      <span className="qm-label-text">LPO date</span>
                      <input type="date" value={lpoDraft.lpo_date || ''} onChange={(event) => setLpoDraft({ ...lpoDraft, lpo_date: event.target.value })} />
                    </label>
                    <label>
                      <span className="qm-label-text">Status</span>
                      <select value={lpoDraft.status} onChange={(event) => setLpoDraft({ ...lpoDraft, status: event.target.value })}>
                        <option value="received">Received</option>
                        <option value="parsed">Parsed</option>
                        <option value="needs_review">Needs review</option>
                        <option value="confirmed">Confirmed</option>
                      </select>
                    </label>
                    <label className="span-two">
                      <span className="qm-label-text">Notes</span>
                      <textarea rows="2" value={lpoDraft.notes} onChange={(event) => setLpoDraft({ ...lpoDraft, notes: event.target.value })} placeholder="Optional internal note" />
                    </label>
                  </div>
                  {(quote.lines || []).length > 0 && (
                    <div className="qm-lpo-warning">
                      <strong>Ordered quotation lines</strong>
                      <p>Select the exact lines covered by this LPO. Parser suggestions are preselected for review; only saved selections appear as LPO provenance in price history. Corrections to confirmed mappings are audited.</p>
                      {(quote.lines || []).map((line) => {
                        const checked = (lpoDraft.applied_outcome_line_ids || []).includes(line.id);
                        return (
                          <label className="qm-checkbox" key={`lpo-line-${line.id}`}>
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => setLpoDraft((current) => ({
                                ...current,
                                applied_outcome_line_ids: checked
                                  ? (current.applied_outcome_line_ids || []).filter((id) => id !== line.id)
                                  : [...(current.applied_outcome_line_ids || []), line.id],
                              }))}
                            />
                            {line.item_name_snapshot || `Line ${line.id}`} ({line.quantity} {line.unit || ''})
                          </label>
                        );
                      })}
                    </div>
                  )}
                  <LpoWarningReview warnings={latestLpo.warnings} />
                  <button type="button" className="qm-secondary" disabled={lpoSaving} onClick={saveLpoDetails}>
                    {lpoSaving ? 'Saving LPO...' : 'Save LPO Details'}
                  </button>
                </>
              ) : (
                <div className="qm-empty compact">No LPO recorded yet. Upload or paste the customer LPO to unlock Proforma Tax Invoice download.</div>
              )}
            </div>
            <div className="qm-lpo-card">
              <span className="qm-step-kicker">Step 3</span>
              <h4>Download proforma</h4>
              <p>Uses the same official layout as the quotation, with Proforma Tax Invoice title, quote reference, LPO details, totals, signature and stamp.</p>
              <button type="button" className="qm-primary" disabled={!latestLpo || proformaDownloadLoading} onClick={downloadProforma}>
                {proformaDownloadLoading ? 'Preparing Proforma...' : 'Download Proforma Tax Invoice'}
              </button>
              <small>No email is sent. This only prepares the PDF for staff to review and share.</small>
            </div>
          </div>
        </div>
      )}
      <div className="qm-panel qm-party-panel">
        <div className="qm-panel-heading">
          <div>
            <h3>Customer & Contact</h3>
            <p>Select the customer company and the purchaser/contact shown on this quotation.</p>
          </div>
          {isEditable && (
            <button type="button" className="qm-primary" disabled={saving || Boolean(actionInFlight) || partyControlsBlocked || !hasUnsavedQuoteParty} onClick={saveQuoteParty}>
              {saving && hasUnsavedQuoteParty ? 'Saving...' : hasUnsavedQuoteParty ? 'Save Customer & Contact' : 'Saved'}
            </button>
          )}
        </div>
        <div className="qm-party-grid">
          <CompanySelectWithCreate
            companies={companiesForQuotePicker}
            value={quotePartyDraft.company}
            required
            disabled={!isEditable || saving || Boolean(actionInFlight) || companyControlBlocked}
            loading={companyDirectoryLoading}
            onChange={(companyId) => {
              updateQuotePartyDraft({ company: companyId, contact: '' });
              setContactForm(emptyContactForm);
              setShowContactForm(false);
              loadContactsForCompany(companyId);
            }}
            onCreated={(company) => {
              rememberCompany(company);
              updateQuotePartyDraft({ company: company.id, contact: '' });
              setContacts([]);
            }}
          />
          <div className="qm-contact-control">
            <label>
              <span className="qm-label-text">Contact / Purchaser</span>
              <select disabled={!isEditable || saving || Boolean(actionInFlight) || contactControlBlocked || !quotePartyDraft.company} value={quotePartyDraft.contact || ''} onChange={(event) => updateQuotePartyDraft({ contact: event.target.value })} aria-busy={companyContactsLoading || loadingContacts}>
                <option value="">{companyContactsLoading || loadingContacts ? 'Loading contacts...' : 'No contact'}</option>
                {contactsForQuoteCompany.map((contact) => <option key={contact.id} value={contact.id}>{contactOptionLabel(contact)}</option>)}
              </select>
            </label>
            {isEditable && (
              <button type="button" className="qm-secondary small" disabled={!quotePartyDraft.company || saving || Boolean(actionInFlight) || contactControlBlocked} onClick={() => setShowContactForm((value) => !value)}>
                {showContactForm ? 'Cancel new contact' : '+ Create contact'}
              </button>
            )}
          </div>
        </div>
        {showContactForm && isEditable && !contactControlBlocked && (
          <div className="qm-inline-card qm-contact-card">
            <label>Name<input required value={contactForm.name} onChange={(event) => setContactForm({ ...contactForm, name: event.target.value })} /></label>
            <label>Phone<input value={contactForm.phone} onChange={(event) => setContactForm({ ...contactForm, phone: event.target.value })} /></label>
            <label>Email<input type="email" value={contactForm.email} onChange={(event) => setContactForm({ ...contactForm, email: event.target.value })} /></label>
            <label>Position / Designation<input value={contactForm.role} onChange={(event) => setContactForm({ ...contactForm, role: event.target.value })} /></label>
            <label>Department<input value={contactForm.department} onChange={(event) => setContactForm({ ...contactForm, department: event.target.value })} /></label>
            <label className="qm-checkbox"><input type="checkbox" checked={contactForm.is_primary} onChange={(event) => setContactForm({ ...contactForm, is_primary: event.target.checked })} /> Primary contact</label>
            <button type="button" className="qm-primary" disabled={contactSaving || !contactForm.name.trim()} onClick={createQuoteContact}>
              {contactSaving ? 'Creating contact...' : 'Create and select contact'}
            </button>
          </div>
        )}
      </div>
      <div className="qm-panel qm-terms-panel">
        <div className="qm-panel-heading qm-terms-heading">
          <div>
            <h3>Quotation Terms &amp; Layout</h3>
            <p>Choose the customer-facing terms and columns used in the saved PDF and Excel quotation.</p>
          </div>
          {isEditable && (
            <button type="button" className="qm-primary" disabled={saving || Boolean(actionInFlight) || !hasUnsavedQuoteTerms} onClick={saveQuoteTerms}>
              {saving && hasUnsavedQuoteTerms ? 'Saving terms & layout...' : hasUnsavedQuoteTerms ? 'Save Terms & Layout' : 'Terms & Layout Saved'}
            </button>
          )}
        </div>
        <div className="qm-terms-fields">
          <label className="qm-terms-field">
            <span className="qm-label-text">Payment terms</span>
            <select disabled={!isEditable || saving || Boolean(actionInFlight)} value={quoteTermsDraft.payment_terms} onChange={(event) => updateQuoteTermDraft({ payment_terms: event.target.value })}>
              {paymentTermOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>
          <label className="qm-terms-field">
            <span className="qm-label-text">Valid until</span>
            <input disabled={!isEditable || saving || Boolean(actionInFlight)} type="date" value={quoteTermsDraft.valid_until || ''} onChange={(event) => updateQuoteTermDraft({ valid_until: event.target.value })} />
          </label>
          <label className="qm-terms-field qm-terms-toggle">
            <span className="qm-label-text">Optional columns</span>
            <span
              className="qm-terms-toggle-control"
              aria-disabled={!isEditable || saving || Boolean(actionInFlight)}
            >
              <input
                type="checkbox"
                aria-label="Show Brand column"
                disabled={!isEditable || saving || Boolean(actionInFlight)}
                checked={!!quoteTermsDraft.show_brand_column}
                onChange={(event) => updateQuoteTermDraft({ show_brand_column: event.target.checked })}
              />
              <span>Show Brand column</span>
            </span>
          </label>
        </div>
      </div>
      {lineFeedback && <div className={`qm-feedback ${lineFeedback.type}`}>{lineFeedback.message}</div>}
      {directFinalizeIssues.length > 0 && (
        <div className="qm-notice">
          <strong>
            {chainedActionsEnabled && reviewEmailIssues.length > 0
              ? 'Finalize and Review Email are blocked until:'
              : 'Finalize is blocked until:'}
          </strong>
          <ul>
            {directFinalizeIssues.slice(0, 5).map((issue) => <li key={issue}><button type="button" className="qm-link-button" onClick={() => setLineFilter('active')}>{issue}</button></li>)}
            {directFinalizeIssues.length > 5 && <li>{directFinalizeIssues.length - 5} more issue(s).</li>}
          </ul>
        </div>
      )}

      <div className="qm-panel">
        <div className="qm-panel-heading">
          <div>
            <h3>Step 4: Edit Quotation Lines</h3>
            <p>Each active line needs a Product decision, quantity, unit price, and VAT before finalization. Create draft/internal Products directly from unmatched lines.</p>
          </div>
          <div className="qm-final-total-area">
            <label className="qm-discount-field" htmlFor={`quotation-discount-${quote.id}`}>
              <span>Final discount ({quote.currency})</span>
              <input
                id={`quotation-discount-${quote.id}`}
                type="number"
                min="0"
                max={formatMoneyCents(liveTotalBeforeDiscountCents)}
                step="0.01"
                inputMode="decimal"
                aria-label={`Final discount (${quote.currency})`}
                disabled={!isEditable || !discountCurrencySupported || saving || Boolean(actionInFlight)}
                value={discountDraft}
                aria-invalid={Boolean(discountError)}
                aria-describedby={`quotation-discount-help-${quote.id}${discountError ? ` quotation-discount-error-${quote.id}` : ''}`}
                onWheel={releaseNumberWheelFocus}
                onChange={(event) => updateDiscountDraft(event.target.value)}
              />
              <small id={`quotation-discount-help-${quote.id}`}>
                {discountCurrencySupported
                  ? 'Optional amount deducted after VAT.'
                  : 'Final discounts are available only for AED quotations. This saved value is read-only.'}
              </small>
              {discountError && (
                <small id={`quotation-discount-error-${quote.id}`} className="qm-field-warning" role="alert">
                  {discountError}
                </small>
              )}
            </label>
            <div className="qm-total qm-total-breakdown" role="status" aria-live="polite" aria-atomic="true">
              <span>Subtotal {quote.currency} {formatMoneyCents(liveQuoteSubtotalCents)}</span>
              <span>VAT {quote.currency} {formatMoneyCents(liveQuoteVatCents)}</span>
              <span>Total before discount {quote.currency} {formatMoneyCents(liveTotalBeforeDiscountCents)}</span>
              {liveDiscountCents > BIGINT_ZERO && (
                <span>Discount {quote.currency} -{formatMoneyCents(liveDiscountCents)}</span>
              )}
              <strong>Final total {quote.currency} {formatMoneyCents(liveFinalTotalCents)}</strong>
            </div>
          </div>
        </div>
        {isEditable && (
          <div className="qm-save-row sticky-line-actions">
            <span className={hasUnsavedLineOrDiscount ? 'qm-unsaved' : 'qm-saved'}>
              {hasUnsavedLines && hasUnsavedDiscount
                ? `${changedLineIds.length} unsaved line change(s) and an unsaved discount`
                : hasUnsavedLines
                  ? `${changedLineIds.length} unsaved line change(s)`
                  : hasUnsavedDiscount
                    ? 'Final discount is unsaved'
                    : 'All line changes saved'}
            </span>
            <span className="qm-sticky-total">
              Final total <strong>{quote.currency} {formatMoneyCents(liveFinalTotalCents)}</strong>
            </span>
            <select className="qm-input compact" value={lineFilter} onChange={(event) => setLineFilter(event.target.value)}>
              <option value="active">Active lines</option>
              <option value="unmatched">Unmatched</option>
              <option value="needs_review">Needs review</option>
              <option value="ready">Ready</option>
              <option value="skipped">Skipped</option>
              <option value="all">All lines</option>
            </select>
            <button type="button" className="qm-secondary small" disabled={productCatalogueBlocked} onClick={selectVisibleUnmatched}>Select visible unmatched</button>
            <button type="button" className="qm-secondary small" disabled={productCatalogueBlocked || !selectedUnmatchedLines.length} onClick={() => openCreateProductModal(selectedUnmatchedLines.map((line) => line.id))}>Create Products for Selected Unmatched Rows</button>
            <button type="button" className="qm-primary" disabled={saving || Boolean(actionInFlight) || !hasUnsavedLineOrDiscount || Boolean(discountError)} onClick={saveAllLines}>
              {saving && hasUnsavedLineOrDiscount
                ? 'Saving...'
                : hasUnsavedDiscount
                  ? 'Save Quotation Changes'
                  : 'Save All Lines'}
            </button>
            <span className="qm-sticky-action-divider" aria-hidden="true" />
            {renderDraftCompletionActions()}
            {!['revised', 'cancelled'].includes(quote.status) && (
              <button type="button" className="qm-secondary danger" disabled={saving || Boolean(actionInFlight)} onClick={() => runAction('Cancel', quotationAPI.quotes.cancel)}>
                {actionInFlight === 'Cancel' ? 'Cancelling...' : 'Cancel'}
              </button>
            )}
          </div>
        )}

        <div className="qm-table-wrap">
          <datalist id="quotation-unit-suggestions">
            {unitSuggestions.map((unit) => <option key={unit} value={unit} />)}
          </datalist>
          {companyPricingVisible && <div className="qm-price-legend"><span className="history">◷ Historical price</span><span>✎ Manual price</span><span className="review">△ Needs review</span><span>Click the icon for details</span></div>}
          <table className={`qm-table line-table${quoteTermsDraft.show_brand_column ? ' with-brand' : ''}`}>
            <thead>
              <tr>
                <th className="qm-check-cell"><input type="checkbox" checked={filteredLines.length > 0 && filteredLines.every((line) => selectedLineIds.includes(line.id))} onChange={() => {
                  const visibleIds = filteredLines.map((line) => line.id);
                  setSelectedLineIds((current) => visibleIds.every((id) => current.includes(id)) ? current.filter((id) => !visibleIds.includes(id)) : Array.from(new Set([...current, ...visibleIds])));
                }} /></th>
                <th className="qm-serial-cell">#</th>
                <th className="qm-line-product-cell">Matched Item <span className="qm-required">*</span></th>
                <th className="qm-line-snapshot-cell">Snapshot Name <span className="qm-required">*</span></th>
                {quoteTermsDraft.show_brand_column && <th className="qm-line-brand-cell">Brand</th>}
                <th className="qm-line-quantity-cell">Qty <span className="qm-required">*</span></th>
                <th className="qm-line-unit-cell">Unit</th>
                <th className="qm-price-cell">Unit Price <span className="qm-required">*</span></th>
                <th className="qm-vat-cell">VAT % <span className="qm-required">*</span></th>
                <th className="qm-line-status-cell">Status</th>
                <th className="qm-line-total-cell">Total</th>
                <th className="qm-line-actions-cell">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredLines.map((line, lineIndex) => {
                const draft = lineDrafts[line.id] || {};
                const isDirty = !draftsMatch(draft, savedLineDrafts[line.id]);
                const statusInfo = derivedLineStatus(line);
                const candidatePriceHint = linePriceHints[line.id];
                const priceHint = candidatePriceHint
                  && String(candidatePriceHint.product || '') === String(draft.product || '')
                  ? candidatePriceHint
                  : null;
                const cachedPriceContext = priceContexts[String(draft.product)];
                const priceHistoryContext = cachedPriceContext
                  && String(cachedPriceContext.product || '') === String(draft.product || '')
                  ? cachedPriceContext
                  : priceHint;
                return (
                  <tr key={line.id}>
                    <td className="qm-check-cell"><input type="checkbox" checked={selectedLineIds.includes(line.id)} onChange={() => toggleLineSelection(line.id)} /></td>
                    <td className="qm-serial-cell">{lineIndex + 1}</td>
                    <td className="qm-line-product-cell">
                      {isEditable ? (
                        <ProductSelect
                          key={`${quote.id}-${line.id}`}
                          catalogue={productCatalogue}
                          value={draft.product}
                          fallbackName={line.product_name || line.matched_product_name || draft.item_name_snapshot}
                          label={`Product for ${lineLabel(line, draft)}`}
                          searchLabel={`Search products for ${lineLabel(line, draft)}`}
                          loading={productCatalogueLoading}
                          disabled={productCatalogueBlocked}
                          allowCreate
                          onChange={(productId) => handleLineProductChange(line, productId)}
                        />
                      ) : (
                        <span className="qm-line-product-name">
                          {draft.product
                            ? line.product_name || line.matched_product_name || draft.item_name_snapshot
                              || productCatalogue.byId.get(String(draft.product))?.name || `Product ${draft.product}`
                            : 'Unmatched'}
                        </span>
                      )}
                    </td>
                    <td className="qm-line-snapshot-cell"><input disabled={!isEditable} value={draft.item_name_snapshot || ''} onChange={(event) => updateLineDraft(line.id, { item_name_snapshot: event.target.value })} /></td>
                    {quoteTermsDraft.show_brand_column && (
                      <td className="qm-line-brand-cell">
                        <input
                          aria-label={`Brand for ${lineLabel(line, draft)}`}
                          disabled={!isEditable}
                          maxLength={200}
                          placeholder="Brand"
                          value={draft.brand_name_snapshot || ''}
                          onChange={(event) => updateLineDraft(line.id, { brand_name_snapshot: event.target.value })}
                        />
                      </td>
                    )}
                    <td className="qm-line-quantity-cell"><input aria-label={`Quantity for ${lineLabel(line, draft)}`} disabled={!isEditable} type="number" min="0" step="0.001" value={draft.quantity || ''} onWheel={releaseNumberWheelFocus} onChange={(event) => updateLineDraft(line.id, { quantity: event.target.value })} /></td>
                    <td className="qm-line-unit-cell">
                      <input
                        className="qm-unit-input"
                        aria-label={`Unit for ${lineLabel(line, draft)}`}
                        disabled={!isEditable}
                        list="quotation-unit-suggestions"
                        inputMode="text"
                        placeholder="Enter unit"
                        value={draft.unit || ''}
                        onKeyDown={preventUnitNumberKey}
                        onChange={(event) => changeLineUnit(line, sanitizeUnitText(event.target.value))}
                        onBlur={() => companyPricingEnabled && refreshLinePrice(line)}
                      />
                    </td>
                    <td className="qm-price-cell">
                      {companyPricingVisible ? <CompanyPriceField
                        draft={draft}
                        recommendation={priceHint?.recommendation || priceHistoryContext?.line_recommendations?.[line.id] || priceHistoryContext?.recommendations_by_unit?.[draft.unit]}
                        loading={priceHistoryLoading || priceHint?.mode === 'loading'}
                        failed={Boolean(priceContextError) || priceHint?.mode === 'error'}
                        onRetry={() => refreshLinePrice(line)} onPatch={(patch) => updateLineDraft(line.id, patch)}
                        onWrongProduct={() => wrongProduct(line, draft)}
                        onHistory={() => setPriceHistoryDialog({ productId: draft.product, productName: line.product_name || priceHint?.product_name || '' })}
                        inputRef={(node) => assignPriceInputRef(line.id, node)}
                        aria-label={`Unit price for ${lineLabel(line, draft)}`} disabled={!isEditable}
                        type="number" min="0" step="0.001" onWheel={releaseNumberWheelFocus}
                        onKeyDown={(event) => moveToNextBlankPrice(event, line.id)}
                      /> : <>
                      <input
                        ref={(node) => assignPriceInputRef(line.id, node)}
                        aria-label={`Unit price for ${lineLabel(line, draft)}`}
                        disabled={!isEditable}
                        type="number"
                        min="0"
                        step="0.001"
                        value={draft.unit_price || ''}
                        onWheel={releaseNumberWheelFocus}
                        onKeyDown={(event) => moveToNextBlankPrice(event, line.id)}
                        onChange={(event) => updateLineDraft(line.id, { unit_price: event.target.value })}
                      />
                      {draft.product && (
                        <span className={`qm-price-hint ${priceHint?.mode || 'on-demand'}`}>
                          {priceHint ? priceHintText(priceHint) : priceHistoryLoading ? 'Loading price history...' : 'Price history available on demand'}
                          <button type="button" onClick={() => setPriceHistoryDialog({
                            productId: draft.product,
                            productName: priceHint?.product_name || items.find((item) => String(item.id) === String(draft.product))?.name || '',
                            context: priceHistoryContext,
                          })}>View price history</button>
                        </span>
                      )}
                      </>}
                    </td>
                    <td className="qm-vat-cell">
                      <select className="qm-vat-select" disabled={!isEditable} value={draft.vat_rate || '0'} onChange={(event) => updateLineDraft(line.id, { vat_rate: event.target.value })}>
                        <option value="0">0%</option>
                        <option value="5">5%</option>
                      </select>
                    </td>
                    <td className="qm-line-status-cell"><span className={`qm-line-status ${statusInfo.id}`}>{statusInfo.label}</span></td>
                    <td className="qm-line-total-cell">{quote.currency} {formatMoneyCents(lineMoneyForDraft(draft).totalCents)}</td>
                    <td className="qm-row-actions qm-line-actions-cell">
                      <span className={isDirty ? 'qm-line-state unsaved' : 'qm-line-state saved'}>{isDirty ? 'Unsaved' : 'Saved'}</span>
                      <button type="button" className="qm-secondary small" disabled={!isEditable || saving || actionInFlight || !isDirty || Boolean(discountError)} onClick={() => saveLine(line.id)}>Save</button>
                      <div className="qm-line-image-tools">
                        <label className={`qm-line-image-toggle ${draft.include_product_image ? 'enabled' : ''}`}>
                          <input
                            type="checkbox"
                            disabled={!isEditable || !draft.product || !draft.has_product_image}
                            checked={!!draft.include_product_image}
                            onChange={(event) => updateLineDraft(line.id, { include_product_image: event.target.checked })}
                          />
                          Image in PDF
                        </label>
                        <label className={`qm-secondary small qm-image-upload ${!isEditable || !draft.product || saving || actionInFlight ? 'disabled' : ''}`}>
                          Upload
                          <input
                            type="file"
                            accept="image/png,image/jpeg,image/webp"
                            disabled={!isEditable || !draft.product || saving || actionInFlight}
                            onChange={(event) => {
                              const file = event.target.files?.[0];
                              event.target.value = '';
                              uploadImageForLine(line.id, file);
                            }}
                          />
                        </label>
                      </div>
                      <button type="button" className="qm-secondary small danger" disabled={!isEditable || saving || actionInFlight} onClick={() => deleteLine(line.id)}>Delete</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {isEditable && (
          <form onSubmit={addLine} className={`qm-add-line${quoteTermsDraft.show_brand_column ? ' with-brand' : ''}`}>
            <ProductSelect
              key={quote.id}
              catalogue={productCatalogue}
              value={lineForm.product}
              fallbackName={lineForm.item_name_snapshot}
              label="Product for new line"
              searchLabel="Search products for new line"
              placeholder="Select item"
              disabled={productCatalogueBlocked}
              loading={productCatalogueLoading}
              onChange={handleLineFormProductChange}
            />
            <input placeholder="Snapshot name" required value={lineForm.item_name_snapshot} onChange={(event) => setLineForm({ ...lineForm, item_name_snapshot: event.target.value })} />
            {quoteTermsDraft.show_brand_column && (
              <input
                aria-label="Brand"
                maxLength={200}
                placeholder="Brand"
                value={lineForm.brand_name_snapshot}
                onChange={(event) => setLineForm({ ...lineForm, brand_name_snapshot: event.target.value })}
              />
            )}
            <input aria-label="Qty" type="number" min="0" step="0.001" value={lineForm.quantity} onWheel={releaseNumberWheelFocus} onChange={(event) => setLineForm({ ...lineForm, quantity: event.target.value })} />
            <input
              aria-label="Unit"
              placeholder="Unit"
              list="quotation-unit-suggestions"
              inputMode="text"
              value={lineForm.unit}
              onKeyDown={preventUnitNumberKey}
              onChange={(event) => {
                lineFormPriceVersionRef.current += 1;
                setLineForm((current) => ({ ...current, unit: sanitizeUnitText(event.target.value),
                  ...(companyPricingEnabled ? { unit_price: current.price_provenance?.kind === 'history' ? '' : current.unit_price,
                    price_source_history: null, price_original_history: null, price_provenance: {}, price_reviewed: false,
                    price_context_changed: !isBlankPrice(current.unit_price) && current.price_provenance?.kind !== 'history',
                    price_review_required: !isBlankPrice(current.unit_price) && current.price_provenance?.kind !== 'history' } : {}) }));
              }}
              onBlur={async () => {
                if (!companyPricingEnabled || !lineForm.product || hasUnsavedQuoteParty) return;
                const version = lineFormPriceVersionRef.current;
                const generation = priceContextGenerationRef.current;
                const context = await maybeFetchProductPrice(lineForm.product, lineForm.unit, lineForm.item_name_snapshot, true);
                if (generation !== priceContextGenerationRef.current || version !== lineFormPriceVersionRef.current) return;
                setLineForm((current) => ({ ...current, price_recommendation: context?.recommendation,
                  ...(context?.recommendation?.eligible && priceShouldAutofill(current) ? historyPricePatch(context.recommendation) : {}) }));
              }}
            />
            {companyPricingVisible ? <CompanyPriceField draft={lineForm} recommendation={lineForm.price_recommendation}
              type="number" min="0" step="0.001" placeholder="Price" aria-label="Price"
              onPatch={(patch) => { lineFormPriceVersionRef.current += 1; setLineForm((current) => ({ ...current, ...patch })); }}
            /> : <input type="number" min="0" step="0.001" placeholder="Price" value={lineForm.unit_price} onWheel={releaseNumberWheelFocus} onChange={(event) => {
              lineFormPriceVersionRef.current += 1;
              setLineForm({ ...lineForm, unit_price: event.target.value });
            }} />}
            <select value={lineForm.vat_rate} onChange={(event) => setLineForm({ ...lineForm, vat_rate: event.target.value })}>
              <option value="0">VAT 0%</option>
              <option value="5">VAT 5%</option>
            </select>
            <button type="submit" className="qm-primary" disabled={saving}>Add Line</button>
          </form>
        )}
      </div>

      {productCreateModal && (
        <div className="qm-modal-backdrop" role="presentation">
          <div className="qm-modal" role="dialog" aria-modal="true" aria-label="Create Products from quotation lines">
            <div className="qm-panel-heading">
              <div>
                <h3>Create Products from unmatched rows</h3>
                <p>The catalog is checked first. Exact matches are reused automatically; similar matches must be reviewed before a new internal Product is created.</p>
              </div>
              <button type="button" className="qm-secondary small" disabled={saving} onClick={closeCreateProductModal}>Close</button>
            </div>
            <QuotationErrorNotice error={productCreateError} onDismiss={() => setProductCreateError(null)} />
            <div className="qm-table-wrap">
              <table className="qm-table">
                <thead>
                  <tr>
                    <th>Line</th>
                    <th>Product name to create/link</th>
                  </tr>
                </thead>
                <tbody>
                  {productCreateModal.lineIds.map((lineId) => {
                    const line = activeLines.find((candidate) => candidate.id === lineId);
                    const warning = productCreateModal.confirmations?.[lineId];
                    return (
                      <tr key={lineId}>
                        <td>{line ? lineLabel(line, lineDrafts[lineId]) : `Line ${lineId}`}</td>
                        <td>
                          <input
                            value={productCreateModal.names[lineId] || ''}
                            disabled={saving}
                            onChange={(event) => setProductCreateModal((current) => {
                              if (!current) return current;
                              const confirmations = { ...current.confirmations };
                              delete confirmations[lineId];
                              return {
                                ...current,
                                names: { ...current.names, [lineId]: event.target.value },
                                confirmations,
                              };
                            })}
                          />
                          {warning && (
                            <div className={`qm-product-match-warning ${warning.creation_blocked ? 'blocked' : ''}`}>
                              <strong>{warning.creation_blocked ? 'Existing identity conflict — select a Product' : 'Likely existing Product found'}</strong>
                              <p>{warning.warning || warning.match_reason}</p>
                              {(warning.candidates || []).length > 0 && (
                                <div className="qm-product-candidate-list">
                                  {(warning.candidates || []).map((candidate) => (
                                    <button
                                      type="button"
                                      className="qm-product-candidate"
                                      key={candidate.product_id}
                                      disabled={saving}
                                      onClick={() => linkCandidateFromCreateModal(lineId, candidate)}
                                    >
                                      <span>Use {candidate.product_name}</span>
                                      <small>
                                        {Math.round(Number(candidate.confidence || candidate.score || 0) * 100)}% match
                                        {candidate.dosage ? ` · ${candidate.dosage}` : ''}
                                        {candidate.pack_size ? ` · ${candidate.pack_size}` : ''}
                                      </small>
                                    </button>
                                  ))}
                                </div>
                              )}
                              {!warning.creation_blocked && <label><input type="checkbox" checked={!!productCreateModal.reviewed?.[lineId]} onChange={(event) => setProductCreateModal((current) => ({ ...current, reviewed: { ...current.reviewed, [lineId]: event.target.checked } }))} /> I checked these candidates; this is a different item. Create it provisionally for owner review.</label>}
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="qm-action-row">
              <button
                type="button"
                className="qm-primary"
                disabled={saving || (hasProductCreationWarnings && !canOverrideProductCreationWarning)}
                onClick={() => confirmCreateProducts(hasProductCreationWarnings)}
              >
                {saving ? 'Checking catalog...' : hasProductCreationWarnings ? 'Create reviewed provisional items' : 'Check catalog and continue'}
              </button>
              <button type="button" className="qm-secondary" disabled={saving} onClick={closeCreateProductModal}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {emailPreviewOpen && (
        <QuotationEmailPreviewDialog
          preview={emailPreview}
          loading={emailPreviewLoading}
          previewError={emailPreviewError}
          sending={emailSending}
          sendError={emailSendError}
          quoteIsFinalized={emailQuoteFinalized || ['finalized', 'sent'].includes(quote.status)}
          threadCandidates={emailThreadCandidates}
          threadCandidatesLoading={emailThreadCandidatesLoading}
          threadCandidatesError={emailThreadCandidatesError}
          threadSearchCompleted={emailThreadSearchCompleted}
          gmailReconnectError={emailGmailReconnectError}
          reconciling={emailReconciling}
          reconcileFeedback={emailReconcileFeedback}
          onRetryPreview={emailPreviewRefreshRequired ? refreshEmailPreview : loadEmailPreview}
          onRefreshPreview={refreshEmailPreview}
          onReconnectGmail={reconnectGmailForSending}
          onReconcileEmail={reconcileQuotationEmail}
          onClearCorrectableError={clearCorrectableEmailError}
          onFindThread={findEmailThreadCandidates}
          onSelectThread={selectEmailThreadCandidate}
          onClearThreadCandidates={clearEmailThreadCandidates}
          onClose={closeEmailPreview}
          onFinalizeOnly={finalizeOnlyFromPreview}
          onSend={sendQuotationEmail}
        />
      )}

      {priceHistoryDialog && (
        <ProductPriceHistoryDialog
          quoteId={quote.id}
          productId={priceHistoryDialog.productId}
          productName={priceHistoryDialog.productName}
          initialContext={priceHistoryDialog.context}
          onClose={() => setPriceHistoryDialog(null)}
        />
      )}

      <div className="bottom-panels">
        <AuditLogPanel quotationId={quote.id} />
      </div>
    </div>
  );
};

export default QuotationEditor;
