// frontend/src/components/ProductManagement.js
import React, { useState, useEffect } from 'react';
import axiosInstance from '../utils/axios';

const ProductManagement = ({ onUpdate }) => {
  const [products, setProducts] = useState([]);
  const [categories, setCategories] = useState([]);
  const [brands, setBrands] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showAddModal, setShowAddModal] = useState(false);
  const [editingProduct, setEditingProduct] = useState(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [filterCategory, setFilterCategory] = useState('');

  // Form state - v2 schema aligned
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
  });

  // Multi-image state
  const [productImages, setProductImages] = useState([]); // Existing images from server
  const [newImages, setNewImages] = useState([]); // New images to upload
  const [imagesToDelete, setImagesToDelete] = useState([]); // Image IDs to delete

  // New brand creation state
  const [newBrandName, setNewBrandName] = useState('');
  const [isCreatingBrand, setIsCreatingBrand] = useState(false);
  const [brandError, setBrandError] = useState('');

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
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
      if (onUpdate) onUpdate();
    } catch (error) {
      console.error('Error fetching data:', error);
    } finally {
      setLoading(false);
    }
  };

  const fetchProducts = async () => {
    try {
      const response = await axiosInstance.get('/products/');
      setProducts(response.data);
      if (onUpdate) onUpdate();
    } catch (error) {
      console.error('Error fetching products:', error);
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

  const handleInputChange = (e) => {
    const { name, value, type, checked } = e.target;
    setFormData({
      ...formData,
      [name]: type === 'checkbox' ? checked : value,
    });
  };

  // Handle multiple image selection
  const handleImagesChange = (e) => {
    if (e.target.files && e.target.files.length > 0) {
      const filesArray = Array.from(e.target.files).map(file => ({
        file,
        preview: URL.createObjectURL(file),
        isPrimary: newImages.length === 0 && productImages.length === 0, // First image is primary
      }));
      setNewImages(prev => [...prev, ...filesArray]);
    }
  };

  // Remove a new image before upload
  const handleRemoveNewImage = (index) => {
    setNewImages(prev => {
      const updated = prev.filter((_, i) => i !== index);
      // If we removed the primary, make the first one primary
      if (prev[index]?.isPrimary && updated.length > 0) {
        updated[0].isPrimary = true;
      }
      return updated;
    });
  };

  // Mark an existing image for deletion
  const handleDeleteExistingImage = (imageId) => {
    setImagesToDelete(prev => [...prev, imageId]);
    setProductImages(prev => prev.filter(img => img.id !== imageId));
  };

  // Set an image as primary
  const handleSetPrimary = async (imageId) => {
    if (!editingProduct) return;

    try {
      await axiosInstance.patch(`/product-images/${imageId}/`, {
        is_primary: true
      });
      // Update local state
      setProductImages(prev => prev.map(img => ({
        ...img,
        is_primary: img.id === imageId
      })));
    } catch (error) {
      console.error('Error setting primary image:', error);
      alert('Failed to set primary image');
    }
  };

  // Set a new image as primary (before upload)
  const handleSetNewImagePrimary = (index) => {
    setNewImages(prev => prev.map((img, i) => ({
      ...img,
      isPrimary: i === index
    })));
    // Also unset primary from existing images
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

    // Check for duplicate (case-insensitive)
    const exists = brands.some(
      b => b.name.toLowerCase() === trimmedName.toLowerCase()
    );
    if (exists) {
      setBrandError('This brand already exists');
      return;
    }

    setIsCreatingBrand(true);
    setBrandError('');

    try {
      const response = await axiosInstance.post('/brands/', { name: trimmedName });
      const newBrand = response.data;

      // Refresh brands list and select the new brand
      await fetchBrands();
      setFormData(prev => ({ ...prev, brand: newBrand.id }));
      setNewBrandName('');
    } catch (error) {
      console.error('Error creating brand:', error);
      const errorMsg = error.response?.data?.name?.[0] || 'Failed to create brand';
      setBrandError(errorMsg);
    } finally {
      setIsCreatingBrand(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();

    const data = new FormData();

    // Append all form fields that match backend expectations
    const fieldsToSend = [
      'name', 'short_description', 'detailed_description',
      'price', 'stock_quantity', 'category', 'brand',
      'dosage', 'pack_size', 'active_ingredient',
      'requires_prescription', 'status', 'is_featured'
    ];

    fieldsToSend.forEach((key) => {
      const value = formData[key];
      if (value !== null && value !== '' && value !== undefined) {
        // Convert booleans to proper format
        if (typeof value === 'boolean') {
          data.append(key, value.toString());
        } else {
          data.append(key, value);
        }
      }
    });

    // Append the first new image as primary if there are new images
    const primaryNewImage = newImages.find(img => img.isPrimary);
    if (primaryNewImage) {
      data.append('image', primaryNewImage.file);
    } else if (newImages.length > 0) {
      data.append('image', newImages[0].file);
    }

    try {
      let productId;

      if (editingProduct) {
        // Update product
        const response = await axiosInstance.put(`/products/${editingProduct.slug}/`, data, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
        productId = response.data.id;

        // Delete marked images
        for (const imageId of imagesToDelete) {
          try {
            await axiosInstance.delete(`/product-images/${imageId}/`);
          } catch (err) {
            console.error('Error deleting image:', err);
          }
        }

        // Upload additional new images (skip the first one if it was sent with the product)
        const additionalImages = primaryNewImage
          ? newImages.filter(img => !img.isPrimary)
          : newImages.slice(1);

        for (const img of additionalImages) {
          const imgData = new FormData();
          imgData.append('product', productId);
          imgData.append('image', img.file);
          imgData.append('is_primary', 'false');
          try {
            await axiosInstance.post('/product-images/', imgData, {
              headers: { 'Content-Type': 'multipart/form-data' },
            });
          } catch (err) {
            console.error('Error uploading image:', err);
          }
        }

        alert('Product updated successfully!');
      } else {
        // Create new product
        const response = await axiosInstance.post('/products/', data, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
        productId = response.data.id;

        // Upload additional images
        const additionalImages = primaryNewImage
          ? newImages.filter(img => !img.isPrimary)
          : newImages.slice(1);

        for (const img of additionalImages) {
          const imgData = new FormData();
          imgData.append('product', productId);
          imgData.append('image', img.file);
          imgData.append('is_primary', 'false');
          try {
            await axiosInstance.post('/product-images/', imgData, {
              headers: { 'Content-Type': 'multipart/form-data' },
            });
          } catch (err) {
            console.error('Error uploading image:', err);
          }
        }

        alert('Product added successfully!');
      }

      resetForm();
      fetchProducts();
      setShowAddModal(false);
    } catch (error) {
      console.error('Error saving product:', error);
      const errorMsg = error.response?.data
        ? JSON.stringify(error.response.data)
        : error.message;
      alert('Error saving product: ' + errorMsg);
    }
  };

  const handleEdit = async (product) => {
    // Fetch full product details to get images
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
      });
      setProductImages(fullProduct.images || []);
      setNewImages([]);
      setImagesToDelete([]);
      setNewBrandName('');
      setBrandError('');
      setShowAddModal(true);
    } catch (error) {
      console.error('Error fetching product details:', error);
      alert('Error loading product details');
    }
  };

  const handleDelete = async (product) => {
    if (window.confirm('Are you sure you want to delete this product?')) {
      try {
        await axiosInstance.delete(`/products/${product.slug}/`);
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
    });
    setEditingProduct(null);
    setProductImages([]);
    setNewImages([]);
    setImagesToDelete([]);
    setNewBrandName('');
    setBrandError('');
  };

  const filteredProducts = products.filter((product) => {
    const matchesSearch = product.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
                          (product.short_description || '').toLowerCase().includes(searchTerm.toLowerCase());
    const matchesCategory = filterCategory === '' || product.category === parseInt(filterCategory);
    return matchesSearch && matchesCategory;
  });

  if (loading) {
    return (
      <div className="loading-products">
        <div className="loading-spinner-admin"></div>
        <p>Loading products...</p>
      </div>
    );
  }

  return (
    <div className="product-management">
      <div className="management-header">
        <h2>Product Management</h2>
        <button className="btn-add-product" onClick={() => setShowAddModal(true)}>
          + Add New Product
        </button>
      </div>

      {/* Search and Filter */}
      <div className="management-controls">
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
              {cat.full_path || cat.name}
            </option>
          ))}
        </select>
      </div>

      {/* Products Table */}
      <div className="products-table-container">
        <table className="products-table">
          <thead>
            <tr>
              <th>Image</th>
              <th>Name</th>
              <th>Brand</th>
              <th>Category</th>
              <th>Price</th>
              <th>Stock</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {filteredProducts.map((product) => (
              <tr key={product.id}>
                <td>
                  {product.primary_image_url ? (
                    <img
                      src={product.primary_image_url}
                      alt={product.name}
                      className="product-thumb"
                    />
                  ) : (
                    <div className="no-image-thumb">No Image</div>
                  )}
                </td>
                <td>
                  <strong>{product.name}</strong>
                  <br />
                  <small>{(product.short_description || '').substring(0, 50)}...</small>
                </td>
                <td>{product.brand_name || 'N/A'}</td>
                <td>{product.category_name || 'N/A'}</td>
                <td>AED {product.price}</td>
                <td>
                  <span className={`stock-badge ${product.stock_quantity > 10 ? 'in-stock' : product.stock_quantity > 0 ? 'low-stock' : 'out-of-stock'}`}>
                    {product.stock_quantity}
                  </span>
                </td>
                <td>
                  <span className={`status-badge status-${product.status}`}>
                    {product.status}
                  </span>
                </td>
                <td>
                  <div className="table-actions">
                    <button onClick={() => handleEdit(product)} className="btn-edit-sm">
                      Edit
                    </button>
                    <button onClick={() => handleDelete(product)} className="btn-delete-sm">
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Add/Edit Modal */}
      {showAddModal && (
        <div className="modal-overlay" onClick={() => { setShowAddModal(false); resetForm(); }}>
          <div className="modal-content modal-large" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>{editingProduct ? 'Edit Product' : 'Add New Product'}</h2>
              <button className="modal-close" onClick={() => { setShowAddModal(false); resetForm(); }}>
                X
              </button>
            </div>

            <form onSubmit={handleSubmit} className="product-form">
              <div className="form-grid-two-col">
                {/* Left Column - Basic Information */}
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
                      name="short_description"
                      value={formData.short_description}
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
                            {cat.full_path || cat.name}
                          </option>
                        ))}
                      </select>
                    </div>

                    <div className="form-group">
                      <label>Brand</label>
                      <select name="brand" value={formData.brand} onChange={handleInputChange}>
                        <option value="">Select Brand</option>
                        {brands.map((brand) => (
                          <option key={brand.id} value={brand.id}>
                            {brand.name}
                          </option>
                        ))}
                      </select>
                    </div>
                  </div>

                  {/* New Brand Creation */}
                  <div className="form-group">
                    <label>Or Add New Brand</label>
                    <div className="new-brand-row">
                      <input
                        type="text"
                        value={newBrandName}
                        onChange={(e) => {
                          setNewBrandName(e.target.value);
                          setBrandError('');
                        }}
                        placeholder="Enter new brand name"
                        className="new-brand-input"
                      />
                      <button
                        type="button"
                        onClick={handleCreateBrand}
                        disabled={isCreatingBrand || !newBrandName.trim()}
                        className="btn-create-brand"
                      >
                        {isCreatingBrand ? 'Creating...' : 'Add Brand'}
                      </button>
                    </div>
                    {brandError && <small className="error-text">{brandError}</small>}
                  </div>

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
                    <label>Active Ingredient</label>
                    <input
                      type="text"
                      name="active_ingredient"
                      value={formData.active_ingredient}
                      onChange={handleInputChange}
                      placeholder="e.g., Paracetamol, Ibuprofen"
                    />
                  </div>

                  <div className="form-row">
                    <div className="form-group">
                      <label>Status *</label>
                      <select name="status" value={formData.status} onChange={handleInputChange} required>
                        <option value="draft">Draft</option>
                        <option value="active">Active</option>
                        <option value="archived">Archived</option>
                      </select>
                      <small>Only "Active" products are visible to customers</small>
                    </div>
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

                  <div className="form-group">
                    <label className="checkbox-label">
                      <input
                        type="checkbox"
                        name="is_featured"
                        checked={formData.is_featured}
                        onChange={handleInputChange}
                      />
                      Featured Product
                    </label>
                  </div>
                </div>

                {/* Right Column - Image Management Section */}
                <div className="form-section">
                  <h3>Product Images</h3>

                  {/* Existing Images */}
                  {productImages.length > 0 && (
                    <div className="existing-images">
                      <label>Current Images ({productImages.length})</label>
                      <div className="image-grid">
                        {productImages.map((img) => (
                          <div key={img.id} className={`image-item ${img.is_primary ? 'is-primary' : ''}`}>
                            <img src={img.image_url} alt="Product" />
                            <div className="image-actions">
                              {!img.is_primary && (
                                <button
                                  type="button"
                                  className="btn-set-primary"
                                  onClick={() => handleSetPrimary(img.id)}
                                  title="Set as primary"
                                >
                                  ★
                                </button>
                              )}
                              {img.is_primary && (
                                <span className="primary-badge">Primary</span>
                              )}
                              <button
                                type="button"
                                className="btn-delete-image"
                                onClick={() => handleDeleteExistingImage(img.id)}
                                title="Delete image"
                              >
                                ✕
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* New Images to Upload */}
                  {newImages.length > 0 && (
                    <div className="new-images">
                      <label>New Images to Upload ({newImages.length})</label>
                      <div className="image-grid">
                        {newImages.map((img, index) => (
                          <div key={index} className={`image-item ${img.isPrimary ? 'is-primary' : ''}`}>
                            <img src={img.preview} alt="Preview" />
                            <div className="image-actions">
                              {!img.isPrimary && (
                                <button
                                  type="button"
                                  className="btn-set-primary"
                                  onClick={() => handleSetNewImagePrimary(index)}
                                  title="Set as primary"
                                >
                                  ★
                                </button>
                              )}
                              {img.isPrimary && (
                                <span className="primary-badge">Primary</span>
                              )}
                              <button
                                type="button"
                                className="btn-delete-image"
                                onClick={() => handleRemoveNewImage(index)}
                                title="Remove"
                              >
                                ✕
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Add Images Button */}
                  <div className="form-group">
                    <label>Add Images</label>
                    <input
                      type="file"
                      accept="image/*"
                      multiple
                      onChange={handleImagesChange}
                      className="file-input"
                    />
                    <small>Select multiple images. Click ★ to set primary. Recommended: 800x800px</small>
                  </div>

                  {productImages.length === 0 && newImages.length === 0 && (
                    <div className="no-images-placeholder">
                      <p>No images yet. Add images to showcase your product.</p>
                    </div>
                  )}
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

export default ProductManagement;
