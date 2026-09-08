import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import QuotationModule, { quotationRouteFromSearch } from './QuotationModule';

jest.mock('./CompanyManager', () => () => <div>Companies view</div>);
jest.mock('./QuoteItemManager', () => () => <div>Items view</div>);
jest.mock('./InquiryManager', () => () => <div>Regular inquiry view</div>);
jest.mock('./QuotationList', () => ({ onReviewOutcome }) => <div>Quotation list<button onClick={() => onReviewOutcome(21)}>Review accepted order</button></div>);
jest.mock('./QuotationOutcomeReview', () => ({ onDeliveryNoteCreated }) => <div>Outcome review<button onClick={() => onDeliveryNoteCreated({ id: 90, delivery_number: 'DN-90' })}>Approve & prepare DO</button></div>);
jest.mock('./DeliveryNoteManager', () => ({ initialNote }) => <div>Delivery workspace {initialNote?.delivery_number || ''}</div>);
jest.mock('./QuotationDashboard', () => () => <div>Quotation dashboard</div>);
jest.mock('./ProformaInvoiceManager', () => () => <div>Proformas view</div>);
jest.mock('./PriceHistoryPanel', () => () => <div>Price history view</div>);
jest.mock('./AuditLogPanel', () => () => <div>Audit view</div>);
jest.mock('./QuotationSettings', () => () => <div>Settings view</div>);
jest.mock('./HistoricalImportManager', () => () => <div>Historical imports view</div>);
jest.mock('./ContractIntelligenceManager', () => () => <div>Contract intelligence view</div>);
jest.mock('./QuotationEditor', () => ({
  quoteId,
  initialEmailReviewFingerprint,
  onInitialEmailReviewHandled,
  onOpenQuote,
  onOpenGmailImport,
}) => (
  <div>
    <span>Quotation editor {quoteId}</span>
    <output aria-label="initial email review fingerprint">
      {initialEmailReviewFingerprint || 'none'}
    </output>
    {initialEmailReviewFingerprint && (
      <button type="button" onClick={onInitialEmailReviewHandled}>Consume email review</button>
    )}
    <button type="button" onClick={() => onOpenQuote(89)}>Open revision</button>
    <button type="button" onClick={() => onOpenGmailImport(31)}>Open Gmail source</button>
  </div>
));
jest.mock('./GmailInquiryReview', () => ({
  __esModule: true,
  default: ({
    token,
    importId,
    onClaimed,
    onOpenQuote,
    onBack,
    backLabel,
    initialShowEvidence,
  }) => (
    <div>
      <span>Gmail token {token || 'none'}</span>
      <span>Gmail import {importId || 'none'}</span>
      <span>Gmail evidence {initialShowEvidence ? 'visible' : 'collapsed'}</span>
      <button type="button" onClick={() => onClaimed(45)}>Remember import</button>
      <button type="button" onClick={() => onOpenQuote(88)}>Open exact quote</button>
      <button type="button" onClick={onBack}>{backLabel}</button>
      <button
        type="button"
        onClick={() => onOpenQuote(88, {
          reviewEmail: true,
          quotationReviewFingerprint: 'a'.repeat(64),
        })}
      >
        Open prepared quote
      </button>
    </div>
  ),
}));

const LocationProbe = () => {
  const location = useLocation();
  return <output aria-label="location">{`${location.pathname}${location.search}`}</output>;
};

const renderModule = (initialEntry, props = {}) => render(
  <MemoryRouter
    initialEntries={[initialEntry]}
    future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
  >
    <QuotationModule {...props} />
    <LocationProbe />
  </MemoryRouter>
);

