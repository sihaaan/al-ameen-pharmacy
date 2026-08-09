import {
  applyDetectedSourcePricing,
  aiCandidateWouldLoseReviewedRows,
  autoApplyDetectedSourcePricing,
  detectedSourcePricingForLine,
  importCompanyRequestIsCurrent,
  importedInquiryLinePayload,
  importedLineNameEditPatch,
  inquiryPreviewHasReviewedPricing,
  inquiryUploadModeForFile,
  insertInquiryRow,
  moveInquiryRow,
  previewWithoutAutoAppliedSourcePricing,
  removeDetectedSourcePricing,
  resetImportedMatchesForCompanyChange,
  summarizeDetectedSourcePricing,
} from './InquiryManager';
import { releaseNumberWheelFocus } from '../../utils/numberInput';

describe('InquiryManager imported match provenance', () => {
  test('editing the requested name clears a stale Product confirmation', () => {
    expect(importedLineNameEditPatch('Different customer item')).toEqual({
      raw_name: 'Different customer item',
      matched_product: null,
      match_reason: '',
      match_status: 'unresolved',
      match_confirmed_by_user: false,
    });
  });

  test('only carries explicit staff confirmation into the imported payload', () => {
    const baseLine = {
      raw_name: 'Customer item',
      quantity: '2.000',
      vat_rate: '5',
      matched_product: 17,
      match_status: 'confirmed',
      parse_confidence: 0.94,
    };

    expect(importedInquiryLinePayload(baseLine).match_confirmed_by_user).toBe(false);
    expect(importedInquiryLinePayload({
      ...baseLine,
      match_confirmed_by_user: true,
    }).match_confirmed_by_user).toBe(true);
  });

  test('detects employee or price-reference pricing before AI cleanup', () => {
    expect(inquiryPreviewHasReviewedPricing({
      lines: [{ raw_name: 'Unpriced', unit_price: null, vat_rate: null }],
    })).toBe(false);
    expect(inquiryPreviewHasReviewedPricing({
      lines: [{ raw_name: 'Typed price', unit_price: '12.50', vat_rate: '0' }],
    })).toBe(true);
    expect(inquiryPreviewHasReviewedPricing({
      lines: [{ raw_name: 'VAT reviewed', unit_price: null, vat_rate: '5' }],
    })).toBe(true);
    expect(inquiryPreviewHasReviewedPricing({
      lines: [{ raw_name: 'Reference', price_reference_status: 'matched' }],
    })).toBe(true);
    expect(inquiryPreviewHasReviewedPricing({
      lines: [{ raw_name: 'Manually cleared price', unit_price: '', _price_reviewed_by_user: true }],
    })).toBe(true);
    expect(inquiryPreviewHasReviewedPricing({
      lines: [{
        raw_name: 'Auto priced',
        unit_price: '12.50',
        vat_rate: '5',
        customer_unit_price: '12.50',
        customer_vat_rate: '5',
        _price_applied_from_source: true,
        _vat_applied_from_source: true,
      }],
    })).toBe(false);
  });

  test('strictly reads valid detected source prices and supported VAT rates', () => {
    expect(detectedSourcePricingForLine({
      customer_unit_price: '12.50',
      customer_vat_rate: '5.00%',
    })).toEqual({ unitPrice: '12.50', vatRate: '5' });
    expect(detectedSourcePricingForLine({
      customer_unit_price: '0',
      customer_vat_rate: '5e0',
      customer_vat: 'VAT rate 0%; VAT amount 0',
    })).toEqual({ unitPrice: '', vatRate: '0' });
    expect(detectedSourcePricingForLine({
      customer_unit_price: '-4',
      customer_vat_rate: '7',
    })).toEqual({ unitPrice: '', vatRate: '' });
  });

  test('applies detected fields without overwriting reviewed price, VAT, or price references', () => {
    const result = applyDetectedSourcePricing([
      {
        _client_row_id: 'blank',
        unit_price: '',
        vat_rate: '0',
        customer_unit_price: '12.50',
        customer_vat_rate: '5',
      },
      {
        _client_row_id: 'reviewed-price',
        unit_price: '99.00',
        vat_rate: '0',
        customer_unit_price: '15.00',
        customer_vat_rate: '5',
      },
      {
        _client_row_id: 'reviewed-vat',
        unit_price: '',
        vat_rate: '0',
        _vat_reviewed_by_user: true,
        customer_unit_price: '20.00',
        customer_vat_rate: '5',
      },
      {
        _client_row_id: 'reference',
        unit_price: '30.00',
        vat_rate: '5',
        price_reference_status: 'matched',
        customer_unit_price: '10.00',
        customer_vat_rate: '0',
      },
    ]);

    expect(result.lines[0]).toEqual(expect.objectContaining({ unit_price: '12.50', vat_rate: '5' }));
    expect(result.lines[1]).toEqual(expect.objectContaining({ unit_price: '99.00', vat_rate: '5' }));
    expect(result.lines[2]).toEqual(expect.objectContaining({ unit_price: '20.00', vat_rate: '0' }));
    expect(result.lines[3]).toEqual(expect.objectContaining({ unit_price: '30.00', vat_rate: '5' }));
    expect(result).toEqual(expect.objectContaining({ priceCount: 2, vatCount: 2, skippedReviewedCount: 1 }));
    expect(summarizeDetectedSourcePricing(result.lines)).toEqual(expect.objectContaining({
      priceRows: 4,
      vatRows: 4,
      appliedPriceRows: 2,
      appliedVatRows: 2,
      evidenceRows: 4,
      adoptableRows: 0,
    }));
  });

  test('auto-applies source pricing and removes only unchanged auto-applied fields', () => {
    const [autoPriced] = autoApplyDetectedSourcePricing([{
      _client_row_id: 'stable-row',
      unit_price: '',
      vat_rate: '0',
      customer_unit_price: '12.50',
      customer_vat_rate: '5',
      notes: 'Customer/source unit price: 12.50',
    }]);
    expect(autoPriced).toEqual(expect.objectContaining({
      _client_row_id: 'stable-row',
      unit_price: '12.50',
      vat_rate: '5',
      _price_applied_from_source: true,
      _vat_applied_from_source: true,
    }));

    const autoOnlyRemoved = removeDetectedSourcePricing([autoPriced]);
    expect(importedInquiryLinePayload(autoOnlyRemoved.lines[0])).toEqual(expect.objectContaining({
      unit_price: null,
      vat_rate: '0',
      notes: 'Customer/source unit price: 12.50',
    }));

    const manualPrice = {
      ...autoPriced,
      unit_price: '99.00',
      _price_applied_from_source: false,
      _source_price_suppressed_by_user: true,
    };
    const removed = removeDetectedSourcePricing([manualPrice]);
    expect(removed).toEqual(expect.objectContaining({ priceCount: 0, vatCount: 1 }));
    expect(removed.lines[0]).toEqual(expect.objectContaining({
      _client_row_id: 'stable-row',
      unit_price: '99.00',
      vat_rate: '0',
      customer_unit_price: '12.50',
      notes: 'Customer/source unit price: 12.50',
    }));
    expect(autoApplyDetectedSourcePricing(removed.lines)[0]).toEqual(expect.objectContaining({
      unit_price: '99.00',
      vat_rate: '0',
    }));

    const referenceLine = {
      ...autoPriced,
      unit_price: '30.00',
      price_reference_status: 'matched',
    };
    expect(removeDetectedSourcePricing([referenceLine]).lines[0]).toEqual(expect.objectContaining({
      unit_price: '30.00',
      vat_rate: '5',
      price_reference_status: 'matched',
    }));
  });

  test('sanitizes only auto-applied pricing before AI cleanup', () => {
    const autoLine = autoApplyDetectedSourcePricing([{
      raw_name: 'Auto priced',
      unit_price: '',
      vat_rate: '0',
      customer_unit_price: '12.50',
      customer_vat_rate: '5',
    }])[0];
    const preview = previewWithoutAutoAppliedSourcePricing({
      _source_pricing_suppressed_by_user: true,
      lines: [
        autoLine,
        { raw_name: 'Staff priced', unit_price: '99.00', vat_rate: '5', customer_unit_price: '10.00' },
        { raw_name: 'Reference', unit_price: '30.00', vat_rate: '5', price_reference_status: 'matched' },
      ],
    });

    expect(preview.lines[0]).toEqual(expect.objectContaining({
      unit_price: null,
      vat_rate: null,
      customer_unit_price: '12.50',
      customer_vat_rate: '5',
    }));
    expect(preview.lines[0]).not.toHaveProperty('_price_applied_from_source');
    expect(preview).not.toHaveProperty('_source_pricing_suppressed_by_user');
    expect(preview.lines[1]).toEqual(expect.objectContaining({ unit_price: '99.00', vat_rate: '5' }));
    expect(preview.lines[2]).toEqual(expect.objectContaining({ unit_price: '30.00', vat_rate: '5' }));
  });

  test('changing company clears every company-scoped imported match', () => {
    const [line] = resetImportedMatchesForCompanyChange([{
      raw_name: 'Customer item',
      quantity: '2.000',
      matched_product: 17,
      match_reason: 'Matched company alias.',
      match_status: 'confirmed',
      match_confirmed_by_user: true,
    }]);

    expect(line).toEqual(expect.objectContaining({
      raw_name: 'Customer item',
      quantity: '2.000',
      matched_product: null,
      match_reason: '',
      match_status: 'unresolved',
      match_confirmed_by_user: false,
    }));
  });

  test('rejects a response captured for an earlier company generation', () => {
    const requestContext = { company: '7', generation: 3 };

    expect(importCompanyRequestIsCurrent(requestContext, '7', 3)).toBe(true);
    expect(importCompanyRequestIsCurrent(requestContext, '8', 4)).toBe(false);
    expect(importCompanyRequestIsCurrent(requestContext, '7', 4)).toBe(false);
  });

  test('rejects a response captured before the source or row revision changed', () => {
    const requestContext = { company: '7', generation: 3, revision: 4 };

    expect(importCompanyRequestIsCurrent(requestContext, '7', 3, 4)).toBe(true);
    expect(importCompanyRequestIsCurrent(requestContext, '7', 3, 5)).toBe(false);
  });

  test('inserts and moves inquiry rows without changing their stable identity', () => {
    const first = { _client_row_id: 'first', raw_name: 'First' };
    const second = { _client_row_id: 'second', raw_name: 'Second' };
    const inserted = { _client_row_id: 'inserted', raw_name: 'Inserted' };

    expect(insertInquiryRow([first, second], 1, inserted)).toEqual([first, inserted, second]);
    expect(moveInquiryRow([first, inserted, second], 2, 0)).toEqual([second, first, inserted]);
  });

  test('detects supported spreadsheet, PDF, and image inquiry files', () => {
    expect(inquiryUploadModeForFile({ name: 'request.xlsx', type: '' })).toBe('excel');
    expect(inquiryUploadModeForFile({ name: 'request.pdf', type: 'application/pdf' })).toBe('pdf');
    expect(inquiryUploadModeForFile({ name: 'screenshot.JPEG', type: 'image/jpeg' })).toBe('image');
    expect(inquiryUploadModeForFile({ name: 'request.txt', type: 'text/plain' })).toBe('');
  });

  test('prevents wheel changes by blurring a protected number input', () => {
    const preventDefault = jest.fn();
    const blur = jest.fn();

    releaseNumberWheelFocus({ preventDefault, currentTarget: { blur } });

    expect(preventDefault).toHaveBeenCalledTimes(1);
    expect(blur).toHaveBeenCalledTimes(1);
  });

  test('refuses zero-row AI replacements and lossy Excel cleanup from either parser', () => {
    const rows = [{ raw_name: 'One' }, { raw_name: 'Two' }];

    expect(aiCandidateWouldLoseReviewedRows(
      { source_type: 'pdf', lines: rows },
      { lines: [] }
    )).toBe(true);
    expect(aiCandidateWouldLoseReviewedRows(
      { source_type: 'excel', parse_method: 'calamine_structured_v2', lines: rows },
      { lines: [rows[0]] }
    )).toBe(true);
    expect(aiCandidateWouldLoseReviewedRows(
      { source_type: 'excel', parse_method: 'openpyxl_structured_v2', lines: rows },
      { lines: [rows[0]] }
    )).toBe(true);
    expect(aiCandidateWouldLoseReviewedRows(
      { source_type: 'pasted_text', lines: rows },
      { lines: [rows[0]] }
    )).toBe(false);
  });
});
