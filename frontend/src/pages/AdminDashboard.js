// frontend/src/pages/AdminDashboard.js
import React, { useState, useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import { useNavigate } from 'react-router-dom';
import axiosInstance from '../utils/axios';
import '../styles/AdminDashboard.css';

const AdminDashboard = () => {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [products, setProducts] = useState([]);
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showAddModal, setShowAddModal] = useState(false);
  const [editingProduct, setEditingProduct] = useState(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [filterCategory, setFilterCategory] = useState('');

  // Form state
  const [formData, setFormData] = useState({
    name: '',
    description: '',
    detailed_description: '',
    price: '',
    stock_quantity: '',
    category: '',
    manufacturer: '',
    dosage: '',
    pack_size: '',
    requires_prescription: false,
    image: null,
  });

  // Check if user is staff/admin
  useEffect(() => {
    if (!user) {
      navigate('/login');
    } else if (!user.is_staff) {
      alert('You do not have permission to access this page');
      navigate('/');
    }
  }, [user, navigate]);

  // Fetch products and categories
  useEffect(() => {
    fetchProducts();
    fetchCategories();
  }, []);

  const fetchProducts = async () => {
    try {
      const response = await axiosInstance.get('/products/');
      setProducts(response.data);
      setLoading(false);
    } catch (error) {
      console.error('Error fetching products:', error);
      setLoading(false);
    }
  };

  const fetchCategories = async () => {
    try {
      const response = await axiosInstance.get('/categories/');
      setCategories(response.data);
    } catch (error) {
      console.error('Error fetching categories:', error);
    }
  };

  const handleInputChange = (e) => {
    const { name, value, type, checked } = e.target;
    setFormData({
      ...formData,
      [name]: type === 'checkbox' ? checked : value,
    });
  };

  const handleImageChange = (e) => {
    if (e.target.files && e.target.files[0]) {
      setFormData({
        ...formData,
        image: e.target.files[0],
      });
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();

    const data = new FormData();
    Object.keys(formData).forEach((key) => {
      if (formData[key] !== null && formData[key] !== '') {
        data.append(key, formData[key]);
      }
    });

    try {
      if (editingProduct) {
        // Update existing product
        await axiosInstance.put(`/products/${editingProduct.id}/`, data, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
        alert('Product updated successfully!');
      } else {
        // Create new product
        await axiosInstance.post('/products/', data, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
        alert('Product added successfully!');
      }

      // Reset form and refresh
      resetForm();
      fetchProducts();
      setShowAddModal(false);
    } catch (error) {
      console.error('Error saving product:', error);
      alert('Error saving product: ' + (error.response?.data?.message || error.message));
    }
  };

  const handleEdit = (product) => {
    setEditingProduct(product);
    setFormData({
      name: product.name,
      description: product.description,
      detailed_description: product.detailed_description || '',
      price: product.price,
      stock_quantity: product.stock_quantity,
      category: product.category || '',
      manufacturer: product.manufacturer || '',
      dosage: product.dosage || '',
      pack_size: product.pack_size || '',
      requires_prescription: product.requires_prescription,
      image: null,
    });
    setShowAddModal(true);
  };

  const handleDelete = async (productId) => {
    if (window.confirm('Are you sure you want to delete this product?')) {
      try {
        await axiosInstance.delete(`/products/${productId}/`);
        alert('Product deleted successfully!');
        fetchProducts();
      } catch (error) {
        console.error('Error deleting product:', error);
        alert('Error deleting product');
      }
    }
  };

  const resetForm = () => {
    setFormData({
      name: '',
      description: '',
      detailed_description: '',
      price: '',
      stock_quantity: '',
      category: '',
      manufacturer: '',
      dosage: '',
      pack_size: '',
      requires_prescription: false,
      image: null,
    });
    setEditingProduct(null);
  };

  const filteredProducts = products.filter((product) => {
    const matchesSearch = product.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
                          product.description.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesCategory = filterCategory === '' || product.category === parseInt(filterCategory);
    return matchesSearch && matchesCategory;
  });

  if (loading) {
    return <div className="admin-loading">Loading...</div>;
  }

  return (
    <div className="admin-dashboard">
      <div className="admin-header">
        <h1>Product Management</h1>
        <button className="btn-add-product" onClick={() => setShowAddModal(true)}>
          + Add New Product
        </button>
      </div>

      {/* Search and Filter */}
      <div className="admin-controls">
        <input
          type="text"
          placeholder="Search products..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          className="search-input"
        />
        <select
          value={filterCategory}
          onChange={(e) => setFilterCategory(e.target.value)}
          className="filter-select"
        >
          <option value="">All Categories</option>
          {categories.map((cat) => (
            <option key={cat.id} value={cat.id}>
              {cat.name}
            </option>
          ))}
        </select>
      </div>

      {/* Products Grid */}
      <div className="admin-products-grid">
        {filteredProducts.map((product) => (
          <div key={product.id} className="admin-product-card">
            <div className="admin-product-image">
              {product.image ? (
                <img src={product.image} alt={product.name} />
              ) : product.image_url ? (
                <img src={product.image_url} alt={product.name} />
              ) : (
                <div className="no-image">No Image</div>
              )}
            </div>
            <div className="admin-product-info">
              <h3>{product.name}</h3>
              <p className="admin-product-description">{product.description}</p>
              <div className="admin-product-details">
                <span className="admin-price">AED {product.price}</span>
                <span className="admin-stock">Stock: {product.stock_quantity}</span>
              </div>
              <div className="admin-product-actions">
                <button onClick={() => handleEdit(product)} className="btn-edit">
                  Edit
                </button>
                <button onClick={() => handleDelete(product.id)} className="btn-delete">
                  Delete
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Add/Edit Modal */}
      {showAddModal && (
        <div className="modal-overlay" onClick={() => { setShowAddModal(false); resetForm(); }}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>{editingProduct ? 'Edit Product' : 'Add New Product'}</h2>
              <button className="modal-close" onClick={() => { setShowAddModal(false); resetForm(); }}>
                ✕
              </button>
            </div>

            <form onSubmit={handleSubmit} className="product-form">
              <div className="form-grid">
                {/* Basic Information */}
                <div className="form-section">
                  <h3>Basic Information</h3>

                  <div className="form-group">
                    <label>Product Name *</label>
                    <input
                      type="text"
                      name="name"
                      value={formData.name}
                      onChange={handleInputChange}
                      required
                    />
                  </div>

                  <div className="form-group">
                    <label>Short Description *</label>
                    <textarea
                      name="description"
                      value={formData.description}
                      onChange={handleInputChange}
                      rows="3"
                      required
                    />
                  </div>

                  <div className="form-group">
                    <label>Detailed Description</label>
                    <textarea
                      name="detailed_description"
                      value={formData.detailed_description}
                      onChange={handleInputChange}
                      rows="5"
                      placeholder="Usage instructions, warnings, side effects, etc."
                    />
                  </div>

                  <div className="form-row">
                    <div className="form-group">
                      <label>Category</label>
                      <select name="category" value={formData.category} onChange={handleInputChange}>
                        <option value="">Select Category</option>
                        {categories.map((cat) => (
                          <option key={cat.id} value={cat.id}>
                            {cat.name}
                          </option>
                        ))}
                      </select>
                    </div>

                    <div className="form-group">
                      <label>Manufacturer</label>
                      <input
                        type="text"
                        name="manufacturer"
                        value={formData.manufacturer}
                        onChange={handleInputChange}
                      />
                    </div>
                  </div>
                </div>

                {/* Product Details */}
                <div className="form-section">
                  <h3>Product Details</h3>

                  <div className="form-row">
                    <div className="form-group">
                      <label>Price (AED) *</label>
                      <input
                        type="number"
                        step="0.01"
                        name="price"
                        value={formData.price}
                        onChange={handleInputChange}
                        required
                      />
                    </div>

                    <div className="form-group">
                      <label>Stock Quantity *</label>
                      <input
                        type="number"
                        name="stock_quantity"
                        value={formData.stock_quantity}
                        onChange={handleInputChange}
                        required
                      />
                    </div>
                  </div>

                  <div className="form-row">
                    <div className="form-group">
                      <label>Dosage</label>
                      <input
                        type="text"
                        name="dosage"
                        value={formData.dosage}
                        onChange={handleInputChange}
                        placeholder="e.g., 500mg, 10ml"
                      />
                    </div>

                    <div className="form-group">
                      <label>Pack Size</label>
                      <input
                        type="text"
                        name="pack_size"
                        value={formData.pack_size}
                        onChange={handleInputChange}
                        placeholder="e.g., 30 tablets, 100ml"
                      />
                    </div>
                  </div>

                  <div className="form-group">
                    <label>Product Image</label>
                    <input
                      type="file"
                      accept="image/*"
                      onChange={handleImageChange}
                      className="file-input"
                    />
                    <small>Recommended: 800x800px, JPG or PNG</small>
                  </div>

                  <div className="form-group">
                    <label className="checkbox-label">
                      <input
                        type="checkbox"
                        name="requires_prescription"
                        checked={formData.requires_prescription}
                        onChange={handleInputChange}
                      />
                      Requires Prescription
                    </label>
                  </div>
                </div>
              </div>

              <div className="form-actions">
                <button type="button" onClick={() => { setShowAddModal(false); resetForm(); }} className="btn-cancel">
                  Cancel
                </button>
                <button type="submit" className="btn-save">
                  {editingProduct ? 'Update Product' : 'Add Product'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default AdminDashboard;