describe('QuotationModule Gmail deep links', () => {
  test('opens the prepared delivery note after outcome approval and clears it when leaving', async () => {
    renderModule('/admin?quotation_tab=quotes');
    fireEvent.click(await screen.findByRole('button', { name: 'Review accepted order' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Approve & prepare DO' }));
    expect(await screen.findByText('Delivery workspace DN-90')).toBeInTheDocument();
    expect(screen.getByLabelText('location')).toHaveTextContent('quotation_tab=deliveries');
    fireEvent.click(await screen.findByRole('button', { name: 'Quotations', exact: true }));
    fireEvent.click(await screen.findByRole('button', { name: 'Orders & Delivery Notes', exact: true }));
    expect(await screen.findByText('Delivery workspace')).toBeInTheDocument();
    expect(screen.queryByText('Delivery workspace DN-90')).not.toBeInTheDocument();
  });

  test('a review link opens the order review directly after refresh', async () => {
    renderModule('/admin?quotation_tab=quotes&quote_id=21&quotation_mode=review');
    expect(await screen.findByText('Outcome review')).toBeInTheDocument();
    expect(screen.queryByText('Quotation editor 21')).not.toBeInTheDocument();
  });

  test('gives Gmail handoffs precedence and makes a claimed import resumable', async () => {
    renderModule('/admin?gmail_import=opaque-token');

    expect(await screen.findByText('Gmail token opaque-token')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Quotations' })).toHaveClass('active');
    fireEvent.click(await screen.findByRole('button', { name: /remember import/i }));

    await waitFor(() => {
      expect(screen.getByLabelText('location').textContent).toContain('gmail_import_id=45');
      expect(screen.getByLabelText('location').textContent).not.toContain('gmail_import=opaque-token');
    });
    expect(await screen.findByText('Gmail import 45')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Quotations' })).toHaveClass('active');

    fireEvent.click(await screen.findByRole('button', { name: /open exact quote/i }));
    expect(await screen.findByText('Quotation editor 88')).toBeInTheDocument();
    expect(screen.getByLabelText('location').textContent).toContain('quote_id=88');
    expect(screen.getByLabelText('location').textContent).not.toContain('gmail_import_id');
  });

  test('opens the exact quotation from a direct quote ID', async () => {
    renderModule('/admin?quotation_tab=inquiries&quote_id=73');

    expect(await screen.findByText('Quotation editor 73')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Quotations' })).toHaveClass('active');
    expect(screen.getByLabelText('initial email review fingerprint')).toHaveTextContent('none');
  });

  test('opens a newly created revision in the editor and updates the exact quote URL', async () => {
    renderModule('/admin?quotation_tab=quotes&quote_id=73');

    fireEvent.click(await screen.findByRole('button', { name: 'Open revision' }));

    expect(await screen.findByText('Quotation editor 89')).toBeInTheDocument();
    expect(screen.getByLabelText('location').textContent).toContain('quotation_tab=quotes');
    expect(screen.getByLabelText('location').textContent).toContain('quote_id=89');
    expect(screen.getByLabelText('location').textContent).not.toContain('quote_id=73');
  });

  test('passes a prepared review only in memory and consumes it once without putting it in the URL', async () => {
    renderModule('/admin?gmail_import_id=31');

    fireEvent.click(await screen.findByRole('button', { name: 'Open prepared quote' }));
    expect(await screen.findByText('Quotation editor 88')).toBeInTheDocument();
    expect(screen.getByLabelText('initial email review fingerprint')).toHaveTextContent('a'.repeat(64));
    expect(screen.getByLabelText('location').textContent).not.toContain('fingerprint');
    expect(screen.getByLabelText('location').textContent).not.toContain('review_email');

    fireEvent.click(await screen.findByRole('button', { name: 'Consume email review' }));
    await waitFor(() => expect(
      screen.getByLabelText('initial email review fingerprint')
    ).toHaveTextContent('none'));
  });

  test('lets the standard editor reopen its Gmail source on the Quotations tab', async () => {
    renderModule('/admin?quote_id=88');

    fireEvent.click(await screen.findByRole('button', { name: 'Open Gmail source' }));

    expect(await screen.findByText('Gmail import 31')).toBeInTheDocument();
    expect(await screen.findByText('Gmail evidence visible')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Quotations' })).toHaveClass('active');
    expect(screen.getByLabelText('location').textContent).toContain('quotation_tab=quotes');
    expect(screen.getByLabelText('location').textContent).toContain('gmail_import_id=31');
    expect(screen.getByLabelText('location').textContent).toContain('gmail_return_quote_id=88');
    expect(screen.getByLabelText('location').textContent).not.toMatch(/[?&]quote_id=/);

    fireEvent.click(await screen.findByRole('button', { name: 'Back to quotation' }));
    expect(await screen.findByText('Quotation editor 88')).toBeInTheDocument();
    expect(screen.getByLabelText('location').textContent).toContain('quote_id=88');
    expect(screen.getByLabelText('location').textContent).not.toContain('gmail_import_id');
    expect(screen.getByLabelText('location').textContent).not.toContain('gmail_return_quote_id');
  });

  test('returns from Gmail intake to the quotation list, not the old inquiries page', async () => {
    renderModule('/admin?quotation_tab=quotes&gmail_import_id=31');

    fireEvent.click(await screen.findByRole('button', { name: 'Back to quotations' }));

    expect(await screen.findByText('Quotation list')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Quotations' })).toHaveClass('active');
    expect(screen.getByLabelText('location').textContent).toContain('quotation_tab=quotes');
    expect(screen.getByLabelText('location').textContent).not.toContain('gmail_import_id');
  });

  test('normalizes route precedence and rejects invalid IDs', async () => {
    expect(quotationRouteFromSearch('?quotation_tab=quotes&gmail_import=token')).toEqual({
      activeTab: 'quotes',
      gmailToken: 'token',
      gmailImportId: '',
      quoteId: null,
    });
    expect(quotationRouteFromSearch('?quote_id=12')).toEqual(expect.objectContaining({
      activeTab: 'quotes',
      quoteId: 12,
    }));
    expect(quotationRouteFromSearch(`?quote_id=-2${'&'}quotation_tab=history`)).toEqual(expect.objectContaining({
      activeTab: 'history',
      quoteId: null,
    }));
    expect(quotationRouteFromSearch('?quotation_tab=audit')).toEqual(expect.objectContaining({
      activeTab: 'dashboard',
    }));
    expect(quotationRouteFromSearch('?quotation_tab=audit', true)).toEqual(expect.objectContaining({
      activeTab: 'audit',
    }));
  });

  test('hides Audit Logs from employees and permits the owner capability', async () => {
    const employee = renderModule('/admin?quotation_tab=audit');

    expect(screen.queryByRole('button', { name: 'Audit Logs' })).not.toBeInTheDocument();
    expect(await screen.findByText('Quotation dashboard')).toBeInTheDocument();

    employee.unmount();
    renderModule('/admin?quotation_tab=audit', { canManageMailboxAudit: true });

    expect(screen.getByRole('button', { name: 'Audit Logs' })).toHaveClass('active');
    expect(await screen.findByText('Audit view')).toBeInTheDocument();
  });
});
