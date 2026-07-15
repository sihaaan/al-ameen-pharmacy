import React, { useCallback, useEffect, useRef, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import ProductPriceHistoryDialog from './ProductPriceHistoryDialog';
import AuditLogPanel from './AuditLogPanel';
import QuotationErrorNotice from './QuotationErrorNotice';
import CompanySelectWithCreate from './CompanySelectWithCreate';

const editableStatuses = new Set(['draft', 'pending_review', 'approved']);
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
  description: String(draft.description || ''),
  quantity: String(draft.quantity || ''),
  unit: String(draft.unit || ''),
  unit_price: String(draft.unit_price || ''),
  vat_rate: normalizeVatRate(draft.vat_rate),
  match_status: String(draft.match_status || 'unresolved'),
  include_product_image: !!draft.include_product_image,
  product_image: String(draft.product_image || ''),
  notes: String(draft.notes || ''),
});

const draftsMatch = (left, right) => JSON.stringify(normalizeDraft(left)) === JSON.stringify(normalizeDraft(right));

const draftFromLine = (line) => ({
  product: line.product || '',
  item_name_snapshot: line.item_name_snapshot || '',
  description: line.description || '',
  quantity: line.quantity || '1',
  unit: line.unit || '',
  unit_price: line.unit_price || '',
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
});

const partyDraftFromQuote = (quote = {}) => ({
  company: quote.company || '',
  contact: quote.contact || '',
});

const termsDraftsMatch = (left = {}, right = {}) => (
  String(left.payment_terms || '') === String(right.payment_terms || '') &&
  String(left.valid_until || '') === String(right.valid_until || '')
);

const partyDraftsMatch = (left = {}, right = {}) => (
  String(left.company || '') === String(right.company || '') &&
  String(left.contact || '') === String(right.contact || '')
);

const releaseNumberWheelFocus = (event) => {
  event.preventDefault();
  event.currentTarget.blur();
};

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

