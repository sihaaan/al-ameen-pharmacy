// frontend/src/pages/AdminDashboard.js
import React, { useCallback, useState, useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import { useLocation, useNavigate } from 'react-router-dom';
import axiosInstance from '../utils/axios';
import ProductManagement from '../components/ProductManagement';
import OrderManagement from '../components/OrderManagement';
import QuotationModule from '../components/quotations/QuotationModule';
import AccountingModule from '../components/accounting/AccountingModule';
import '../styles/Dashboard.css';

const ADMIN_TABS = new Set(['overview', 'products', 'orders', 'quotations', 'accounting']);

export const adminTabFromSearch = (search, canAccessAccounting = true) => {
  const params = new URLSearchParams(search || '');
  const requested = params.get('admin_tab');
  if (!requested && ['quotation_tab', 'gmail_import', 'gmail_import_id', 'quote_id'].some((key) => params.has(key))) {
    return 'quotations';
  }
  if (!ADMIN_TABS.has(requested)) return 'overview';
  if (requested === 'accounting' && !canAccessAccounting) return 'overview';
  return requested;
};

const AdminDashboard = () => {
  const { user, loading } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const canAccessAccounting = !!(user?.is_superuser || user?.can_access_accounting);
  const [activeTab, setActiveTab] = useState(
    () => adminTabFromSearch(location.search, canAccessAccounting)
  );
  const [stats, setStats] = useState({
    totalProducts: 0,
    totalOrders: 0,
    pendingOrders: 0,
    totalRevenue: 0
  });
  const [statsLoading, setStatsLoading] = useState(false);
  const [statsError, setStatsError] = useState('');

  const fetchStats = useCallback(async () => {
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
  }, []);

  useEffect(() => {
    setActiveTab(adminTabFromSearch(location.search, canAccessAccounting));
  }, [canAccessAccounting, location.search]);

  const selectAdminTab = (tab) => {
    setActiveTab(tab);
    const params = new URLSearchParams(location.search);
    params.set('admin_tab', tab);
    if (tab !== 'quotations') {
      params.delete('quotation_tab');
      params.delete('gmail_import');
      params.delete('gmail_import_id');
      params.delete('quote_id');
    }
    navigate(`${location.pathname}?${params.toString()}`, { replace: true });
  };

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
    }
  }, [user, loading, navigate, location.pathname, location.search]);

  useEffect(() => {
    if (!loading && user?.is_staff) fetchStats();
  }, [fetchStats, loading, user?.id, user?.is_staff]);

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

  // Do not mount quotation deep-link children until JWT-backed identity and
  // staff access are known. In particular, this keeps one-time Gmail handoff
  // claims from firing before the login redirect completes.
  if (!user || !user.is_staff) {
    return (
      <div className="admin-dashboard">
        <div className="admin-header">
          <h1>Admin Dashboard</h1>
          <p>{!user ? 'Redirecting to sign in...' : 'Checking staff access...'}</p>
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
          onClick={() => selectAdminTab('overview')}
        >
          📊 Overview
        </button>
        <button
          className={`tab-button ${activeTab === 'products' ? 'active' : ''}`}
          onClick={() => selectAdminTab('products')}
        >
          📦 Products
        </button>
        <button
          className={`tab-button ${activeTab === 'orders' ? 'active' : ''}`}
          onClick={() => selectAdminTab('orders')}
        >
          📋 Orders
        </button>
        <button
          className={`tab-button ${activeTab === 'quotations' ? 'active' : ''}`}
          onClick={() => selectAdminTab('quotations')}
        >
          Quotations
        </button>
        {canAccessAccounting && (
          <button
            className={`tab-button ${activeTab === 'accounting' ? 'active' : ''}`}
            onClick={() => selectAdminTab('accounting')}
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
                  onClick={() => selectAdminTab('products')}
                >
                  <span>➕</span>
                  Manage Products
                </button>
                <button
                  className="action-button"
                  onClick={() => selectAdminTab('orders')}
                >
                  <span>📦</span>
                  View Orders
                </button>
                <button
                  className="action-button"
                  onClick={() => selectAdminTab('quotations')}
                >
                  <span>QT</span>
                  Manage Quotations
                </button>
                {canAccessAccounting && (
                  <button
                    className="action-button"
                    onClick={() => selectAdminTab('accounting')}
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
