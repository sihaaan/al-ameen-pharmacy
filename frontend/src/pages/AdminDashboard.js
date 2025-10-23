// frontend/src/pages/AdminDashboard.js
import React, { useState, useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import { useNavigate } from 'react-router-dom';
import axiosInstance from '../utils/axios';
import ProductManagement from '../components/ProductManagement';
import OrderManagement from '../components/OrderManagement';
import '../styles/Dashboard.css';

const AdminDashboard = () => {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState('overview');
  const [stats, setStats] = useState({
    totalProducts: 0,
    totalOrders: 0,
    pendingOrders: 0,
    totalRevenue: 0
  });
  const [loading, setLoading] = useState(true);

  // Check if user is admin
  useEffect(() => {
    if (!user) {
      navigate('/login');
    } else if (!user.is_staff) {
      alert('You do not have permission to access this page');
      navigate('/');
    } else {
      fetchStats();
    }
  }, [user, navigate]);

  const fetchStats = async () => {
    try {
      const [productsRes, ordersRes] = await Promise.all([
        axiosInstance.get('/products/'),
        axiosInstance.get('/orders/')
      ]);

      const orders = ordersRes.data;
      const pendingCount = orders.filter(o => o.status === 'pending').length;
      const revenue = orders
        .filter(o => o.status !== 'cancelled')
        .reduce((sum, o) => sum + parseFloat(o.total_amount), 0);

      setStats({
        totalProducts: productsRes.data.length,
        totalOrders: orders.length,
        pendingOrders: pendingCount,
        totalRevenue: revenue
      });
      setLoading(false);
    } catch (error) {
      console.error('Error fetching stats:', error);
      setLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="admin-loading">
        <div className="loading-spinner-admin"></div>
        <p>Loading dashboard...</p>
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
      </div>

      <div className="admin-content">
        {activeTab === 'overview' && (
          <div className="overview-section">
            <div className="stats-grid">
              <div className="stat-card">
                <div className="stat-icon" style={{ background: '#3b82f6' }}>📦</div>
                <div className="stat-details">
                  <h3>{stats.totalProducts}</h3>
                  <p>Total Products</p>
                </div>
              </div>

              <div className="stat-card">
                <div className="stat-icon" style={{ background: '#10b981' }}>📋</div>
                <div className="stat-details">
                  <h3>{stats.totalOrders}</h3>
                  <p>Total Orders</p>
                </div>
              </div>

              <div className="stat-card">
                <div className="stat-icon" style={{ background: '#f59e0b' }}>⏳</div>
                <div className="stat-details">
                  <h3>{stats.pendingOrders}</h3>
                  <p>Pending Orders</p>
                </div>
              </div>

              <div className="stat-card">
                <div className="stat-icon" style={{ background: '#8b5cf6' }}>💰</div>
                <div className="stat-details">
                  <h3>AED {stats.totalRevenue.toFixed(2)}</h3>
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
              </div>
            </div>
          </div>
        )}

        {activeTab === 'products' && <ProductManagement onUpdate={fetchStats} />}
        {activeTab === 'orders' && <OrderManagement onUpdate={fetchStats} />}
      </div>
    </div>
  );
};

export default AdminDashboard;
