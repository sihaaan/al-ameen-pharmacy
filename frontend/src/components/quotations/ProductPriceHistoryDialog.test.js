import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ProductPriceHistoryDialog from './ProductPriceHistoryDialog';
import quotationAPI from '../../api/quotations';

jest.mock('../../api/quotations', () => ({
  __esModule: true,
  default: { quotes: { productPrice: jest.fn() } },
  describeQuotationError: jest.fn(async (error, action, endpoint) => ({
    action,
    endpoint,
    status: error?.response?.status || 'Network error',
    detail: error?.message || 'Request failed',
  })),
  formatQuotationError: jest.fn(() => 'Request failed'),
}));

const context = {
  product: 11,
  product_name: 'Powder-free Gloves',
  latest_quoted: {
    quotation: 20,
    quotation_number: 'Q-0020',
    quoted_at: '2026-06-01',
    quoted_unit_price: '100.000',
    quantity: '10.000',
    unit: 'box',
    currency: 'AED',
    outcome_status: 'rejected',
    accepted_unit_price: null,
    accepted_quantity: null,
    accepted_at: null,
    lpo_number: '',
  },
  latest_accepted: {
    quotation: 18,
    quotation_number: 'Q-0018',
    quoted_at: '2026-05-01',
    quoted_unit_price: '98.000',
    quantity: '12.000',
    unit: 'box',
    currency: 'AED',
    outcome_status: 'accepted',
    accepted_unit_price: '95.000',
    accepted_quantity: '12.000',
    accepted_at: '2026-05-04',
    lpo_number: 'LPO-7781',
  },
  history: [],
};

describe('ProductPriceHistoryDialog', () => {
  beforeEach(() => jest.clearAllMocks());

  test('shows separate quoted and accepted/LPO prices and closes explicitly', () => {
    const onClose = jest.fn();
    render(
      <ProductPriceHistoryDialog
        quoteId={21}
        productId={11}
        productName="Powder-free Gloves"
        initialContext={context}
        onClose={onClose}
      />
    );

    expect(screen.getByText('Last quoted')).toBeInTheDocument();
    expect(screen.getByText('AED 100.00')).toBeInTheDocument();
    expect(screen.getByText('Last accepted / LPO')).toBeInTheDocument();
    expect(screen.getByText('AED 95.00')).toBeInTheDocument();
    expect(screen.getByText(/LPO LPO-7781/)).toBeInTheDocument();
    expect(screen.getByText(/no earlier prices exist/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(quotationAPI.quotes.productPrice).not.toHaveBeenCalled();
  });

  test('loads context when it was not prefetched and reports an empty history', async () => {
    quotationAPI.quotes.productPrice.mockResolvedValue({
      data: {
        product: 12,
        product_name: 'Masks',
        latest_quoted: null,
        latest_accepted: null,
        history: [],
      },
    });

    render(<ProductPriceHistoryDialog quoteId={21} productId={12} productName="Masks" onClose={jest.fn()} />);

    await waitFor(() => expect(quotationAPI.quotes.productPrice).toHaveBeenCalledWith(21, {
      product: 12,
      history_limit: 50,
    }));
    expect(await screen.findAllByText('No history')).toHaveLength(2);
    expect(screen.getByText(/no earlier prices exist/i)).toBeInTheDocument();
  });
});
