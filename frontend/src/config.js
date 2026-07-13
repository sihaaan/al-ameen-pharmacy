export const API_BASE_URL = (process.env.REACT_APP_API_URL || 'http://localhost:8000/api').replace(/\/+$/, '');

export const AUTH_REQUEST_TIMEOUT_MS = Number(
  process.env.REACT_APP_AUTH_REQUEST_TIMEOUT_MS || 30000
);
