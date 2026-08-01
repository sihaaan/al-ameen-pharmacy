import React from 'react';
import { render, screen } from '@testing-library/react';
import quotationAPI from '../../api/quotations';
import {
  ContractIntelligenceManager,
  ContractWarningReview,
} from './ContractIntelligenceManager';

jest.mock('../../api/quotations', () => ({
  __esModule: true,
  default: {
    gmail: {
      status: jest.fn(),
      connectUrl: jest.fn(),
      disconnect: jest.fn(),
    },
    companies: { list: jest.fn() },
    contractIntelligence: { runs: jest.fn() },
  },
  describeQuotationError: jest.fn(),
  formatQuotationError: jest.fn(() => 'formatted error'),
}));

describe('ContractWarningReview', () => {
  test('keeps later attachment warnings discoverable instead of silently dropping them', () => {
    render(
      <ContractWarningReview
        warnings={[
          'First warning',
          'Second warning',
          'Third warning',
          'Fourth attachment warning',
          'Fifth attachment warning',
        ]}
      />,
    );

    expect(screen.getByText('First warning')).toBeInTheDocument();
    expect(screen.getByText('Third warning')).toBeInTheDocument();
    expect(screen.getByText('Show 2 more warnings')).toBeInTheDocument();
    expect(screen.getByText('Fourth attachment warning')).toBeInTheDocument();
    expect(screen.getByText('Fifth attachment warning')).toBeInTheDocument();
  });
});

describe('ContractIntelligenceManager Gmail recovery status', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    quotationAPI.companies.list.mockResolvedValue({ data: [] });
    quotationAPI.contractIntelligence.runs.mockResolvedValue({ data: [] });
  });

  test('shows a reconnect action instead of presenting a mismatched mailbox as connected', async () => {
    quotationAPI.gmail.status.mockResolvedValue({
      data: {
        configured: true,
        can_manage: true,
        can_reconnect: true,
        reconnect_required: true,
        connection_unavailable_reason: 'The stored Gmail account does not match the configured designated mailbox.',
        connection: {
          email: 'wrong@example.com',
          is_connected: false,
          mailbox_matches_designated: false,
          credential_owner_username: 'gmail-owner',
        },
      },
    });

    render(<ContractIntelligenceManager />);

    expect(await screen.findByText('Reconnect required')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reconnect Gmail' })).toBeEnabled();
    expect(screen.getByText(/does not match the configured designated mailbox/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Disconnect' })).not.toBeInTheDocument();
  });

  test('does not launch an OAuth flow that cannot preserve the current mailbox row', async () => {
    quotationAPI.gmail.status.mockResolvedValue({
      data: {
        configured: true,
        can_manage: true,
        can_reconnect: false,
        reconnect_required: true,
        connection_unavailable_reason: 'Reconnect the correct Google account as a conflict-free superuser.',
        connection: {
          email: 'wrong@example.com',
          is_connected: false,
          mailbox_matches_designated: false,
          credential_owner_username: 'gmail-owner',
        },
      },
    });

    render(<ContractIntelligenceManager />);

    expect(await screen.findByText(/conflict-free superuser/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reconnect Gmail' })).toBeDisabled();
    expect(quotationAPI.gmail.connectUrl).not.toHaveBeenCalled();
  });

  test('names the missing designated-mailbox setting instead of blaming OAuth credentials', async () => {
    quotationAPI.gmail.status.mockResolvedValue({
      data: {
        configured: false,
        can_manage: true,
        reconnect_required: false,
        configuration_error: 'Set a valid GMAIL_ADDON_SHARED_MAILBOX_EMAIL before enabling designated-mailbox enforcement.',
        connection: null,
      },
    });

    render(<ContractIntelligenceManager />);

    expect(await screen.findByText(/Set a valid GMAIL_ADDON_SHARED_MAILBOX_EMAIL/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Connect Gmail' })).toBeDisabled();
  });
});
