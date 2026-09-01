import React, { createContext, useState, useEffect, useContext } from 'react';
import axios from 'axios';
import axiosInstance, { AUTH_SESSION_CLEARED_EVENT } from '../utils/axios';
import { API_BASE_URL, AUTH_REQUEST_TIMEOUT_MS } from '../config';


// Create the context
const AuthContext = createContext();

// Custom hook to use auth context easily
export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

// AuthProvider component that wraps our app
export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Check if user is logged in on page load
  useEffect(() => {
    let active = true;

    const restoreSession = async () => {
      const token = localStorage.getItem('token');
      const storedUser = localStorage.getItem('user');
      if (!token) {
        if (active) setLoading(false);
        return;
      }

      axios.defaults.headers.common['Authorization'] = `Bearer ${token}`;
      let cachedUser = null;
      try {
        cachedUser = storedUser ? JSON.parse(storedUser) : null;
      } catch {
        cachedUser = null;
      }

      try {
        // Authorization capabilities are server-owned and may change between
        // sessions. Refresh them instead of trusting a stale localStorage copy.
        const response = await axiosInstance.get('/me/');
        if (!active) return;
        localStorage.setItem('user', JSON.stringify(response.data));
        setUser(response.data);
      } catch {
        if (!active) return;
        if (localStorage.getItem('token') !== token) {
          setUser(null);
        } else if (cachedUser) {
          // Keep ordinary offline navigation available, but never retain a
          // privileged mailbox-audit capability without server confirmation.
          const safeCachedUser = {
            ...cachedUser,
            can_manage_mailbox_audit: false,
          };
          localStorage.setItem('user', JSON.stringify(safeCachedUser));
          setUser(safeCachedUser);
        }
      } finally {
        if (active) setLoading(false);
      }
    };

    restoreSession();
    return () => {
      active = false;
    };
  }, []);

  // The API client owns automatic JWT refreshes. If it determines that the
  // session has expired, keep React state in sync with the cleared token store
  // before BrowserRouter moves to the login page.
  useEffect(() => {
    const handleSessionCleared = () => {
      setUser(null);
      setError(null);
      setLoading(false);
    };

    window.addEventListener(AUTH_SESSION_CLEARED_EVENT, handleSessionCleared);
    return () => {
      window.removeEventListener(
        AUTH_SESSION_CLEARED_EVENT,
        handleSessionCleared
      );
    };
  }, []);

  // Login function
  const login = async (username, password) => {
    setError(null);
    try {
      // Call Django JWT token endpoint
      const response = await axios.post(`${API_BASE_URL}/token/`, {
        username,
        password,
      }, {
        timeout: AUTH_REQUEST_TIMEOUT_MS,
      });

      const { access, refresh } = response.data;

      // Store tokens
      localStorage.setItem('token', access);
      localStorage.setItem('refreshToken', refresh);

      // Set authorization header
      axios.defaults.headers.common['Authorization'] = `Bearer ${access}`;

      // Get user info
      const userResponse = await axiosInstance.get('/me/');
      const userData = userResponse.data;

      // Store user data
      localStorage.setItem('user', JSON.stringify(userData));
      setUser(userData);

      return { success: true };
    } catch (err) {
      console.error('Login error:', err);
      const errorMessage = err.code === 'ECONNABORTED'
        ? 'Login timed out while waiting for the server. Please try again in a moment.'
        : err.response?.data?.detail || 'Login failed. Please try again.';
      setError(errorMessage);
      return { success: false, error: errorMessage };
    }
  };

  // Register function
  const register = async (userData) => {
    setError(null);
    try {
      // Call Django register endpoint
      await axiosInstance.post('/register/', userData);

      // After successful registration, log the user in
      const loginResult = await login(userData.username, userData.password);

      return loginResult;
    } catch (err) {
      console.error('Registration error:', err);
      const errorMessage = err.response?.data?.username?.[0]
        || err.response?.data?.email?.[0]
        || 'Registration failed. Please try again.';
      setError(errorMessage);
      return { success: false, error: errorMessage };
    }
  };

  // Logout function
  const logout = () => {
    // Clear tokens and user data
    localStorage.removeItem('token');
    localStorage.removeItem('refreshToken');
    localStorage.removeItem('user');

    // Clear authorization header
    delete axios.defaults.headers.common['Authorization'];

    // Clear user state
    setUser(null);
    setError(null);
  };

  // Refresh token function (for keeping user logged in)
  const refreshToken = async () => {
    const refresh = localStorage.getItem('refreshToken');
    if (!refresh) {
      logout();
      return false;
    }

    try {
      const response = await axios.post(`${API_BASE_URL}/token/refresh/`, {
        refresh,
      }, {
        timeout: AUTH_REQUEST_TIMEOUT_MS,
      });

      const { access, refresh: rotatedRefresh } = response.data;
      localStorage.setItem('token', access);
      if (rotatedRefresh) {
        localStorage.setItem('refreshToken', rotatedRefresh);
      }
      axios.defaults.headers.common['Authorization'] = `Bearer ${access}`;

      return true;
    } catch (err) {
      console.error('Token refresh failed:', err);
      logout();
      return false;
    }
  };

  // Value provided to all components
  const value = {
    user,
    loading,
    error,
    login,
    register,
    logout,
    refreshToken,
    isAuthenticated: !!user,
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
};

export default AuthContext;
