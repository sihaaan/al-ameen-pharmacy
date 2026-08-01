import { PROFORMA_LPO_UPLOAD_ACCEPT } from './ProformaInvoiceManager';

describe('Proforma LPO upload contract', () => {
  test('offers every spreadsheet and PDF type accepted by the backend', () => {
    expect(new Set(PROFORMA_LPO_UPLOAD_ACCEPT.split(','))).toEqual(new Set([
      '.pdf',
      '.xlsx',
      '.xls',
      '.xlsb',
    ]));
  });
});
