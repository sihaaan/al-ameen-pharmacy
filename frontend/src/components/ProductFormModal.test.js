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

    fireEvent.click(screen.getByRole('button', { name: /create new product anyway/i }));
    await waitFor(() => expect(axiosInstance.post).toHaveBeenCalledTimes(2));
    const retryBody = axiosInstance.post.mock.calls[1][1];
    expect(retryBody.get('confirm_create')).toBe('true');
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ id: 9 }));
  });
});
