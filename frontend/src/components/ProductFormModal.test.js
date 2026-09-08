import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ProductFormModal from './ProductFormModal';
import axiosInstance from '../utils/axios';

jest.mock('../utils/axios', () => ({
  __esModule: true,
  default: {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    patch: jest.fn(),
    delete: jest.fn(),
  },
}));

describe('ProductFormModal duplicate prevention', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    axiosInstance.get.mockResolvedValue({ data: [] });
  });

  test('shows ranked existing Products and requires an explicit create-anyway retry', async () => {
    axiosInstance.post
      .mockResolvedValueOnce({ data: { results: [{ id: 'product', standard_name: '', reason: 'AI could not confirm a unique existing item.' }] } })
      .mockRejectedValueOnce({
        response: {
          status: 409,
          data: {
            requires_confirmation: true,
            creation_blocked: false,
            warning: 'A similar Product already exists.',
            candidates: [{
              product_id: 8,
              product_name: 'Paracetamol 500mg tablets',
              confidence: 0.94,
              dosage: '500mg',
              pack_size: '20 tablets',
              status: 'active',
            }],
          },
        },
      })
      .mockResolvedValueOnce({ data: { id: 9, name: 'Paracetamol 500mg tablet pack', status: 'draft' } });
    const onSaved = jest.fn();
    const { container } = render(
      <ProductFormModal isOpen onClose={jest.fn()} onSaved={onSaved} defaultStatus="draft" />
    );

    fireEvent.submit(container.querySelector('form'));
    expect(await screen.findByText('Check likely existing Products')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /use existing: paracetamol 500mg tablets/i })).toBeInTheDocument();
    expect(screen.getByText('AI could not confirm a unique existing item.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /create new product anyway/i }));
    await waitFor(() => expect(axiosInstance.post).toHaveBeenCalledTimes(3));
    const retryBody = axiosInstance.post.mock.calls[2][1];
    expect(retryBody.get('confirm_create')).toBe('true');
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ id: 9 }));
  });

  test('blurs stock quantity on wheel and retains the typed value', () => {
    axiosInstance.get.mockReturnValue(new Promise(() => {}));
    const { container } = render(
      <ProductFormModal isOpen onClose={jest.fn()} onSaved={jest.fn()} defaultStatus="draft" />
    );
    const stockQuantity = container.querySelector('input[name="stock_quantity"]');

    fireEvent.change(stockQuantity, { target: { value: '125' } });
    stockQuantity.focus();
    fireEvent.wheel(stockQuantity, { deltaY: 100 });

    expect(document.activeElement).not.toBe(stockQuantity);
    expect(stockQuantity).toHaveValue(125);
  });

  test('AI check completes before creation and sends the existing catalogue name', async () => {
    let resolveReview;
    axiosInstance.post.mockImplementation((url) => url.includes('/creation_review/')
      ? new Promise((resolve) => { resolveReview = resolve; })
      : Promise.resolve({ data: { id: 8, name: 'Digital Thermometer', reused: true } }));
    const onSaved = jest.fn();
    const { container } = render(<ProductFormModal isOpen onClose={jest.fn()} onSaved={onSaved} />);
    fireEvent.change(container.querySelector('input[name="name"]'), { target: { value: 'Digital Thermometre' } });
    fireEvent.submit(container.querySelector('form'));
    expect(axiosInstance.post).toHaveBeenCalledTimes(1);
    resolveReview({ data: { results: [{ id: 'product', standard_name: 'Digital Thermometer', ai_status: 'ai_matched' }] } });
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ id: 8 })));
    expect(axiosInstance.post.mock.calls[1][1].get('name')).toBe('Digital Thermometer');
  });
});
