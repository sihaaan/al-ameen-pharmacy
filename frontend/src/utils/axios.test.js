import axios from 'axios';
import React from 'react';
import { act, render, screen } from '@testing-library/react';
import { AuthProvider, useAuth } from '../context/AuthContext';
import axiosInstance, { AUTH_SESSION_CLEARED_EVENT } from './axios';

jest.mock('axios', () => {
  const instance = jest.fn((config) => Promise.resolve({ config }));
  instance.interceptors = {
    request: {
      use: jest.fn((fulfilled, rejected) => {
        instance.requestFulfilled = fulfilled;
        instance.requestRejected = rejected;
      }),
    },
    response: {
      use: jest.fn((fulfilled, rejected) => {
        instance.responseFulfilled = fulfilled;
        instance.responseRejected = rejected;
      }),
    },
  };

  return {
    __esModule: true,
    default: {
      create: jest.fn(() => instance),
      defaults: { headers: { common: {} } },
      post: jest.fn(),
      testInstance: instance,
    },
  };
});

const unauthorized = (url = '/quotations/quotes/') => ({
  config: {
    url,
    headers: { Authorization: 'Bearer expired-access' },
  },
  response: { status: 401 },
});

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
};

const AuthState = () => {
  const { user, loading } = useAuth();
  if (loading) return <span>Loading</span>;
  return <span>{user ? `Signed in: ${user.username}` : 'Signed out'}</span>;
};

