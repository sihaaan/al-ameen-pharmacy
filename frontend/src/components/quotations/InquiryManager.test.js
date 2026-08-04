import {
  applyDetectedSourcePricing,
  aiCandidateWouldLoseReviewedRows,
  detectedSourcePricingForLine,
  importCompanyRequestIsCurrent,
  importedInquiryLinePayload,
  importedLineNameEditPatch,
  inquiryPreviewHasReviewedPricing,
  inquiryUploadModeForFile,
  insertInquiryRow,
  moveInquiryRow,
  resetImportedMatchesForCompanyChange,
  summarizeDetectedSourcePricing,
  undoAppliedSourcePricing,
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
      evidenceRows: 4,
      adoptableRows: 0,
    }));
  });

  test('undo reverts only unchanged values applied to the same stable row', () => {
    const applied = applyDetectedSourcePricing([{
      _client_row_id: 'stable-row',
      unit_price: '',
      vat_rate: '0',
      customer_unit_price: '12.50',
      customer_vat_rate: '5',
    }]);
    const edited = [{ ...applied.lines[0], unit_price: '99.00' }];

    expect(undoAppliedSourcePricing(edited, applied.changes)[0]).toEqual(expect.objectContaining({
      unit_price: '99.00',
      vat_rate: '0',
    }));
    expect(undoAppliedSourcePricing([{ ...applied.lines[0], _client_row_id: 'different-row' }], applied.changes)[0]).toEqual(
      expect.objectContaining({ unit_price: '12.50', vat_rate: '5' })
    );
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
