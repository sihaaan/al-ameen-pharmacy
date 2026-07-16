import {
  importCompanyRequestIsCurrent,
  importedInquiryLinePayload,
  importedLineNameEditPatch,
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
});
