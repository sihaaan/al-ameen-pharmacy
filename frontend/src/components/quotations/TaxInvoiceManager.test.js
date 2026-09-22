import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import TaxInvoiceManager from './TaxInvoiceManager';
import quotationAPI, { describeQuotationError } from '../../api/quotations';

jest.mock('../../api/quotations', () => ({
  __esModule: true,
  default: { companies: { list: jest.fn(), retrieve: jest.fn() }, taxInvoices: {
    list: jest.fn(), retrieve: jest.fn(), create: jest.fn(), update: jest.fn(), issue: jest.fn(), pdf: jest.fn(), parseDocument: jest.fn(),
  } },
  describeQuotationError: jest.fn().mockResolvedValue({ action: 'Save', status: 400, detail: 'Please reopen the draft.' }),
}));
jest.mock('./CompanySelectWithCreate', () => ({ value, onChange, disabled }) => <select aria-label="Company" value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)}>
  <option value="">Select</option><option value="1">Resort LLC</option></select>);

const company = { id: 1, name: 'Resort LLC', billing_address: 'Dubai, UAE', trn: '100000000000003' };
const draft = { id: 20, company: 1, customer_name: company.name, customer_address: company.billing_address, customer_trn: company.trn,
  attention: '', quotation_reference: 'QT-123', lpo_number: '', invoice_date: '2026-09-22', supply_date: '2026-09-22', currency: 'AED',
  revision: 1, status: 'draft', notes: '', subtotal: '20.00', vat_total: '1.00', total: '21.00',
  lines: [{ item_name: 'Gauze', description: '', quantity: '2', unit: 'Box', unit_price: '10', vat_rate: '5', discount: '0' }] };

beforeEach(() => {
  jest.clearAllMocks();
  describeQuotationError.mockResolvedValue({ action: 'Save', status: 400, detail: 'Please reopen the draft.' });
  quotationAPI.companies.list.mockResolvedValue({ data: [company] });
  quotationAPI.companies.retrieve.mockResolvedValue({ data: company });
  quotationAPI.taxInvoices.list.mockResolvedValue({ data: { results: [draft], count: 1 } });
  quotationAPI.taxInvoices.retrieve.mockResolvedValue({ data: draft });
  quotationAPI.taxInvoices.create.mockResolvedValue({ data: draft });
  quotationAPI.taxInvoices.issue.mockResolvedValue({ data: { ...draft, status: 'issued', invoice_number: 'TI-2026-000001', revision: 2 } });
  Element.prototype.scrollIntoView = jest.fn();
});

