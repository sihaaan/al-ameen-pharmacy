// frontend/src/pages/Checkout.js
import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useCart } from '../context/CartContext';
import { useAuth } from '../context/AuthContext';
import axiosInstance from '../utils/axios';
import '../styles/Checkout.css';

const Checkout = () => {
  const navigate = useNavigate();
  const { items, totalPrice, clearCart } = useCart();
  const { user } = useAuth();

  const [formData, setFormData] = useState({
    fullName: '',
    email: '',
    phone: '',
    address: '',
    city: '',
    emirate: '',
    notes: '',
    paymentMethod: 'cash_on_delivery'
  });

  const [errors, setErrors] = useState({});
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Redirect if cart is empty or user not logged in
  useEffect(() => {
    if (!user) {
      navigate('/login');
      return;
    }
    if (items.length === 0) {
      navigate('/');
    }
  }, [user, items, navigate]);

  const emirates = [
    'Abu Dhabi',
    'Dubai',
    'Sharjah',
    'Ajman',
    'Umm Al Quwain',
    'Ras Al Khaimah',
    'Fujairah'
  ];

  const handleInputChange = (e) => {
    const { name, value } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: value
    }));
    // Clear error for this field when user starts typing
    if (errors[name]) {
      setErrors(prev => ({
        ...prev,
        [name]: ''
      }));
    }
  };

  const validateForm = () => {
    const newErrors = {};

    if (!formData.fullName.trim()) {
      newErrors.fullName = 'Full name is required';
    }

    if (!formData.email.trim()) {
      newErrors.email = 'Email is required';
    } else if (!/\S+@\S+\.\S+/.test(formData.email)) {
      newErrors.email = 'Email is invalid';
    }

    if (!formData.phone.trim()) {
      newErrors.phone = 'Phone number is required';
    } else if (!/^[0-9]{10,}$/.test(formData.phone.replace(/[\s-]/g, ''))) {
      newErrors.phone = 'Phone number must be at least 10 digits';
    }

    if (!formData.address.trim()) {
      newErrors.address = 'Delivery address is required';
    }

    if (!formData.city.trim()) {
      newErrors.city = 'City is required';
    }

    if (!formData.emirate) {
      newErrors.emirate = 'Emirate is required';
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();

    if (!validateForm()) {
      return;
    }

    setIsSubmitting(true);

    try {
      // Create order via backend API
      const orderData = {
        full_name: formData.fullName,
        email: formData.email,
        phone: formData.phone,
        address: formData.address,
        city: formData.city,
        emirate: formData.emirate,
        notes: formData.notes,
        payment_method: formData.paymentMethod
      };

      const response = await axiosInstance.post('/orders/', orderData);

      // Navigate to order confirmation
      navigate('/order-confirmation', {
        state: {
          orderData: {
            ...formData,
            items,
            totalPrice,
            orderNumber: response.data.order_number
          }
        }
      });
    } catch (error) {
      console.error('Order submission failed:', error);
      const errorMessage = error.response?.data?.error || 'Failed to place order. Please try again.';
      alert(errorMessage);
    } finally {
      setIsSubmitting(false);
    }
  };

  if (!user || items.length === 0) {
    return null;
  }

  return (
    <div className="checkout-page">
      <div className="checkout-container">
        <h1>Checkout</h1>

        <div className="checkout-content">
          {/* Order Summary */}
          <div className="order-summary">
            <h2>Order Summary</h2>
            <div className="summary-items">
              {items.map(item => (
                <div key={item.id} className="summary-item">
                  <div className="summary-item-info">
                    <h4>{item.product.name}</h4>
                    <p className="summary-item-quantity">Qty: {item.quantity}</p>
                  </div>
                  <p className="summary-item-price">
                    AED {(item.product.price * item.quantity).toFixed(2)}
                  </p>
                </div>
              ))}
            </div>

            <div className="summary-totals">
              <div className="summary-row">
                <span>Subtotal:</span>
                <span>AED {totalPrice.toFixed(2)}</span>
              </div>
              <div className="summary-row">
                <span>Delivery:</span>
                <span>FREE</span>
              </div>
              <div className="summary-row summary-total">
                <strong>Total:</strong>
                <strong>AED {totalPrice.toFixed(2)}</strong>
              </div>
            </div>
          </div>

          {/* Checkout Form */}
          <div className="checkout-form-container">
            <form onSubmit={handleSubmit} className="checkout-form">
              <div className="form-section">
                <h3>Contact Information</h3>

                <div className="form-group">
                  <label htmlFor="fullName">Full Name *</label>
                  <input
                    type="text"
                    id="fullName"
                    name="fullName"
                    value={formData.fullName}
                    onChange={handleInputChange}
                    className={errors.fullName ? 'error' : ''}
                  />
                  {errors.fullName && <span className="error-message">{errors.fullName}</span>}
                </div>

                <div className="form-row">
                  <div className="form-group">
                    <label htmlFor="email">Email *</label>
                    <input
                      type="email"
                      id="email"
                      name="email"
                      value={formData.email}
                      onChange={handleInputChange}
                      className={errors.email ? 'error' : ''}
                    />
                    {errors.email && <span className="error-message">{errors.email}</span>}
                  </div>

                  <div className="form-group">
                    <label htmlFor="phone">Phone Number *</label>
                    <input
                      type="tel"
                      id="phone"
                      name="phone"
                      value={formData.phone}
                      onChange={handleInputChange}
                      placeholder="05XXXXXXXX"
                      className={errors.phone ? 'error' : ''}
                    />
                    {errors.phone && <span className="error-message">{errors.phone}</span>}
                  </div>
                </div>
              </div>

              <div className="form-section">
                <h3>Delivery Address</h3>

                <div className="form-group">
                  <label htmlFor="address">Street Address *</label>
                  <input
                    type="text"
                    id="address"
                    name="address"
                    value={formData.address}
                    onChange={handleInputChange}
                    placeholder="Building name, street, area"
                    className={errors.address ? 'error' : ''}
                  />
                  {errors.address && <span className="error-message">{errors.address}</span>}
                </div>

                <div className="form-row">
                  <div className="form-group">
                    <label htmlFor="city">City *</label>
                    <input
                      type="text"
                      id="city"
                      name="city"
                      value={formData.city}
                      onChange={handleInputChange}
                      className={errors.city ? 'error' : ''}
                    />
                    {errors.city && <span className="error-message">{errors.city}</span>}
                  </div>

                  <div className="form-group">
                    <label htmlFor="emirate">Emirate *</label>
                    <select
                      id="emirate"
                      name="emirate"
                      value={formData.emirate}
                      onChange={handleInputChange}
                      className={errors.emirate ? 'error' : ''}
                    >
                      <option value="">Select Emirate</option>
                      {emirates.map(emirate => (
                        <option key={emirate} value={emirate}>{emirate}</option>
                      ))}
                    </select>
                    {errors.emirate && <span className="error-message">{errors.emirate}</span>}
                  </div>
                </div>

                <div className="form-group">
                  <label htmlFor="notes">Delivery Notes (Optional)</label>
                  <textarea
                    id="notes"
                    name="notes"
                    value={formData.notes}
                    onChange={handleInputChange}
                    rows="3"
                    placeholder="Any special instructions for delivery"
                  />
                </div>
              </div>

              <div className="form-section">
                <h3>Payment Method</h3>

                <div className="payment-methods">
                  <label className="payment-option">
                    <input
                      type="radio"
                      name="paymentMethod"
                      value="cash_on_delivery"
                      checked={formData.paymentMethod === 'cash_on_delivery'}
                      onChange={handleInputChange}
                    />
                    <div className="payment-option-content">
                      <strong>Cash on Delivery</strong>
                      <p>Pay with cash when your order arrives</p>
                    </div>
                  </label>
                </div>
              </div>

              <div className="form-actions">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => navigate('/')}
                  disabled={isSubmitting}
                >
                  Continue Shopping
                </button>
                <button
                  type="submit"
                  className="btn-primary"
                  disabled={isSubmitting}
                >
                  {isSubmitting ? 'Processing...' : 'Place Order'}
                </button>
              </div>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Checkout;
