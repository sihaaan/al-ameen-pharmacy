// frontend/src/pages/ProductDetail.js
import React, { useState, useEffect } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useCart } from '../context/CartContext';
import { useAuth } from '../context/AuthContext';
import axiosInstance from '../utils/axios';
import '../styles/ProductDetail.css';

const ProductDetail = () => {
  const { id } = useParams();
  const navigate = useNavigate();
  const { addToCart } = useCart();
  const { user } = useAuth();
  const [product, setProduct] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [quantity, setQuantity] = useState(1);
  const [addingToCart, setAddingToCart] = useState(false);
  const [addedSuccess, setAddedSuccess] = useState(false);

  useEffect(() => {
    fetchProduct();
  }, [id]);

  const fetchProduct = async () => {
    try {
      setLoading(true);
      const response = await axiosInstance.get(`/products/${id}/`);
      setProduct(response.data);
      setLoading(false);
    } catch (err) {
      console.error('Error fetching product:', err);
      setError('Product not found');
      setLoading(false);
    }
  };

  const handleAddToCart = async () => {
    if (!user) {
      alert('Please login to add items to cart');
      navigate('/login');
      return;
    }

    setAddingToCart(true);
    const result = await addToCart(product, quantity);
    setAddingToCart(false);

    if (result.success) {
      setAddedSuccess(true);
      setTimeout(() => setAddedSuccess(false), 3000);
    } else {
      alert(result.error || 'Failed to add to cart');
    }
  };

  const handleQuantityChange = (change) => {
    const newQuantity = quantity + change;
    if (newQuantity >= 1 && newQuantity <= product.stock_quantity) {
      setQuantity(newQuantity);
    }
  };

  if (loading) {
    return (
      <div className="product-detail-loading">
        <div className="loading-spinner"></div>
        <p>Loading product...</p>
      </div>
    );
  }

  if (error || !product) {
    return (
      <div className="product-detail-error">
        <h2>Product Not Found</h2>
        <p>{error}</p>
        <Link to="/" className="back-home-btn">Back to Home</Link>
      </div>
    );
  }

  return (
    <div className="product-detail-container">
      {/* Breadcrumbs */}
      <div className="breadcrumbs">
        <Link to="/">Home</Link>
        <span className="breadcrumb-separator">/</span>
        {product.category_name && (
          <>
            <span>{product.category_name}</span>
            <span className="breadcrumb-separator">/</span>
          </>
        )}
        <span className="breadcrumb-current">{product.name}</span>
      </div>

      <div className="product-detail-content">
        {/* Product Image */}
        <div className="product-image-section">
          <div className="product-image-container">
            {product.image ? (
              <img src={product.image} alt={product.name} />
            ) : product.image_url ? (
              <img src={product.image_url} alt={product.name} />
            ) : (
              <div className="no-image-placeholder">
                <span>No Image Available</span>
              </div>
            )}
          </div>
          {product.requires_prescription && (
            <div className="prescription-badge">
              <span>⚕️ Prescription Required</span>
            </div>
          )}
        </div>

        {/* Product Info */}
        <div className="product-info-section">
          <h1 className="product-title">{product.name}</h1>

          {product.manufacturer && (
            <p className="product-manufacturer">by {product.manufacturer}</p>
          )}

          <div className="product-meta">
            {product.category_name && (
              <span className="meta-item">
                <strong>Category:</strong> {product.category_name}
              </span>
            )}
            {product.dosage && (
              <span className="meta-item">
                <strong>Dosage:</strong> {product.dosage}
              </span>
            )}
            {product.pack_size && (
              <span className="meta-item">
                <strong>Pack Size:</strong> {product.pack_size}
              </span>
            )}
          </div>

          <div className="product-price">
            <span className="price-label">Price:</span>
            <span className="price-amount">AED {product.price}</span>
          </div>

          <div className="product-stock">
            {product.in_stock ? (
              <span className="in-stock">✓ In Stock ({product.stock_quantity} available)</span>
            ) : (
              <span className="out-of-stock">✗ Out of Stock</span>
            )}
          </div>

          {/* Quantity Selector */}
          {product.in_stock && (
            <div className="quantity-section">
              <label>Quantity:</label>
              <div className="quantity-controls">
                <button
                  onClick={() => handleQuantityChange(-1)}
                  disabled={quantity <= 1}
                  className="qty-btn"
                >
                  -
                </button>
                <span className="quantity-display">{quantity}</span>
                <button
                  onClick={() => handleQuantityChange(1)}
                  disabled={quantity >= product.stock_quantity}
                  className="qty-btn"
                >
                  +
                </button>
              </div>
            </div>
          )}

          {/* Add to Cart Button */}
          <button
            onClick={handleAddToCart}
            disabled={!product.in_stock || addingToCart}
            className={`add-to-cart-btn-detail ${addedSuccess ? 'success' : ''}`}
          >
            {addingToCart ? (
              'Adding...'
            ) : addedSuccess ? (
              '✓ Added to Cart!'
            ) : !product.in_stock ? (
              'Out of Stock'
            ) : (
              'Add to Cart'
            )}
          </button>

          {/* Short Description */}
          <div className="product-description">
            <h3>Description</h3>
            <p>{product.description}</p>
          </div>
        </div>
      </div>

      {/* Detailed Description */}
      {product.detailed_description && (
        <div className="product-detailed-section">
          <h2>Detailed Information</h2>
          <div className="detailed-content">
            {product.detailed_description.split('\n').map((paragraph, index) => (
              <p key={index}>{paragraph}</p>
            ))}
          </div>
        </div>
      )}

      {/* Back Button */}
      <div className="back-button-section">
        <button onClick={() => navigate(-1)} className="back-btn">
          ← Back to Products
        </button>
      </div>
    </div>
  );
};

export default ProductDetail;
