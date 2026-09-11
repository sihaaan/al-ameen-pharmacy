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
      parseDocument: jest.fn(),
      list: jest.fn(), retrieve: jest.fn(), create: jest.fn(), update: jest.fn(),
      issue: jest.fn(), confirmReceipt: jest.fn(), cancel: jest.fn(), pdf: jest.fn(), updateReferences: jest.fn(),
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

test('issued note has a prominent reference editor while quantities remain locked', async () => {
  quotationAPI.deliveryNotes.updateReferences.mockResolvedValue({ data: { ...note, lpo_number: 'PO112_112353' } });
  render(<DeliveryNoteManager initialNote={note} />);
  await screen.findByRole('heading', { name: 'DN-EXAMPLE' });
  expect(screen.getByLabelText('LPO number')).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Received by'), { target: { value: 'Receiver in progress' } });
  fireEvent.click(screen.getByRole('button', { name: 'Edit references / add LPO' }));
  const editor = screen.getByRole('form', { name: 'Edit delivery note references' });
  expect(screen.getByLabelText('Quantity 1')).toBeDisabled();
  expect(screen.getByLabelText('Delivery date')).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Download PDF' })).toBeDisabled();
  expect(within(editor).queryByLabelText('Quotation reference')).not.toBeInTheDocument();
  fireEvent.change(within(editor).getByLabelText('LPO number'), { target: { value: 'PO112_112353' } });
  fireEvent.submit(editor);
  await waitFor(() => expect(quotationAPI.deliveryNotes.updateReferences).toHaveBeenCalledWith(20, { lpo_number: 'PO112_112353', invoice_number: '' }));
  expect(await screen.findByText(/References updated/)).toBeInTheDocument();
  expect(screen.getByLabelText('LPO number')).toHaveValue('PO112_112353');
  expect(screen.getByRole('button', { name: 'Download PDF' })).toBeEnabled();
  expect(screen.getByLabelText('Received by')).toHaveValue('Receiver in progress');
  expect(quotationAPI.deliveryNotes.update).not.toHaveBeenCalled();
});

