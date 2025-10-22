import React, { createContext, useState, useEffect, useContext } from 'react';
import axios from 'axios';
import axiosInstance from '../utils/axios';

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
    const token = localStorage.getItem('token');
    const userData = localStorage.getItem('user');

    if (token && userData) {
      // User was previously logged in
      setUser(JSON.parse(userData));
      // Set default authorization header for all axios requests
      axios.defaults.headers.common['Authorization'] = `Bearer ${token}`;
    }
    setLoading(false);
  }, []);

  // Login function
  const login = async (username, password) => {
    setError(null);
    try {
      // Call Django JWT token endpoint
      const response = await axios.post('http://localhost:8000/api/token/', {
        username,
        password,
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
      const errorMessage = err.response?.data?.detail || 'Login failed. Please try again.';
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
      const response = await axios.post('http://localhost:8000/api/token/refresh/', {
        refresh,
      });

      const { access } = response.data;
      localStorage.setItem('token', access);
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
