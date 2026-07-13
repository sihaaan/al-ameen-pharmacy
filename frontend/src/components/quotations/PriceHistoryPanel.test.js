import { render, screen, waitFor } from '@testing-library/react';
import PriceHistoryPanel from './PriceHistoryPanel';
import quotationAPI from '../../api/quotations';

jest.mock('../../api/quotations', () => ({
  __esModule: true,
  default: {
    priceHistory: { list: jest.fn() },
    companies: { list: jest.fn(), create: jest.fn() },
    items: { list: jest.fn() },
  },
  describeQuotationError: jest.fn(async (error, action, endpoint) => ({
    action,
    endpoint,
    status: error?.response?.status || 'Network error',
    detail: error?.message || 'Request failed',
  })),
  formatQuotationError: jest.fn(() => 'Request failed'),
}));

describe('PriceHistoryPanel', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    quotationAPI.companies.list.mockResolvedValue({ data: [{ id: 7, name: 'Customer A' }] });
    quotationAPI.items.list.mockResolvedValue({ data: [{ id: 11, name: 'Gloves' }, { id: 12, name: 'Masks' }] });
    quotationAPI.priceHistory.list.mockResolvedValue({ data: [] });
  });

  test('uses the typed Product filter and follows changed props', async () => {
    const { rerender } = render(<PriceHistoryPanel companyId="7" productId="11" />);

    await waitFor(() => expect(quotationAPI.priceHistory.list).toHaveBeenCalledWith({
      company: '7',
      product: '11',
    }));
    expect(await screen.findByText(/no quoted prices match/i)).toBeInTheDocument();

    rerender(<PriceHistoryPanel companyId="7" productId="12" />);
    await waitFor(() => expect(quotationAPI.priceHistory.list).toHaveBeenLastCalledWith({
      company: '7',
      product: '12',
    }));
  });
});
