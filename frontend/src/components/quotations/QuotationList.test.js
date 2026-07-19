import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import QuotationList, { MAILBOX_AUDIT_REQUEST_BUDGET } from './QuotationList';
import quotationAPI from '../../api/quotations';

jest.mock('./CompanySelectWithCreate', () => ({ companies = [], onChange, required, value }) => (
  <label>
    Company
    <select
      aria-label="Company"
      required={required}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      <option value="">Select company</option>
      {companies.map((company) => (
        <option key={company.id} value={company.id}>{company.name}</option>
      ))}
    </select>
  </label>
));

jest.mock('../../api/quotations', () => ({
  __esModule: true,
  default: {
    quotes: {
      list: jest.fn(),
      create: jest.fn(),
    },
    mailboxPOAudits: {
      latest: jest.fn(),
      start: jest.fn(),
      scanPage: jest.fn(),
      repairPage: jest.fn(),
      reconcile: jest.fn(),
    },
    companies: {
      list: jest.fn(),
    },
    contacts: {
      list: jest.fn(),
      create: jest.fn(),
    },
  },
  describeQuotationError: jest.fn(async (error, action, endpoint) => ({
    action,
    endpoint,
    status: error?.response?.status || 'Network error',
    detail: error?.message || 'Request failed',
  })),
  formatQuotationError: jest.fn(() => 'Request failed'),
}));

const quotation = {
  id: 21,
  quotation_number: 'Q-0021',
  company_name: 'Customer A',
  created_by_username: 'sihan',
  status: 'sent',
  outcome_status: 'pending',
  version: 1,
  currency: 'AED',
  total: '125.00',
  updated_at: '2026-07-02T08:00:00Z',
  po_evidence_candidate_count: 3,
  po_evidence_parsed_count: 1,
  po_evidence_ambiguous_count: 2,
};

