import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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

const EVIDENCE_TEXT_PREVIEW_LIMIT = 5000;

const EvidenceTextSection = ({ title, text, backendTruncated = false, emptyMessage }) => {
  const [expanded, setExpanded] = useState(false);
  const fullText = String(text || '').trim();
  const canExpand = fullText.length > EVIDENCE_TEXT_PREVIEW_LIMIT;
  const visibleText = expanded || !canExpand
    ? fullText
    : fullText.slice(0, EVIDENCE_TEXT_PREVIEW_LIMIT);

  useEffect(() => {
    setExpanded(false);
  }, [title, fullText]);

  return (
    <div className="qm-evidence-detail-section">
      <h4>{title}</h4>
      <pre className="qm-evidence-preview">{visibleText || emptyMessage}</pre>
      {canExpand && (
        <div className="qm-action-row">
          <button
            type="button"
            className="qm-secondary small"
            aria-expanded={expanded}
            onClick={() => setExpanded((current) => !current)}
          >
            {expanded ? `Show less ${title.toLowerCase()}` : `Show full ${title.toLowerCase()}`}
          </button>
          {!expanded && (
            <small className="qm-evidence-preview-note">
              Showing the first {EVIDENCE_TEXT_PREVIEW_LIMIT.toLocaleString()} of {fullText.length.toLocaleString()} characters.
            </small>
          )}
        </div>
      )}
      {backendTruncated && (
        <small className="qm-evidence-preview-note">
          The backend returned only part of this text because it exceeded the configured source limit. This section is incomplete.
        </small>
      )}
    </div>
  );
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
  const selected = selectedAttachmentIdentity(evidence);
  const identifier = attachmentIdentifier(attachment);
  const hasSelectedId = selected.id !== undefined
    && selected.id !== null
    && String(selected.id).trim() !== '';
  if (hasSelectedId) {
    return identifier !== undefined && String(selected.id) === String(identifier);
  }
  if ([attachment?.is_selected, attachment?.selected, attachment?.is_primary, attachment?.selected_source].some((value) => value === true)) {
    return true;
  }
  return Boolean(selected.filename && attachment?.filename && selected.filename === attachment.filename);
};

const evidenceSourceKind = (evidence) => {
  const rawKind = firstDefined(
    evidence?.selected_source_kind,
    evidence?.match_signals?.source?.kind,
    evidence?.selected_source?.kind,
    evidence?.selected_attachment?.kind
  );
  const normalized = String(rawKind || '').trim().toLowerCase();
  if (['attachment', 'file'].includes(normalized)) return 'attachment';
  if (['body', 'email', 'email_body', 'message_body'].includes(normalized)) return 'email_body';
  return selectedAttachmentIdentity(evidence).id !== undefined ? 'attachment' : 'unknown';
};

const evidenceSourceLabel = (evidence) => {
  const kind = evidenceSourceKind(evidence);
  const selected = selectedAttachmentIdentity(evidence);
  if (kind === 'attachment') {
    return selected.filename ? `Attachment · ${selected.filename}` : 'Attachment';
  }
  if (kind === 'email_body') return 'Email body';
  return 'Not recorded (legacy evidence)';
};

