// frontend/src/components/ProductManagement.js
import React, { useState, useEffect, useCallback, useMemo } from 'react';
import axiosInstance from '../utils/axios';
import ProductFormModal from './ProductFormModal';

const ProductManagement = ({ onUpdate }) => {
  const [products, setProducts] = useState([]);
  const [categories, setCategories] = useState([]);
  const [brands, setBrands] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showProductModal, setShowProductModal] = useState(false);
  const [showCategoryModal, setShowCategoryModal] = useState(false);
  const [editingProduct, setEditingProduct] = useState(null);
  const [editingCategory, setEditingCategory] = useState(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [filterCategory, setFilterCategory] = useState('');
  const [filterStatus, setFilterStatus] = useState('');
  const [filterBrand, setFilterBrand] = useState('');
  const [activeTab, setActiveTab] = useState('products');
  const [saving, setSaving] = useState(false);

  // Multi-select state
  const [selectedProducts, setSelectedProducts] = useState(new Set());
  const [bulkActionInProgress, setBulkActionInProgress] = useState(false);

  // Sorting state
  const [sortField, setSortField] = useState('name');
  const [sortDirection, setSortDirection] = useState('asc');

  // Category form state
  const [categoryForm, setCategoryForm] = useState({
    name: '',
    description: '',
    parent: '',
    is_active: true,
    display_order: 0,
  });

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [productsRes, categoriesRes, brandsRes] = await Promise.all([
        axiosInstance.get('/products/'),
        axiosInstance.get('/categories/?flat=true'),
        axiosInstance.get('/brands/')
      ]);
      setProducts(productsRes.data);
      setCategories(categoriesRes.data);
      setBrands(brandsRes.data);
    } catch (error) {
      console.error('Error fetching data:', error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Clear selection when filters change
  useEffect(() => {
    setSelectedProducts(new Set());
  }, [searchTerm, filterCategory, filterStatus, filterBrand]);

  const fetchCategories = async () => {
    try {
      const response = await axiosInstance.get('/categories/?flat=true');
      setCategories(response.data);
    } catch (error) {
      console.error('Error fetching categories:', error);
    }
  };

  const fetchProducts = async () => {
    try {
      const response = await axiosInstance.get('/products/');
      setProducts(response.data);
      setSelectedProducts(new Set());
      if (onUpdate) onUpdate();
    } catch (error) {
      console.error('Error fetching products:', error);
    }
  };

  // ==================== FILTERING & SORTING ====================

  const filteredAndSortedProducts = useMemo(() => {
    let result = products.filter((product) => {
      const matchesSearch = product.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
                            (product.short_description || '').toLowerCase().includes(searchTerm.toLowerCase());
      const matchesCategory = filterCategory === '' || product.category === parseInt(filterCategory);
      const matchesStatus = filterStatus === '' || product.status === filterStatus;
      const matchesBrand = filterBrand === '' || product.brand === parseInt(filterBrand);
      return matchesSearch && matchesCategory && matchesStatus && matchesBrand;
    });

    // Sort
    result.sort((a, b) => {
      let aVal, bVal;

      switch (sortField) {
        case 'name':
          aVal = a.name.toLowerCase();
          bVal = b.name.toLowerCase();
          break;
        case 'category':
          aVal = (a.category_name || '').toLowerCase();
          bVal = (b.category_name || '').toLowerCase();
          break;
        case 'brand':
          aVal = (a.brand_name || '').toLowerCase();
          bVal = (b.brand_name || '').toLowerCase();
          break;
        case 'price':
          aVal = parseFloat(a.price) || 0;
          bVal = parseFloat(b.price) || 0;
          break;
        case 'stock':
          aVal = a.stock_quantity || 0;
          bVal = b.stock_quantity || 0;
          break;
        case 'status':
          aVal = a.status || '';
          bVal = b.status || '';
          break;
        default:
          aVal = a.name.toLowerCase();
          bVal = b.name.toLowerCase();
      }

      if (aVal < bVal) return sortDirection === 'asc' ? -1 : 1;
      if (aVal > bVal) return sortDirection === 'asc' ? 1 : -1;
      return 0;
    });

    return result;
  }, [products, searchTerm, filterCategory, filterStatus, filterBrand, sortField, sortDirection]);

  const handleSort = (field) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('asc');
    }
  };

  const SortIcon = ({ field }) => {
    if (sortField !== field) {
      return (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" opacity="0.3">
          <path d="M7 15l5 5 5-5M7 9l5-5 5 5"/>
        </svg>
      );
    }
    return sortDirection === 'asc' ? (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M7 15l5 5 5-5"/>
      </svg>
    ) : (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M7 9l5-5 5 5"/>
      </svg>
    );
  };

  // ==================== MULTI-SELECT HANDLERS ====================

  const handleSelectAll = (e) => {
    if (e.target.checked) {
      const allIds = new Set(filteredAndSortedProducts.map(p => p.id));
      setSelectedProducts(allIds);
    } else {
      setSelectedProducts(new Set());
    }
  };

  const handleSelectProduct = (productId) => {
    setSelectedProducts(prev => {
      const newSet = new Set(prev);
      if (newSet.has(productId)) {
        newSet.delete(productId);
      } else {
        newSet.add(productId);
      }
      return newSet;
    });
  };

  const isAllSelected = filteredAndSortedProducts.length > 0 &&
    filteredAndSortedProducts.every(p => selectedProducts.has(p.id));

  const isSomeSelected = selectedProducts.size > 0 && !isAllSelected;

  // ==================== BULK ACTIONS ====================

  const handleBulkStatusChange = async (newStatus) => {
    if (selectedProducts.size === 0) return;

    const statusLabel = newStatus === 'active' ? 'activate' : newStatus === 'archived' ? 'archive' : 'set to draft';
    const confirm = window.confirm(
      `Are you sure you want to ${statusLabel} ${selectedProducts.size} product(s)?`
    );

    if (!confirm) return;

    setBulkActionInProgress(true);
    try {
      const promises = Array.from(selectedProducts).map(id => {
        const product = products.find(p => p.id === id);
        if (product) {
          return axiosInstance.patch(`/products/${product.slug}/`, { status: newStatus });
        }
        return Promise.resolve();
      });

      await Promise.all(promises);
      await fetchProducts();
    } catch (error) {
      console.error('Error updating products:', error);
      alert('Some products could not be updated. Please try again.');
    } finally {
      setBulkActionInProgress(false);
    }
  };

  const handleBulkDelete = async () => {
    if (selectedProducts.size === 0) return;

    const confirm = window.confirm(
      `Are you sure you want to delete ${selectedProducts.size} product(s)? This action cannot be undone.`
    );

    if (!confirm) return;

    setBulkActionInProgress(true);
    try {
      const promises = Array.from(selectedProducts).map(id => {
        const product = products.find(p => p.id === id);
        if (product) {
          return axiosInstance.delete(`/products/${product.slug}/`);
        }
        return Promise.resolve();
      });

      await Promise.all(promises);
      await fetchProducts();
    } catch (error) {
      console.error('Error deleting products:', error);
      alert('Some products could not be deleted. Please try again.');
    } finally {
      setBulkActionInProgress(false);
    }
  };

  const handleBulkToggleFeatured = async (featured) => {
    if (selectedProducts.size === 0) return;

    setBulkActionInProgress(true);
    try {
      const promises = Array.from(selectedProducts).map(id => {
        const product = products.find(p => p.id === id);
        if (product) {
          return axiosInstance.patch(`/products/${product.slug}/`, { is_featured: featured });
        }
        return Promise.resolve();
      });

      await Promise.all(promises);
      await fetchProducts();
    } catch (error) {
      console.error('Error updating products:', error);
      alert('Some products could not be updated. Please try again.');
    } finally {
      setBulkActionInProgress(false);
    }
  };

  // ==================== PRODUCT HANDLERS ====================

  const openNewProductModal = () => {
    setEditingProduct(null);
    setShowProductModal(true);
  };

  const handleEditProduct = (product) => {
    setEditingProduct(product);
    setShowProductModal(true);
  };

  const closeProductModal = () => {
    setShowProductModal(false);
    setEditingProduct(null);
  };

  const handleProductSaved = async () => {
    closeProductModal();
    await fetchData();
    if (onUpdate) onUpdate();
  };

  const handleDeleteProduct = async (product) => {
    if (window.confirm(`Delete "${product.name}"? This action cannot be undone.`)) {
      try {
        await axiosInstance.delete(`/products/${product.slug}/`);
        fetchProducts();
      } catch (error) {
        console.error('Error deleting product:', error);
        alert('Error deleting product');
      }
    }
  };

  // ==================== CATEGORY HANDLERS ====================

  const handleCategoryInputChange = (e) => {
    const { name, value, type, checked } = e.target;
    setCategoryForm(prev => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value,
    }));
  };

  const handleCategorySubmit = async (e) => {
    e.preventDefault();
    setSaving(true);

    try {
      const payload = {
        name: categoryForm.name,
        description: categoryForm.description,
        is_active: categoryForm.is_active,
        display_order: parseInt(categoryForm.display_order) || 0,
      };
      if (categoryForm.parent) {
        payload.parent = categoryForm.parent;
      }

      if (editingCategory) {
        await axiosInstance.put(`/categories/${editingCategory.slug}/`, payload);
      } else {
        await axiosInstance.post('/categories/', payload);
      }

      resetCategoryForm();
      fetchCategories();
      setShowCategoryModal(false);
    } catch (error) {
      console.error('Error saving category:', error);
      alert('Error saving category: ' + (error.response?.data ? JSON.stringify(error.response.data) : error.message));
    } finally {
      setSaving(false);
    }
  };

  const handleEditCategory = (category) => {
    setEditingCategory(category);
    setCategoryForm({
      name: category.name,
      description: category.description || '',
      parent: category.parent || '',
      is_active: category.is_active !== false,
      display_order: category.display_order || 0,
    });
    setShowCategoryModal(true);
  };

  const handleDeleteCategory = async (category) => {
    if (window.confirm(`Delete category "${category.name}"? Products in this category will be uncategorized.`)) {
      try {
        await axiosInstance.delete(`/categories/${category.slug}/`);
        fetchCategories();
        fetchProducts();
      } catch (error) {
        console.error('Error deleting category:', error);
        alert('Error deleting category');
      }
    }
  };

  const resetCategoryForm = () => {
    setCategoryForm({
      name: '',
      description: '',
      parent: '',
      is_active: true,
      display_order: 0,
    });
    setEditingCategory(null);
  };

  // ==================== HELPERS ====================

  const getStatusConfig = (status) => {
    const configs = {
      draft: { label: 'Draft', color: '#6b7280', bg: '#f3f4f6' },
      active: { label: 'Active', color: '#059669', bg: '#d1fae5' },
      archived: { label: 'Archived', color: '#dc2626', bg: '#fee2e2' },
    };
    return configs[status] || configs.draft;
  };

  if (loading) {
    return (
      <div className="loading-products">
        <div className="loading-spinner-admin"></div>
        <p>Loading...</p>
      </div>
    );
  }

  return (
    <div className="product-management">
      {/* Tab Navigation */}
      <div className="pm-tabs">
        <button
          className={`pm-tab ${activeTab === 'products' ? 'active' : ''}`}
          onClick={() => setActiveTab('products')}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path>
          </svg>
          Products ({products.length})
        </button>
        <button
          className={`pm-tab ${activeTab === 'categories' ? 'active' : ''}`}
          onClick={() => setActiveTab('categories')}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
          </svg>
          Categories ({categories.length})
        </button>
      </div>

      {/* Products Tab */}
      {activeTab === 'products' && (
        <>
          <div className="pm-header">
            <div className="pm-title-row">
              <h2>Products</h2>
              <button className="btn-primary" onClick={openNewProductModal}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="12" y1="5" x2="12" y2="19"></line>
                  <line x1="5" y1="12" x2="19" y2="12"></line>
                </svg>
                Add Product
              </button>
            </div>

            <div className="pm-filters">
              <div className="pm-search">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="11" cy="11" r="8"></circle>
                  <path d="m21 21-4.35-4.35"></path>
                </svg>
                <input
                  type="text"
                  placeholder="Search products..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                />
              </div>
              <select value={filterCategory} onChange={(e) => setFilterCategory(e.target.value)} className="pm-filter-select">
                <option value="">All Categories</option>
                {categories.map((cat) => (
                  <option key={cat.id} value={cat.id}>{cat.name}</option>
                ))}
              </select>
              <select value={filterBrand} onChange={(e) => setFilterBrand(e.target.value)} className="pm-filter-select">
                <option value="">All Brands</option>
                {brands.map((brand) => (
                  <option key={brand.id} value={brand.id}>{brand.name}</option>
                ))}
              </select>
              <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)} className="pm-filter-select">
                <option value="">All Status</option>
                <option value="draft">Draft</option>
                <option value="active">Active</option>
                <option value="archived">Archived</option>
              </select>
            </div>
          </div>

          {/* Bulk Actions Toolbar */}
          {selectedProducts.size > 0 && (
            <div className="pm-bulk-toolbar">
              <div className="pm-bulk-info">
                <span className="pm-bulk-count">{selectedProducts.size}</span>
                <span>product{selectedProducts.size !== 1 ? 's' : ''} selected</span>
                <button className="pm-bulk-clear" onClick={() => setSelectedProducts(new Set())}>
                  Clear selection
                </button>
              </div>
              <div className="pm-bulk-actions">
                <button
                  className="pm-bulk-btn pm-bulk-activate"
                  onClick={() => handleBulkStatusChange('active')}
                  disabled={bulkActionInProgress}
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <polyline points="20 6 9 17 4 12"></polyline>
                  </svg>
                  Activate
                </button>
                <button
                  className="pm-bulk-btn pm-bulk-archive"
                  onClick={() => handleBulkStatusChange('archived')}
                  disabled={bulkActionInProgress}
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <polyline points="21 8 21 21 3 21 3 8"></polyline>
                    <rect x="1" y="3" width="22" height="5"></rect>
                  </svg>
                  Archive
                </button>
                <button
                  className="pm-bulk-btn pm-bulk-draft"
                  onClick={() => handleBulkStatusChange('draft')}
                  disabled={bulkActionInProgress}
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
                    <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
                  </svg>
                  Set Draft
                </button>
                <div className="pm-bulk-divider"></div>
                <button
                  className="pm-bulk-btn pm-bulk-feature"
                  onClick={() => handleBulkToggleFeatured(true)}
                  disabled={bulkActionInProgress}
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
                  </svg>
                  Feature
                </button>
                <button
                  className="pm-bulk-btn pm-bulk-unfeature"
                  onClick={() => handleBulkToggleFeatured(false)}
                  disabled={bulkActionInProgress}
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
                    <line x1="2" y1="2" x2="22" y2="22"></line>
                  </svg>
                  Unfeature
                </button>
                <div className="pm-bulk-divider"></div>
                <button
                  className="pm-bulk-btn pm-bulk-delete"
                  onClick={handleBulkDelete}
                  disabled={bulkActionInProgress}
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <polyline points="3 6 5 6 21 6"></polyline>
                    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                  </svg>
                  Delete
                </button>
              </div>
              {bulkActionInProgress && (
                <div className="pm-bulk-loading">
                  <div className="pm-bulk-spinner"></div>
                  Processing...
                </div>
              )}
            </div>
          )}

          <div className="pm-table-container">
            <table className="pm-table">
              <thead>
                <tr>
                  <th style={{ width: '40px' }}>
                    <label className="pm-checkbox">
                      <input
                        type="checkbox"
                        checked={isAllSelected}
                        ref={(el) => { if (el) el.indeterminate = isSomeSelected; }}
                        onChange={handleSelectAll}
                      />
                      <span className="pm-checkmark"></span>
                    </label>
                  </th>
                  <th style={{ width: '60px' }}>Image</th>
                  <th className="pm-sortable" onClick={() => handleSort('name')}>
                    Product <SortIcon field="name" />
                  </th>
                  <th className="pm-sortable" onClick={() => handleSort('category')}>
                    Category <SortIcon field="category" />
                  </th>
                  <th className="pm-sortable" onClick={() => handleSort('brand')}>
                    Brand <SortIcon field="brand" />
                  </th>
                  <th className="pm-sortable" onClick={() => handleSort('price')}>
                    Price <SortIcon field="price" />
                  </th>
                  <th className="pm-sortable" onClick={() => handleSort('stock')}>
                    Stock <SortIcon field="stock" />
                  </th>
                  <th className="pm-sortable" onClick={() => handleSort('status')}>
                    Status <SortIcon field="status" />
                  </th>
                  <th style={{ width: '100px' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredAndSortedProducts.length === 0 ? (
                  <tr>
                    <td colSpan="9" className="pm-empty">
                      <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1">
                        <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path>
                      </svg>
                      <p>No products found</p>
                    </td>
                  </tr>
                ) : (
                  filteredAndSortedProducts.map((product) => {
                    const statusConfig = getStatusConfig(product.status);
                    const isSelected = selectedProducts.has(product.id);
                    return (
                      <tr key={product.id} className={isSelected ? 'pm-row-selected' : ''}>
                        <td>
                          <label className="pm-checkbox">
                            <input
                              type="checkbox"
                              checked={isSelected}
                              onChange={() => handleSelectProduct(product.id)}
                            />
                            <span className="pm-checkmark"></span>
                          </label>
                        </td>
                        <td>
                          {product.primary_image_url ? (
                            <img src={product.primary_image_url} alt="" className="pm-product-thumb" />
                          ) : (
                            <div className="pm-no-image">
                              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                                <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
                                <circle cx="8.5" cy="8.5" r="1.5"></circle>
                                <polyline points="21 15 16 10 5 21"></polyline>
                              </svg>
                            </div>
                          )}
                        </td>
                        <td>
                          <div className="pm-product-info">
                            <span className="pm-product-name">
                              {product.name}
                              {product.is_featured && (
                                <span className="pm-featured-badge" title="Featured">★</span>
                              )}
                            </span>
                            {product.pack_size && <span className="pm-product-detail">{product.pack_size}</span>}
                          </div>
                        </td>
                        <td>
                          <span className="pm-category-tag">{product.category_name || 'Uncategorized'}</span>
                        </td>
                        <td>
                          <span className="pm-brand-tag">{product.brand_name || '—'}</span>
                        </td>
                        <td><span className="pm-price">AED {parseFloat(product.price).toFixed(2)}</span></td>
                        <td>
                          <span className={`pm-stock ${product.stock_quantity > 10 ? 'high' : product.stock_quantity > 0 ? 'low' : 'out'}`}>
                            {product.stock_quantity}
                          </span>
                        </td>
                        <td>
                          <span className="pm-status" style={{ color: statusConfig.color, backgroundColor: statusConfig.bg }}>
                            {statusConfig.label}
                          </span>
                        </td>
                        <td>
                          <div className="pm-actions">
                            <button className="pm-btn-icon" onClick={() => handleEditProduct(product)} title="Edit">
                              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
                                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
                              </svg>
                            </button>
                            <button className="pm-btn-icon danger" onClick={() => handleDeleteProduct(product)} title="Delete">
                              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <polyline points="3 6 5 6 21 6"></polyline>
                                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                              </svg>
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>

          {/* Results count */}
          <div className="pm-results-count">
            Showing {filteredAndSortedProducts.length} of {products.length} products
          </div>
        </>
      )}

      {/* Categories Tab */}
      {activeTab === 'categories' && (
        <>
          <div className="pm-header">
            <div className="pm-title-row">
              <h2>Categories</h2>
              <button className="btn-primary" onClick={() => { resetCategoryForm(); setShowCategoryModal(true); }}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="12" y1="5" x2="12" y2="19"></line>
                  <line x1="5" y1="12" x2="19" y2="12"></line>
                </svg>
                Add Category
              </button>
            </div>
          </div>

          <div className="pm-categories-grid">
            {categories.length === 0 ? (
              <div className="pm-empty-state">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1">
                  <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
                </svg>
                <p>No categories yet</p>
                <button className="btn-primary" onClick={() => { resetCategoryForm(); setShowCategoryModal(true); }}>
                  Create Your First Category
                </button>
              </div>
            ) : (
              categories.map((cat) => (
                <div key={cat.id} className={`pm-category-card ${!cat.is_active ? 'inactive' : ''}`}>
                  <div className="pm-category-header">
                    <h3>{cat.name}</h3>
                    <span className={`pm-category-status ${cat.is_active ? 'active' : 'inactive'}`}>
                      {cat.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </div>
                  {cat.description && <p className="pm-category-desc">{cat.description}</p>}
                  <div className="pm-category-meta">
                    <span>{cat.product_count || 0} products</span>
                    {cat.parent_name && <span>Parent: {cat.parent_name}</span>}
                  </div>
                  <div className="pm-category-actions">
                    <button className="pm-btn-text" onClick={() => handleEditCategory(cat)}>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
                        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
                      </svg>
                      Edit
                    </button>
                    <button className="pm-btn-text danger" onClick={() => handleDeleteCategory(cat)}>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="3 6 5 6 21 6"></polyline>
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                      </svg>
                      Delete
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </>
      )}

      <ProductFormModal
        isOpen={showProductModal}
        product={editingProduct}
        onClose={closeProductModal}
        onSaved={handleProductSaved}
      />

      {/* Category Modal */}
      {showCategoryModal && (
        <div className="pm-modal-overlay" onClick={() => { setShowCategoryModal(false); resetCategoryForm(); }}>
          <div className="pm-modal pm-modal-sm" onClick={(e) => e.stopPropagation()}>
            <div className="pm-modal-header">
              <h2>{editingCategory ? 'Edit Category' : 'New Category'}</h2>
              <button className="pm-modal-close" onClick={() => { setShowCategoryModal(false); resetCategoryForm(); }}>
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="18" y1="6" x2="6" y2="18"></line>
                  <line x1="6" y1="6" x2="18" y2="18"></line>
                </svg>
              </button>
            </div>

            <form onSubmit={handleCategorySubmit} className="pm-modal-body">
              <div className="pm-field">
                <label>Category Name <span className="required">*</span></label>
                <input type="text" name="name" value={categoryForm.name} onChange={handleCategoryInputChange} required placeholder="Enter category name" />
              </div>

              <div className="pm-field">
                <label>Description</label>
                <textarea name="description" value={categoryForm.description} onChange={handleCategoryInputChange} rows="3" placeholder="Optional description" />
              </div>

              <div className="pm-field">
                <label>Parent Category</label>
                <select name="parent" value={categoryForm.parent} onChange={handleCategoryInputChange}>
                  <option value="">None (Top Level)</option>
                  {categories.filter(c => c.id !== editingCategory?.id).map((cat) => (
                    <option key={cat.id} value={cat.id}>{cat.name}</option>
                  ))}
                </select>
              </div>

              <div className="pm-field-row">
                <div className="pm-field">
                  <label>Display Order</label>
                  <input type="number" name="display_order" value={categoryForm.display_order} onChange={handleCategoryInputChange} min="0" />
                </div>
              </div>

              <label className="pm-toggle">
                <input type="checkbox" name="is_active" checked={categoryForm.is_active} onChange={handleCategoryInputChange} />
                <span className="pm-toggle-slider"></span>
                <span>Active</span>
              </label>

              <div className="pm-modal-footer">
                <button type="button" className="pm-btn-secondary" onClick={() => { setShowCategoryModal(false); resetCategoryForm(); }}>
                  Cancel
                </button>
                <button type="submit" className="pm-btn-primary" disabled={saving}>
                  {saving ? 'Saving...' : (editingCategory ? 'Update Category' : 'Create Category')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};

export default ProductManagement;

