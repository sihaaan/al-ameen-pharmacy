import React, { useState } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import axiosInstance from '../utils/axios';
import './Auth.css';

const ResetPassword = () => {
  const { token } = useParams();
  const navigate = useNavigate();

  const [formData, setFormData] = useState({
    password: '',
    password_confirm: ''
  });
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(false);

  const handleChange = (e) => {
    setFormData({
      ...formData,
      [e.target.name]: e.target.value
    });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');

    if (!formData.password || !formData.password_confirm) {
      setError('Please fill in all fields');
      return;
    }

    if (formData.password !== formData.password_confirm) {
      setError('Passwords do not match');
      return;
    }

    if (formData.password.length < 8) {
      setError('Password must be at least 8 characters long');
      return;
    }

    setIsSubmitting(true);

    try {
      await axiosInstance.post('/password-reset/confirm/', {
        token,
        password: formData.password,
        password_confirm: formData.password_confirm
      });

      setSuccess(true);

      // Redirect to login after 3 seconds
      setTimeout(() => {
        navigate('/login');
      }, 3000);
    } catch (err) {
      setError(err.response?.data?.error || 'Failed to reset password. Please try again.');
      setIsSubmitting(false);
    }
  };

  if (success) {
    return (
      <div className="auth-container">
        <div className="auth-box">
          <div className="auth-header">
            <h1>صيدلية الأمين</h1>
            <h2>AL AMEEN PHARMACY</h2>
          </div>

          <div className="success-container">
            <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="#10b981" strokeWidth="2">
              <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
              <polyline points="22 4 12 14.01 9 11.01"></polyline>
            </svg>
            <h3>Password Reset Successful!</h3>
            <p>Your password has been reset successfully. You can now login with your new password.</p>
            <p style={{ marginTop: '20px', color: '#718096' }}>
              Redirecting to login page...
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="auth-container">
      <div className="auth-box">
        <div className="auth-header">
          <h1>صيدلية الأمين</h1>
          <h2>AL AMEEN PHARMACY</h2>
        </div>

        <h3 className="auth-title">Reset Your Password</h3>
        <p className="auth-subtitle">
          Enter your new password below.
        </p>

        {error && (
          <div className="error-message">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="12" y1="8" x2="12" y2="12"></line>
              <line x1="12" y1="16" x2="12.01" y2="16"></line>
            </svg>
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="auth-form">
          <div className="form-group">
            <label htmlFor="password">New Password</label>
            <input
              type="password"
              id="password"
              name="password"
              value={formData.password}
              onChange={handleChange}
              placeholder="Enter new password (min. 8 characters)"
              disabled={isSubmitting}
            />
          </div>

          <div className="form-group">
            <label htmlFor="password_confirm">Confirm New Password</label>
            <input
              type="password"
              id="password_confirm"
              name="password_confirm"
              value={formData.password_confirm}
              onChange={handleChange}
              placeholder="Confirm your new password"
              disabled={isSubmitting}
            />
          </div>

          <button type="submit" className="auth-button" disabled={isSubmitting}>
            {isSubmitting ? 'Resetting Password...' : 'Reset Password'}
          </button>
        </form>

        <div className="auth-footer">
          <p>
            Remember your password? <Link to="/login">Back to Login</Link>
          </p>
        </div>
      </div>
    </div>
  );
};

export default ResetPassword;
