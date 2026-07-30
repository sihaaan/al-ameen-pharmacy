import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import AdminDashboard, { adminTabFromSearch } from './AdminDashboard';
import { useAuth } from '../context/AuthContext';
import axiosInstance from '../utils/axios';

jest.mock('../context/AuthContext', () => ({
  useAuth: jest.fn(),
}));
jest.mock('../utils/axios', () => ({
  get: jest.fn(),
}));
jest.mock('../components/ProductManagement', () => () => <div>Products workspace</div>);
jest.mock('../components/OrderManagement', () => () => <div>Orders workspace</div>);
jest.mock('../components/quotations/QuotationModule', () => () => <div>Quotation workspace</div>);
jest.mock('../components/accounting/AccountingModule', () => () => <div>Accounting workspace</div>);

describe('AdminDashboard deep-link tab inference', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test.each([
    ['?quotation_tab=contract-intelligence'],
    ['?gmail_import=opaque-token'],
    ['?gmail_import_id=42'],
    ['?quote_id=77'],
  ])('opens Quotations for quotation-specific query %s', (search) => {
    expect(adminTabFromSearch(search, false)).toBe('quotations');
  });

  test('keeps explicit valid tabs and protects accounting access', () => {
    expect(adminTabFromSearch('?admin_tab=products', false)).toBe('products');
    expect(adminTabFromSearch('?admin_tab=accounting', false)).toBe('overview');
    expect(adminTabFromSearch('?admin_tab=accounting', true)).toBe('accounting');
    expect(adminTabFromSearch('?admin_tab=unknown', true)).toBe('overview');
  });

  test('does not mount the one-time Gmail claim workspace before login is known', async () => {
    useAuth.mockReturnValue({ user: null, loading: false });

    render(
      <MemoryRouter
        initialEntries={['/admin?gmail_import=opaque-token']}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/admin" element={<AdminDashboard />} />
          <Route path="/login" element={<div>Login destination</div>} />
        </Routes>
      </MemoryRouter>
    );

    expect(await screen.findByText('Login destination')).toBeInTheDocument();
    expect(screen.queryByText('Quotation workspace')).not.toBeInTheDocument();
  });

  test('mounts Quotations after an authenticated staff user follows a Gmail link', async () => {
    useAuth.mockReturnValue({
      user: { id: 3, username: 'sara', is_staff: true },
      loading: false,
    });
    axiosInstance.get
      .mockResolvedValueOnce({ data: { count: 0 } })
      .mockResolvedValueOnce({ data: [] });

    render(
      <MemoryRouter
        initialEntries={['/admin?gmail_import=opaque-token']}
        future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
      >
        <Routes>
          <Route path="/admin" element={<AdminDashboard />} />
        </Routes>
      </MemoryRouter>
    );

    expect(await screen.findByText('Quotation workspace')).toBeInTheDocument();
    await waitFor(() => expect(axiosInstance.get).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole('button', { name: /products/i }));
    expect(await screen.findByText('Products workspace')).toBeInTheDocument();
    await waitFor(() => expect(axiosInstance.get).toHaveBeenCalledTimes(2));
  });
});
