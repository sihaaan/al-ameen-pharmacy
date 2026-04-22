// frontend/src/components/ProductManagement.js
import React, { useState, useEffect, useCallback, useMemo } from 'react';
import axiosInstance from '../utils/axios';

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

  // Product form state
  const [formData, setFormData] = useState({
    name: '',
    short_description: '',
    detailed_description: '',
    price: '',
    stock_quantity: '',
    category: '',
    brand: '',
    dosage: '',
    pack_size: '',
    active_ingredient: '',
    requires_prescription: false,
    status: 'draft',
    is_featured: false,
    show_price: false,
  });

  // Category form state
  const [categoryForm, setCategoryForm] = useState({
    name: '',
    description: '',
    parent: '',
    is_active: true,
    display_order: 0,
  });

  // Multi-image state
  const [productImages, setProductImages] = useState([]);
  const [newImages, setNewImages] = useState([]);
  const [imagesToDelete, setImagesToDelete] = useState([]);

  // Brand creation state
  const [newBrandName, setNewBrandName] = useState('');
  const [isCreatingBrand, setIsCreatingBrand] = useState(false);
  const [brandError, setBrandError] = useState('');

  // New category creation inline
  const [newCategoryName, setNewCategoryName] = useState('');
  const [isCreatingCategory, setIsCreatingCategory] = useState(false);
  const [categoryError, setCategoryError] = useState('');

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

  const fetchBrands = async () => {
    try {
      const response = await axiosInstance.get('/brands/');
      setBrands(response.data);
    } catch (error) {
      console.error('Error fetching brands:', error);
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

  const handleInputChange = (e) => {
    const { name, value, type, checked } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value,
    }));
  };

  const handleImagesChange = (e) => {
    if (e.target.files && e.target.files.length > 0) {
      const filesArray = Array.from(e.target.files).map(file => ({
        file,
        preview: URL.createObjectURL(file),
        isPrimary: newImages.length === 0 && productImages.length === 0,
      }));
      setNewImages(prev => [...prev, ...filesArray]);
    }
  };

  const handleRemoveNewImage = (index) => {
    setNewImages(prev => {
      const updated = prev.filter((_, i) => i !== index);
      if (prev[index]?.isPrimary && updated.length > 0) {
        updated[0].isPrimary = true;
      }
      return updated;
    });
  };

  const handleDeleteExistingImage = (imageId) => {
    setImagesToDelete(prev => [...prev, imageId]);
    setProductImages(prev => prev.filter(img => img.id !== imageId));
  };

  const handleSetPrimary = async (imageId) => {
    if (!editingProduct) return;
    try {
      await axiosInstance.patch(`/product-images/${imageId}/`, { is_primary: true });
      setProductImages(prev => prev.map(img => ({
        ...img,
        is_primary: img.id === imageId
      })));
    } catch (error) {
      console.error('Error setting primary image:', error);
    }
  };

  const handleSetNewImagePrimary = (index) => {
    setNewImages(prev => prev.map((img, i) => ({
      ...img,
      isPrimary: i === index
    })));
    setProductImages(prev => prev.map(img => ({
      ...img,
      is_primary: false
    })));
  };

  const handleCreateBrand = async () => {
    const trimmedName = newBrandName.trim();
    if (!trimmedName) {
      setBrandError('Brand name is required');
      return;
    }
    const exists = brands.some(b => b.name.toLowerCase() === trimmedName.toLowerCase());
    if (exists) {
      setBrandError('This brand already exists');
      return;
    }
    setIsCreatingBrand(true);
    setBrandError('');
    try {
      const response = await axiosInstance.post('/brands/', { name: trimmedName });
      await fetchBrands();
      setFormData(prev => ({ ...prev, brand: response.data.id }));
      setNewBrandName('');
    } catch (error) {
      setBrandError(error.response?.data?.name?.[0] || 'Failed to create brand');
    } finally {
      setIsCreatingBrand(false);
    }
  };

  const handleCreateCategoryInline = async () => {
    const trimmedName = newCategoryName.trim();
    if (!trimmedName) {
      setCategoryError('Category name is required');
      return;
    }
    const exists = categories.some(c => c.name.toLowerCase() === trimmedName.toLowerCase());
    if (exists) {
      setCategoryError('This category already exists');
      return;
    }
    setIsCreatingCategory(true);
    setCategoryError('');
    try {
      const response = await axiosInstance.post('/categories/', { name: trimmedName, is_active: true });
      await fetchCategories();
      setFormData(prev => ({ ...prev, category: response.data.id }));
      setNewCategoryName('');
    } catch (error) {
      setCategoryError(error.response?.data?.name?.[0] || 'Failed to create category');
    } finally {
      setIsCreatingCategory(false);
    }
  };

  const handleProductSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);

    const data = new FormData();
    const fieldsToSend = [
      'name', 'short_description', 'detailed_description',
      'price', 'stock_quantity', 'category', 'brand',
      'dosage', 'pack_size', 'active_ingredient',
      'requires_prescription', 'status', 'is_featured', 'show_price'
    ];

    fieldsToSend.forEach((key) => {
      const value = formData[key];
      if (value !== null && value !== '' && value !== undefined) {
        data.append(key, typeof value === 'boolean' ? value.toString() : value);
      }
    });

    const primaryNewImage = newImages.find(img => img.isPrimary);
    if (primaryNewImage) {
      data.append('image', primaryNewImage.file);
    } else if (newImages.length > 0) {
      data.append('image', newImages[0].file);
    }

    try {
      let productId;

      if (editingProduct) {
        const response = await axiosInstance.put(`/products/${editingProduct.slug}/`, data, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
        productId = response.data.id;

        for (const imageId of imagesToDelete) {
          try {
            await axiosInstance.delete(`/product-images/${imageId}/`);
          } catch (err) {
            console.error('Error deleting image:', err);
          }
        }

        const additionalImages = primaryNewImage
          ? newImages.filter(img => !img.isPrimary)
          : newImages.slice(1);

        for (const img of additionalImages) {
          const imgData = new FormData();
          imgData.append('product', productId);
          imgData.append('image', img.file);
          imgData.append('is_primary', 'false');
          await axiosInstance.post('/product-images/', imgData, {
            headers: { 'Content-Type': 'multipart/form-data' },
          });
        }
      } else {
        const response = await axiosInstance.post('/products/', data, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
        productId = response.data.id;

        const additionalImages = primaryNewImage
          ? newImages.filter(img => !img.isPrimary)
          : newImages.slice(1);

        for (const img of additionalImages) {
          const imgData = new FormData();
          imgData.append('product', productId);
          imgData.append('image', img.file);
          imgData.append('is_primary', 'false');
          await axiosInstance.post('/product-images/', imgData, {
            headers: { 'Content-Type': 'multipart/form-data' },
          });
        }
      }

      resetProductForm();
      fetchProducts();
      setShowProductModal(false);
    } catch (error) {
      console.error('Error saving product:', error);
      alert('Error saving product: ' + (error.response?.data ? JSON.stringify(error.response.data) : error.message));
    } finally {
      setSaving(false);
    }
  };

  const handleEditProduct = async (product) => {
    try {
      const response = await axiosInstance.get(`/products/${product.slug}/`);
      const fullProduct = response.data;

      setEditingProduct(fullProduct);
      setFormData({
        name: fullProduct.name,
        short_description: fullProduct.short_description || '',
        detailed_description: fullProduct.detailed_description || '',
        price: fullProduct.price,
        stock_quantity: fullProduct.stock_quantity,
        category: fullProduct.category || '',
        brand: fullProduct.brand || '',
        dosage: fullProduct.dosage || '',
        pack_size: fullProduct.pack_size || '',
        active_ingredient: fullProduct.active_ingredient || '',
        requires_prescription: fullProduct.requires_prescription || false,
        status: fullProduct.status || 'draft',
        is_featured: fullProduct.is_featured || false,
        show_price: fullProduct.show_price || false,
      });
      setProductImages(fullProduct.images || []);
      setNewImages([]);
      setImagesToDelete([]);
      setNewBrandName('');
      setBrandError('');
      setNewCategoryName('');
      setCategoryError('');
      setShowProductModal(true);
    } catch (error) {
      console.error('Error fetching product details:', error);
      alert('Error loading product details');
    }
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

  const resetProductForm = () => {
    setFormData({
      name: '',
      short_description: '',
      detailed_description: '',
      price: '',
      stock_quantity: '',
      category: '',
      brand: '',
      dosage: '',
      pack_size: '',
      active_ingredient: '',
      requires_prescription: false,
      status: 'draft',
      is_featured: false,
      show_price: false,
    });
    setEditingProduct(null);
    setProductImages([]);
    setNewImages([]);
    setImagesToDelete([]);
    setNewBrandName('');
    setBrandError('');
    setNewCategoryName('');
    setCategoryError('');
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
              <button className="btn-primary" onClick={() => { resetProductForm(); setShowProductModal(true); }}>
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

      {/* Product Modal */}
      {showProductModal && (
        <div className="pm-modal-overlay" onClick={() => { setShowProductModal(false); resetProductForm(); }}>
          <div className="pm-modal pm-modal-xl" onClick={(e) => e.stopPropagation()}>
            <div className="pm-modal-header">
              <h2>{editingProduct ? 'Edit Product' : 'New Product'}</h2>
              <button className="pm-modal-close" onClick={() => { setShowProductModal(false); resetProductForm(); }}>
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="18" y1="6" x2="6" y2="18"></line>
                  <line x1="6" y1="6" x2="18" y2="18"></line>
                </svg>
              </button>
            </div>

            <form onSubmit={handleProductSubmit} className="pm-modal-body">
              <div className="pm-form-grid">
                {/* Left Column */}
                <div className="pm-form-column">
                  <div className="pm-form-section">
                    <h3>Basic Information</h3>

                    <div className="pm-field">
                      <label>Product Name <span className="required">*</span></label>
                      <input type="text" name="name" value={formData.name} onChange={handleInputChange} required placeholder="Enter product name" />
                    </div>

                    <div className="pm-field">
                      <label>Short Description <span className="required">*</span></label>
                      <textarea name="short_description" value={formData.short_description} onChange={handleInputChange} rows="2" required placeholder="Brief description for product cards" />
                    </div>

                    <div className="pm-field">
                      <label>Detailed Description</label>
                      <textarea name="detailed_description" value={formData.detailed_description} onChange={handleInputChange} rows="4" placeholder="Usage instructions, warnings, ingredients..." />
                    </div>
                  </div>

                  <div className="pm-form-section">
                    <h3>Organization</h3>

                    <div className="pm-field-row">
                      <div className="pm-field">
                        <label>Category</label>
                        <select name="category" value={formData.category} onChange={handleInputChange}>
                          <option value="">Select category</option>
                          {categories.map((cat) => (
                            <option key={cat.id} value={cat.id}>{cat.name}</option>
                          ))}
                        </select>
                      </div>
                      <div className="pm-field">
                        <label>Brand</label>
                        <select name="brand" value={formData.brand} onChange={handleInputChange}>
                          <option value="">Select brand</option>
                          {brands.map((brand) => (
                            <option key={brand.id} value={brand.id}>{brand.name}</option>
                          ))}
                        </select>
                      </div>
                    </div>

                    {/* Quick create */}
                    <div className="pm-quick-create">
                      <div className="pm-quick-create-row">
                        <input
                          type="text"
                          value={newCategoryName}
                          onChange={(e) => { setNewCategoryName(e.target.value); setCategoryError(''); }}
                          placeholder="New category name"
                        />
                        <button type="button" onClick={handleCreateCategoryInline} disabled={isCreatingCategory || !newCategoryName.trim()} className="pm-btn-sm">
                          {isCreatingCategory ? '...' : '+ Category'}
                        </button>
                      </div>
                      {categoryError && <span className="pm-error">{categoryError}</span>}

                      <div className="pm-quick-create-row">
                        <input
                          type="text"
                          value={newBrandName}
                          onChange={(e) => { setNewBrandName(e.target.value); setBrandError(''); }}
                          placeholder="New brand name"
                        />
                        <button type="button" onClick={handleCreateBrand} disabled={isCreatingBrand || !newBrandName.trim()} className="pm-btn-sm">
                          {isCreatingBrand ? '...' : '+ Brand'}
                        </button>
                      </div>
                      {brandError && <span className="pm-error">{brandError}</span>}
                    </div>
                  </div>

                  <div className="pm-form-section">
                    <h3>Pricing & Inventory</h3>

                    <div className="pm-field-row">
                      <div className="pm-field">
                        <label>Price (AED) <span className="required">*</span></label>
                        <input type="number" step="0.01" min="0" name="price" value={formData.price} onChange={handleInputChange} required placeholder="0.00" />
                      </div>
                      <div className="pm-field">
                        <label>Stock Quantity <span className="required">*</span></label>
                        <input type="number" min="0" name="stock_quantity" value={formData.stock_quantity} onChange={handleInputChange} required placeholder="0" />
                      </div>
                    </div>
                  </div>

                  <div className="pm-form-section">
                    <h3>Pharmacy Details</h3>

                    <div className="pm-field-row">
                      <div className="pm-field">
                        <label>Dosage</label>
                        <input type="text" name="dosage" value={formData.dosage} onChange={handleInputChange} placeholder="e.g., 500mg" />
                      </div>
                      <div className="pm-field">
                        <label>Pack Size</label>
                        <input type="text" name="pack_size" value={formData.pack_size} onChange={handleInputChange} placeholder="e.g., 30 tablets" />
                      </div>
                    </div>

                    <div className="pm-field">
                      <label>Active Ingredient</label>
                      <input type="text" name="active_ingredient" value={formData.active_ingredient} onChange={handleInputChange} placeholder="e.g., Paracetamol" />
                    </div>
                  </div>
                </div>

                {/* Right Column */}
                <div className="pm-form-column">
                  <div className="pm-form-section">
                    <h3>Product Status</h3>

                    <div className="pm-status-selector">
                      {['draft', 'active', 'archived'].map((status) => {
                        const config = getStatusConfig(status);
                        return (
                          <label key={status} className={`pm-status-option ${formData.status === status ? 'selected' : ''}`}>
                            <input
                              type="radio"
                              name="status"
                              value={status}
                              checked={formData.status === status}
                              onChange={handleInputChange}
                            />
                            <span className="pm-status-dot" style={{ backgroundColor: config.color }}></span>
                            <span>{config.label}</span>
                          </label>
                        );
                      })}
                    </div>
                    <p className="pm-help-text">Only Active products are visible to customers</p>

                    <div className="pm-toggles">
                      <label className="pm-toggle">
                        <input type="checkbox" name="requires_prescription" checked={formData.requires_prescription} onChange={handleInputChange} />
                        <span className="pm-toggle-slider"></span>
                        <span>Requires Prescription</span>
                      </label>
                      <label className="pm-toggle">
                        <input type="checkbox" name="is_featured" checked={formData.is_featured} onChange={handleInputChange} />
                        <span className="pm-toggle-slider"></span>
                        <span>Featured Product</span>
                      </label>
                      <label className="pm-toggle pm-toggle--price">
                        <input type="checkbox" name="show_price" checked={formData.show_price} onChange={handleInputChange} />
                        <span className="pm-toggle-slider"></span>
                        <span>Show Price Publicly</span>
                      </label>
                    </div>
                  </div>

                  <div className="pm-form-section">
                    <h3>Product Images</h3>

                    <div className="pm-images-grid">
                      {productImages.map((img) => (
                        <div key={img.id} className={`pm-image-item ${img.is_primary ? 'primary' : ''}`}>
                          <img src={img.image_url} alt="" />
                          <div className="pm-image-overlay">
                            {!img.is_primary && (
                              <button type="button" onClick={() => handleSetPrimary(img.id)} className="pm-img-btn" title="Set as primary">★</button>
                            )}
                            {img.is_primary && <span className="pm-primary-tag">Primary</span>}
                            <button type="button" onClick={() => handleDeleteExistingImage(img.id)} className="pm-img-btn delete" title="Delete">×</button>
                          </div>
                        </div>
                      ))}
                      {newImages.map((img, index) => (
                        <div key={`new-${index}`} className={`pm-image-item new ${img.isPrimary ? 'primary' : ''}`}>
                          <img src={img.preview} alt="" />
                          <div className="pm-image-overlay">
                            {!img.isPrimary && (
                              <button type="button" onClick={() => handleSetNewImagePrimary(index)} className="pm-img-btn" title="Set as primary">★</button>
                            )}
                            {img.isPrimary && <span className="pm-primary-tag">Primary</span>}
                            <button type="button" onClick={() => handleRemoveNewImage(index)} className="pm-img-btn delete" title="Remove">×</button>
                          </div>
                        </div>
                      ))}
                      <label className="pm-image-upload">
                        <input type="file" accept="image/*" multiple onChange={handleImagesChange} />
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <line x1="12" y1="5" x2="12" y2="19"></line>
                          <line x1="5" y1="12" x2="19" y2="12"></line>
                        </svg>
                        <span>Add Images</span>
                      </label>
                    </div>
                    <p className="pm-help-text">Click ★ to set primary image. Recommended: 800×800px</p>
                  </div>
                </div>
              </div>

              <div className="pm-modal-footer">
                <button type="button" className="pm-btn-secondary" onClick={() => { setShowProductModal(false); resetProductForm(); }}>
                  Cancel
                </button>
                <button type="submit" className="pm-btn-primary" disabled={saving}>
                  {saving ? 'Saving...' : (editingProduct ? 'Update Product' : 'Create Product')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

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