describe('authenticated axios refresh handling', () => {
  beforeEach(() => {
    localStorage.clear();
    window.history.replaceState(null, '', '/admin?tab=quotations');
    axios.post.mockReset();
    axios.testInstance.mockClear();
    axios.testInstance.mockImplementation((config) => Promise.resolve({ config }));
    axios.defaults.headers.common.Authorization = 'Bearer expired-access';
  });

  test('uses one refresh for concurrent 401 responses and stores a rotated refresh token', async () => {
    localStorage.setItem('token', 'expired-access');
    localStorage.setItem('refreshToken', 'old-refresh');
    localStorage.setItem('user', '{"id":1}');
    const refresh = deferred();
    axios.post.mockReturnValue(refresh.promise);

    const firstError = unauthorized('/quotations/quotes/1/');
    const secondError = unauthorized('/companies/');
    const firstRetry = axios.testInstance.responseRejected(firstError);
    const secondRetry = axios.testInstance.responseRejected(secondError);

    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(axios.post).toHaveBeenCalledWith(
      expect.stringMatching(/\/token\/refresh\/$/),
      { refresh: 'old-refresh' }
    );

    refresh.resolve({
      data: {
        access: 'fresh-access',
        refresh: 'rotated-refresh',
      },
    });

    const [firstResponse, secondResponse] = await Promise.all([
      firstRetry,
      secondRetry,
    ]);

    expect(localStorage.getItem('token')).toBe('fresh-access');
    expect(localStorage.getItem('refreshToken')).toBe('rotated-refresh');
    expect(axios.defaults.headers.common.Authorization).toBe(
      'Bearer fresh-access'
    );
    expect(firstResponse.config.headers.Authorization).toBe(
      'Bearer fresh-access'
    );
    expect(secondResponse.config.headers.Authorization).toBe(
      'Bearer fresh-access'
    );
    expect(axios.testInstance).toHaveBeenCalledTimes(2);
  });

  test('retries a late stale 401 with the current access token without refreshing again', async () => {
    localStorage.setItem('token', 'expired-access');
    localStorage.setItem('refreshToken', 'old-refresh');
    axios.post.mockResolvedValue({
      data: {
        access: 'fresh-access',
        refresh: 'rotated-refresh',
      },
    });

    await axios.testInstance.responseRejected(
      unauthorized('/quotations/quotes/1/')
    );

    const lateError = unauthorized('/companies/');
    const lateResponse = await axios.testInstance.responseRejected(lateError);

    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(lateResponse.config.headers.Authorization).toBe(
      'Bearer fresh-access'
    );
    expect(localStorage.getItem('refreshToken')).toBe('rotated-refresh');
  });

  test('keeps the existing refresh token when rotation is not enabled by the server', async () => {
    localStorage.setItem('token', 'expired-access');
    localStorage.setItem('refreshToken', 'existing-refresh');
    axios.post.mockResolvedValue({
      data: { access: 'fresh-access' },
    });

    await axios.testInstance.responseRejected(unauthorized());

    expect(localStorage.getItem('refreshToken')).toBe('existing-refresh');
  });

  test('clears authentication and redirects to login when no refresh token exists', async () => {
    localStorage.setItem('token', 'expired-access');
    localStorage.setItem('user', '{"id":1}');
    const error = unauthorized();
    const popstate = jest.fn();
    window.addEventListener('popstate', popstate);

    await expect(
      axios.testInstance.responseRejected(error)
    ).rejects.toBe(error);

    expect(axios.post).not.toHaveBeenCalled();
    expect(localStorage.getItem('token')).toBeNull();
    expect(localStorage.getItem('refreshToken')).toBeNull();
    expect(localStorage.getItem('user')).toBeNull();
    expect(axios.defaults.headers.common.Authorization).toBeUndefined();
    expect(window.location.pathname).toBe('/login');
    expect(window.location.search).toBe(
      '?next=%2Fadmin%3Ftab%3Dquotations'
    );
    expect(popstate).toHaveBeenCalledTimes(1);
    window.removeEventListener('popstate', popstate);
  });

  test('shares a failed refresh, clears authentication, and redirects only once', async () => {
    localStorage.setItem('token', 'expired-access');
    localStorage.setItem('refreshToken', 'invalid-refresh');
    localStorage.setItem('user', '{"id":1}');
    const refreshError = new Error('refresh rejected');
    const refresh = deferred();
    axios.post.mockReturnValue(refresh.promise);
    const popstate = jest.fn();
    window.addEventListener('popstate', popstate);

    const firstRetry = axios.testInstance.responseRejected(
      unauthorized('/quotations/quotes/1/')
    );
    const secondRetry = axios.testInstance.responseRejected(
      unauthorized('/companies/')
    );
    refresh.reject(refreshError);

    await expect(firstRetry).rejects.toBe(refreshError);
    await expect(secondRetry).rejects.toBe(refreshError);

    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem('token')).toBeNull();
    expect(localStorage.getItem('refreshToken')).toBeNull();
    expect(localStorage.getItem('user')).toBeNull();
    expect(window.location.pathname).toBe('/login');
    expect(popstate).toHaveBeenCalledTimes(1);
    window.removeEventListener('popstate', popstate);
  });

  test('does not refresh or redirect when an authentication endpoint returns 401', async () => {
    localStorage.setItem('token', 'expired-access');
    localStorage.setItem('refreshToken', 'refresh-token');
    const error = unauthorized('/token/refresh/');

    await expect(
      axios.testInstance.responseRejected(error)
    ).rejects.toBe(error);

    expect(axios.post).not.toHaveBeenCalled();
    expect(localStorage.getItem('token')).toBe('expired-access');
    expect(localStorage.getItem('refreshToken')).toBe('refresh-token');
    expect(window.location.pathname).toBe('/admin');
  });

  test('does not redirect back to login when session cleanup happens on the login page', async () => {
    window.history.replaceState(null, '', '/login?next=%2Fadmin');
    localStorage.setItem('token', 'expired-access');
    const error = unauthorized();
    const popstate = jest.fn();
    window.addEventListener('popstate', popstate);

    await expect(
      axios.testInstance.responseRejected(error)
    ).rejects.toBe(error);

    expect(localStorage.getItem('token')).toBeNull();
    expect(window.location.pathname).toBe('/login');
    expect(window.location.search).toBe('?next=%2Fadmin');
    expect(popstate).not.toHaveBeenCalled();
    window.removeEventListener('popstate', popstate);
  });

  test('session cleanup also clears the in-memory authentication context', async () => {
    localStorage.setItem('token', 'expired-access');
    localStorage.setItem('user', '{"id":1,"username":"staff"}');
    render(
      <AuthProvider>
        <AuthState />
      </AuthProvider>
    );

    expect(await screen.findByText('Signed in: staff')).toBeInTheDocument();

    act(() => {
      window.dispatchEvent(new Event(AUTH_SESSION_CLEARED_EVENT));
    });

    expect(await screen.findByText('Signed out')).toBeInTheDocument();
  });

  test('request interceptor attaches the latest stored access token', () => {
    localStorage.setItem('token', 'latest-access');
    const config = { headers: {} };

    expect(axios.testInstance.requestFulfilled(config)).toEqual({
      headers: { Authorization: 'Bearer latest-access' },
    });
  });

  test('exports the configured axios instance', () => {
    expect(axiosInstance).toBe(axios.testInstance);
  });
});