const evidenceSourceText = (evidence) => {
  const kind = evidenceSourceKind(evidence);
  const extractedText = firstDefined(evidence?.extracted_text, evidence?.extracted_text_preview, '');
  const directEmailBody = firstDefined(
    evidence?.email_body_text,
    evidence?.email_body_preview,
    evidence?.body_preview,
    evidence?.email_body,
    evidence?.body_text,
    ''
  );
  const emailBodyUsesExtractedText = kind === 'email_body' && !directEmailBody && Boolean(extractedText);
  return {
    kind,
    attachmentText: kind === 'attachment' ? extractedText : '',
    selectedLegacyText: kind === 'unknown' ? extractedText : '',
    emailBody: emailBodyUsesExtractedText ? extractedText : directEmailBody,
    attachmentBackendTruncated: kind === 'attachment' && Boolean(evidence?.extracted_text_truncated),
    selectedLegacyBackendTruncated: kind === 'unknown' && Boolean(evidence?.extracted_text_truncated),
    emailBodyBackendTruncated: emailBodyUsesExtractedText
      ? Boolean(evidence?.extracted_text_truncated)
      : Boolean(evidence?.email_body_text_truncated),
  };
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

const optionalMoney = (value, currency = 'AED', missingLabel = '-') => {
  if (value === undefined || value === null || value === '') return missingLabel;
  const number = Number(value);
  return Number.isFinite(number) ? money(number, currency) : missingLabel;
};

const optionalUnitMoney = (value, currency = 'AED', missingLabel = '-') => {
  if (value === undefined || value === null || value === '') return missingLabel;
  const number = Number(value);
  return Number.isFinite(number) ? unitMoney(number, currency) : missingLabel;
};

const optionalQuantity = (value, unit, missingLabel = '-') => {
  if (value === undefined || value === null || value === '') return missingLabel;
  const normalizedUnit = String(unit || '').trim();
  return `${value}${normalizedUnit ? ` ${normalizedUnit}` : ''}`;
};

const sameLineId = (left, right) => (
  left !== undefined
  && left !== null
  && right !== undefined
  && right !== null
  && String(left) === String(right)
);

const hasLineId = (values, id) => values.some((value) => sameLineId(value, id));

const uniqueLineIds = (values) => values.reduce((result, value) => {
  if (value !== undefined && value !== null && !hasLineId(result, value)) result.push(value);
  return result;
}, []);

const numberOrNull = (value) => {
  if (value === undefined || value === null || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
};

const derivedLineTotal = (quantity, price) => {
  const normalizedQuantity = numberOrNull(quantity);
  const normalizedPrice = numberOrNull(price);
  if (normalizedQuantity === null || normalizedPrice === null) return undefined;
  return normalizedQuantity * normalizedPrice;
};

const comparisonStatusDetails = {
  accepted: { label: 'Accepted as quoted', className: 'accepted' },
  accepted_price_not_stated: { label: 'Accepted - price not stated', className: 'accepted-not-stated' },
  repriced: { label: 'Accepted at changed price', className: 'repriced' },
  reduced: { label: 'Reduced quantity', className: 'reduced' },
  reduced_price_not_stated: { label: 'Reduced - price not stated', className: 'reduced-not-stated' },
  reduced_repriced: { label: 'Reduced and repriced', className: 'reduced-repriced' },
  not_ordered: { label: 'Not ordered / omitted', className: 'not-ordered' },
  unmatched: { label: 'Unmatched LPO item', className: 'unmatched' },
  uncertain: { label: 'Needs review', className: 'uncertain' },
};

const normalizeComparisonStatus = (row) => {
  const rawStatus = String(firstDefined(
    row?.status,
    row?.decision,
    row?.match_status,
    row?.suggested_outcome_status,
    ''
  )).trim().toLowerCase().replaceAll(' ', '_').replaceAll('-', '_');
  if (row?.unmatched_lpo || row?.unmatched_po || row?.po_only) return 'unmatched';
  if (['not_ordered', 'rejected', 'missing', 'missing_from_po', 'omitted', 'unavailable_missing'].includes(rawStatus)) {
    return 'not_ordered';
  }
  if (['unmatched', 'unmatched_lpo', 'unmatched_po', 'po_only'].includes(rawStatus)) return 'unmatched';
  if (['uncertain', 'needs_review', 'conflict', 'ambiguous', 'unmatched_quote'].includes(rawStatus)) return 'uncertain';
  if (['accepted_price_not_stated', 'accepted_no_price', 'price_not_stated'].includes(rawStatus)) {
    return 'accepted_price_not_stated';
  }
  if (['reduced_price_not_stated', 'reduced_no_price'].includes(rawStatus)) return 'reduced_price_not_stated';
  if (['reduced_repriced', 'quantity_and_price_changed'].includes(rawStatus)) return 'reduced_repriced';
  if (['repriced', 'price_changed', 'price_conflict'].includes(rawStatus)) return 'repriced';
  if (['reduced', 'quantity_reduced', 'partial'].includes(rawStatus)) return 'reduced';
  if (row?.review_required || row?.requires_review) return 'uncertain';

  const quotedQuantity = numberOrNull(firstDefined(row?.quoted_quantity, row?.quote_quantity));
  const acceptedQuantity = numberOrNull(firstDefined(row?.accepted_quantity, row?.po_quantity, row?.lpo_quantity));
  const quotedPrice = numberOrNull(firstDefined(row?.quoted_unit_price, row?.quote_unit_price));
  const acceptedPrice = numberOrNull(firstDefined(row?.accepted_unit_price, row?.po_unit_price, row?.lpo_unit_price));
  const reduced = quotedQuantity !== null && acceptedQuantity !== null && acceptedQuantity < quotedQuantity;
  const repriced = quotedPrice !== null && acceptedPrice !== null && Math.abs(acceptedPrice - quotedPrice) > 0.0005;
  const acceptedLike = ['accepted', 'exact', 'matched', 'accepted_exact', 'quantity_changed'].includes(rawStatus);
  if (acceptedLike && acceptedQuantity === null) return 'uncertain';
  if (acceptedLike && acceptedPrice === null) {
    return reduced ? 'reduced_price_not_stated' : 'accepted_price_not_stated';
  }
  if (reduced && repriced) return 'reduced_repriced';
  if (reduced) return 'reduced';
  if (repriced) return 'repriced';
  if (acceptedLike) return 'accepted';
  return 'uncertain';
};

const normalizeComparisonLine = (row, quoteLines = []) => {
  const quotationLineId = firstDefined(row?.quotation_line_id, row?.quote_line_id, row?.line_id);
  const quoteLine = quoteLines.find((line) => sameLineId(line.id, quotationLineId));
  const quotedQuantity = firstDefined(row?.quoted_quantity, row?.quote_quantity, quoteLine?.quantity);
  const acceptedQuantity = firstDefined(
    row?.accepted_quantity,
    row?.po_quantity,
    row?.lpo_quantity,
    row?.ordered_quantity,
    row?.po_row?.quantity
  );
  const quotedUnitPrice = firstDefined(row?.quoted_unit_price, row?.quote_unit_price, quoteLine?.unit_price);
  const acceptedUnitPrice = firstDefined(
    row?.accepted_unit_price,
    row?.po_unit_price,
    row?.lpo_unit_price,
    row?.po_row?.unit_price
  );
  const quotedLineTotal = firstDefined(
    row?.quoted_line_total,
    row?.quote_line_total,
    quoteLine?.line_total,
    derivedLineTotal(quotedQuantity, quotedUnitPrice)
  );
  const acceptedLineTotal = firstDefined(
    row?.accepted_line_total,
    row?.po_line_total,
    row?.lpo_line_total,
    row?.po_row?.line_total,
    derivedLineTotal(acceptedQuantity, acceptedUnitPrice)
  );
  const normalized = {
    ...row,
    quotationLineId,
    quoteItemName: firstDefined(
      row?.quote_item_name,
      row?.quote_name,
      row?.quotation_item_name,
      quoteLine?.item_name_snapshot,
      quoteLine?.product_name,
      quotationLineId ? `Quotation line ${quotationLineId}` : '-'
    ),
    lpoItemName: firstDefined(
      row?.lpo_item_name,
      row?.po_item_name,
      row?.po_name,
      row?.accepted_item_name,
      row?.po_row?.item_name,
      '-'
    ),
    quotedQuantity,
    acceptedQuantity,
    quotedUnit: firstDefined(row?.quoted_unit, row?.quote_unit, quoteLine?.unit),
    acceptedUnit: firstDefined(row?.accepted_unit, row?.po_unit, row?.lpo_unit, row?.po_row?.unit),
    quotedUnitPrice,
    acceptedUnitPrice,
    quotedLineTotal,
    acceptedLineTotal,
    confidence: firstDefined(row?.confidence, row?.match_confidence),
    reason: firstDefined(row?.reason, row?.matching_reason, row?.detail, ''),
  };
  normalized.status = normalizeComparisonStatus({ ...row, ...normalized });
  return normalized;
};

const normalizeUnmatchedLPORow = (row, index) => normalizeComparisonLine({
  ...row,
  unmatched_lpo: true,
  lpo_item_name: firstDefined(row?.lpo_item_name, row?.po_item_name, row?.item_name, row?.name, `LPO row ${index + 1}`),
  accepted_quantity: firstDefined(row?.accepted_quantity, row?.po_quantity, row?.quantity),
  accepted_unit: firstDefined(row?.accepted_unit, row?.po_unit, row?.unit),
  accepted_unit_price: firstDefined(row?.accepted_unit_price, row?.po_unit_price, row?.unit_price),
  accepted_line_total: firstDefined(row?.accepted_line_total, row?.po_line_total, row?.line_total, row?.total),
  reason: firstDefined(row?.reason, 'No confident quotation-line match was found.'),
  status: 'unmatched',
}, []);

const normalizeCommercialComparison = (rawComparison, quote, summary) => {
  const comparison = rawComparison && typeof rawComparison === 'object' ? rawComparison : {};
  const quoteLines = quote?.lines || [];
  const rawLines = Array.isArray(comparison.lines)
    ? comparison.lines
    : Array.isArray(comparison.line_comparisons)
      ? comparison.line_comparisons
      : [];
  const lines = rawLines.map((line) => normalizeComparisonLine(line, quoteLines));
  const unmatchedRows = firstDefined(comparison.unmatched_lpo_rows, comparison.unmatched_po_rows, []);
  const normalizedUnmatchedRows = Array.isArray(unmatchedRows)
    ? unmatchedRows.map(normalizeUnmatchedLPORow)
    : [];
  return {
    companyName: firstDefined(comparison.company_name, quote?.company_name, '-'),
    quotationNumber: firstDefined(comparison.quotation_number, quote?.quotation_number, '-'),
    lpoNumber: firstDefined(
      comparison.lpo_number,
      comparison.po_number,
      comparison.purchase_order_number,
      'Not found in source'
    ),
    currency: firstDefined(comparison.currency, quote?.currency, 'AED'),
    quotationSubtotal: firstDefined(comparison.quotation_subtotal, quote?.subtotal),
    quotationVatTotal: firstDefined(comparison.quotation_vat_total, quote?.vat_total),
    quotationTotal: firstDefined(
      comparison.quotation_total,
      comparison.quote_total,
      quote?.grand_total,
      quote?.total,
      summary?.quoted_value
    ),
    lpoTotal: firstDefined(comparison.lpo_total, comparison.po_total, comparison.document_total),
    totalResult: firstDefined(comparison.total_result, comparison.document_total_result, 'unknown'),
    totalBasis: firstDefined(comparison.total_basis, comparison.document_total_basis, ''),
    totalDetail: firstDefined(comparison.total_detail, comparison.document_total_detail, ''),
    sourceKind: firstDefined(comparison.source_kind, ''),
    sourceFilename: firstDefined(comparison.source_filename, ''),
    parseSource: firstDefined(comparison.parse_source, ''),
    reviewOnly: comparison.review_only !== false,
    completeForMissingLines: comparison.complete_for_missing_lines === true,
    warnings: Array.isArray(comparison.warnings) ? comparison.warnings : [],
    summary: comparison.summary && typeof comparison.summary === 'object' ? comparison.summary : {},
    lines: [...lines, ...normalizedUnmatchedRows],
  };
};

const comparisonFromPOResult = (result, quote, summary) => {
  if (!result) return normalizeCommercialComparison({}, quote, summary);
  if (result.commercial_comparison) {
    return normalizeCommercialComparison(result.commercial_comparison, quote, summary);
  }
  const quoteLines = quote?.lines || [];
  const suggestions = Array.isArray(result.suggestions) ? result.suggestions : [];
  const matchedLines = suggestions.map((suggestion) => normalizeComparisonLine({
    ...suggestion,
    quotation_line_id: suggestion.quotation_line_id,
    quote_item_name: firstDefined(suggestion.quotation_line_label, suggestion.requested_item_name),
    lpo_item_name: firstDefined(suggestion.po_item_name, suggestion.po_row?.item_name, suggestion.requested_item_name),
    // The PO-facing columns must only show values actually parsed from the
    // customer's document. Suggested values can fall back to our quotation.
    accepted_quantity: firstDefined(suggestion.po_quantity, suggestion.po_row?.quantity),
    accepted_unit: firstDefined(suggestion.po_unit, suggestion.po_row?.unit),
    accepted_unit_price: firstDefined(suggestion.po_unit_price, suggestion.po_row?.unit_price),
    accepted_line_total: firstDefined(suggestion.po_line_total, suggestion.po_row?.line_total),
    status: suggestion.comparison_status || suggestion.suggested_outcome_status,
  }, quoteLines));
  const matchedIds = matchedLines.map((line) => line.quotationLineId);
  const missingIds = Array.isArray(result.missing_quote_line_ids) ? result.missing_quote_line_ids : [];
  const resultWarnings = Array.isArray(result.warnings) ? result.warnings : [];
  const hasIncompleteWarning = resultWarnings.some((warning) => (
    /aggregate|incomplete|unmatched|could not|failed|manual(?:ly)? review|missing source/i.test(String(warning || ''))
  ));
  const missingLinesAreComplete = result.complete_for_missing_lines === true
    || (
      result.complete_for_missing_lines !== false
      && !(result.unmatched_po_rows || []).length
      && !hasIncompleteWarning
    );
  const missingLines = missingIds
    .filter((lineId) => !hasLineId(matchedIds, lineId))
    .map((lineId) => normalizeComparisonLine({
      quotation_line_id: lineId,
      status: missingLinesAreComplete ? 'not_ordered' : 'uncertain',
      review_required: !missingLinesAreComplete,
      reason: missingLinesAreComplete
        ? 'This quoted line was not found on the parsed LPO.'
        : 'This line was not matched, but the parse was incomplete or had unmatched LPO rows.',
    }, quoteLines));
  const unmatchedRows = Array.isArray(result.unmatched_po_rows)
    ? result.unmatched_po_rows.map(normalizeUnmatchedLPORow)
    : [];
  const comparison = normalizeCommercialComparison({
    company_name: quote?.company_name,
    quotation_number: quote?.quotation_number,
    lpo_number: firstDefined(result.lpo_number, result.po_number, result.document_number),
    lpo_total: firstDefined(result.lpo_total, result.po_total, result.document_total),
    total_result: firstDefined(result.total_result, result.document_total_result),
    warnings: result.warnings,
  }, quote, summary);
  return { ...comparison, lines: [...matchedLines, ...missingLines, ...unmatchedRows] };
};

const totalResultText = (value, basis, detail) => {
  if (detail) return String(detail);
  const normalized = String(value || 'unknown').trim().toLowerCase();
  const normalizedBasis = String(basis || '').trim().toLowerCase();
  if (['exact', 'match', 'matched'].includes(normalized)) {
    if (/subtotal|before_vat|net/.test(normalizedBasis)) return 'Matches quote subtotal before VAT';
    if (/total|gross|vat/.test(normalizedBasis)) return 'Matches quote total incl. VAT';
    return 'Totals match (basis not recorded)';
  }
  if (['conflict', 'mismatch', 'different'].includes(normalized)) return 'Totals differ - review';
  if (['partial', 'selected_lines'].includes(normalized)) return 'Partial LPO total';
  return 'Not enough data to compare';
};

const CommercialComparisonTable = ({ comparison, selectedLineIds, actionableLineIds, onToggleLine }) => {
  const rows = comparison?.lines || [];
  const currency = comparison?.currency || 'AED';
  const selectable = typeof onToggleLine === 'function';
  if (!rows.length) {
    return (
      <div className="qm-empty subtle">
        No line-level comparison is available yet. Confirm and parse this evidence to build it from the selected LPO source.
      </div>
    );
  }
  return (
    <div className="qm-table-wrap qm-commercial-comparison-wrap">
      <table className="qm-table qm-commercial-comparison-table">
        <thead>
          <tr>
            {selectable && <th rowSpan="2">Apply</th>}
            <th rowSpan="2">Our quoted item</th>
            <th rowSpan="2">Customer LPO item</th>
            <th colSpan="3">Our quotation</th>
            <th colSpan="3">Customer accepted</th>
            <th rowSpan="2">Decision</th>
          </tr>
          <tr>
            <th>Qty</th>
            <th>Unit price</th>
            <th>Total incl. VAT</th>
            <th>Qty</th>
            <th>Unit price</th>
            <th>LPO line total</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((line, index) => {
            const statusInfo = comparisonStatusDetails[line.status] || comparisonStatusDetails.uncertain;
            const canApply = selectable
              && line.quotationLineId !== undefined
              && line.quotationLineId !== null
              && hasLineId(actionableLineIds || [], line.quotationLineId);
            const checked = canApply && hasLineId(selectedLineIds || [], line.quotationLineId);
            const acceptedMissingLabel = line.status === 'not_ordered' ? 'Not ordered' : 'Not stated';
            return (
              <tr key={`${line.quotationLineId ?? 'lpo'}-${line.po_row_index ?? line.row_index ?? index}`} className={`qm-commercial-row status-${statusInfo.className} ${line.review_required ? 'review-required' : ''}`}>
                {selectable && (
                  <td>
                    {canApply ? (
                      <input
                        type="checkbox"
                        aria-label={`Apply decision for ${line.quoteItemName}`}
                        checked={checked}
                        onChange={() => onToggleLine(line.quotationLineId)}
                      />
                    ) : <span title="An unmatched LPO row cannot be applied until it is linked to a quotation line.">-</span>}
                  </td>
                )}
                <td><strong>{line.quoteItemName}</strong></td>
                <td><strong>{line.lpoItemName}</strong></td>
                <td>{optionalQuantity(line.quotedQuantity, line.quotedUnit)}</td>
                <td>{optionalUnitMoney(line.quotedUnitPrice, currency)}</td>
                <td>{optionalMoney(line.quotedLineTotal, currency)}</td>
                <td>{optionalQuantity(line.acceptedQuantity, line.acceptedUnit, acceptedMissingLabel)}</td>
                <td>{optionalUnitMoney(line.acceptedUnitPrice, currency, acceptedMissingLabel)}</td>
                <td>
                  {optionalMoney(line.acceptedLineTotal, currency, acceptedMissingLabel)}
                  {line.accepted_line_total_derived && (
                    <small className="qm-derived-total-note">Calculated from LPO qty x price</small>
                  )}
                </td>
                <td>
                  <span className={`qm-commercial-status ${statusInfo.className}`}>{statusInfo.label}</span>
                  {line.review_required && <small className="qm-commercial-review-flag">Review required</small>}
                  {line.confidence !== undefined && line.confidence !== null && line.confidence !== '' && (
                    <small>{Math.round(Number(line.confidence || 0))}% match confidence</small>
                  )}
                  {line.reason && <small>{line.reason}</small>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

const safelyActionableComparisonStatuses = new Set(['accepted', 'repriced', 'reduced', 'reduced_repriced']);

const actualAcceptedValues = (row) => ({
  quantity: firstDefined(
    row?.acceptedQuantity,
    row?.accepted_quantity,
    row?.po_quantity,
    row?.lpo_quantity,
    row?.po_row?.quantity
  ),
  unitPrice: firstDefined(
    row?.acceptedUnitPrice,
    row?.accepted_unit_price,
    row?.po_unit_price,
    row?.lpo_unit_price,
    row?.po_row?.unit_price
  ),
});

const isSafelyActionableMatchedLine = (line) => {
  const status = normalizeComparisonStatus(line);
  const actual = actualAcceptedValues(line);
  return safelyActionableComparisonStatuses.has(status)
    && !line?.review_required
    && !line?.requires_review
    && numberOrNull(actual.quantity) !== null
    && numberOrNull(actual.unitPrice) !== null;
};

const defaultSelectedPOResultIds = (result) => {
  const authoritativeLines = result?.commercial_comparison?.lines;
  if (Array.isArray(authoritativeLines)) {
    return uniqueLineIds(
      authoritativeLines
        .filter(isSafelyActionableMatchedLine)
        .map((line) => firstDefined(line.quotation_line_id, line.quote_line_id))
    );
  }
  return uniqueLineIds(
    (result?.suggestions || [])
      .filter(isSafelyActionableMatchedLine)
      .map((suggestion) => suggestion.quotation_line_id)
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
  const [poResultEvidenceId, setPoResultEvidenceId] = useState(null);
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
  const evidenceReturnFocusRef = useRef(null);
  const evidenceCloseButtonRef = useRef(null);

  const openEvidenceReview = useCallback((evidenceId) => {
    evidenceReturnFocusRef.current = typeof document === 'undefined' ? null : document.activeElement;
    setNotice(null);
    setErrorInfo(null);
    setSelectedEvidenceId(evidenceId);
  }, []);

  const closeEvidenceReview = useCallback(() => {
    setSelectedEvidenceId(null);
  }, []);

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
  const selectedEvidenceSourceText = useMemo(
    () => evidenceSourceText(selectedEvidenceForReview),
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
  const sourceEvidenceComparison = useMemo(
    () => normalizeCommercialComparison(
      selectedEvidenceForReview?.commercial_comparison,
      quote,
      summary
    ),
    [selectedEvidenceForReview, quote, summary]
  );
  const parsedPOComparison = useMemo(
    () => comparisonFromPOResult(poResult, quote, summary),
    [poResult, quote, summary]
  );
  const selectedEvidenceUsesParsedComparison = Boolean(
    poResult
    && poResultEvidenceId !== null
    && sameLineId(poResultEvidenceId, selectedEvidenceId)
  );
  const selectedEvidenceComparison = useMemo(
    () => (
      selectedEvidenceUsesParsedComparison
        ? parsedPOComparison
        : sourceEvidenceComparison
    ),
    [selectedEvidenceUsesParsedComparison, parsedPOComparison, sourceEvidenceComparison]
  );
  const parsedActionableLineIds = useMemo(
    () => uniqueLineIds([
      ...parsedPOComparison.lines
        .filter((line) => (
          isSafelyActionableMatchedLine(line)
          && (poResult?.suggestions || []).some((suggestion) => (
            sameLineId(suggestion.quotation_line_id, line.quotationLineId)
          ))
        ))
        .map((line) => line.quotationLineId),
      ...(poResult?.missing_quote_line_ids || []).filter((lineId) => (
        parsedPOComparison.lines.some((line) => (
          sameLineId(line.quotationLineId, lineId) && line.status === 'not_ordered'
        ))
      )),
    ]),
    [poResult, parsedPOComparison]
  );
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
    if (!selectedEvidenceId) return undefined;
    const returnFocusTo = evidenceReturnFocusRef.current;
    evidenceCloseButtonRef.current?.focus();
    const handleKeyDown = (event) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      closeEvidenceReview();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      if (returnFocusTo?.isConnected && typeof returnFocusTo.focus === 'function') returnFocusTo.focus();
    };
  }, [selectedEvidenceId, closeEvidenceReview]);

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
      return true;
    } catch (error) {
      const details = await describeQuotationError(error, 'Save quotation outcome', `PATCH /quotations/quotes/${quoteId}/outcome/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
      return false;
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
      setPoResultEvidenceId(null);
      setSelectedSuggestions(defaultSelectedPOResultIds(response.data));
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
      setPoResultEvidenceId(evidenceId);
      setSelectedSuggestions(defaultSelectedPOResultIds(response.data));
      await loadPOEvidence();
      setNotice({ type: 'success', message: 'Email link approved and parsed. The line comparison is ready in this review window; no line outcome was applied.' });
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

  const applySelectedSuggestions = async () => {
    const selectedMatchedLines = parsedPOComparison.lines.filter((line) => (
      hasLineId(selectedSuggestions, line.quotationLineId)
      && isSafelyActionableMatchedLine(line)
      && (poResult?.suggestions || []).some((suggestion) => (
        sameLineId(suggestion.quotation_line_id, line.quotationLineId)
      ))
    ));
    const suggestionIds = uniqueLineIds(selectedMatchedLines.map((line) => line.quotationLineId));
    const explicitlySelectedMissingIds = (poResult?.missing_quote_line_ids || []).filter(
      (lineId) => hasLineId(selectedSuggestions, lineId) && !hasLineId(suggestionIds, lineId)
    );
    if (!selectedMatchedLines.length && !explicitlySelectedMissingIds.length) return;
    const suggestionForLine = (lineId) => (poResult?.suggestions || []).find(
      (suggestion) => sameLineId(suggestion.quotation_line_id, lineId)
    );
    const missingLineUpdates = explicitlySelectedMissingIds.map((lineId) => ({
      id: lineId,
      outcome_status: 'rejected',
      outcome_notes: 'PO suggestion applied: this quoted line was not ordered on the reviewed LPO.',
    }));
    const applied = await patchOutcome({
      line_updates: [
        ...selectedMatchedLines.map((line) => {
          const suggestion = suggestionForLine(line.quotationLineId);
          const actual = actualAcceptedValues(line);
          const quotedQuantity = numberOrNull(line.quotedQuantity);
          const acceptedQuantity = numberOrNull(actual.quantity);
          const quantityChanged = quotedQuantity !== null
            && acceptedQuantity !== null
            && Math.abs(quotedQuantity - acceptedQuantity) > 0.0005;
          return {
            id: line.quotationLineId,
            outcome_status: quantityChanged ? 'quantity_changed' : 'accepted',
            accepted_quantity: actual.quantity,
            accepted_unit_price: actual.unitPrice,
            outcome_notes: `PO suggestion applied: ${firstDefined(suggestion?.reason, line.reason, 'Deterministic LPO line match')}`,
          };
        }),
        ...missingLineUpdates,
      ],
      po_import_id: poResult?.id,
      // Omitted quotation lines are an explicit staff outcome, not a parsed PO
      // suggestion, so they must not receive suggestion provenance.
      applied_po_line_ids: suggestionIds,
    }, 'Selected PO line decisions applied. Review and save the final outcome when ready.');
    if (applied) setSelectedSuggestions([]);
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
      {!selectedEvidence && <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />}
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

      {notice && !selectedEvidence && <div className={`qm-feedback ${notice.type}`}>{notice.message}</div>}

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
                  onReview={openEvidenceReview}
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
                  onReview={openEvidenceReview}
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
        <div className="qm-modal-backdrop" role="presentation" onClick={closeEvidenceReview}>
          <div
            className="qm-modal qm-evidence-detail-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby={`qm-evidence-dialog-title-${selectedEvidence.id}`}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="qm-panel-heading">
              <div>
                <span className="qm-step-kicker">Gmail source review</span>
                <h3 id={`qm-evidence-dialog-title-${selectedEvidence.id}`}>{selectedEvidenceComparison.companyName} - {selectedEvidence.subject || 'Untitled email'}</h3>
                <p>{evidenceStatusInfo(selectedEvidence).description}</p>
              </div>
              <button ref={evidenceCloseButtonRef} type="button" className="qm-secondary small" onClick={closeEvidenceReview}>Close</button>
            </div>

            <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
            {notice && <div className={`qm-feedback ${notice.type}`} aria-live="polite">{notice.message}</div>}

            <div className="qm-evidence-detail-section qm-evidence-commercial-context">
              <h4>Customer, quotation, and LPO</h4>
              <div className="qm-evidence-detail-grid qm-commercial-summary-grid">
                <div>
                  <span>Company</span>
                  <strong>{selectedEvidenceComparison.companyName}</strong>
                </div>
                <div>
                  <span>Quotation</span>
                  <strong>{selectedEvidenceComparison.quotationNumber}</strong>
                </div>
                {selectedEvidenceComparison.quotationSubtotal !== undefined && selectedEvidenceComparison.quotationSubtotal !== null && (
                  <div>
                    <span>Quote subtotal before VAT</span>
                    <strong>{optionalMoney(selectedEvidenceComparison.quotationSubtotal, selectedEvidenceComparison.currency)}</strong>
                  </div>
                )}
                {selectedEvidenceComparison.quotationVatTotal !== undefined && selectedEvidenceComparison.quotationVatTotal !== null && (
                  <div>
                    <span>Quote VAT</span>
                    <strong>{optionalMoney(selectedEvidenceComparison.quotationVatTotal, selectedEvidenceComparison.currency)}</strong>
                  </div>
                )}
                <div>
                  <span>Quote total incl. VAT</span>
                  <strong>{optionalMoney(selectedEvidenceComparison.quotationTotal, selectedEvidenceComparison.currency, 'Not available')}</strong>
                </div>
                <div>
                  <span>PO / LPO reference</span>
                  <strong>{loadingEvidenceSource && !selectedEvidenceUsesParsedComparison ? 'Loading source...' : selectedEvidenceComparison.lpoNumber}</strong>
                </div>
                <div>
                  <span>LPO stated total</span>
                  <strong>{loadingEvidenceSource && !selectedEvidenceUsesParsedComparison
                    ? 'Loading source...'
                    : optionalMoney(selectedEvidenceComparison.lpoTotal, selectedEvidenceComparison.currency, 'Not stated')}</strong>
                </div>
                <div className={`qm-total-result status-${String(selectedEvidenceComparison.totalResult || 'unknown').toLowerCase()}`}>
                  <span>Total comparison</span>
                  <strong>{loadingEvidenceSource && !selectedEvidenceUsesParsedComparison
                    ? 'Loading source...'
                    : totalResultText(
                      selectedEvidenceComparison.totalResult,
                      selectedEvidenceComparison.totalBasis,
                      selectedEvidenceComparison.totalDetail
                    )}</strong>
                </div>
              </div>
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
              <div>
                <span>Selected source</span>
                <strong>{evidenceSourceLabel(selectedEvidenceForReview)}</strong>
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

            <div className="qm-evidence-detail-section qm-commercial-comparison-section">
              <div className="qm-evidence-section-heading">
                <strong>Scanned line comparison</strong>
                <span>Review-only - nothing here changes the outcome</span>
              </div>
              <p className="qm-evidence-comparison-help">
                Omitted means the item was not present on this LPO; it does not prove the customer explicitly rejected it. Prices marked not stated are never copied from our quote into the customer-price column.
              </p>
              {selectedEvidenceComparison.warnings.map((warning) => (
                <div className="qm-notice warning" key={warning}>{warning}</div>
              ))}
              {loadingEvidenceSource && !selectedEvidenceComparison.lines.length ? (
                <div className="qm-empty subtle">Loading the deterministic item, quantity, and price comparison...</div>
              ) : (
                <CommercialComparisonTable comparison={selectedEvidenceComparison} />
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

            {selectedEvidenceSourceText.kind === 'attachment' && (
              <EvidenceTextSection
                title="Extracted attachment text"
                text={selectedEvidenceSourceText.attachmentText}
                backendTruncated={selectedEvidenceSourceText.attachmentBackendTruncated}
                emptyMessage={loadingEvidenceSource
                  ? 'Loading extracted attachment text...'
                  : 'No extracted text is available. Open the attachment below to inspect the original file.'}
              />
            )}
            {selectedEvidenceSourceText.kind === 'unknown' && (
              <EvidenceTextSection
                title="Selected source text"
                text={selectedEvidenceSourceText.selectedLegacyText}
                backendTruncated={selectedEvidenceSourceText.selectedLegacyBackendTruncated}
                emptyMessage={loadingEvidenceSource
                  ? 'Loading the selected source text...'
                  : 'No selected source text is available for this legacy evidence record.'}
              />
            )}
            <EvidenceTextSection
              title={selectedEvidenceSourceText.kind === 'email_body' ? 'Email body (selected source)' : 'Email body'}
              text={selectedEvidenceSourceText.emailBody}
              backendTruncated={selectedEvidenceSourceText.emailBodyBackendTruncated}
              emptyMessage={loadingEvidenceSource
                ? 'Loading the newest email body...'
                : 'No email body text was captured for this evidence.'}
            />
            {!selectedEvidenceSourceText.attachmentText
              && !selectedEvidenceSourceText.selectedLegacyText
              && !selectedEvidenceSourceText.emailBody
              && selectedEvidence.snippet && (
                <EvidenceTextSection title="Gmail snippet" text={selectedEvidence.snippet} emptyMessage="" />
            )}
            {evidenceSourceError && <div className="qm-notice warning">{evidenceSourceError}</div>}

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
            {poResult && sameLineId(poResultEvidenceId, selectedEvidence.id) && (
              <div className="qm-evidence-detail-section qm-parsed-comparison-section" aria-live="polite">
                <div className="qm-evidence-section-heading">
                  <strong>Confirmed link - parsed line decisions</strong>
                  <span>{selectedSuggestions.length} selected to apply</span>
                </div>
                <p className="qm-evidence-comparison-help">
                  Deterministic matched rows are preselected. Not-ordered rows stay unselected; checking one explicitly will mark that quotation line rejected when you apply. Unmatched LPO rows cannot be applied until they are linked.
                </p>
                {!!parsedPOComparison.warnings.length && (
                  <div className="qm-notice warning">{parsedPOComparison.warnings.join(' ')}</div>
                )}
                <CommercialComparisonTable
                  comparison={parsedPOComparison}
                  selectedLineIds={selectedSuggestions}
                  actionableLineIds={parsedActionableLineIds}
                  onToggleLine={(lineId) => setSelectedSuggestions((current) => (
                    hasLineId(current, lineId)
                      ? current.filter((id) => !sameLineId(id, lineId))
                      : [...current, lineId]
                  ))}
                />
                <div className="qm-action-row qm-parsed-comparison-actions">
                  <button
                    type="button"
                    className="qm-primary"
                    disabled={!selectedSuggestions.length || saving}
                    onClick={applySelectedSuggestions}
                  >
                    {saving ? 'Applying decisions...' : 'Apply selected line decisions'}
                  </button>
                  <small>Applying is the explicit staff confirmation step; parsing alone saves no line outcome.</small>
                </div>
              </div>
            )}
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
              <h3>PO line comparison</h3>
              <p>{(poResult.suggestions || []).length} matched line(s), {(poResult.unmatched_po_rows || []).length} unmatched PO row(s), {(poResult.missing_quote_line_ids || []).length} quoted line(s) not found in the PO.</p>
            </div>
            <button type="button" className="qm-primary" disabled={!selectedSuggestions.length || saving} onClick={applySelectedSuggestions}>
              {saving ? 'Applying decisions...' : 'Apply selected line decisions'}
            </button>
          </div>
          <p className="qm-evidence-comparison-help">
            Our columns come from the quotation. Customer columns show only values actually stated on the parsed PO/LPO. Deterministic matches are preselected; omitted lines require an explicit checkbox before they can be applied as rejected.
          </p>
          {!!parsedPOComparison.warnings.length && <div className="qm-notice warning">{parsedPOComparison.warnings.join(' ')}</div>}
          <CommercialComparisonTable
            comparison={parsedPOComparison}
            selectedLineIds={selectedSuggestions}
            actionableLineIds={parsedActionableLineIds}
            onToggleLine={(lineId) => setSelectedSuggestions((current) => (
              hasLineId(current, lineId)
                ? current.filter((id) => !sameLineId(id, lineId))
                : [...current, lineId]
            ))}
          />
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
