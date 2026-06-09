import React, { useCallback, useEffect, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import PriceHistoryPanel from './PriceHistoryPanel';
import AuditLogPanel from './AuditLogPanel';
import QuotationErrorNotice from './QuotationErrorNotice';

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

const emptyLine = {
  product: '',
  item_name_snapshot: '',
  description: '',
  quantity: '1',
  unit: '',
  unit_price: '',
  vat_rate: '0',
  match_status: 'unresolved',
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
  notes: line.notes || '',
});

const termsDraftFromQuote = (quote = {}) => ({
  payment_terms: quote.payment_terms || 'credit_30_days',
  valid_until: quote.valid_until || '',
});

const termsDraftsMatch = (left = {}, right = {}) => (
  String(left.payment_terms || '') === String(right.payment_terms || '') &&
  String(left.valid_until || '') === String(right.valid_until || '')
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

const QuotationEditor = ({ quoteId, onClose }) => {
  const [quote, setQuote] = useState(null);
  const [quoteTermsDraft, setQuoteTermsDraft] = useState(termsDraftFromQuote());
  const [savedQuoteTermsDraft, setSavedQuoteTermsDraft] = useState(termsDraftFromQuote());
  const [items, setItems] = useState([]);
  const [companyItems, setCompanyItems] = useState([]);
  const [showFullProductCatalog, setShowFullProductCatalog] = useState(false);
  const [lineForm, setLineForm] = useState(emptyLine);
  const [lineDrafts, setLineDrafts] = useState({});
  const [savedLineDrafts, setSavedLineDrafts] = useState({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [actionInFlight, setActionInFlight] = useState('');
  const [downloadLoading, setDownloadLoading] = useState(false);
  const [excelDownloadLoading, setExcelDownloadLoading] = useState(false);
  const [lineFeedback, setLineFeedback] = useState(null);
  const [linePriceHints, setLinePriceHints] = useState({});
  const [historyItem, setHistoryItem] = useState('');
  const [errorInfo, setErrorInfo] = useState(null);
  const [selectedLineIds, setSelectedLineIds] = useState([]);
  const [lineFilter, setLineFilter] = useState('active');
  const [productCreateModal, setProductCreateModal] = useState(null);

  const setLoadedQuote = useCallback((quoteData) => {
    setQuote(quoteData);
    const nextTermsDraft = termsDraftFromQuote(quoteData);
    setQuoteTermsDraft(nextTermsDraft);
    setSavedQuoteTermsDraft(nextTermsDraft);
    const drafts = Object.fromEntries((quoteData.lines || []).map((line) => [line.id, draftFromLine(line)]));
    setLineDrafts(drafts);
    setSavedLineDrafts(drafts);
    setLinePriceHints({});
    setSelectedLineIds((current) => current.filter((id) => (quoteData.lines || []).some((line) => line.id === id)));
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setErrorInfo(null);
    try {
      const quoteRes = await quotationAPI.quotes.retrieve(quoteId);
      const [itemsRes, companyItemsRes] = await Promise.all([
        quotationAPI.items.list({ active: 'true' }),
        quotationAPI.items.list({ active: 'true', company_used: quoteRes.data.company }),
      ]);
      setLoadedQuote(quoteRes.data);
      setItems(itemsRes.data);
      setCompanyItems(companyItemsRes.data);
    } catch (error) {
      const details = await describeQuotationError(error, 'Load quotation', `GET /quotations/quotes/${quoteId}/, GET /quotations/items/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setLoading(false);
    }
  }, [quoteId, setLoadedQuote]);

  useEffect(() => {
    load();
  }, [load]);

  const isEditable = quote && editableStatuses.has(quote.status);
  const activeLines = quote?.lines || [];
  const changedLineIds = quote ? (quote.lines || [])
    .filter((line) => !draftsMatch(lineDrafts[line.id], savedLineDrafts[line.id]))
    .map((line) => line.id) : [];
  const hasUnsavedLines = changedLineIds.length > 0;
  const hasUnsavedQuoteTerms = !termsDraftsMatch(quoteTermsDraft, savedQuoteTermsDraft);

  const lineLabel = (line, draft = {}) => draft.item_name_snapshot || line.inquiry_line_raw_name || line.item_name_snapshot || `Line ${line.sort_order + 1}`;

  const productOptionsForDraft = (draft = {}) => {
    const baseItems = showFullProductCatalog ? items : companyItems;
    const byId = new Map(baseItems.map((item) => [String(item.id), item]));
    if (draft.product && !byId.has(String(draft.product))) {
      const selected = items.find((item) => String(item.id) === String(draft.product));
      if (selected) byId.set(String(selected.id), selected);
    }
    return Array.from(byId.values()).sort((a, b) => a.name.localeCompare(b.name));
  };

  const lineTotalForDraft = (draft = {}) => {
    const quantity = Number(draft.quantity || 0);
    const unitPrice = Number(draft.unit_price || 0);
    const vatRate = Number(draft.vat_rate || 0);
    const subtotal = quantity * unitPrice;
    return Number.isFinite(subtotal) ? subtotal * (1 + (Number.isFinite(vatRate) ? vatRate : 0) / 100) : 0;
  };

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
    if (Object.prototype.hasOwnProperty.call(patch, 'unit_price') || Object.prototype.hasOwnProperty.call(patch, 'product')) {
      setLinePriceHints((current) => {
        const next = { ...current };
        delete next[lineId];
        return next;
      });
    }
    setLineDrafts((current) => ({
      ...current,
      [lineId]: { ...current[lineId], ...patch },
    }));
  };

  const updateQuoteTermDraft = (patch) => {
    setLineFeedback(null);
    setQuoteTermsDraft((current) => ({ ...current, ...patch }));
  };

  const productPatch = (draft, productId) => {
    const item = items.find((candidate) => String(candidate.id) === String(productId));
    return {
      product: productId,
      item_name_snapshot: item ? item.name : draft.item_name_snapshot,
      unit: draft.unit || item?.unit || '',
      match_status: productId ? 'confirmed' : 'unresolved',
    };
  };

  const priceShouldAutofill = (draft) => !draft.unit_price || Number(draft.unit_price) <= 0;

  const setPriceHintForLine = (lineId, suggestion, mode) => {
    setLinePriceHints((current) => ({
      ...current,
      [lineId]: {
        mode,
        unit_price: suggestion?.unit_price || '',
        quoted_at: suggestion?.quoted_at || '',
        source_label: suggestion?.source_label || '',
      },
    }));
  };

  const maybeFetchProductPrice = async (productId) => {
    if (!quote?.id || !productId) return null;
    try {
      const response = await quotationAPI.quotes.productPrice(quote.id, { product: productId });
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
    const dateText = hint.quoted_at ? ` • ${hint.quoted_at}` : '';
    if (hint.mode === 'autofilled') return `Price history AED ${hint.unit_price}${dateText} applied`;
    if (hint.mode === 'current_kept') return `Last price AED ${hint.unit_price}${dateText}; current price kept`;
    if (hint.mode === 'history_found') return `Last price AED ${hint.unit_price}${dateText}`;
    return 'No previous company price';
  };

  const handleLineProductChange = async (line, productId) => {
    if (productId === '__create__') {
      createProductForLine(line.id);
      return;
    }
    const currentDraft = lineDrafts[line.id] || {};
    const patch = productPatch(currentDraft, productId);
    updateLineDraft(line.id, patch);
    setHistoryItem(productId);
    if (!productId) return;

    const suggestion = await maybeFetchProductPrice(productId);
    if (!suggestion) return;
    if (suggestion.source !== 'company_price_history') {
      setPriceHintForLine(line.id, suggestion, 'no_history');
      return;
    }
    if (!suggestion?.unit_price) return;
    const pricePatch = {};
    if (priceShouldAutofill(currentDraft)) {
      pricePatch.unit_price = suggestion.unit_price;
    }
    if (!currentDraft.unit && suggestion.unit) {
      pricePatch.unit = suggestion.unit;
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
    setLineForm((current) => ({ ...current, ...patch }));
    if (!productId) return;
    const suggestion = await maybeFetchProductPrice(productId);
    if (!suggestion?.unit_price) return;
    if (suggestion.source !== 'company_price_history') return;
    setLineForm((current) => ({
      ...current,
      unit_price: priceShouldAutofill(current) ? suggestion.unit_price : current.unit_price,
      unit: current.unit || suggestion.unit || '',
    }));
  };

  const payloadForLine = (draft) => ({
    ...draft,
    product: draft.product || null,
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
        payment_terms: quoteTermsDraft.payment_terms || 'credit_30_days',
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

  const rememberAlias = async (lineId) => {
    if (saving || actionInFlight) return;
    setSaving(true);
    setLineFeedback(null);
    setErrorInfo(null);
    try {
      await quotationAPI.lines.rememberAlias(lineId);
      setLineFeedback({ type: 'success', message: 'Company-specific alias remembered for this product.' });
      await load();
    } catch (error) {
      const details = await describeQuotationError(error, 'Remember product alias', `POST /quotations/quote-lines/${lineId}/remember_alias/`);
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

  const clearSelection = () => setSelectedLineIds([]);

  const bulkPatchSelected = (patch) => {
    if (!selectedLineIds.length) return;
    selectedLineIds.forEach((lineId) => updateLineDraft(lineId, patch));
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
    setProductCreateModal({ lineIds: ids, names });
  };

  const confirmCreateProducts = async () => {
    if (!productCreateModal || saving || actionInFlight) return;
    setSaving(true);
    setErrorInfo(null);
    setLineFeedback(null);
    try {
      const response = await quotationAPI.quotes.bulkCreateProductsForLines(quote.id, {
        line_ids: productCreateModal.lineIds,
        names: productCreateModal.names,
      });
      const updatedLines = response.data.updated_lines || [];
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
      setSelectedLineIds((current) => current.filter((id) => !productCreateModal.lineIds.includes(id)));
      setProductCreateModal(null);
      setLineFeedback({ type: 'success', message: response.data.message || 'Products created/linked.' });
    } catch (error) {
      const details = await describeQuotationError(error, 'Create Products from quote lines', `POST /quotations/quotes/${quote.id}/bulk_create_products_for_lines/`);
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
      const details = await describeQuotationError(error, 'Create Product from quote line', `POST /quotations/quote-lines/${lineId}/create_product/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
      setActionInFlight('');
    }
  };

  const actionEndpoint = (label) => {
    const endpointNames = {
      'Submit Review': 'submit_review',
      Approve: 'approve',
      Finalize: 'finalize',
      'Mark Sent': 'mark_sent',
      'Create Revision': 'revise',
      Cancel: 'cancel',
    };
    return `POST /quotations/quotes/${quote.id}/${endpointNames[label] || label.toLowerCase()}/`;
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
      const response = await quotationAPI.quotes.pdf(quote.id);
      const url = window.URL.createObjectURL(new Blob([response.data], { type: 'application/pdf' }));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', quotationDownloadFilename(quote, 'pdf'));
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
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

  if (loading) return <div className="qm-loading">Loading quotation...</div>;
  if (!quote) {
    return (
      <div className="qm-section">
        <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
        <div className="qm-empty">Quotation not found</div>
      </div>
    );
  }

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
          {quote.status === 'draft' && <button type="button" className="qm-secondary" disabled={saving || Boolean(actionInFlight)} onClick={() => runAction('Submit Review', quotationAPI.quotes.submitReview)}>{actionInFlight === 'Submit Review' ? 'Submitting...' : 'Submit Review'}</button>}
          {['draft', 'pending_review'].includes(quote.status) && <button type="button" className="qm-secondary" disabled={saving || Boolean(actionInFlight)} onClick={() => runAction('Approve', quotationAPI.quotes.approve)}>{actionInFlight === 'Approve' ? 'Approving...' : 'Approve'}</button>}
          {['draft', 'pending_review', 'approved'].includes(quote.status) && <button type="button" className="qm-primary" disabled={saving || Boolean(actionInFlight) || finalizeIssues.length > 0} onClick={() => runAction('Finalize', quotationAPI.quotes.finalize)}>{actionInFlight === 'Finalize' ? 'Finalizing...' : 'Finalize'}</button>}
          {quote.status === 'finalized' && <button type="button" className="qm-secondary" disabled={saving || Boolean(actionInFlight)} onClick={() => runAction('Mark Sent', quotationAPI.quotes.markSent)}>{actionInFlight === 'Mark Sent' ? 'Saving...' : 'Mark Sent'}</button>}
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
            <select className="qm-input compact" value={lineFilter} onChange={(event) => setLineFilter(event.target.value)}>
              <option value="active">Active lines</option>
              <option value="unmatched">Unmatched</option>
              <option value="needs_review">Needs review</option>
              <option value="ready">Ready</option>
              <option value="skipped">Skipped</option>
              <option value="all">All lines</option>
            </select>
            <button type="button" className="qm-secondary small" onClick={selectVisibleUnmatched}>Select visible unmatched</button>
            <button type="button" className="qm-secondary small" disabled={!selectedLineIds.length} onClick={clearSelection}>Clear selection</button>
            <button type="button" className="qm-secondary small" disabled={!selectedLineIds.length} onClick={() => bulkPatchSelected({ vat_rate: '0' })}>VAT 0%</button>
            <button type="button" className="qm-secondary small" disabled={!selectedLineIds.length} onClick={() => bulkPatchSelected({ vat_rate: '5' })}>VAT 5%</button>
            <button type="button" className="qm-secondary small" disabled={!selectedLineIds.length} onClick={() => bulkPatchSelected({ match_status: 'ignored' })}>Skip selected</button>
            <button type="button" className="qm-secondary small" disabled={!selectedUnmatchedLines.length} onClick={() => openCreateProductModal(selectedUnmatchedLines.map((line) => line.id))}>Create Products for Selected Unmatched Rows</button>
            <label className="qm-checkbox compact">
              <input type="checkbox" checked={showFullProductCatalog} onChange={(event) => setShowFullProductCatalog(event.target.checked)} />
              Show full catalog
            </label>
            <button type="button" className="qm-primary" disabled={saving || Boolean(actionInFlight) || !hasUnsavedLines} onClick={saveAllLines}>
              {saving && hasUnsavedLines ? 'Saving...' : 'Save All Lines'}
            </button>
          </div>
        )}

        <div className="qm-table-wrap">
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
                const productOptions = productOptionsForDraft(draft);
                const priceHint = linePriceHints[line.id];
                return (
                  <tr key={line.id}>
                    <td className="qm-check-cell"><input type="checkbox" checked={selectedLineIds.includes(line.id)} onChange={() => toggleLineSelection(line.id)} /></td>
                    <td className="qm-serial-cell">{lineIndex + 1}</td>
                    <td>
                      <select disabled={!isEditable} value={draft.product || ''} onChange={(event) => handleLineProductChange(line, event.target.value)}>
                        <option value="">Unmatched</option>
                        {!showFullProductCatalog && productOptions.length === 0 && <option disabled value="__none__">No Products used by this company yet</option>}
                        {productOptions.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                      </select>
                    </td>
                    <td><input disabled={!isEditable} value={draft.item_name_snapshot || ''} onChange={(event) => updateLineDraft(line.id, { item_name_snapshot: event.target.value })} /></td>
                    <td><input disabled={!isEditable} type="number" min="0" step="0.001" value={draft.quantity || ''} onChange={(event) => updateLineDraft(line.id, { quantity: event.target.value })} /></td>
                    <td><input disabled={!isEditable} value={draft.unit || ''} onChange={(event) => updateLineDraft(line.id, { unit: event.target.value })} /></td>
                    <td className="qm-price-cell">
                      <input disabled={!isEditable} type="number" min="0" step="0.01" value={draft.unit_price || ''} onWheel={releaseNumberWheelFocus} onChange={(event) => updateLineDraft(line.id, { unit_price: event.target.value })} />
                      {priceHint && <span className={`qm-price-hint ${priceHint.mode}`}>{priceHintText(priceHint)}</span>}
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
                      <button type="button" className="qm-secondary small" disabled={!isEditable || saving || actionInFlight} onClick={() => updateLineDraft(line.id, { match_status: draft.match_status === 'ignored' ? (draft.product ? 'confirmed' : 'unresolved') : 'ignored' })}>{draft.match_status === 'ignored' ? 'Unskip' : 'Skip'}</button>
                      <button type="button" className="qm-secondary small" onClick={() => setHistoryItem(draft.product || '')}>History</button>
                      <button type="button" className="qm-secondary small" disabled={!isEditable || saving || actionInFlight || !draft.product} onClick={() => rememberAlias(line.id)}>Remember Alias</button>
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
              {!showFullProductCatalog && productOptionsForDraft(lineForm).length === 0 && <option disabled value="__none__">No Products used by this company yet</option>}
              {productOptionsForDraft(lineForm).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
            <input placeholder="Snapshot name" required value={lineForm.item_name_snapshot} onChange={(event) => setLineForm({ ...lineForm, item_name_snapshot: event.target.value })} />
            <input aria-label="Qty" type="number" min="0" step="0.001" value={lineForm.quantity} onChange={(event) => setLineForm({ ...lineForm, quantity: event.target.value })} />
            <input placeholder="Unit" value={lineForm.unit} onChange={(event) => setLineForm({ ...lineForm, unit: event.target.value })} />
            <input type="number" min="0" step="0.01" placeholder="Price" value={lineForm.unit_price} onWheel={releaseNumberWheelFocus} onChange={(event) => setLineForm({ ...lineForm, unit_price: event.target.value })} />
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
                <p>Products will be draft/internal and hidden from public product pages. Same normalized names are created once and linked to all matching selected rows.</p>
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
                    return (
                      <tr key={lineId}>
                        <td>{line ? lineLabel(line, lineDrafts[lineId]) : `Line ${lineId}`}</td>
                        <td>
                          <input
                            value={productCreateModal.names[lineId] || ''}
                            onChange={(event) => setProductCreateModal((current) => ({
                              ...current,
                              names: { ...current.names, [lineId]: event.target.value },
                            }))}
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="qm-action-row">
              <button type="button" className="qm-primary" disabled={saving} onClick={confirmCreateProducts}>
                {saving ? 'Creating...' : 'Create / Link Products'}
              </button>
              <button type="button" className="qm-secondary" disabled={saving} onClick={() => setProductCreateModal(null)}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      <div className="qm-grid-two bottom-panels">
        <PriceHistoryPanel companyId={quote.company} itemId={historyItem} />
        <AuditLogPanel quotationId={quote.id} />
      </div>
    </div>
  );
};

export default QuotationEditor;