test('failed reference update preserves edits and discarding restores saved values', async () => {
  quotationAPI.deliveryNotes.updateReferences.mockRejectedValue(new Error('Failed'));
  render(<DeliveryNoteManager initialNote={{ ...note, quotation: null, quotation_number: null }} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Edit references / add LPO' }));
  fireEvent.change(screen.getByLabelText('LPO number'), { target: { value: 'UNSAVED_123' } });
  fireEvent.change(screen.getByLabelText('Quotation reference'), { target: { value: 'QT_123' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save references' }));
  await screen.findByRole('alert');
  expect(screen.getByLabelText('LPO number')).toHaveValue('UNSAVED_123');
  fireEvent.click(screen.getByRole('button', { name: 'Discard reference changes' }));
  expect(screen.getByLabelText('LPO number')).toHaveValue('LPO-123');
});

test('cancelled notes keep the reference editor unavailable', async () => {
  render(<DeliveryNoteManager initialNote={{ ...note, status: 'cancelled' }} />);
  await screen.findByRole('heading', { name: 'DN-EXAMPLE' });
  expect(screen.queryByRole('button', { name: 'Edit references / add LPO' })).not.toBeInTheDocument();
});

test('deliver later persists on draft rows and all-deferred notes cannot be issued', async () => {
  const draft = { ...note, status: 'draft' };
  quotationAPI.deliveryNotes.update.mockImplementation(async (id, payload) => ({ data: { ...draft, ...payload } }));
  render(<DeliveryNoteManager initialNote={draft} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Deliver later: Gloves' }));
  expect(screen.getByText('Saved for next delivery')).toBeInTheDocument();
  expect(screen.getByLabelText('Quantity 1')).toHaveValue(4);
  expect(screen.getByRole('button', { name: 'Save & issue delivery note' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Download draft PDF' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Save draft', exact: true }));
  await waitFor(() => expect(quotationAPI.deliveryNotes.update).toHaveBeenCalledWith(20, expect.objectContaining({
    lines: [expect.objectContaining({ deliver_later: true, quantity: '4' })],
  })));
  await screen.findByText('Delivery note saved as a draft.');
  fireEvent.click(screen.getByRole('button', { name: 'Deliver now: Gloves' }));
  expect(screen.getByRole('button', { name: 'Save & issue delivery note' })).toBeEnabled();
});

test('issuing shows the linked next-delivery draft returned by the server', async () => {
  const draft = { ...note, status: 'draft' };
  quotationAPI.deliveryNotes.update.mockResolvedValue({ data: draft });
  quotationAPI.deliveryNotes.issue.mockResolvedValue({ data: { ...note,
    later_deliveries: [{ id: 21, delivery_number: 'DN-LATER', status: 'draft' }],
  } });
  quotationAPI.deliveryNotes.retrieve.mockResolvedValue({ data: { ...draft, id: 21, delivery_number: 'DN-LATER', continued_from_number: 'DN-EXAMPLE' } });
  render(<DeliveryNoteManager initialNote={draft} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Save & issue delivery note' }));
  const panel = await screen.findByRole('region', { name: 'Items saved for later' });
  expect(within(panel).getByText('DN-LATER')).toBeInTheDocument();
  fireEvent.click(within(panel).getByRole('button', { name: 'Open next delivery' }));
  await screen.findByRole('heading', { name: 'DN-LATER' });
  expect(screen.getByText(/Items saved for later from DN-EXAMPLE/)).toBeInTheDocument();
});

const parsedLpo = {
  company: 2, import_token: 'signed-source', source_filename: 'purchase-order.pdf',
  details: { customer_name: 'Warehouse customer', lpo_number: 'NEW-PO-123', delivery_address: 'Site warehouse',
    attention: 'Receiving contact', contact_phone: '', requested_delivery_date: '2026-09-06' },
  lines: [{ item_name: 'Gauze 7.5cm', quantity: '10', unit: 'BOX', description: 'Product code: GI123', quotation_line: null }],
  warnings: [],
};

test('an item marked deliver later in the parser preview keeps that choice when filling the note', async () => {
  quotationAPI.deliveryNotes.parseDocument.mockResolvedValue({ data: parsedLpo });
  render(<DeliveryNoteManager />);
  await screen.findByRole('button', { name: 'View order' });
  fireEvent.click(screen.getByRole('button', { name: 'New standalone delivery note' }));
  fireEvent.change(screen.getByLabelText('Or paste document text'), { target: { value: 'PO No: PO112_112353' } });
  fireEvent.click(screen.getByRole('button', { name: 'Read document' }));
  const preview = await screen.findByRole('region', { name: 'Detected document details' });
  fireEvent.click(within(preview).getByRole('button', { name: 'Deliver later: Gauze 7.5cm' }));
  expect(within(preview).getByText('Saved for next delivery')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Fill delivery note' }));
  expect(screen.getByRole('button', { name: 'Deliver now: Gauze 7.5cm' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Save & issue delivery note' })).toBeDisabled();
  expect(screen.getByLabelText('Quantity 1')).toHaveValue(10);
});

test('reads an LPO, stages it for review, then fills editable fields without creating or issuing a note', async () => {
  quotationAPI.deliveryNotes.parseDocument.mockResolvedValue({ data: parsedLpo });
  quotationAPI.deliveryNotes.create.mockResolvedValue({ data: { ...note, status: 'draft', quotation: null } });
  render(<DeliveryNoteManager />);
  await screen.findByRole('button', { name: 'View order' });
  fireEvent.click(screen.getByRole('button', { name: 'New standalone delivery note' }));
  const deliveryDate = screen.getByLabelText('Delivery date').value;
  fireEvent.change(screen.getByLabelText('Source file'), { target: { files: [new File(['PDF'], 'purchase-order.pdf', { type: 'application/pdf' })] } });
  fireEvent.click(screen.getByRole('button', { name: 'Read document' }));
  await screen.findByRole('region', { name: 'Detected document details' });
  const request = quotationAPI.deliveryNotes.parseDocument.mock.calls[0][0];
  expect(request.get('file').name).toBe('purchase-order.pdf');
  expect(request.get('use_ai')).toBe('true');
  expect(request.get('document_type')).toBe('auto');
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

test('imports a quotation with an editable reference and quantities without requiring an LPO', async () => {
  quotationAPI.deliveryNotes.parseDocument.mockResolvedValue({ data: {
    ...parsedLpo, document_type: 'quotation', source_filename: 'quotation.pdf',
    details: { ...parsedLpo.details, lpo_number: '', quotation_number: 'QT-123' },
  } });
  quotationAPI.deliveryNotes.create.mockResolvedValue({ data: { ...note, status: 'draft', quotation: null, quotation_reference: 'QT-123' } });
  render(<DeliveryNoteManager />);
  await screen.findByRole('button', { name: 'View order' });
  fireEvent.click(screen.getByRole('button', { name: 'New standalone delivery note' }));
  fireEvent.change(screen.getByLabelText('Document type'), { target: { value: 'quotation' } });
  fireEvent.change(screen.getByLabelText('LPO number'), { target: { value: 'OLD-LPO' } });
  fireEvent.change(screen.getByLabelText('Source file'), { target: { files: [new File(['PDF'], 'quotation.pdf', { type: 'application/pdf' })] } });
  fireEvent.click(screen.getByRole('button', { name: 'Read document' }));
  await screen.findByRole('region', { name: 'Detected document details' });
  expect(quotationAPI.deliveryNotes.parseDocument.mock.calls[0][0].get('document_type')).toBe('quotation');
  expect(screen.getByText('Quotation: QT-123')).toBeInTheDocument();
  expect(quotationAPI.deliveryNotes.create).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Fill delivery note' }));
  expect(screen.getByLabelText('LPO number')).toHaveValue('');
  expect(screen.getByLabelText('Quotation reference')).toHaveValue('QT-123');
  fireEvent.change(screen.getByLabelText('Quantity 1'), { target: { value: '4' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save draft', exact: true }));
  await waitFor(() => expect(quotationAPI.deliveryNotes.create).toHaveBeenCalledWith(expect.objectContaining({
    quotation: null, quotation_reference: 'QT-123', lpo_number: '',
    lines: [expect.objectContaining({ quantity: '4' })],
  })));
  expect(quotationAPI.deliveryNotes.issue).not.toHaveBeenCalled();
});

test('discarding a parsed LPO keeps existing item edits and does not attach its source', async () => {
  quotationAPI.deliveryNotes.parseDocument.mockResolvedValue({ data: parsedLpo });
  render(<DeliveryNoteManager />);
  await screen.findByRole('button', { name: 'View order' });
  fireEvent.click(screen.getByRole('button', { name: 'New standalone delivery note' }));
  fireEvent.change(screen.getByLabelText('Item 1'), { target: { value: 'Existing edited item' } });
  fireEvent.change(screen.getByLabelText('Or paste document text'), { target: { value: 'Purchase order text' } });
  fireEvent.click(screen.getByRole('button', { name: 'Read document' }));
  await screen.findByRole('button', { name: 'Replace items with this document' });
  fireEvent.click(screen.getByRole('button', { name: 'Discard preview' }));
  expect(screen.getByLabelText('Item 1')).toHaveValue('Existing edited item');
  expect(screen.getByRole('button', { name: 'Save draft', exact: true })).toBeEnabled();
  expect(quotationAPI.deliveryNotes.create).not.toHaveBeenCalled();
});

test('a parsing failure preserves the manually entered delivery details', async () => {
  quotationAPI.deliveryNotes.parseDocument.mockRejectedValue(new Error('Invalid LPO'));
  render(<DeliveryNoteManager />);
  await screen.findByRole('button', { name: 'View order' });
  fireEvent.click(screen.getByRole('button', { name: 'New standalone delivery note' }));
  fireEvent.change(screen.getByLabelText('LPO number'), { target: { value: 'KEEP-PO' } });
  fireEvent.change(screen.getByLabelText('Or paste document text'), { target: { value: 'Purchase order text' } });
  fireEvent.click(screen.getByRole('button', { name: 'Read document' }));
  await screen.findByRole('alert');
  expect(screen.getByLabelText('LPO number')).toHaveValue('KEEP-PO');
  expect(screen.queryByRole('button', { name: 'Fill delivery note' })).not.toBeInTheDocument();
});

test('an old parsing response cannot replace a different quotation delivery draft', async () => {
  let resolve;
  quotationAPI.deliveryNotes.parseDocument.mockReturnValue(new Promise((done) => { resolve = done; }));
  const { rerender } = render(<DeliveryNoteManager />);
  await screen.findByRole('button', { name: 'View order' });
  fireEvent.click(screen.getByRole('button', { name: 'New standalone delivery note' }));
  fireEvent.change(screen.getByLabelText('Or paste document text'), { target: { value: 'Purchase order text' } });
  fireEvent.click(screen.getByRole('button', { name: 'Read document' }));
  rerender(<DeliveryNoteManager initialNote={{ ...note, status: 'draft' }} />);
  resolve({ data: parsedLpo });
  await waitFor(() => expect(screen.getByLabelText('Item 1')).toHaveValue('Gloves'));
  expect(screen.queryByRole('region', { name: 'Detected document details' })).not.toBeInTheDocument();
  expect(screen.getByText(/filled from the approved order/)).toBeInTheDocument();
});
