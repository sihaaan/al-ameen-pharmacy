// frontend/src/components/OrderManagement.js
import React, { useState, useEffect } from 'react';
import axiosInstance from '../utils/axios';

const OrderManagement = ({ onUpdate }) => {
  const [orders, setOrders] = useState([]);
  const [filteredOrders, setFilteredOrders] = useState([]);
  const [statusFilter, setStatusFilter] = useState('all');
  const [searchTerm, setSearchTerm] = useState('');
  const [selectedOrder, setSelectedOrder] = useState(null);
  const [loading, setLoading] = useState(true);

  const statusOptions = [
    { value: 'pending', label: 'Pending', color: '#f59e0b' },
    { value: 'processing', label: 'Processing', color: '#3b82f6' },
    { value: 'shipped', label: 'Shipped', color: '#8b5cf6' },
    { value: 'delivered', label: 'Delivered', color: '#10b981' },
    { value: 'cancelled', label: 'Cancelled', color: '#ef4444' },
  ];

  useEffect(() => {
    fetchOrders();
  }, []);

  useEffect(() => {
    filterOrders();
  }, [orders, statusFilter, searchTerm]);

  const fetchOrders = async () => {
    try {
      const response = await axiosInstance.get('/orders/');
      setOrders(response.data);
      setLoading(false);
      if (onUpdate) onUpdate();
    } catch (error) {
      console.error('Error fetching orders:', error);
      setLoading(false);
    }
  };

  const filterOrders = () => {
    let result = [...orders];

    // Filter by status
    if (statusFilter !== 'all') {
      result = result.filter(order => order.status === statusFilter);
    }

    // Filter by search term
    if (searchTerm) {
      const term = searchTerm.toLowerCase();
      result = result.filter(order =>
        order.order_number.toLowerCase().includes(term) ||
        order.full_name.toLowerCase().includes(term) ||
        order.email.toLowerCase().includes(term)
      );
    }

    setFilteredOrders(result);
  };

  const handleStatusUpdate = async (orderId, newStatus) => {
    try {
      await axiosInstance.patch(`/orders/${orderId}/update_status/`, {
        status: newStatus
      });
      alert('Order status updated successfully!');
      fetchOrders();
      if (selectedOrder?.id === orderId) {
        const updatedOrder = orders.find(o => o.id === orderId);
        setSelectedOrder({ ...updatedOrder, status: newStatus });
      }
    } catch (error) {
      console.error('Error updating order status:', error);
      alert('Error updating order status: ' + (error.response?.data?.error || error.message));
    }
  };

  const getStatusColor = (status) => {
    const statusObj = statusOptions.find(s => s.value === status);
    return statusObj ? statusObj.color : '#6b7280';
  };

  const formatDate = (dateString) => {
    return new Date(dateString).toLocaleString('en-AE', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  if (loading) {
    return <div className="loading-orders">Loading orders...</div>;
  }

  return (
    <div className="order-management">
      <div className="management-header">
        <h2>Order Management</h2>
        <div className="order-stats">
          <span>Total Orders: {orders.length}</span>
          <span>Pending: {orders.filter(o => o.status === 'pending').length}</span>
        </div>
      </div>

      {/* Search and Filter */}
      <div className="management-controls">
        <input
          type="text"
          placeholder="Search by order number, name, or email..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          className="search-input"
        />
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="filter-select"
        >
          <option value="all">All Statuses</option>
          {statusOptions.map(status => (
            <option key={status.value} value={status.value}>
              {status.label}
            </option>
          ))}
        </select>
      </div>

      {/* Orders Table */}
      <div className="orders-table-container">
        <table className="orders-table">
          <thead>
            <tr>
              <th>Order #</th>
              <th>Customer</th>
              <th>Date</th>
              <th>Items</th>
              <th>Total</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {filteredOrders.map((order) => (
              <tr key={order.id}>
                <td>
                  <strong>{order.order_number}</strong>
                </td>
                <td>
                  {order.full_name}
                  <br />
                  <small>{order.email}</small>
                </td>
                <td>{formatDate(order.created_at)}</td>
                <td>{order.items?.length || 0} items</td>
                <td><strong>AED {parseFloat(order.total_amount).toFixed(2)}</strong></td>
                <td>
                  <span
                    className="status-badge"
                    style={{ backgroundColor: getStatusColor(order.status) }}
                  >
                    {order.status}
                  </span>
                </td>
                <td>
                  <button
                    onClick={() => setSelectedOrder(order)}
                    className="btn-view-sm"
                  >
                    View Details
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {filteredOrders.length === 0 && (
          <div className="no-orders">
            <p>No orders found matching your filters.</p>
          </div>
        )}
      </div>

      {/* Order Details Modal */}
      {selectedOrder && (
        <div className="modal-overlay" onClick={() => setSelectedOrder(null)}>
          <div className="modal-content modal-large" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Order Details - {selectedOrder.order_number}</h2>
              <button className="modal-close" onClick={() => setSelectedOrder(null)}>
                
              </button>
            </div>

            <div className="order-details-content">
              {/* Order Status Update */}
              <div className="order-status-section">
                <label>Update Order Status:</label>
                <div className="status-buttons">
                  {statusOptions.map(status => (
                    <button
                      key={status.value}
                      className={`status-button ${selectedOrder.status === status.value ? 'active' : ''}`}
                      style={{
                        backgroundColor: selectedOrder.status === status.value ? status.color : 'transparent',
                        borderColor: status.color,
                        color: selectedOrder.status === status.value ? 'white' : status.color
                      }}
                      onClick={() => handleStatusUpdate(selectedOrder.id, status.value)}
                    >
                      {status.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Customer Information */}
              <div className="order-section">
                <h3>Customer Information</h3>
                <div className="info-grid">
                  <div className="info-item">
                    <strong>Name:</strong> {selectedOrder.full_name}
                  </div>
                  <div className="info-item">
                    <strong>Email:</strong> {selectedOrder.email}
                  </div>
                  <div className="info-item">
                    <strong>Phone:</strong> {selectedOrder.phone}
                  </div>
                  <div className="info-item">
                    <strong>Order Date:</strong> {formatDate(selectedOrder.created_at)}
                  </div>
                </div>
              </div>

              {/* Delivery Information */}
              <div className="order-section">
                <h3>Delivery Address</h3>
                <p>
                  {selectedOrder.address}<br />
                  {selectedOrder.city}, {selectedOrder.emirate}
                </p>
                {selectedOrder.delivery_notes && (
                  <div className="delivery-notes">
                    <strong>Delivery Notes:</strong> {selectedOrder.delivery_notes}
                  </div>
                )}
              </div>

              {/* Order Items */}
              <div className="order-section">
                <h3>Order Items</h3>
                <table className="items-table">
                  <thead>
                    <tr>
                      <th>Product</th>
                      <th>Quantity</th>
                      <th>Price</th>
                      <th>Subtotal</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selectedOrder.items?.map((item, index) => (
                      <tr key={index}>
                        <td>{item.product_name}</td>
                        <td>{item.quantity}</td>
                        <td>AED {parseFloat(item.price_at_purchase).toFixed(2)}</td>
                        <td>AED {parseFloat(item.subtotal).toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <td colSpan="3"><strong>Total</strong></td>
                      <td><strong>AED {parseFloat(selectedOrder.total_amount).toFixed(2)}</strong></td>
                    </tr>
                  </tfoot>
                </table>
              </div>

              {/* Payment Information */}
              <div className="order-section">
                <h3>Payment Information</h3>
                <div className="info-grid">
                  <div className="info-item">
                    <strong>Payment Method:</strong> {selectedOrder.payment_method === 'cash_on_delivery' ? 'Cash on Delivery' : selectedOrder.payment_method}
                  </div>
                  <div className="info-item">
                    <strong>Payment Status:</strong>
                    <span className="payment-status" style={{ color: selectedOrder.payment_status === 'paid' ? '#10b981' : '#f59e0b' }}>
                      {selectedOrder.payment_status}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default OrderManagement;
