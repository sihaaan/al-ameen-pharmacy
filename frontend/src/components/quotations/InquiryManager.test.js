import {
  aiCandidateWouldLoseReviewedRows,
  importCompanyRequestIsCurrent,
  importedInquiryLinePayload,
  importedLineNameEditPatch,
  inquiryUploadModeForFile,
  insertInquiryRow,
  moveInquiryRow,
  releaseNumberWheelFocus,
  resetImportedMatchesForCompanyChange,
} from './InquiryManager';

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

  test('prevents wheel price changes by blurring the number input', () => {
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
