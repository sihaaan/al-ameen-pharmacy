// frontend/src/utils/axios.js
import axios from 'axios';
import { API_BASE_URL } from '../config';
import { internalPathOrHome } from './internalRedirect';

export const AUTH_SESSION_CLEARED_EVENT = 'auth-session-cleared';

// Create axios instance with base configuration
const axiosInstance = axios.create({
  baseURL: API_BASE_URL,
});

let refreshPromise = null;

const isAuthenticationEndpoint = (url) => {
  if (!url) return false;

  try {
    const parsedUrl = new URL(
      url,
      typeof window !== 'undefined' ? window.location.origin : 'http://localhost'
    );
    return /(?:^|\/)(?:token(?:\/(?:refresh|verify))?|login|register)\/?$/.test(
      parsedUrl.pathname
    );
  } catch {
    return false;
  }
};

const setAuthorizationHeader = (headers, token) => {
  const requestHeaders = headers || {};
  if (typeof requestHeaders.set === 'function') {
    requestHeaders.set('Authorization', `Bearer ${token}`);
  } else {
    requestHeaders.Authorization = `Bearer ${token}`;
  }
  return requestHeaders;
};

const accessTokenFromHeaders = (headers) => {
  if (!headers) return '';
  const authorization = typeof headers.get === 'function'
    ? headers.get('Authorization')
    : headers.Authorization || headers.authorization;
  const match = String(authorization || '').match(/^Bearer\s+(.+)$/i);
  return match ? match[1] : '';
};

const clearStoredAuthentication = () => {
  localStorage.removeItem('token');
  localStorage.removeItem('refreshToken');
  localStorage.removeItem('user');
  if (axios.defaults?.headers?.common) {
    delete axios.defaults.headers.common.Authorization;
  }
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(AUTH_SESSION_CLEARED_EVENT));
  }
};

const redirectToLogin = () => {
  if (typeof window === 'undefined' || window.location.pathname === '/login') {
    return;
  }

  const next = internalPathOrHome(
    `${window.location.pathname}${window.location.search}${window.location.hash}`
  );
  const loginUrl = `/login?next=${encodeURIComponent(next)}`;

  // BrowserRouter listens for popstate, so this moves to the login page without
  // reloading the entire application or adding a redirect loop to browser history.
  window.history.replaceState(null, '', loginUrl);
  window.dispatchEvent(new PopStateEvent('popstate'));
};

const refreshAccessToken = () => {
  if (refreshPromise) {
    return refreshPromise;
  }

  const refreshToken = localStorage.getItem('refreshToken');
  if (!refreshToken) {
    return null;
  }

  refreshPromise = axios
    .post(`${API_BASE_URL}/token/refresh/`, { refresh: refreshToken })
    .then((response) => {
      const { access, refresh } = response.data || {};
      if (!access) {
        throw new Error('Token refresh response did not include an access token.');
      }

      localStorage.setItem('token', access);
      if (refresh) {
        localStorage.setItem('refreshToken', refresh);
      }
      if (axios.defaults?.headers?.common) {
        axios.defaults.headers.common.Authorization = `Bearer ${access}`;
      }
      return access;
    })
    .finally(() => {
      refreshPromise = null;
    });

  return refreshPromise;
};

// Add request interceptor to include token in every request
axiosInstance.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('token');
    if (token) {
      config.headers = setAuthorizationHeader(config.headers, token);
    }
    return config;
  },
  (error) => {
    return Promise.reject(error);
  }
);

// Add response interceptor to handle token expiration
axiosInstance.interceptors.response.use(
  (response) => {
    return response;
  },
  async (error) => {
    const originalRequest = error.config;

    if (
      error.response?.status !== 401
      || !originalRequest
      || isAuthenticationEndpoint(originalRequest.url)
    ) {
      return Promise.reject(error);
    }

    // A retried request receiving another 401 means the refreshed session is no
    // longer usable. Stop here instead of entering a refresh/redirect loop.
    if (originalRequest._retry) {
      clearStoredAuthentication();
      redirectToLogin();
      return Promise.reject(error);
    }

    originalRequest._retry = true;

    // Another request may have completed the shared refresh before this 401 was
    // delivered. In that case, retry with the newer access token and do not spend
    // (or invalidate) the newly rotated refresh token a second time.
    const storedAccessToken = localStorage.getItem('token');
    const requestAccessToken = accessTokenFromHeaders(originalRequest.headers);
    if (
      storedAccessToken
      && requestAccessToken
      && storedAccessToken !== requestAccessToken
    ) {
      originalRequest.headers = setAuthorizationHeader(
        originalRequest.headers,
        storedAccessToken
      );
      return axiosInstance(originalRequest);
    }

    const pendingRefresh = refreshAccessToken();
    if (!pendingRefresh) {
      clearStoredAuthentication();
      redirectToLogin();
      return Promise.reject(error);
    }

    try {
      const access = await pendingRefresh;
      originalRequest.headers = setAuthorizationHeader(
        originalRequest.headers,
        access
      );
      return axiosInstance(originalRequest);
    } catch (refreshError) {
      clearStoredAuthentication();
      redirectToLogin();
      return Promise.reject(refreshError);
    }
  }
);

export default axiosInstance;