const QuotationEditor = ({ quoteId, onClose, onReviewOutcome }) => {
  const [quote, setQuote] = useState(null);
  const [companies, setCompanies] = useState([]);
  const [contacts, setContacts] = useState([]);
  const [loadingContacts, setLoadingContacts] = useState(false);
  const [quotePartyDraft, setQuotePartyDraft] = useState(partyDraftFromQuote());
  const [savedQuotePartyDraft, setSavedQuotePartyDraft] = useState(partyDraftFromQuote());
  const [quoteTermsDraft, setQuoteTermsDraft] = useState(termsDraftFromQuote());
  const [savedQuoteTermsDraft, setSavedQuoteTermsDraft] = useState(termsDraftFromQuote());
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
  const [selectedLineIds, setSelectedLineIds] = useState([]);
  const [lineFilter, setLineFilter] = useState('active');
  const [productCreateModal, setProductCreateModal] = useState(null);
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
  const linePriceVersionRef = useRef({});
  const lineSelectedProductRef = useRef({});
  const lineFormPriceVersionRef = useRef(0);
  const lineFormSelectedProductRef = useRef('');
  const priceContextGenerationRef = useRef(0);

  const setLoadedQuote = useCallback((quoteData) => {
    setQuote(quoteData);
    const nextPartyDraft = partyDraftFromQuote(quoteData);
    setQuotePartyDraft(nextPartyDraft);
    setSavedQuotePartyDraft(nextPartyDraft);
    const nextTermsDraft = termsDraftFromQuote(quoteData);
    setQuoteTermsDraft(nextTermsDraft);
    setSavedQuoteTermsDraft(nextTermsDraft);
    const drafts = Object.fromEntries((quoteData.lines || []).map((line) => [line.id, draftFromLine(line)]));
    setLineDrafts(drafts);
    setSavedLineDrafts(drafts);
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

  const load = useCallback(async () => {
    setLoading(true);
    setErrorInfo(null);
    setPriceContextError(null);
    try {
      const quoteRes = await quotationAPI.quotes.retrieve(quoteId);
      const productIds = Array.from(new Set(
        (quoteRes.data.lines || []).map((line) => line.product).filter(Boolean).map(String)
      ));
      const [itemsRes, companyItemsRes, companiesRes, contactsRes, lposRes] = await Promise.all([
        quotationAPI.items.list({ active: 'true' }),
        quotationAPI.items.list({ active: 'true', company_used: quoteRes.data.company }),
        quotationAPI.companies.list({ active: 'true' }),
        quoteRes.data.company
          ? quotationAPI.contacts.list({ company: quoteRes.data.company, active: 'true' })
          : Promise.resolve({ data: [] }),
        quotationAPI.quotes.lpos(quoteId),
      ]);
      setLoadedQuote(quoteRes.data);
      setItems(itemsRes.data);
      setCompanyItems(companyItemsRes.data);
      setCompanies(companiesRes.data);
      setContacts(contactsRes.data);
      syncLpos(lposRes.data);

      // Price history is useful context, but it is not required to edit a quote.
      // Load it after the core editor data so a transient batch failure cannot
      // turn an otherwise healthy quotation into a blank/error screen.
      const requestGeneration = priceContextGenerationRef.current;
      if (productIds.length) {
        void (async () => {
          const contextRequests = [];
          for (let index = 0; index < productIds.length; index += 100) {
            contextRequests.push(quotationAPI.quotes.productPrices(quoteId, {
              products: productIds.slice(index, index + 100).join(','),
              history_limit: 10,
            }));
          }
          const results = await Promise.allSettled(contextRequests);
          if (priceContextGenerationRef.current !== requestGeneration) return;

          const successfulResponses = results
            .filter((result) => result.status === 'fulfilled')
            .map((result) => result.value);
          const nextPriceContexts = Object.assign(
            {},
            ...successfulResponses.map((response) => response.data?.results || {})
          );
          setPriceContexts(nextPriceContexts);
          setLinePriceHints(Object.fromEntries(
            (quoteRes.data.lines || [])
              .filter((line) => line.product && nextPriceContexts[String(line.product)])
              .map((line) => {
                const context = nextPriceContexts[String(line.product)];
                return [line.id, { ...context, mode: context.latest_quoted ? 'history_found' : 'no_history' }];
              })
          ));

          const failedResult = results.find((result) => result.status === 'rejected');
          if (failedResult) {
            const details = await describeQuotationError(
              failedResult.reason,
              'Load price history previews',
              `GET /quotations/quotes/${quoteId}/product_prices/`
            );
            if (priceContextGenerationRef.current !== requestGeneration) return;
            setPriceContextError(details);
            console.error(formatQuotationError(details), failedResult.reason);
          }
        })();
      }
    } catch (error) {
      const details = await describeQuotationError(error, 'Load quotation', `GET /quotations/quotes/${quoteId}/, GET /quotations/items/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setLoading(false);
    }
  }, [quoteId, setLoadedQuote, syncLpos]);

  useEffect(() => {
    load();
  }, [load]);

  const isEditable = quote && editableStatuses.has(quote.status);
  const activeLines = quote?.lines || [];
  const changedLineIds = quote ? (quote.lines || [])
    .filter((line) => !draftsMatch(lineDrafts[line.id], savedLineDrafts[line.id]))
    .map((line) => line.id) : [];
  const hasUnsavedLines = changedLineIds.length > 0;
  const hasUnsavedQuoteParty = !partyDraftsMatch(quotePartyDraft, savedQuotePartyDraft);
  const hasUnsavedQuoteTerms = !termsDraftsMatch(quoteTermsDraft, savedQuoteTermsDraft);
  const contactsForQuoteCompany = contacts;

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

  const lineLabel = (line, draft = {}) => draft.item_name_snapshot || line.inquiry_line_raw_name || line.item_name_snapshot || `Line ${line.sort_order + 1}`;

  const productOptionsForDraft = (draft = {}) => {
    const companyProductIds = new Set(companyItems.map((item) => String(item.id)));
    const byId = new Map(items.map((item) => [String(item.id), item]));
    if (draft.product && !byId.has(String(draft.product))) {
      const selected = items.find((item) => String(item.id) === String(draft.product));
      if (selected) byId.set(String(selected.id), selected);
    }
    return Array.from(byId.values()).sort((a, b) => {
      const aUsed = companyProductIds.has(String(a.id));
      const bUsed = companyProductIds.has(String(b.id));
      if (aUsed !== bUsed) return aUsed ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
  };

  const renderProductOptions = (draft = {}) => {
    const companyProductIds = new Set(companyItems.map((item) => String(item.id)));
    const options = productOptionsForDraft(draft);
    const previouslyUsed = options.filter((item) => companyProductIds.has(String(item.id)));
    const remaining = options.filter((item) => !companyProductIds.has(String(item.id)));
    return (
      <>
        {previouslyUsed.length > 0 && (
          <optgroup label="Previously quoted for this customer">
            {previouslyUsed.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </optgroup>
        )}
        {remaining.length > 0 && (
          <optgroup label="All other Products">
            {remaining.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </optgroup>
        )}
      </>
    );
  };

  const lineTotalForDraft = (draft = {}) => {
    if (draft.match_status === 'ignored') return 0;
    const quantity = Number(draft.quantity || 0);
    const unitPrice = Number(draft.unit_price || 0);
    const vatRate = Number(draft.vat_rate || 0);
    const subtotal = quantity * unitPrice;
    return Number.isFinite(subtotal) ? subtotal * (1 + (Number.isFinite(vatRate) ? vatRate : 0) / 100) : 0;
  };
  const liveLineDraftFor = (line) => ({ ...line, ...(lineDrafts[line.id] || {}) });
  const liveQuoteTotal = activeLines.reduce((sum, line) => sum + lineTotalForDraft(liveLineDraftFor(line)), 0);

  const derivedLineStatus = (line) => {
    const draft = lineDrafts[line.id] || {};
    if (draft.match_status === 'ignored') return { id: 'skipped', label: 'Skipped' };
    if (!draft.product) return { id: 'unmatched', label: 'Unmatched' };
    if (!draft.quantity || Number(draft.quantity) <= 0 || !draft.unit_price || Number(draft.unit_price) <= 0) {
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

  const selectedLines = activeLines.filter((line) => selectedLineIds.includes(line.id));
  const selectedUnmatchedLines = selectedLines.filter((line) => derivedLineStatus(line).id === 'unmatched');

  const finalizeIssues = (() => {
    if (!quote || !['draft', 'pending_review', 'approved'].includes(quote.status)) return [];
    const issues = [];
    if (!quote.lines?.length) issues.push('Add at least one quotation line.');
    if (hasUnsavedQuoteParty) issues.push('Save customer/contact before finalizing.');
    if (hasUnsavedQuoteTerms) issues.push('Save quotation terms before finalizing.');
    if (hasUnsavedLines) issues.push('Save all line changes before finalizing.');
    (quote.lines || []).forEach((line, index) => {
      const draft = lineDrafts[line.id] || {};
      const name = draft.item_name_snapshot || `Line ${index + 1}`;
      if (draft.match_status !== 'ignored') {
        if (!draft.product) issues.push(`${name}: select or create a Product.`);
        if (!draft.quantity || Number(draft.quantity) <= 0) issues.push(`${name}: enter a valid quantity.`);
        if (!draft.unit_price || Number(draft.unit_price) <= 0) issues.push(`${name}: enter a valid unit price.`);
      }
    });
    return issues;
  })();

  const updateLineDraft = (lineId, patch) => {
    setLineFeedback(null);
    const affectsPriceRequest = Object.prototype.hasOwnProperty.call(patch, 'unit_price') || Object.prototype.hasOwnProperty.call(patch, 'product');
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
    setQuoteTermsDraft((current) => ({ ...current, ...patch }));
  };

  const updateQuotePartyDraft = (patch) => {
    setLineFeedback(null);
    setQuotePartyDraft((current) => ({ ...current, ...patch }));
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
    return {
      product: productId,
      item_name_snapshot: item ? item.name : draft.item_name_snapshot,
      unit: draft.unit || sanitizeUnitText(item?.unit || ''),
      match_status: productId ? 'confirmed' : 'unresolved',
      product_image: '',
      product_image_url: item?.primary_image_url || '',
      has_product_image: !!item?.primary_image_url,
      include_product_image: false,
    };
  };

  const priceShouldAutofill = (draft) => !draft.unit_price || Number(draft.unit_price) <= 0;

  const setPriceHintForLine = (lineId, suggestion, mode) => {
    setLinePriceHints((current) => ({
      ...current,
      [lineId]: {
        ...suggestion,
        mode,
      },
    }));
  };

  const maybeFetchProductPrice = async (productId) => {
    if (!quote?.id || !productId) return null;
    const cached = priceContexts[String(productId)];
    if (cached) return cached;
    try {
      const response = await quotationAPI.quotes.productPrice(quote.id, { product: productId });
      setPriceContexts((current) => ({ ...current, [String(productId)]: response.data }));
      return response.data;
    } catch (error) {
      const details = await describeQuotationError(error, 'Load company Product price', `GET /quotations/quotes/${quote.id}/product_price/?product=${productId}`);
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

    const suggestion = await maybeFetchProductPrice(productId);
    if (!suggestion) return;
    if (priceContextGenerationRef.current !== requestGeneration || lineSelectedProductRef.current[line.id] !== String(productId)) return;
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
    const suggestion = await maybeFetchProductPrice(productId);
    if (!suggestion || priceContextGenerationRef.current !== requestGeneration || lineFormSelectedProductRef.current !== String(productId)) return;
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

  const payloadForLine = (draft) => ({
    ...draft,
    product: draft.product || null,
    product_image: draft.product_image || null,
    include_product_image: !!draft.include_product_image,
    unit_price: draft.unit_price || null,
    match_status: draft.product && draft.match_status === 'unresolved' ? 'confirmed' : draft.match_status,
  });

  const mergeSavedQuote = (quoteData, savedIds = []) => {
    const savedSet = new Set(savedIds);
    setQuote(quoteData);
    setLineDrafts((current) => {
      const next = {};
      (quoteData.lines || []).forEach((line) => {
        next[line.id] = savedSet.has(line.id) ? draftFromLine(line) : (current[line.id] || draftFromLine(line));
      });
      return next;
    });
    setSavedLineDrafts((current) => {
      const next = {};
      (quoteData.lines || []).forEach((line) => {
        next[line.id] = savedSet.has(line.id) ? draftFromLine(line) : (current[line.id] || draftFromLine(line));
      });
      return next;
    });
  };

  const saveLine = async (lineId) => {
    if (saving || actionInFlight) return;
    setSaving(true);
    setLineFeedback(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.quotes.bulkUpdateLines(quote.id, {
        lines: [{ id: lineId, ...payloadForLine(lineDrafts[lineId]) }],
      });
      mergeSavedQuote(response.data.quotation, [lineId]);
      setLineFeedback({ type: 'success', message: 'Line saved.' });
    } catch (error) {
      const details = await describeQuotationError(error, 'Save quote line', `PATCH /quotations/quote-lines/${lineId}/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const saveAllLines = async () => {
    if (saving || actionInFlight || !changedLineIds.length) return;
    setSaving(true);
    setLineFeedback(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.quotes.bulkUpdateLines(quote.id, {
        lines: changedLineIds.map((lineId) => ({ id: lineId, ...payloadForLine(lineDrafts[lineId]) })),
      });
      mergeSavedQuote(response.data.quotation, changedLineIds);
      setLineFeedback({ type: 'success', message: `Saved ${changedLineIds.length} line${changedLineIds.length === 1 ? '' : 's'}.` });
    } catch (error) {
      const details = await describeQuotationError(error, 'Save all quote lines', 'PATCH /quotations/quote-lines/{id}/');
      setErrorInfo(details);
      setLineFeedback({ type: 'error', message: 'Some line changes could not be saved.' });
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const saveQuoteTerms = async () => {
    if (saving || actionInFlight || !hasUnsavedQuoteTerms) return;
    setSaving(true);
    setLineFeedback(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.quotes.update(quote.id, {
        payment_terms: quoteTermsDraft.payment_terms || 'as_per_agreement',
        valid_until: quoteTermsDraft.valid_until || null,
      });
      setQuote(response.data);
      const nextTermsDraft = termsDraftFromQuote(response.data);
      setQuoteTermsDraft(nextTermsDraft);
      setSavedQuoteTermsDraft(nextTermsDraft);
      setLineFeedback({ type: 'success', message: 'Quotation terms saved.' });
    } catch (error) {
      const details = await describeQuotationError(error, 'Save quotation terms', `PATCH /quotations/quotes/${quote.id}/`);
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
      await load();
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
      await load();
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
    setProductCreateModal({ lineIds: ids, names, confirmations: {} });
  };

  const confirmCreateProducts = async (forceCreate = false) => {
    if (!productCreateModal || saving || actionInFlight) return;
    setSaving(true);
    setErrorInfo(null);
    setLineFeedback(null);
    try {
      const response = await quotationAPI.quotes.bulkCreateProductsForLines(quote.id, {
        line_ids: productCreateModal.lineIds,
        names: productCreateModal.names,
        confirm_create_line_ids: forceCreate
          ? productCreateModal.lineIds.filter((lineId) => {
            const warning = productCreateModal.confirmations?.[lineId];
            return warning && !warning.creation_blocked;
          })
          : [],
      });
      const updatedLines = response.data.updated_lines || [];
      const confirmationRequired = response.data.confirmation_required || [];
      const updatedById = Object.fromEntries(updatedLines.map((line) => [line.id, line]));
      setQuote((current) => ({
        ...current,
        lines: (current.lines || []).map((line) => updatedById[line.id] || line),
      }));
      setLineDrafts((current) => ({
        ...current,
        ...Object.fromEntries(updatedLines.map((line) => [line.id, draftFromLine(line)])),
      }));
      setSavedLineDrafts((current) => ({
        ...current,
        ...Object.fromEntries(updatedLines.map((line) => [line.id, draftFromLine(line)])),
      }));
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
        setProductCreateModal((current) => ({
          ...current,
          lineIds: pendingIds,
          confirmations: Object.fromEntries(confirmationRequired.map((entry) => [entry.line_id, entry])),
        }));
        setLineFeedback({
          type: 'warning',
          message: `${confirmationRequired.length} row${confirmationRequired.length === 1 ? '' : 's'} look like existing Products. Review the matches before creating anything new.`,
        });
      } else {
        setProductCreateModal(null);
        setLineFeedback({ type: 'success', message: response.data.message || 'Products created/linked.' });
      }
    } catch (error) {
      const details = await describeQuotationError(error, 'Create Products from quote lines', `POST /quotations/quotes/${quote.id}/bulk_create_products_for_lines/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const linkCandidateFromCreateModal = async (lineId, candidate) => {
    if (!candidate?.product_id || saving || actionInFlight) return;
    const currentDraft = lineDrafts[lineId] || {};
    const linkedDraft = {
      ...currentDraft,
      product: String(candidate.product_id),
      item_name_snapshot: candidate.product_name || currentDraft.item_name_snapshot,
      match_status: 'confirmed',
      product_image: '',
      product_image_url: '',
      has_product_image: false,
      include_product_image: false,
    };
    setSaving(true);
    setErrorInfo(null);
    setLineFeedback(null);
    try {
      const response = await quotationAPI.quotes.bulkUpdateLines(quote.id, {
        lines: [{ id: lineId, ...payloadForLine(linkedDraft) }],
      });
      mergeSavedQuote(response.data.quotation, [lineId]);
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
      quotationAPI.lines.rememberAlias(lineId).catch((error) => {
        console.warn('The row was linked, but its source wording could not be remembered as an alias.', error);
      });
    } catch (error) {
      const details = await describeQuotationError(error, 'Link existing Product to quote line', `POST /quotations/quotes/${quote.id}/bulk_update_lines/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const applyUpdatedLines = (updatedLines = []) => {
    const updatedById = Object.fromEntries(updatedLines.map((line) => [line.id, line]));
    setQuote((current) => ({
      ...current,
      lines: (current.lines || []).map((line) => updatedById[line.id] || line),
    }));
    setLineDrafts((current) => ({
      ...current,
      ...Object.fromEntries(updatedLines.map((line) => {
        const nextDraft = draftFromLine(line);
        const currentDraft = current[line.id] || {};
        const savedDraft = savedLineDrafts[line.id] || {};
        return [line.id, {
          ...nextDraft,
          quantity: currentDraft.quantity !== savedDraft.quantity ? currentDraft.quantity : nextDraft.quantity,
          unit: currentDraft.unit !== savedDraft.unit ? currentDraft.unit : nextDraft.unit,
          unit_price: currentDraft.unit_price !== savedDraft.unit_price ? currentDraft.unit_price : nextDraft.unit_price,
          vat_rate: currentDraft.vat_rate !== savedDraft.vat_rate ? currentDraft.vat_rate : nextDraft.vat_rate,
          description: currentDraft.description !== savedDraft.description ? currentDraft.description : nextDraft.description,
          notes: currentDraft.notes !== savedDraft.notes ? currentDraft.notes : nextDraft.notes,
        }];
      })),
    }));
    setSavedLineDrafts((current) => ({
      ...current,
      ...Object.fromEntries(updatedLines.map((line) => [line.id, draftFromLine(line)])),
    }));
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
      const response = await quotationAPI.lines.createProduct(lineId, { product_name: draft.item_name_snapshot || '' });
      applyUpdatedLines([response.data.line]);
      rememberProductsInList([response.data.product]);
      setSelectedLineIds((current) => current.filter((id) => id !== lineId));
      setLineFeedback({ type: 'success', message: response.data.message || 'Created Product and linked row.' });
    } catch (error) {
      const warning = error?.response?.data;
      if (error?.response?.status === 409 && warning?.requires_confirmation) {
        const draft = lineDrafts[lineId] || {};
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
    try {
      const response = await quotationAPI.lines.uploadProductImage(lineId, formData);
      applyUpdatedLines([response.data.line]);
      setLineFeedback({ type: 'success', message: response.data.message || 'Image saved for this Product.' });
    } catch (error) {
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

  const runAction = async (label, action) => {
    if (saving || actionInFlight) return;
    if (label === 'Finalize' && finalizeIssues.length > 0) return;
    if ((label === 'Finalize' || label === 'Cancel') && !window.confirm(`${label} this quotation?`)) return;
    setSaving(true);
    setActionInFlight(label);
    setErrorInfo(null);
    try {
      const response = await action(quote.id);
      if (label === 'Create Revision' && response.data?.id) {
        window.alert(`Created revision ${response.data.quotation_number}`);
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
      await load();
    } catch (error) {
      const details = await describeQuotationError(error, label, actionEndpoint(label));
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
      setActionInFlight('');
    }
  };

  const downloadPdf = async () => {
    if (downloadLoading || actionInFlight) return;
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
    if (excelDownloadLoading || actionInFlight) return;
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
        <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
        <div className="qm-empty">Quotation not found</div>
      </div>
    );
  }

  const latestLpo = lpos[0] || null;
  const canUseLpoWorkflow = ['approved', 'finalized', 'sent'].includes(quote.status);
  const productCreationWarnings = productCreateModal ? Object.values(productCreateModal.confirmations || {}) : [];
  const hasProductCreationWarnings = productCreationWarnings.length > 0;
  const canOverrideProductCreationWarning = productCreationWarnings.some((warning) => !warning.creation_blocked);

  return (
    <div className="qm-editor">
      <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
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
          {['draft', 'pending_review', 'approved'].includes(quote.status) && <button type="button" className="qm-primary" disabled={saving || Boolean(actionInFlight) || finalizeIssues.length > 0} onClick={() => runAction('Finalize', quotationAPI.quotes.finalize)}>{actionInFlight === 'Finalize' ? 'Finalizing...' : 'Finalize'}</button>}
          {quote.status === 'finalized' && <button type="button" className="qm-secondary" disabled={saving || Boolean(actionInFlight)} onClick={() => runAction('Mark Sent', quotationAPI.quotes.markSent)}>{actionInFlight === 'Mark Sent' ? 'Saving...' : 'Mark Sent'}</button>}
          {['finalized', 'sent'].includes(quote.status) && <button type="button" className="qm-primary" disabled={saving || Boolean(actionInFlight)} onClick={() => onReviewOutcome && onReviewOutcome(quote.id)}>Review Outcome</button>}
          {['finalized', 'sent'].includes(quote.status) && <button type="button" className="qm-secondary" disabled={saving || Boolean(actionInFlight)} onClick={() => runAction('Create Revision', quotationAPI.quotes.revise)}>{actionInFlight === 'Create Revision' ? 'Creating...' : 'Create Revision'}</button>}
          {!['revised', 'cancelled'].includes(quote.status) && <button type="button" className="qm-secondary danger" disabled={saving || Boolean(actionInFlight)} onClick={() => runAction('Cancel', quotationAPI.quotes.cancel)}>{actionInFlight === 'Cancel' ? 'Cancelling...' : 'Cancel'}</button>}
          <button type="button" className="qm-secondary" disabled={downloadLoading || Boolean(actionInFlight)} onClick={downloadPdf}>{downloadLoading ? 'Preparing PDF...' : quote.status === 'draft' ? 'Download Draft PDF' : ['finalized', 'sent'].includes(quote.status) ? 'Download Final PDF' : 'Download PDF'}</button>
          <button type="button" className="qm-secondary" disabled={excelDownloadLoading || Boolean(actionInFlight)} onClick={downloadExcel}>{excelDownloadLoading ? 'Preparing Excel...' : 'Download Excel'}</button>
        </div>
      </div>

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
      <div className="qm-helper">PDF is generated from the latest saved quotation data and current quotation settings. Save line changes before downloading or finalizing.</div>
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
                  {latestLpo.warnings?.length > 0 && (
                    <div className="qm-lpo-warning">
                      {latestLpo.warnings.slice(0, 3).map((warning) => <p key={warning}>{warning}</p>)}
                    </div>
                  )}
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
            <button type="button" className="qm-primary" disabled={saving || Boolean(actionInFlight) || !hasUnsavedQuoteParty} onClick={saveQuoteParty}>
              {saving && hasUnsavedQuoteParty ? 'Saving...' : hasUnsavedQuoteParty ? 'Save Customer & Contact' : 'Saved'}
            </button>
          )}
        </div>
        <div className="qm-party-grid">
          <CompanySelectWithCreate
            companies={companies}
            value={quotePartyDraft.company}
            required
            disabled={!isEditable || saving || Boolean(actionInFlight)}
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
              <select disabled={!isEditable || saving || Boolean(actionInFlight) || !quotePartyDraft.company} value={quotePartyDraft.contact || ''} onChange={(event) => updateQuotePartyDraft({ contact: event.target.value })}>
                <option value="">{loadingContacts ? 'Loading contacts...' : 'No contact'}</option>
                {contactsForQuoteCompany.map((contact) => <option key={contact.id} value={contact.id}>{contactOptionLabel(contact)}</option>)}
              </select>
            </label>
            {isEditable && (
              <button type="button" className="qm-secondary small" disabled={!quotePartyDraft.company || saving || Boolean(actionInFlight)} onClick={() => setShowContactForm((value) => !value)}>
                {showContactForm ? 'Cancel new contact' : '+ Create contact'}
              </button>
            )}
          </div>
        </div>
        {showContactForm && isEditable && (
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
        <div>
          <h3>Quotation Terms</h3>
          <p>Default validity is 30 days. Leave Valid Until blank to use the 30-day default in PDF/Excel.</p>
        </div>
        <label>
          <span className="qm-label-text">Payment terms</span>
          <select disabled={!isEditable || saving || Boolean(actionInFlight)} value={quoteTermsDraft.payment_terms} onChange={(event) => updateQuoteTermDraft({ payment_terms: event.target.value })}>
            {paymentTermOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
        <label>
          <span className="qm-label-text">Valid until</span>
          <input disabled={!isEditable || saving || Boolean(actionInFlight)} type="date" value={quoteTermsDraft.valid_until || ''} onChange={(event) => updateQuoteTermDraft({ valid_until: event.target.value })} />
        </label>
        {isEditable && (
          <button type="button" className="qm-primary" disabled={saving || Boolean(actionInFlight) || !hasUnsavedQuoteTerms} onClick={saveQuoteTerms}>
            {saving && hasUnsavedQuoteTerms ? 'Saving terms...' : hasUnsavedQuoteTerms ? 'Save Terms' : 'Terms Saved'}
          </button>
        )}
      </div>
      {lineFeedback && <div className={`qm-feedback ${lineFeedback.type}`}>{lineFeedback.message}</div>}
      {finalizeIssues.length > 0 && (
        <div className="qm-notice">
          <strong>Finalize is blocked until:</strong>
          <ul>
            {finalizeIssues.slice(0, 5).map((issue) => <li key={issue}><button type="button" className="qm-link-button" onClick={() => setLineFilter('active')}>{issue}</button></li>)}
            {finalizeIssues.length > 5 && <li>{finalizeIssues.length - 5} more issue(s).</li>}
          </ul>
        </div>
      )}

      <div className="qm-panel">
        <div className="qm-panel-heading">
          <div>
            <h3>Step 4: Edit Quotation Lines</h3>
            <p>Each active line needs a Product decision, quantity, unit price, and VAT before finalization. Create draft/internal Products directly from unmatched lines.</p>
          </div>
          <div className="qm-total">
            <span>Subtotal {quote.currency} {parseFloat(quote.subtotal).toFixed(2)}</span>
            <strong>Total {quote.currency} {parseFloat(quote.total).toFixed(2)}</strong>
          </div>
        </div>
        {isEditable && (
          <div className="qm-save-row sticky-line-actions">
            <span className={hasUnsavedLines ? 'qm-unsaved' : 'qm-saved'}>{hasUnsavedLines ? `${changedLineIds.length} unsaved line change(s)` : 'All line changes saved'}</span>
            <span className="qm-sticky-total">
              Total <strong>{quote.currency} {liveQuoteTotal.toFixed(2)}</strong>
            </span>
            <select className="qm-input compact" value={lineFilter} onChange={(event) => setLineFilter(event.target.value)}>
              <option value="active">Active lines</option>
              <option value="unmatched">Unmatched</option>
              <option value="needs_review">Needs review</option>
              <option value="ready">Ready</option>
              <option value="skipped">Skipped</option>
              <option value="all">All lines</option>
            </select>
            <button type="button" className="qm-secondary small" onClick={selectVisibleUnmatched}>Select visible unmatched</button>
            <button type="button" className="qm-secondary small" disabled={!selectedUnmatchedLines.length} onClick={() => openCreateProductModal(selectedUnmatchedLines.map((line) => line.id))}>Create Products for Selected Unmatched Rows</button>
            <button type="button" className="qm-primary" disabled={saving || Boolean(actionInFlight) || !hasUnsavedLines} onClick={saveAllLines}>
              {saving && hasUnsavedLines ? 'Saving...' : 'Save All Lines'}
            </button>
            <span className="qm-sticky-action-divider" aria-hidden="true" />
            {['draft', 'pending_review', 'approved'].includes(quote.status) && (
              <button type="button" className="qm-primary" disabled={saving || Boolean(actionInFlight) || finalizeIssues.length > 0} onClick={() => runAction('Finalize', quotationAPI.quotes.finalize)}>
                {actionInFlight === 'Finalize' ? 'Finalizing...' : 'Finalize'}
              </button>
            )}
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
          <table className="qm-table line-table">
            <thead>
              <tr>
                <th className="qm-check-cell"><input type="checkbox" checked={filteredLines.length > 0 && filteredLines.every((line) => selectedLineIds.includes(line.id))} onChange={() => {
                  const visibleIds = filteredLines.map((line) => line.id);
                  setSelectedLineIds((current) => visibleIds.every((id) => current.includes(id)) ? current.filter((id) => !visibleIds.includes(id)) : Array.from(new Set([...current, ...visibleIds])));
                }} /></th>
                <th className="qm-serial-cell">#</th>
                <th>Matched Item <span className="qm-required">*</span></th>
                <th>Snapshot Name <span className="qm-required">*</span></th>
                <th>Qty <span className="qm-required">*</span></th>
                <th>Unit</th>
                <th>Unit Price <span className="qm-required">*</span></th>
                <th>VAT % <span className="qm-required">*</span></th>
                <th>Status</th>
                <th>Total</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredLines.map((line, lineIndex) => {
                const draft = lineDrafts[line.id] || {};
                const isDirty = !draftsMatch(draft, savedLineDrafts[line.id]);
                const statusInfo = derivedLineStatus(line);
                const priceHint = linePriceHints[line.id];
                return (
                  <tr key={line.id}>
                    <td className="qm-check-cell"><input type="checkbox" checked={selectedLineIds.includes(line.id)} onChange={() => toggleLineSelection(line.id)} /></td>
                    <td className="qm-serial-cell">{lineIndex + 1}</td>
                    <td>
                      <select aria-label={`Product for ${lineLabel(line, draft)}`} disabled={!isEditable} value={draft.product || ''} onChange={(event) => handleLineProductChange(line, event.target.value)}>
                        <option value="">Unmatched</option>
                        {isEditable && <option value="__create__">+ Create a new Product…</option>}
                        {renderProductOptions(draft)}
                      </select>
                    </td>
                    <td><input disabled={!isEditable} value={draft.item_name_snapshot || ''} onChange={(event) => updateLineDraft(line.id, { item_name_snapshot: event.target.value })} /></td>
                    <td><input disabled={!isEditable} type="number" min="0" step="0.001" value={draft.quantity || ''} onChange={(event) => updateLineDraft(line.id, { quantity: event.target.value })} /></td>
                    <td>
                      <input
                        className="qm-unit-input"
                        disabled={!isEditable}
                        list="quotation-unit-suggestions"
                        inputMode="text"
                        placeholder="each"
                        value={draft.unit || ''}
                        onKeyDown={preventUnitNumberKey}
                        onChange={(event) => updateLineDraft(line.id, { unit: sanitizeUnitText(event.target.value) })}
                      />
                    </td>
                    <td className="qm-price-cell">
                      <input aria-label={`Unit price for ${lineLabel(line, draft)}`} disabled={!isEditable} type="number" min="0" step="0.001" value={draft.unit_price || ''} onWheel={releaseNumberWheelFocus} onChange={(event) => updateLineDraft(line.id, { unit_price: event.target.value })} />
                      {draft.product && (
                        <span className={`qm-price-hint ${priceHint?.mode || 'on-demand'}`}>
                          {priceHint ? priceHintText(priceHint) : 'Price history available on demand'}
                          <button type="button" onClick={() => setPriceHistoryDialog({
                            productId: draft.product,
                            productName: priceHint?.product_name || items.find((item) => String(item.id) === String(draft.product))?.name || '',
                            context: priceContexts[String(draft.product)] || priceHint,
                          })}>View price history</button>
                        </span>
                      )}
                    </td>
                    <td className="qm-vat-cell">
                      <select className="qm-vat-select" disabled={!isEditable} value={draft.vat_rate || '0'} onChange={(event) => updateLineDraft(line.id, { vat_rate: event.target.value })}>
                        <option value="0">0%</option>
                        <option value="5">5%</option>
                      </select>
                    </td>
                    <td><span className={`qm-line-status ${statusInfo.id}`}>{statusInfo.label}</span></td>
                    <td>{quote.currency} {lineTotalForDraft(draft).toFixed(2)}</td>
                    <td className="qm-row-actions">
                      <span className={isDirty ? 'qm-line-state unsaved' : 'qm-line-state saved'}>{isDirty ? 'Unsaved' : 'Saved'}</span>
                      <button type="button" className="qm-secondary small" disabled={!isEditable || saving || actionInFlight || !isDirty} onClick={() => saveLine(line.id)}>Save</button>
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
          <form onSubmit={addLine} className="qm-add-line">
            <select value={lineForm.product} onChange={(event) => handleLineFormProductChange(event.target.value)}>
              <option value="">Select item</option>
              {renderProductOptions(lineForm)}
            </select>
            <input placeholder="Snapshot name" required value={lineForm.item_name_snapshot} onChange={(event) => setLineForm({ ...lineForm, item_name_snapshot: event.target.value })} />
            <input aria-label="Qty" type="number" min="0" step="0.001" value={lineForm.quantity} onChange={(event) => setLineForm({ ...lineForm, quantity: event.target.value })} />
            <input
              placeholder="Unit"
              list="quotation-unit-suggestions"
              inputMode="text"
              value={lineForm.unit}
              onKeyDown={preventUnitNumberKey}
              onChange={(event) => setLineForm({ ...lineForm, unit: sanitizeUnitText(event.target.value) })}
            />
            <input type="number" min="0" step="0.001" placeholder="Price" value={lineForm.unit_price} onWheel={releaseNumberWheelFocus} onChange={(event) => {
              lineFormPriceVersionRef.current += 1;
              setLineForm({ ...lineForm, unit_price: event.target.value });
            }} />
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
              <button type="button" className="qm-secondary small" onClick={() => setProductCreateModal(null)}>Close</button>
            </div>
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
                            onChange={(event) => setProductCreateModal((current) => {
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
                              <strong>{warning.creation_blocked ? 'Identifier conflict — a new Product cannot be created' : 'Likely existing Product found'}</strong>
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
                              {!warning.creation_blocked && <small>Only choose “Create anyway” if none of these Products is actually the same item.</small>}
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
                {saving ? 'Checking catalog...' : hasProductCreationWarnings ? 'Create new Product anyway' : 'Check catalog and continue'}
              </button>
              <button type="button" className="qm-secondary" disabled={saving} onClick={() => setProductCreateModal(null)}>Cancel</button>
            </div>
          </div>
        </div>
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
