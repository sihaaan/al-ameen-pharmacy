import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import DeliveryNoteManager from './DeliveryNoteManager';
import quotationAPI, { describeQuotationError } from '../../api/quotations';

jest.mock('../../api/quotations', () => ({
  __esModule: true,
  default: {
    companies: { list: jest.fn() },
    deliveryOrders: { list: jest.fn(), retrieve: jest.fn() },
    deliveryNotes: {
      parseLpo: jest.fn(),
      list: jest.fn(), retrieve: jest.fn(), create: jest.fn(), update: jest.fn(),
      issue: jest.fn(), confirmReceipt: jest.fn(), cancel: jest.fn(), pdf: jest.fn(),
    },
  },
  describeQuotationError: jest.fn(),
}));

const order = {
  id: 1, quotation_number: 'QT-EXAMPLE', company: 2, company_name: 'Warehouse customer',
  delivery_status: 'partially_delivered', lpo_numbers: ['LPO-123'], line_count: 1, completed_line_count: 0,
  lines: [{ id: 10, item_name: 'Gloves', unit: 'Box', accepted_quantity: '10', delivered_quantity: '6', issued_quantity: '0', remaining_quantity: '4', available_quantity: '4' }],
};
const note = {
  id: 20, delivery_number: 'DN-EXAMPLE', company: 2, company_name: 'Warehouse customer',
  quotation: 1, quotation_number: 'QT-EXAMPLE', status: 'issued', delivery_date: '2026-09-05',
  lpo_number: 'LPO-123', invoice_number: '', delivery_address: 'Dubai', attention: '', contact_phone: '', notes: '',
  lines: [{ id: 30, quotation_line: 10, item_name: 'Gloves', description: '', quantity: '4', unit: 'Box' }],
};
const page = (results, extra = {}) => ({ data: { count: results.length, next: null, previous: null, results, ...extra } });

beforeEach(() => {
  jest.clearAllMocks();
  quotationAPI.companies.list.mockResolvedValue({ data: [{ id: 2, name: 'Warehouse customer', billing_address: 'Dubai' }] });
  quotationAPI.deliveryOrders.list.mockResolvedValue(page([order], { summary: { partially_delivered: 1 } }));
  quotationAPI.deliveryOrders.retrieve.mockResolvedValue({ data: order });
  quotationAPI.deliveryNotes.list.mockResolvedValue(page([note]));
  quotationAPI.deliveryNotes.retrieve.mockResolvedValue({ data: note });
  describeQuotationError.mockResolvedValue({ action: 'Delivery', detail: 'Not enough quantity available', status: 400, endpoint: 'Delivery notes' });
});

test('shows remaining accepted quantities and creates a linked draft without confirming delivery', async () => {
  quotationAPI.deliveryNotes.create.mockResolvedValue({ data: { ...note, status: 'draft' } });
  render(<DeliveryNoteManager />);
  fireEvent.click(await screen.findByRole('button', { name: 'View order' }));
  const progress = await screen.findByRole('region', { name: 'Order delivery progress' });
  expect(within(progress).getByText('Still to deliver')).toBeInTheDocument();
  fireEvent.click(within(progress).getByRole('button', { name: 'Create delivery note for remaining items' }));
  await screen.findByRole('heading', { name: 'DN-EXAMPLE' });
  expect(quotationAPI.deliveryNotes.create).toHaveBeenCalledWith({ quotation: 1, lines: [{ quotation_line: 10, quantity: '4' }] });
  expect(quotationAPI.deliveryNotes.issue).not.toHaveBeenCalled();
  expect(quotationAPI.deliveryNotes.confirmReceipt).not.toHaveBeenCalled();
});

test('opens the approved LPO delivery draft for editing without creating or issuing it again', async () => {
  render(<DeliveryNoteManager initialNote={{ ...note, status: 'draft' }} />);
  await screen.findByRole('heading', { name: 'DN-EXAMPLE' });
  expect(screen.getByLabelText('Quantity 1')).toHaveValue(4);
  fireEvent.change(screen.getByLabelText('Quantity 1'), { target: { value: '2' } });
  expect(screen.getByLabelText('Quantity 1')).toHaveValue(2);
  expect(screen.getByText(/Acceptance approved/)).toBeInTheDocument();
  expect(quotationAPI.deliveryNotes.create).not.toHaveBeenCalled();
  expect(quotationAPI.deliveryNotes.issue).not.toHaveBeenCalled();
});

test('records a partial receipt using the user-entered quantity and receiver', async () => {
  quotationAPI.deliveryNotes.confirmReceipt.mockResolvedValue({ data: {
    ...note, status: 'delivered', received_by: 'Storekeeper', received_date: '2026-09-05',
    lines: [{ ...note.lines[0], received_quantity: '2' }],
  } });
  render(<DeliveryNoteManager />);
  fireEvent.click(screen.getByRole('button', { name: 'Delivery notes', exact: true }));
  fireEvent.click(await screen.findByRole('button', { name: 'Open note' }));
  const receipt = await screen.findByRole('form', { name: 'Confirm delivery receipt' });
  fireEvent.change(within(receipt).getByLabelText('Received by'), { target: { value: 'Storekeeper' } });
  fireEvent.change(within(receipt).getByLabelText('Received quantity 1'), { target: { value: '2' } });
  fireEvent.submit(receipt);
  await screen.findByText('Receipt recorded');
  expect(quotationAPI.deliveryNotes.confirmReceipt).toHaveBeenCalledWith(20, expect.objectContaining({
    received_by: 'Storekeeper', lines: [{ id: 30, received_quantity: '2' }],
  }));
  expect(screen.getByText('Gloves: 2 of 4 Box received')).toBeInTheDocument();
});