describe('QuotationList PO/LPO evidence summaries', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    quotationAPI.quotes.list.mockResolvedValue({ data: [quotation] });
    quotationAPI.companies.list.mockResolvedValue({ data: [] });
    quotationAPI.contacts.list.mockResolvedValue({ data: [] });
    quotationAPI.mailboxPOAudits.latest.mockResolvedValue({
      data: { run: null, match_run: null, inventory_done: false, done: false },
    });
  });

  test('separates unparsed candidates, parsed matches, and matches needing assignment', async () => {
    render(<QuotationList onOpenQuote={jest.fn()} onReviewOutcome={jest.fn()} />);

    expect(await screen.findByText('Q-0021')).toBeInTheDocument();
    expect(screen.getByText('2 candidates')).toBeInTheDocument();
    expect(screen.getByText('2 need assignment')).toBeInTheDocument();
    expect(screen.getByText('1 parsed')).toBeInTheDocument();
  });

  test('uses the full page width and keeps the new quotation form out of the list', async () => {
    const { container } = render(<QuotationList onOpenQuote={jest.fn()} onReviewOutcome={jest.fn()} />);

    expect(await screen.findByText('Q-0021')).toBeInTheDocument();
    expect(container.querySelector('.qm-split.wide-left.single-panel')).toBeInTheDocument();

    const listPanel = screen.getByRole('region', { name: 'Quotations' });
    expect(listPanel).toHaveClass('qm-panel', 'qm-quotation-list-panel');
    expect(within(listPanel).getByRole('table')).toHaveClass('qm-table', 'qm-quotation-list-table');
    expect(within(listPanel).getByRole('button', { name: 'New Quotation' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'New Quotation' })).not.toBeInTheDocument();
  });

  test('opens the compact new quotation dialog and Cancel closes it', async () => {
    render(<QuotationList onOpenQuote={jest.fn()} onReviewOutcome={jest.fn()} />);

    await screen.findByText('Q-0021');
    fireEvent.click(screen.getByRole('button', { name: 'New Quotation' }));

    const dialog = screen.getByRole('dialog', { name: 'New Quotation' });
    expect(within(dialog).getByRole('heading', { name: 'New Quotation' })).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Company')).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Contact')).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Notes')).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByRole('dialog', { name: 'New Quotation' })).not.toBeInTheDocument();
  });

  test('creates a quotation from the dialog and opens the new quote', async () => {
    const onOpenQuote = jest.fn();
    quotationAPI.companies.list.mockResolvedValue({
      data: [{ id: 7, name: 'Customer B' }],
    });
    quotationAPI.quotes.create.mockResolvedValue({ data: { id: 88 } });

    render(<QuotationList onOpenQuote={onOpenQuote} onReviewOutcome={jest.fn()} />);

    await screen.findByText('Q-0021');
    fireEvent.click(screen.getByRole('button', { name: 'New Quotation' }));

    const dialog = screen.getByRole('dialog', { name: 'New Quotation' });
    fireEvent.change(within(dialog).getByLabelText('Company'), { target: { value: '7' } });
    fireEvent.change(within(dialog).getByLabelText('Notes'), { target: { value: 'Urgent delivery' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create Quotation' }));

    await waitFor(() => expect(quotationAPI.quotes.create).toHaveBeenCalledWith({
      company: '7',
      contact: null,
      notes: 'Urgent delivery',
    }));
    expect(onOpenQuote).toHaveBeenCalledWith(88);
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'New Quotation' })).not.toBeInTheDocument();
    });
  });

  test('ignores a stale contact response after the company changes', async () => {
    let resolveCompanyA;
    let resolveCompanyB;
    const companyAContacts = new Promise((resolve) => { resolveCompanyA = resolve; });
    const companyBContacts = new Promise((resolve) => { resolveCompanyB = resolve; });
    quotationAPI.companies.list.mockResolvedValue({
      data: [
        { id: 1, name: 'Company A' },
        { id: 2, name: 'Company B' },
      ],
    });
    quotationAPI.contacts.list.mockImplementation(({ company }) => (
      String(company) === '1' ? companyAContacts : companyBContacts
    ));

    render(<QuotationList onOpenQuote={jest.fn()} onReviewOutcome={jest.fn()} />);

    await screen.findByText('Q-0021');
    fireEvent.click(screen.getByRole('button', { name: 'New Quotation' }));
    const dialog = screen.getByRole('dialog', { name: 'New Quotation' });
    const companySelect = within(dialog).getByLabelText('Company');
    fireEvent.change(companySelect, { target: { value: '1' } });
    fireEvent.change(companySelect, { target: { value: '2' } });

    await act(async () => {
      resolveCompanyB({ data: [{ id: 22, name: 'Contact B' }] });
      await companyBContacts;
    });
    expect(await within(dialog).findByRole('option', { name: 'Contact B' })).toBeInTheDocument();

    await act(async () => {
      resolveCompanyA({ data: [{ id: 11, name: 'Contact A' }] });
      await companyAContacts;
    });
    expect(within(dialog).queryByRole('option', { name: 'Contact A' })).not.toBeInTheDocument();
    expect(within(dialog).getByRole('option', { name: 'Contact B' })).toBeInTheDocument();
  });

  test('clears contact loading state when the dialog closes and reopens', async () => {
    let resolveContacts;
    const pendingContacts = new Promise((resolve) => { resolveContacts = resolve; });
    quotationAPI.companies.list.mockResolvedValue({
      data: [{ id: 1, name: 'Company A' }],
    });
    quotationAPI.contacts.list.mockReturnValue(pendingContacts);

    render(<QuotationList onOpenQuote={jest.fn()} onReviewOutcome={jest.fn()} />);

    await screen.findByText('Q-0021');
    fireEvent.click(screen.getByRole('button', { name: 'New Quotation' }));
    let dialog = screen.getByRole('dialog', { name: 'New Quotation' });
    fireEvent.change(within(dialog).getByLabelText('Company'), { target: { value: '1' } });
    expect(within(dialog).getByRole('option', { name: 'Loading contacts...' })).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'New Quotation' })).not.toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: 'New Quotation' }));

    dialog = screen.getByRole('dialog', { name: 'New Quotation' });
    expect(within(dialog).getByRole('option', { name: 'No contact' })).toBeInTheDocument();
    expect(within(dialog).queryByRole('option', { name: 'Loading contacts...' })).not.toBeInTheDocument();

    await act(async () => {
      resolveContacts({ data: [{ id: 11, name: 'Late Contact' }] });
      await pendingContacts;
    });
  });

  test('moves focus into the dialog and restores it after Escape closes', async () => {
    render(<QuotationList onOpenQuote={jest.fn()} onReviewOutcome={jest.fn()} />);

    await screen.findByText('Q-0021');
    const trigger = screen.getByRole('button', { name: 'New Quotation' });
    trigger.focus();
    fireEvent.click(trigger);

    const dialog = screen.getByRole('dialog', { name: 'New Quotation' });
    await waitFor(() => expect(dialog).toContainElement(document.activeElement));

    fireEvent.keyDown(document, { key: 'Escape', code: 'Escape' });

    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'New Quotation' })).not.toBeInTheDocument();
    });
    expect(trigger).toHaveFocus();
  });

  test('shows mailbox inventory and safe-match totals returned by the audit API', async () => {
    quotationAPI.mailboxPOAudits.start.mockResolvedValue({
      data: {
        run: {
          id: 9,
          status: 'completed',
          exhausted: true,
          messages_scanned: 1724,
          result_size_estimate: 1724,
          relevant_messages: 84,
          pages_scanned: 18,
          errors: [],
        },
        match_run: {
          status: 'completed',
          errors: [],
          summary: { active_evidence: 2, ambiguous_messages: 3, unmatched_messages: 61 },
        },
        inventory_done: true,
        done: true,
      },
    });
    render(<QuotationList onOpenQuote={jest.fn()} onReviewOutcome={jest.fn()} />);

    await screen.findByText('Q-0021');
    fireEvent.click(screen.getByRole('button', { name: 'Audit New Mailbox Run' }));

    await waitFor(() => expect(quotationAPI.mailboxPOAudits.start).toHaveBeenCalledWith({ restart: false }));
    expect(await screen.findByText('1724 / ~1724 emails inventoried')).toBeInTheDocument();
    expect(screen.getByText('84 possible PO/LPO emails')).toBeInTheDocument();
    expect(screen.getByText('2 active review matches')).toBeInTheDocument();
    expect(screen.getByText('3 emails need assignment')).toBeInTheDocument();
    expect(screen.getByText('61 possible emails had no safe quote match')).toBeInTheDocument();
  });

  test('reports an exhausted inventory as incomplete when messages were tombstoned', async () => {
    quotationAPI.mailboxPOAudits.start.mockResolvedValue({
      data: {
        run: {
          id: 10,
          status: 'completed',
          exhausted: true,
          messages_scanned: 100,
          result_size_estimate: 100,
          relevant_messages: 5,
          incomplete_messages: 2,
          pages_scanned: 2,
          errors: [],
        },
        match_run: { status: 'completed', errors: [], summary: {} },
        inventory_done: true,
        inventory_complete: false,
        done: true,
      },
    });
    render(<QuotationList onOpenQuote={jest.fn()} onReviewOutcome={jest.fn()} />);

    await screen.findByText('Q-0021');
    fireEvent.click(screen.getByRole('button', { name: 'Audit New Mailbox Run' }));

    expect(await screen.findByText('2 emails could not be read after three attempts')).toBeInTheDocument();
    expect(screen.queryByText('Mailbox inventory complete')).not.toBeInTheDocument();
  });

  test('repairs one unreadable PDF per request before reconciling the completed inventory', async () => {
    const inventoryPayload = {
      run: {
        id: 12,
        status: 'completed',
        exhausted: true,
        messages_scanned: 200,
        result_size_estimate: 200,
        relevant_messages: 7,
        pages_scanned: 3,
        errors: [],
      },
      match_run: null,
      inventory_done: true,
      inventory_complete: true,
      repair_done: false,
      repair_remaining: 2,
      mailbox_vision_available: true,
      mailbox_vision_reason: '',
      done: false,
    };
    quotationAPI.mailboxPOAudits.start.mockResolvedValue({ data: inventoryPayload });
    quotationAPI.mailboxPOAudits.repairPage
      .mockResolvedValueOnce({
        data: {
          ...inventoryPayload,
          repair_remaining: 1,
          repair_summary: { repaired: 1 },
        },
      })
      .mockResolvedValueOnce({
        data: {
          ...inventoryPayload,
          repair_done: true,
          repair_remaining: 0,
          repair_summary: { repaired: 1 },
        },
      });
    quotationAPI.mailboxPOAudits.reconcile.mockResolvedValue({
      data: {
        ...inventoryPayload,
        match_run: { status: 'completed', errors: [], summary: { active_evidence: 1 } },
        repair_done: true,
        repair_remaining: 0,
        done: true,
      },
    });

    render(<QuotationList onOpenQuote={jest.fn()} onReviewOutcome={jest.fn()} />);

    await screen.findByText('Q-0021');
    fireEvent.click(screen.getByRole('button', { name: 'Audit New Mailbox Run' }));

    await waitFor(() => expect(quotationAPI.mailboxPOAudits.reconcile).toHaveBeenCalledWith(12));
    expect(quotationAPI.mailboxPOAudits.repairPage).toHaveBeenCalledTimes(2);
    expect(quotationAPI.mailboxPOAudits.repairPage).toHaveBeenNthCalledWith(1, 12);
    expect(quotationAPI.mailboxPOAudits.repairPage).toHaveBeenNthCalledWith(2, 12);
    expect(quotationAPI.mailboxPOAudits.reconcile.mock.invocationCallOrder[0]).toBeGreaterThan(
      quotationAPI.mailboxPOAudits.repairPage.mock.invocationCallOrder[1],
    );
    expect(await screen.findByText('1 active review match')).toBeInTheDocument();
  });

  test('shows why PDF vision is unavailable without blocking reconciliation', async () => {
    quotationAPI.mailboxPOAudits.start.mockResolvedValue({
      data: {
        run: {
          id: 13,
          status: 'completed',
          exhausted: true,
          messages_scanned: 10,
          result_size_estimate: 10,
          relevant_messages: 1,
          pages_scanned: 1,
          errors: [],
        },
        match_run: { status: 'completed', errors: [], summary: {} },
        inventory_done: true,
        inventory_complete: true,
        repair_done: true,
        repair_remaining: 0,
        mailbox_vision_available: false,
        mailbox_vision_reason: 'Mailbox PDF vision is not enabled.',
        done: true,
      },
    });

    render(<QuotationList onOpenQuote={jest.fn()} onReviewOutcome={jest.fn()} />);

    await screen.findByText('Q-0021');
    fireEvent.click(screen.getByRole('button', { name: 'Audit New Mailbox Run' }));

    expect(await screen.findByText('PDF vision unavailable: Mailbox PDF vision is not enabled.')).toBeInTheDocument();
    expect(quotationAPI.mailboxPOAudits.repairPage).not.toHaveBeenCalled();
  });

  test('pauses explicitly at the browser request budget and resumes the saved audit', async () => {
    const activePayload = {
      run: {
        id: 11,
        status: 'running',
        exhausted: false,
        messages_scanned: 25,
        result_size_estimate: 5000,
        relevant_messages: 1,
        pages_scanned: 1,
        errors: [],
      },
      match_run: null,
      inventory_done: false,
      done: false,
    };
    quotationAPI.mailboxPOAudits.start.mockResolvedValue({ data: activePayload });
    quotationAPI.mailboxPOAudits.scanPage.mockResolvedValue({ data: activePayload });

    render(<QuotationList onOpenQuote={jest.fn()} onReviewOutcome={jest.fn()} />);

    await screen.findByText('Q-0021');
    fireEvent.click(screen.getByRole('button', { name: 'Audit New Mailbox Run' }));

    expect(await screen.findByText(
      `Paused after ${MAILBOX_AUDIT_REQUEST_BUDGET} audit requests in this browser action; progress is saved. Select Resume Mailbox Audit to continue.`,
    )).toBeInTheDocument();
    expect(quotationAPI.mailboxPOAudits.scanPage).toHaveBeenCalledTimes(MAILBOX_AUDIT_REQUEST_BUDGET - 1);

    quotationAPI.mailboxPOAudits.start.mockResolvedValueOnce({
      data: {
        ...activePayload,
        run: { ...activePayload.run, status: 'completed', exhausted: true, messages_scanned: 5000 },
        match_run: { status: 'completed', errors: [], summary: {} },
        inventory_done: true,
        inventory_complete: true,
        done: true,
      },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Resume Mailbox Audit' }));

    await waitFor(() => expect(quotationAPI.mailboxPOAudits.start).toHaveBeenCalledTimes(2));
    expect(quotationAPI.mailboxPOAudits.start).toHaveBeenLastCalledWith({ restart: false });
    expect(await screen.findByText('Mailbox inventory complete')).toBeInTheDocument();
  });
});
