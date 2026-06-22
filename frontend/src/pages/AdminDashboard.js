// frontend/src/pages/AdminDashboard.js
import React, { useState, useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import { useLocation, useNavigate } from 'react-router-dom';
import axiosInstance from '../utils/axios';
import ProductManagement from '../components/ProductManagement';
import OrderManagement from '../components/OrderManagement';
import QuotationModule from '../components/quotations/QuotationModule';
import AccountingModule from '../components/accounting/AccountingModule';
import '../styles/Dashboard.css';

const AdminDashboard = () => {
  const { user, loading } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [activeTab, setActiveTab] = useState('overview');
  const [stats, setStats] = useState({
    totalProducts: 0,
    totalOrders: 0,
    pendingOrders: 0,
    totalRevenue: 0
  });
  const [statsLoading, setStatsLoading] = useState(false);
  const [statsError, setStatsError] = useState('');
  const canAccessAccounting = !!(user?.is_superuser || user?.can_access_accounting);

  // Check if user is admin
  useEffect(() => {
    if (loading) {
      return;
    }
    if (!user) {
      const next = `${location.pathname}${location.search}`;
      navigate(`/login?next=${encodeURIComponent(next)}`);
    } else if (!user.is_staff) {
      alert('You do not have permission to access this page');
      navigate('/');
    } else {
      fetchStats();
    }
  }, [user, loading, navigate, location.pathname, location.search]);

  const fetchStats = async () => {
    setStatsLoading(true);
    setStatsError('');
    try {
      const [productSummaryRes, ordersRes] = await Promise.all([
        axiosInstance.get('/products/summary/'),
        axiosInstance.get('/orders/')
      ]);

      const orders = ordersRes.data;
      const pendingCount = orders.filter(o => o.status === 'pending').length;
      const revenue = orders
        .filter(o => o.status !== 'cancelled')
        .reduce((sum, o) => sum + parseFloat(o.total_amount), 0);

      setStats({
        totalProducts: productSummaryRes.data.count || 0,
        totalOrders: orders.length,
        pendingOrders: pendingCount,
        totalRevenue: revenue
      });
    } catch (error) {
      console.error('Error fetching stats:', error);
      setStatsError('Could not load dashboard stats');
    } finally {
      setStatsLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="admin-dashboard">
        <div className="admin-header">
          <h1>Admin Dashboard</h1>
          <p>Loading admin session...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="admin-dashboard">
      <div className="admin-header">
        <h1>Admin Dashboard</h1>
        <p>Welcome back, {user?.username}!</p>
      </div>

      <div className="admin-tabs">
        <button
          className={`tab-button ${activeTab === 'overview' ? 'active' : ''}`}
          onClick={() => setActiveTab('overview')}
        >
          📊 Overview
        </button>
        <button
          className={`tab-button ${activeTab === 'products' ? 'active' : ''}`}
          onClick={() => setActiveTab('products')}
        >
          📦 Products
        </button>
        <button
          className={`tab-button ${activeTab === 'orders' ? 'active' : ''}`}
          onClick={() => setActiveTab('orders')}
        >
          📋 Orders
        </button>
        <button
          className={`tab-button ${activeTab === 'quotations' ? 'active' : ''}`}
          onClick={() => setActiveTab('quotations')}
        >
          Quotations
        </button>
        {canAccessAccounting && (
          <button
            className={`tab-button ${activeTab === 'accounting' ? 'active' : ''}`}
            onClick={() => setActiveTab('accounting')}
          >
            Accounting
          </button>
        )}
      </div>

      <div className="admin-content">
        {activeTab === 'overview' && (
          <div className="overview-section">
            {statsError && <div className="admin-error">{statsError}</div>}
            <div className="stats-grid">
              <div className="stat-card">
                <div className="stat-icon" style={{ background: '#3b82f6' }}>📦</div>
                <div className="stat-details">
                  <h3>{statsLoading ? '...' : stats.totalProducts}</h3>
                  <p>Total Products</p>
                </div>
              </div>

              <div className="stat-card">
                <div className="stat-icon" style={{ background: '#10b981' }}>📋</div>
                <div className="stat-details">
                  <h3>{statsLoading ? '...' : stats.totalOrders}</h3>
                  <p>Total Orders</p>
                </div>
              </div>

              <div className="stat-card">
                <div className="stat-icon" style={{ background: '#f59e0b' }}>⏳</div>
                <div className="stat-details">
                  <h3>{statsLoading ? '...' : stats.pendingOrders}</h3>
                  <p>Pending Orders</p>
                </div>
              </div>

              <div className="stat-card">
                <div className="stat-icon" style={{ background: '#8b5cf6' }}>💰</div>
                <div className="stat-details">
                  <h3>{statsLoading ? '...' : `AED ${stats.totalRevenue.toFixed(2)}`}</h3>
                  <p>Total Revenue</p>
                </div>
              </div>
            </div>

            <div className="quick-actions">
              <h2>Quick Actions</h2>
              <div className="action-buttons">
                <button
                  className="action-button"
                  onClick={() => setActiveTab('products')}
                >
                  <span>➕</span>
                  Manage Products
                </button>
                <button
                  className="action-button"
                  onClick={() => setActiveTab('orders')}
                >
                  <span>📦</span>
                  View Orders
                </button>
                <button
                  className="action-button"
                  onClick={() => setActiveTab('quotations')}
                >
                  <span>QT</span>
                  Manage Quotations
                </button>
                {canAccessAccounting && (
                  <button
                    className="action-button"
                    onClick={() => setActiveTab('accounting')}
                  >
                    <span>AC</span>
                    Prepare Statements
                  </button>
                )}
              </div>
            </div>
          </div>
        )}

        {activeTab === 'products' && <ProductManagement onUpdate={fetchStats} />}
        {activeTab === 'orders' && <OrderManagement onUpdate={fetchStats} />}
        {activeTab === 'quotations' && <QuotationModule />}
        {activeTab === 'accounting' && canAccessAccounting && <AccountingModule />}
        {activeTab === 'accounting' && !canAccessAccounting && (
          <div className="admin-error">You do not have permission to access Accounting.</div>
        )}
      </div>
    </div>
  );
};

export default AdminDashboard;
