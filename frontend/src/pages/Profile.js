import React, { useState, useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import { useNavigate } from 'react-router-dom';
import axiosInstance from '../utils/axios';
import '../styles/Profile.css';

const Profile = () => {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const [orders, setOrders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('orders');
  const [orderFilter, setOrderFilter] = useState('all');

  useEffect(() => {
    if (!user) {
      navigate('/login');
      return;
    }
    fetchOrders();
  }, [user, navigate]);

  const fetchOrders = async () => {
    try {
      const response = await axiosInstance.get('/orders/');
      setOrders(response.data);
      setLoading(false);
    } catch (error) {
      console.error('Error fetching orders:', error);
      setLoading(false);
    }
  };

  const getStatusBadgeClass = (status) => {
    const statusClasses = {
      pending: 'badge-pending',
      processing: 'badge-processing',
      shipped: 'badge-shipped',
      delivered: 'badge-delivered',
      cancelled: 'badge-cancelled'
    };
    return statusClasses[status] || 'badge-default';
  };

  const filteredOrders = orders.filter(order => {
    if (orderFilter === 'all') return true;
    return order.status === orderFilter;
  });

  if (loading) {
    return (
      <div className="profile-loading">
        <div className="loading-spinner-profile"></div>
        <p>Loading profile...</p>
      </div>
    );
  }

  return (
    <div className="profile-page">
      <div className="profile-container">
        {/* Profile Header */}
        <div className="profile-header">
          <div className="profile-avatar">
            <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
              <circle cx="12" cy="7" r="4"></circle>
            </svg>
          </div>
          <div className="profile-info">
            <h1>{user?.username}</h1>
            <p className="profile-email">{user?.email}</p>
            {user?.is_staff && (
              <span className="admin-badge">Admin</span>
            )}
          </div>
          <button className="btn-logout" onClick={logout}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path>
              <polyline points="16 17 21 12 16 7"></polyline>
              <line x1="21" y1="12" x2="9" y2="12"></line>
            </svg>
            Logout
          </button>
        </div>

        {/* Profile Tabs */}
        <div className="profile-tabs">
          <button
            className={`tab-button ${activeTab === 'orders' ? 'active' : ''}`}
            onClick={() => setActiveTab('orders')}
          >
            📦 Order History ({orders.length})
          </button>
          <button
            className={`tab-button ${activeTab === 'account' ? 'active' : ''}`}
            onClick={() => setActiveTab('account')}
          >
            👤 Account Details
          </button>
        </div>

        {/* Tab Content */}
        <div className="profile-content">
          {activeTab === 'orders' && (
            <div className="orders-tab">
              <div className="orders-header">
                <h2>Your Orders</h2>
                <div className="order-filters">
                  <button
                    className={`filter-btn ${orderFilter === 'all' ? 'active' : ''}`}
                    onClick={() => setOrderFilter('all')}
                  >
                    All ({orders.length})
                  </button>
                  <button
                    className={`filter-btn ${orderFilter === 'pending' ? 'active' : ''}`}
                    onClick={() => setOrderFilter('pending')}
                  >
                    Pending ({orders.filter(o => o.status === 'pending').length})
                  </button>
                  <button
                    className={`filter-btn ${orderFilter === 'processing' ? 'active' : ''}`}
                    onClick={() => setOrderFilter('processing')}
                  >
                    Processing ({orders.filter(o => o.status === 'processing').length})
                  </button>
                  <button
                    className={`filter-btn ${orderFilter === 'delivered' ? 'active' : ''}`}
                    onClick={() => setOrderFilter('delivered')}
                  >
                    Delivered ({orders.filter(o => o.status === 'delivered').length})
                  </button>
                </div>
              </div>

              {filteredOrders.length === 0 ? (
                <div className="no-orders">
                  <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
                    <line x1="9" y1="9" x2="15" y2="15"></line>
                    <line x1="15" y1="9" x2="9" y2="15"></line>
                  </svg>
                  <h3>No orders found</h3>
                  <p>
                    {orderFilter === 'all'
                      ? "You haven't placed any orders yet."
                      : `You don't have any ${orderFilter} orders.`}
                  </p>
                  <button className="btn-primary" onClick={() => navigate('/')}>
                    Start Shopping
                  </button>
                </div>
              ) : (
                <div className="orders-list">
                  {filteredOrders.map(order => (
                    <div key={order.id} className="order-card">
                      <div className="order-card-header">
                        <div className="order-number">
                          <strong>Order #{order.order_number}</strong>
                          <span className="order-date">
                            {new Date(order.created_at).toLocaleDateString('en-US', {
                              year: 'numeric',
                              month: 'long',
                              day: 'numeric'
                            })}
                          </span>
                        </div>
                        <span className={`status-badge ${getStatusBadgeClass(order.status)}`}>
                          {order.status_display || order.status}
                        </span>
                      </div>

                      <div className="order-card-body">
                        <div className="order-items">
                          <h4>Items ({order.items?.length || 0})</h4>
                          {order.items?.map(item => (
                            <div key={item.id} className="order-item">
                              <div className="order-item-image">
                                {item.product_image ? (
                                  <img src={item.product_image} alt={item.product_name} />
                                ) : (
                                  <div className="no-image-placeholder">
                                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                      <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
                                      <circle cx="8.5" cy="8.5" r="1.5"></circle>
                                      <polyline points="21 15 16 10 5 21"></polyline>
                                    </svg>
                                  </div>
                                )}
                              </div>
                              <div className="order-item-info">
                                <p className="item-name">{item.product_name}</p>
                                <p className="item-quantity">Qty: {item.quantity}</p>
                              </div>
                              <p className="item-price">
                                AED {(parseFloat(item.price_at_purchase) * item.quantity).toFixed(2)}
                              </p>
                            </div>
                          ))}
                        </div>

                        <div className="order-summary">
                          <div className="order-delivery">
                            <h4>Delivery Address</h4>
                            <p>{order.full_name}</p>
                            <p>{order.address}</p>
                            <p>{order.city}, {order.emirate}</p>
                            <p>{order.phone}</p>
                          </div>

                          <div className="order-payment">
                            <h4>Payment</h4>
                            <p>{order.payment_method_display || order.payment_method}</p>
                            <p className="payment-status">
                              Status: {order.payment_status_display || order.payment_status}
                            </p>
                          </div>

                          <div className="order-total">
                            <h4>Total</h4>
                            <p className="total-amount">AED {parseFloat(order.total_amount).toFixed(2)}</p>
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {activeTab === 'account' && (
            <div className="account-tab">
              <div className="account-section">
                <h2>Account Information</h2>
                <div className="account-info-grid">
                  <div className="info-item">
                    <label>Username</label>
                    <p>{user?.username}</p>
                  </div>
                  <div className="info-item">
                    <label>Email</label>
                    <p>{user?.email}</p>
                  </div>
                  <div className="info-item">
                    <label>First Name</label>
                    <p>{user?.first_name || 'Not provided'}</p>
                  </div>
                  <div className="info-item">
                    <label>Last Name</label>
                    <p>{user?.last_name || 'Not provided'}</p>
                  </div>
                  <div className="info-item">
                    <label>Account Type</label>
                    <p>{user?.is_staff ? 'Administrator' : 'Customer'}</p>
                  </div>
                  <div className="info-item">
                    <label>Member Since</label>
                    <p>
                      {user?.date_joined ? new Date(user.date_joined).toLocaleDateString('en-US', {
                        year: 'numeric',
                        month: 'long',
                        day: 'numeric'
                      }) : 'N/A'}
                    </p>
                  </div>
                </div>

                <div className="account-stats">
                  <div className="stat-box">
                    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <rect x="1" y="4" width="22" height="16" rx="2" ry="2"></rect>
                      <line x1="1" y1="10" x2="23" y2="10"></line>
                    </svg>
                    <div>
                      <h3>{orders.length}</h3>
                      <p>Total Orders</p>
                    </div>
                  </div>
                  <div className="stat-box">
                    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <circle cx="12" cy="12" r="10"></circle>
                      <polyline points="12 6 12 12 16 14"></polyline>
                    </svg>
                    <div>
                      <h3>{orders.filter(o => o.status === 'pending' || o.status === 'processing').length}</h3>
                      <p>Active Orders</p>
                    </div>
                  </div>
                  <div className="stat-box">
                    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <polyline points="20 6 9 17 4 12"></polyline>
                    </svg>
                    <div>
                      <h3>{orders.filter(o => o.status === 'delivered').length}</h3>
                      <p>Completed Orders</p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default Profile;
