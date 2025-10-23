// frontend/src/pages/OrderConfirmation.js
import React, { useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import '../styles/OrderConfirmation.css';

const OrderConfirmation = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const orderData = location.state?.orderData;

  useEffect(() => {
    // Redirect to home if no order data
    if (!orderData) {
      navigate('/');
    }
  }, [orderData, navigate]);

  if (!orderData) {
    return null;
  }

  return (
    <div className="order-confirmation-page">
      <div className="confirmation-container">
        <div className="confirmation-icon">
          <svg width="80" height="80" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
            <polyline points="22 4 12 14.01 9 11.01"></polyline>
          </svg>
        </div>

        <h1>Order Placed Successfully!</h1>
        <p className="confirmation-message">
          Thank you for your order. We've received your order and will begin processing it shortly.
        </p>

        <div className="order-number">
          <span>Order Number:</span>
          <strong>{orderData.orderNumber}</strong>
        </div>

        <div className="order-details-card">
          <h2>Order Details</h2>

          <div className="order-section">
            <h3>Items Ordered</h3>
            <div className="order-items">
              {orderData.items.map(item => (
                <div key={item.id} className="order-item">
                  <div className="order-item-info">
                    <h4>{item.product.name}</h4>
                    <p>Quantity: {item.quantity}</p>
                  </div>
                  <p className="order-item-price">
                    AED {(item.product.price * item.quantity).toFixed(2)}
                  </p>
                </div>
              ))}
            </div>
            <div className="order-total">
              <strong>Total:</strong>
              <strong>AED {orderData.totalPrice.toFixed(2)}</strong>
            </div>
          </div>

          <div className="order-section">
            <h3>Delivery Information</h3>
            <div className="info-grid">
              <div className="info-item">
                <span className="info-label">Name:</span>
                <span>{orderData.fullName}</span>
              </div>
              <div className="info-item">
                <span className="info-label">Email:</span>
                <span>{orderData.email}</span>
              </div>
              <div className="info-item">
                <span className="info-label">Phone:</span>
                <span>{orderData.phone}</span>
              </div>
              <div className="info-item full-width">
                <span className="info-label">Address:</span>
                <span>
                  {orderData.address}, {orderData.city}, {orderData.emirate}
                </span>
              </div>
              {orderData.notes && (
                <div className="info-item full-width">
                  <span className="info-label">Delivery Notes:</span>
                  <span>{orderData.notes}</span>
                </div>
              )}
            </div>
          </div>

          <div className="order-section">
            <h3>Payment Method</h3>
            <p className="payment-info">
              {orderData.paymentMethod === 'cash_on_delivery' ? 'Cash on Delivery' : orderData.paymentMethod}
            </p>
          </div>
        </div>

        <div className="confirmation-actions">
          <button
            className="btn-primary"
            onClick={() => navigate('/')}
          >
            Continue Shopping
          </button>
        </div>

        <div className="confirmation-note">
          <p>
            📧 A confirmation email has been sent to <strong>{orderData.email}</strong>
          </p>
          <p>
            🚚 Your order will be delivered within 2-3 business days
          </p>
        </div>
      </div>
    </div>
  );
};

export default OrderConfirmation;
