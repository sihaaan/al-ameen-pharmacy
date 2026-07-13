import React, { useEffect, useState } from 'react';
import axiosInstance from '../utils/axios';

const emptyProductForm = (defaultStatus = 'draft') => ({
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
  status: defaultStatus,
  is_featured: false,
  show_price: false,
});

const getStatusConfig = (status) => {
  const configs = {
    draft: { label: 'Draft', color: '#6b7280', bg: '#f3f4f6' },
    active: { label: 'Active', color: '#059669', bg: '#d1fae5' },
    archived: { label: 'Archived', color: '#dc2626', bg: '#fee2e2' },
  };
  return configs[status] || configs.draft;
};

const ProductFormModal = ({
  isOpen,
  product = null,
  onClose,
  onSaved,
  defaultStatus = 'draft',
  createTitle = 'New Product',
  editTitle = 'Edit Product',
  contextHelpText = '',
}) => {
  const [categories, setCategories] = useState([]);
  const [brands, setBrands] = useState([]);
  const [editingProduct, setEditingProduct] = useState(null);
  const [formData, setFormData] = useState(emptyProductForm(defaultStatus));
  const [productImages, setProductImages] = useState([]);
  const [newImages, setNewImages] = useState([]);
  const [imagesToDelete, setImagesToDelete] = useState([]);
  const [newBrandName, setNewBrandName] = useState('');
  const [isCreatingBrand, setIsCreatingBrand] = useState(false);
  const [brandError, setBrandError] = useState('');
  const [newCategoryName, setNewCategoryName] = useState('');
  const [isCreatingCategory, setIsCreatingCategory] = useState(false);
  const [categoryError, setCategoryError] = useState('');
  const [saving, setSaving] = useState(false);
  const [loadingProduct, setLoadingProduct] = useState(false);
  const [duplicateWarning, setDuplicateWarning] = useState(null);

  const resetProductForm = () => {
    setEditingProduct(null);
    setFormData(emptyProductForm(defaultStatus));
    setProductImages([]);
    setNewImages([]);
    setImagesToDelete([]);
    setNewBrandName('');
    setBrandError('');
    setNewCategoryName('');
    setCategoryError('');
    setDuplicateWarning(null);
  };

  const closeModal = () => {
    resetProductForm();
    if (onClose) onClose();
  };

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

  useEffect(() => {
    if (!isOpen) return;
    fetchCategories();
    fetchBrands();
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    if (!product) {
      resetProductForm();
      return;
    }

    const loadProduct = async () => {
      setLoadingProduct(true);
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
          status: fullProduct.status || defaultStatus,
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
      } catch (error) {
        console.error('Error fetching product details:', error);
        alert('Error loading product details');
        closeModal();
      } finally {
        setLoadingProduct(false);
      }
    };

    loadProduct();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, product?.slug, defaultStatus]);

  if (!isOpen) return null;

  const handleInputChange = (event) => {
    const { name, value, type, checked } = event.target;
    setDuplicateWarning(null);
    setFormData((current) => ({
      ...current,
      [name]: type === 'checkbox' ? checked : value,
    }));
  };

  const handleImagesChange = (event) => {
    if (event.target.files && event.target.files.length > 0) {
      const filesArray = Array.from(event.target.files).map((file) => ({
        file,
        preview: URL.createObjectURL(file),
        isPrimary: newImages.length === 0 && productImages.length === 0,
      }));
      setNewImages((current) => [...current, ...filesArray]);
    }
  };

  const handleRemoveNewImage = (index) => {
    setNewImages((current) => {
      const updated = current.filter((_, imageIndex) => imageIndex !== index);
      if (current[index]?.isPrimary && updated.length > 0) {
        updated[0].isPrimary = true;
      }
      return updated;
    });
  };

  const handleDeleteExistingImage = (imageId) => {
    setImagesToDelete((current) => [...current, imageId]);
    setProductImages((current) => current.filter((image) => image.id !== imageId));
  };

  const handleSetPrimary = async (imageId) => {
    if (!editingProduct) return;
    try {
      await axiosInstance.patch(`/product-images/${imageId}/`, { is_primary: true });
      setProductImages((current) => current.map((image) => ({
        ...image,
        is_primary: image.id === imageId,
      })));
    } catch (error) {
      console.error('Error setting primary image:', error);
    }
  };

  const handleSetNewImagePrimary = (index) => {
    setNewImages((current) => current.map((image, imageIndex) => ({
      ...image,
      isPrimary: imageIndex === index,
    })));
    setProductImages((current) => current.map((image) => ({
      ...image,
      is_primary: false,
    })));
  };

  const handleCreateBrand = async () => {
    const trimmedName = newBrandName.trim();
    if (!trimmedName) {
      setBrandError('Brand name is required');
      return;
    }
    const exists = brands.some((brand) => brand.name.toLowerCase() === trimmedName.toLowerCase());
    if (exists) {
      setBrandError('This brand already exists');
      return;
    }
    setIsCreatingBrand(true);
    setBrandError('');
    try {
      const response = await axiosInstance.post('/brands/', { name: trimmedName });
      await fetchBrands();
      setFormData((current) => ({ ...current, brand: response.data.id }));
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
    const exists = categories.some((category) => category.name.toLowerCase() === trimmedName.toLowerCase());
    if (exists) {
      setCategoryError('This category already exists');
      return;
    }
    setIsCreatingCategory(true);
    setCategoryError('');
    try {
      const response = await axiosInstance.post('/categories/', { name: trimmedName, is_active: true });
      await fetchCategories();
      setFormData((current) => ({ ...current, category: response.data.id }));
      setNewCategoryName('');
    } catch (error) {
      setCategoryError(error.response?.data?.name?.[0] || 'Failed to create category');
    } finally {
      setIsCreatingCategory(false);
    }
  };

  const handleProductSubmit = async (event, confirmCreate = false) => {
    if (event) event.preventDefault();
    if (saving) return;
    setSaving(true);

    const data = new FormData();
    const fieldsToSend = [
      'name', 'short_description', 'detailed_description',
      'price', 'stock_quantity', 'category', 'brand',
      'dosage', 'pack_size', 'active_ingredient',
      'requires_prescription', 'status', 'is_featured', 'show_price',
    ];

    fieldsToSend.forEach((key) => {
      const value = formData[key];
      if (value !== null && value !== '' && value !== undefined) {
        data.append(key, typeof value === 'boolean' ? value.toString() : value);
      }
    });
    if (confirmCreate) data.append('confirm_create', 'true');

    const primaryNewImage = newImages.find((image) => image.isPrimary);
    if (primaryNewImage) {
      data.append('image', primaryNewImage.file);
    } else if (newImages.length > 0) {
      data.append('image', newImages[0].file);
    }

    try {
      let productId;
      let savedProduct;

      if (editingProduct) {
        const response = await axiosInstance.put(`/products/${editingProduct.slug}/`, data, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
        savedProduct = response.data;
        productId = response.data.id;

        for (const imageId of imagesToDelete) {
          try {
            await axiosInstance.delete(`/product-images/${imageId}/`);
          } catch (error) {
            console.error('Error deleting image:', error);
          }
        }
      } else {
        const response = await axiosInstance.post('/products/', data, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
        savedProduct = response.data;
        productId = response.data.id;
      }

      const additionalImages = primaryNewImage
        ? newImages.filter((image) => !image.isPrimary)
        : newImages.slice(1);

      for (const image of additionalImages) {
        const imageData = new FormData();
        imageData.append('product', productId);
        imageData.append('image', image.file);
        imageData.append('is_primary', 'false');
        await axiosInstance.post('/product-images/', imageData, {
          headers: { 'Content-Type': 'multipart/form-data' },
        });
      }

      if (onSaved) await onSaved(savedProduct);
      closeModal();
    } catch (error) {
      const warning = error.response?.data;
      if (!editingProduct && error.response?.status === 409 && warning?.requires_confirmation) {
        setDuplicateWarning(warning);
        return;
      }
      console.error('Error saving product:', error);
      alert(`Error saving product: ${error.response?.data ? JSON.stringify(error.response.data) : error.message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="pm-modal-overlay" onClick={closeModal}>
      <div className="pm-modal pm-modal-xl" onClick={(event) => event.stopPropagation()}>
        <div className="pm-modal-header">
          <div>
            <h2>{editingProduct ? editTitle : createTitle}</h2>
            {contextHelpText && <p className="pm-help-text">{contextHelpText}</p>}
          </div>
          <button className="pm-modal-close" onClick={closeModal}>
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
        </div>

        {loadingProduct ? (
          <div className="pm-modal-body">
            <div className="loading-products">
              <div className="loading-spinner-admin"></div>
              <p>Loading product...</p>
            </div>
          </div>
        ) : (
          <form onSubmit={handleProductSubmit} className="pm-modal-body">
            <div className="pm-form-grid">
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
                        {categories.map((category) => (
                          <option key={category.id} value={category.id}>{category.name}</option>
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

                  <div className="pm-quick-create">
                    <div className="pm-quick-create-row">
                      <input
                        type="text"
                        value={newCategoryName}
                        onChange={(event) => { setNewCategoryName(event.target.value); setCategoryError(''); }}
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
                        onChange={(event) => { setNewBrandName(event.target.value); setBrandError(''); }}
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
                    {productImages.map((image) => (
                      <div key={image.id} className={`pm-image-item ${image.is_primary ? 'primary' : ''}`}>
                        <img src={image.image_url} alt="" />
                        <div className="pm-image-overlay">
                          {!image.is_primary && (
                            <button type="button" onClick={() => handleSetPrimary(image.id)} className="pm-img-btn" title="Set as primary">Set</button>
                          )}
                          {image.is_primary && <span className="pm-primary-tag">Primary</span>}
                          <button type="button" onClick={() => handleDeleteExistingImage(image.id)} className="pm-img-btn delete" title="Delete">x</button>
                        </div>
                      </div>
                    ))}
                    {newImages.map((image, index) => (
                      <div key={`new-${index}`} className={`pm-image-item new ${image.isPrimary ? 'primary' : ''}`}>
                        <img src={image.preview} alt="" />
                        <div className="pm-image-overlay">
                          {!image.isPrimary && (
                            <button type="button" onClick={() => handleSetNewImagePrimary(index)} className="pm-img-btn" title="Set as primary">Set</button>
                          )}
                          {image.isPrimary && <span className="pm-primary-tag">Primary</span>}
                          <button type="button" onClick={() => handleRemoveNewImage(index)} className="pm-img-btn delete" title="Remove">x</button>
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
                  <p className="pm-help-text">Use one clear primary image. Recommended: 800 x 800px</p>
                </div>
              </div>
            </div>

            {duplicateWarning && (
              <div className={`pm-duplicate-warning ${duplicateWarning.creation_blocked ? 'blocked' : ''}`} role="alert">
                <h3>{duplicateWarning.creation_blocked ? 'Existing identifier conflict' : 'Check likely existing Products'}</h3>
                <p>{duplicateWarning.warning || duplicateWarning.detail || duplicateWarning.match_reason}</p>
                {(duplicateWarning.candidates || []).length > 0 && (
                  <div className="pm-duplicate-candidates">
                    {(duplicateWarning.candidates || []).map((candidate) => (
                      <button
                        type="button"
                        key={candidate.product_id}
                        onClick={async () => {
                          if (onSaved) await onSaved({
                            id: candidate.product_id,
                            name: candidate.product_name,
                            status: candidate.status,
                            sku: candidate.sku,
                            barcode: candidate.barcode,
                            dosage: candidate.dosage,
                            pack_size: candidate.pack_size,
                          });
                          closeModal();
                        }}
                      >
                        <strong>Use existing: {candidate.product_name}</strong>
                        <small>
                          {Math.round(Number(candidate.confidence || candidate.score || 0) * 100)}% match
                          {candidate.dosage ? ` · ${candidate.dosage}` : ''}
                          {candidate.pack_size ? ` · ${candidate.pack_size}` : ''}
                        </small>
                      </button>
                    ))}
                  </div>
                )}
                {duplicateWarning.creation_blocked && <small>Correct the conflicting SKU/barcode, or use the existing Product.</small>}
              </div>
            )}

            <div className="pm-modal-footer">
              <button type="button" className="pm-btn-secondary" onClick={closeModal}>
                Cancel
              </button>
              {duplicateWarning && !duplicateWarning.creation_blocked && (
                <button type="button" className="pm-btn-secondary pm-btn-warning" disabled={saving} onClick={() => handleProductSubmit(null, true)}>
                  {saving ? 'Creating...' : 'Create new Product anyway'}
                </button>
              )}
              <button type="submit" className="pm-btn-primary" disabled={saving}>
                {saving ? 'Saving...' : (editingProduct ? 'Update Product' : duplicateWarning ? 'Recheck Product' : 'Create Product')}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
};

export default ProductFormModal;
