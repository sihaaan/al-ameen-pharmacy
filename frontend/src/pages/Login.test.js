import { render, screen } from '@testing-library/react';
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
} from 'react-router-dom';
import Login from './Login';
import { useAuth } from '../context/AuthContext';

jest.mock('../context/AuthContext', () => ({
  useAuth: jest.fn(),
}));

const Destination = ({ label }) => {
  const location = useLocation();
  return <div>{`${label}${location.search}${location.hash}`}</div>;
};

const renderAuthenticatedLogin = (entry) => {
  useAuth.mockReturnValue({
    login: jest.fn(),
    error: '',
    user: { id: 1, username: 'staff' },
    loading: false,
  });

  return render(
    <MemoryRouter
      initialEntries={[entry]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<Destination label="Home" />} />
        <Route path="/admin" element={<Destination label="Admin" />} />
      </Routes>
    </MemoryRouter>
  );
};

describe('Login post-authentication redirect', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('preserves a legitimate internal path with query and fragment', async () => {
    renderAuthenticatedLogin(
      '/login?next=%2Fadmin%3Fquote_id%3D42%23lines'
    );

    expect(await screen.findByText('Admin?quote_id=42#lines')).toBeInTheDocument();
  });

  test.each([
    'https%3A%2F%2Fevil.example%2Fsteal',
    '%2F%2Fevil.example%2Fsteal',
    '%2F%255cevil.example',
    'javascript%3Aalert%281%29',
  ])('falls back home for unsafe next value %s', async (next) => {
    renderAuthenticatedLogin(`/login?next=${next}`);

    expect(await screen.findByText('Home')).toBeInTheDocument();
  });
});
