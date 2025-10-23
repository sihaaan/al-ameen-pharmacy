import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import './Auth.css';

function Login() {
  const navigate = useNavigate();
  const { login, error } = useAuth();

  const [formData, setFormData] = useState({
    username: '',
    password: '',
  });
  const [loading, setLoading] = useState(false);
  const [localError, setLocalError] = useState('');

  // Handle input changes
  const handleChange = (e) => {
    setFormData({
      ...formData,
      [e.target.name]: e.target.value,
    });
    // Clear errors when user types
    setLocalError('');
  };

  // Handle form submission
  const handleSubmit = async (e) => {
    e.preventDefault();
    setLocalError('');

    // Validation
    if (!formData.username || !formData.password) {
      setLocalError('Please fill in all fields');
      return;
    }

    setLoading(true);

    // Call login function from AuthContext
    const result = await login(formData.username, formData.password);

    setLoading(false);

    if (result.success) {
      // Redirect to home page
      navigate('/');
    } else {
      setLocalError(result.error || 'Login failed');
    }
  };

  return (
    <div className="auth-container">
      <div className="auth-card">
        <h1>Welcome Back! 🏥</h1>
        <p className="auth-subtitle">Log in to Al Ameen Pharmacy</p>

        {(localError || error) && (
          <div className="error-message">
            {localError || error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="auth-form">
          <div className="form-group">
            <label htmlFor="username">Username</label>
            <input
              type="text"
              id="username"
              name="username"
              value={formData.username}
              onChange={handleChange}
              placeholder="Enter your username"
              disabled={loading}
              autoFocus
            />
          </div>

          <div className="form-group">
            <label htmlFor="password">Password</label>
            <input
              type="password"
              id="password"
              name="password"
              value={formData.password}
              onChange={handleChange}
              placeholder="Enter your password"
              disabled={loading}
            />
          </div>

          <button
            type="submit"
            className="auth-button"
            disabled={loading}
          >
            {loading ? 'Logging in...' : 'Login'}
          </button>
        </form>

        <div className="auth-footer">
          <p>
            <Link to="/forgot-password" className="auth-link">
              Forgot Password?
            </Link>
          </p>
          <p>
            Don't have an account?{' '}
            <Link to="/register" className="auth-link">
              Register here
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}

export default Login;