test('saves a standalone delivery note with LPO and invoice references', async () => {
  quotationAPI.deliveryNotes.create.mockResolvedValue({ data: { ...note, status: 'draft', quotation: null, quotation_number: null } });
  render(<DeliveryNoteManager />);
  await screen.findByRole('button', { name: 'View order' });
  fireEvent.click(screen.getByRole('button', { name: 'New standalone delivery note' }));
  fireEvent.change(screen.getByLabelText('Customer'), { target: { value: '2' } });
  fireEvent.change(screen.getByLabelText('LPO number'), { target: { value: 'LPO111370' } });
  fireEvent.change(screen.getByLabelText('Invoice reference'), { target: { value: '594173' } });
  fireEvent.change(screen.getByLabelText('Item 1'), { target: { value: 'Cold packs' } });
  fireEvent.change(screen.getByLabelText('Quantity 1'), { target: { value: '5' } });
  fireEvent.change(screen.getByLabelText('Unit 1'), { target: { value: 'Packet' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save draft', exact: true }));
  await waitFor(() => expect(quotationAPI.deliveryNotes.create).toHaveBeenCalled());
  expect(quotationAPI.deliveryNotes.create).toHaveBeenCalledWith(expect.objectContaining({
    company: '2', quotation: null, lpo_number: 'LPO111370', invoice_number: '594173',
    lines: [{ quotation_line: null, item_name: 'Cold packs', description: '', unit: 'Packet', quantity: '5' }],
  }));
});

test('shows a server error and does not claim a rejected receipt succeeded', async () => {
  quotationAPI.deliveryNotes.confirmReceipt.mockRejectedValue(new Error('Rejected'));
  render(<DeliveryNoteManager />);
  fireEvent.click(screen.getByRole('button', { name: 'Delivery notes', exact: true }));
  fireEvent.click(await screen.findByRole('button', { name: 'Open note' }));
  const receipt = await screen.findByRole('form', { name: 'Confirm delivery receipt' });
  fireEvent.change(within(receipt).getByLabelText('Received by'), { target: { value: 'Receiver' } });
  fireEvent.submit(receipt);
  expect(await screen.findByRole('alert')).toHaveTextContent('Not enough quantity available');
  expect(screen.queryByText('Receipt recorded')).not.toBeInTheDocument();
});

test('issue saves the reviewed draft first and a changed draft cannot download stale contents', async () => {
  const draft = { ...note, status: 'draft' };
  quotationAPI.deliveryNotes.retrieve.mockResolvedValue({ data: draft });
  quotationAPI.deliveryNotes.update.mockResolvedValue({ data: draft });
  quotationAPI.deliveryNotes.issue.mockResolvedValue({ data: note });
  render(<DeliveryNoteManager />);
  fireEvent.click(screen.getByRole('button', { name: 'Delivery notes', exact: true }));
  fireEvent.click(await screen.findByRole('button', { name: 'Open note' }));
  await screen.findByRole('heading', { name: 'DN-EXAMPLE' });
  fireEvent.change(screen.getByLabelText('Invoice reference'), { target: { value: 'INV-NEW' } });
  expect(screen.getByRole('button', { name: 'Download draft PDF' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Save & issue delivery note' }));
  await waitFor(() => expect(quotationAPI.deliveryNotes.issue).toHaveBeenCalledWith(20));
  expect(quotationAPI.deliveryNotes.update).toHaveBeenCalledWith(20, expect.objectContaining({ invoice_number: 'INV-NEW' }));
  expect(await screen.findByRole('form', { name: 'Confirm delivery receipt' })).toBeInTheDocument();
});

const parsedLpo = {
  company: 2, import_token: 'signed-source', source_filename: 'purchase-order.pdf',
  details: { customer_name: 'Warehouse customer', lpo_number: 'NEW-PO-123', delivery_address: 'Site warehouse',
    attention: 'Receiving contact', contact_phone: '', requested_delivery_date: '2026-09-06' },
  lines: [{ item_name: 'Gauze 7.5cm', quantity: '10', unit: 'BOX', description: 'Product code: GI123', quotation_line: null }],
  warnings: [],
};

test('reads an LPO, stages it for review, then fills editable fields without creating or issuing a note', async () => {
  quotationAPI.deliveryNotes.parseLpo.mockResolvedValue({ data: parsedLpo });
  quotationAPI.deliveryNotes.create.mockResolvedValue({ data: { ...note, status: 'draft', quotation: null } });
  render(<DeliveryNoteManager />);
  await screen.findByRole('button', { name: 'View order' });
  fireEvent.click(screen.getByRole('button', { name: 'New standalone delivery note' }));
  const deliveryDate = screen.getByLabelText('Delivery date').value;
  fireEvent.change(screen.getByLabelText('LPO file'), { target: { files: [new File(['PDF'], 'purchase-order.pdf', { type: 'application/pdf' })] } });
  fireEvent.click(screen.getByRole('button', { name: 'Read LPO' }));
  await screen.findByRole('region', { name: 'Detected LPO details' });
  const request = quotationAPI.deliveryNotes.parseLpo.mock.calls[0][0];
  expect(request.get('file').name).toBe('purchase-order.pdf');
  expect(request.get('use_ai')).toBe('true');
  expect(screen.getByLabelText('Item 1')).toHaveValue('');
  expect(screen.getByRole('button', { name: 'Save draft', exact: true })).toBeDisabled();
  expect(quotationAPI.deliveryNotes.create).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Fill delivery note' }));
  expect(screen.getByLabelText('Customer')).toHaveValue('2');
  expect(screen.getByLabelText('LPO number')).toHaveValue('NEW-PO-123');
  expect(screen.getByLabelText('Delivery address')).toHaveValue('Site warehouse');
  expect(screen.getByLabelText('Attention')).toHaveValue('Receiving contact');
  expect(screen.getByLabelText('Item 1')).toHaveValue('Gauze 7.5cm');
  expect(screen.getByLabelText('Delivery date')).toHaveValue(deliveryDate);
  fireEvent.change(screen.getByLabelText('Quantity 1'), { target: { value: '6' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save draft', exact: true }));
  await waitFor(() => expect(quotationAPI.deliveryNotes.create).toHaveBeenCalledWith(expect.objectContaining({
    lpo_import_token: 'signed-source', lines: [expect.objectContaining({ quantity: '6' })],
  })));
  expect(quotationAPI.deliveryNotes.issue).not.toHaveBeenCalled();
});

test('discarding a parsed LPO keeps existing item edits and does not attach its source', async () => {
  quotationAPI.deliveryNotes.parseLpo.mockResolvedValue({ data: parsedLpo });
  render(<DeliveryNoteManager />);
  await screen.findByRole('button', { name: 'View order' });
  fireEvent.click(screen.getByRole('button', { name: 'New standalone delivery note' }));
  fireEvent.change(screen.getByLabelText('Item 1'), { target: { value: 'Existing edited item' } });
  fireEvent.change(screen.getByLabelText('Or paste LPO text'), { target: { value: 'Purchase order text' } });
  fireEvent.click(screen.getByRole('button', { name: 'Read LPO' }));
  await screen.findByRole('button', { name: 'Replace items with this LPO' });
  fireEvent.click(screen.getByRole('button', { name: 'Discard preview' }));
  expect(screen.getByLabelText('Item 1')).toHaveValue('Existing edited item');
  expect(screen.getByRole('button', { name: 'Save draft', exact: true })).toBeEnabled();
  expect(quotationAPI.deliveryNotes.create).not.toHaveBeenCalled();
});

test('a parsing failure preserves the manually entered delivery details', async () => {
  quotationAPI.deliveryNotes.parseLpo.mockRejectedValue(new Error('Invalid LPO'));
  render(<DeliveryNoteManager />);
  await screen.findByRole('button', { name: 'View order' });
  fireEvent.click(screen.getByRole('button', { name: 'New standalone delivery note' }));
  fireEvent.change(screen.getByLabelText('LPO number'), { target: { value: 'KEEP-PO' } });
  fireEvent.change(screen.getByLabelText('Or paste LPO text'), { target: { value: 'Purchase order text' } });
  fireEvent.click(screen.getByRole('button', { name: 'Read LPO' }));
  await screen.findByRole('alert');
  expect(screen.getByLabelText('LPO number')).toHaveValue('KEEP-PO');
  expect(screen.queryByRole('button', { name: 'Fill delivery note' })).not.toBeInTheDocument();
});

test('an old parsing response cannot replace a different quotation delivery draft', async () => {
  let resolve;
  quotationAPI.deliveryNotes.parseLpo.mockReturnValue(new Promise((done) => { resolve = done; }));
  const { rerender } = render(<DeliveryNoteManager />);
  await screen.findByRole('button', { name: 'View order' });
  fireEvent.click(screen.getByRole('button', { name: 'New standalone delivery note' }));
  fireEvent.change(screen.getByLabelText('Or paste LPO text'), { target: { value: 'Purchase order text' } });
  fireEvent.click(screen.getByRole('button', { name: 'Read LPO' }));
  rerender(<DeliveryNoteManager initialNote={{ ...note, status: 'draft' }} />);
  resolve({ data: parsedLpo });
  await waitFor(() => expect(screen.getByLabelText('Item 1')).toHaveValue('Gloves'));
  expect(screen.queryByRole('region', { name: 'Detected LPO details' })).not.toBeInTheDocument();
  expect(screen.getByText(/filled from the approved order/)).toBeInTheDocument();
});