test('manual draft saves item fields and issuance requires review of the saved draft', async () => {
  render(<TaxInvoiceManager />);
  await screen.findByText('Draft #20');
  fireEvent.click(screen.getByRole('button', { name: 'New tax invoice' }));
  fireEvent.change(screen.getByLabelText('Company'), { target: { value: '1' } });
  await waitFor(() => expect(screen.getByLabelText('Customer name')).toHaveValue('Resort LLC'));
  fireEvent.change(screen.getByLabelText('Item 1'), { target: { value: 'Gauze' } });
  fireEvent.change(screen.getByLabelText('Unit price 1'), { target: { value: '10' } });
  fireEvent.change(screen.getByLabelText('VAT 1'), { target: { value: '5' } });
  expect(screen.getByRole('button', { name: 'Issue tax invoice' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
  await waitFor(() => expect(quotationAPI.taxInvoices.create).toHaveBeenCalledWith(expect.objectContaining({ company: '1', lines: [expect.objectContaining({ item_name: 'Gauze', unit_price: '10', vat_rate: '5' })] })));
  const review = await screen.findByRole('checkbox', { name: /I checked the customer/ });
  expect(screen.getByRole('button', { name: 'Issue tax invoice' })).toBeDisabled();
  fireEvent.click(review);
  fireEvent.click(screen.getByRole('button', { name: 'Issue tax invoice' }));
  await waitFor(() => expect(quotationAPI.taxInvoices.issue).toHaveBeenCalledWith(20, 1));
  expect(await screen.findByRole('button', { name: 'Download tax invoice' })).toBeEnabled();
  expect(screen.getByLabelText('Unit price 1')).toBeDisabled();
  expect(screen.queryByRole('button', { name: 'Save draft' })).not.toBeInTheDocument();
});

test('quotation scan requires applying its preview and keeps unknown price and VAT blank', async () => {
  quotationAPI.taxInvoices.parseDocument.mockResolvedValue({ data: { company: 1, details: { customer_name: 'Resort LLC', quotation_number: 'QT-123' },
    lines: [{ item_name: 'Gauze', quantity: '2', unit: 'Box', unit_price: null, vat_rate: null, discount: '0' }],
    warnings: ['Check prices'], import_token: 'signed-source', source_filename: 'quotation.pdf' } });
  render(<TaxInvoiceManager />);
  fireEvent.click(screen.getByRole('button', { name: 'New tax invoice' }));
  fireEvent.change(screen.getByLabelText('Or paste quotation text'), { target: { value: 'Quote: Gauze 2 boxes' } });
  fireEvent.click(screen.getByRole('button', { name: 'Scan quotation' }));
  await screen.findByText('Check prices');
  expect(screen.getByLabelText('Item 1')).toHaveValue('');
  fireEvent.click(screen.getByRole('button', { name: 'Use these items' }));
  await waitFor(() => expect(screen.getByLabelText('Item 1')).toHaveValue('Gauze'));
  expect(screen.getByLabelText('Unit price 1')).toHaveValue(null);
  expect(screen.getByLabelText('VAT 1')).toHaveValue('');
  expect(screen.getByLabelText('Quotation reference')).toHaveValue('QT-123');
  expect(quotationAPI.taxInvoices.create).not.toHaveBeenCalled();
  expect(quotationAPI.taxInvoices.issue).not.toHaveBeenCalled();
});

test('opening scrolls to the invoice and editing resets review until the next save', async () => {
  render(<TaxInvoiceManager />);
  fireEvent.click(await screen.findByRole('button', { name: 'Open invoice' }));
  fireEvent.click(await screen.findByRole('checkbox', { name: /I checked the customer/ }));
  expect(screen.getByRole('button', { name: 'Issue tax invoice' })).toBeEnabled();
  fireEvent.change(screen.getByLabelText('Unit price 1'), { target: { value: '99' } });
  expect(screen.getByRole('button', { name: 'Issue tax invoice' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Preview draft PDF' })).toBeDisabled();
  await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalled());
});

test('save failure keeps the entered values available for correction', async () => {
  quotationAPI.taxInvoices.update.mockRejectedValue(new Error('stale draft'));
  render(<TaxInvoiceManager />);
  fireEvent.click(await screen.findByRole('button', { name: 'Open invoice' }));
  await screen.findByLabelText('Unit price 1');
  fireEvent.change(screen.getByLabelText('Unit price 1'), { target: { value: '99' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
  await screen.findByText('Please reopen the draft.');
  expect(screen.getByLabelText('Unit price 1')).toHaveValue(99);
  expect(screen.getByRole('button', { name: 'Issue tax invoice' })).toBeDisabled();
});

test('choosing a company loads its billing address from the detail endpoint', async () => {
  quotationAPI.companies.list.mockResolvedValue({ data: [{ id: 1, name: company.name }] });
  render(<TaxInvoiceManager />);
  await screen.findByText('Draft #20');
  fireEvent.click(screen.getByRole('button', { name: 'New tax invoice' }));
  fireEvent.change(screen.getByLabelText('Company'), { target: { value: '1' } });
  await waitFor(() => expect(screen.getByLabelText('Customer billing address')).toHaveValue(company.billing_address));
  expect(quotationAPI.companies.retrieve).toHaveBeenCalledWith('1');
});
