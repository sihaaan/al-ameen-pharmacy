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
  const [selectedImageIndex, setSelectedImageIndex] = useState(0);

  useEffect(() => {
    fetchProduct();
  }, [id]);

  const fetchProduct = async () => {
    try {
      setLoading(true);
      const response = await axiosInstance.get(`/products/${id}/`);
      setProduct(response.data);
      setSelectedImageIndex(0);
      setLoading(false);
    } catch (err) {
      console.error('Error fetching product:', err);
      setError('Product not found');
      setLoading(false);
    }
  };

  // Get all images
  const getImages = () => {
    if (!product) return [];
    if (product.images && product.images.length > 0) {
      return product.images.map(img => img.image_url);
    }
    if (product.primary_image_url) {
      return [product.primary_image_url];
    }
    return [];
  };

  const images = product ? getImages() : [];
  const selectedImage = images[selectedImageIndex] || null;

  const nextImage = () => {
    if (images.length > 1) {
      setSelectedImageIndex((prev) => (prev + 1) % images.length);
    }
  };

  const prevImage = () => {
    if (images.length > 1) {
      setSelectedImageIndex((prev) => (prev - 1 + images.length) % images.length);
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
            {selectedImage ? (
              <>
                <img src={selectedImage} alt={product.name} />
                {/* Navigation Arrows */}
                {images.length > 1 && (
                  <>
                    <button className="image-nav-btn prev" onClick={prevImage}>
                      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="15 18 9 12 15 6"></polyline>
                      </svg>
                    </button>
                    <button className="image-nav-btn next" onClick={nextImage}>
                      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="9 18 15 12 9 6"></polyline>
                      </svg>
                    </button>
                    <div className="image-counter">
                      {selectedImageIndex + 1} / {images.length}
                    </div>
                  </>
                )}
              </>
            ) : (
              <div className="no-image-placeholder">
                <span>No Image Available</span>
              </div>
            )}
          </div>
          {/* Thumbnail Strip */}
          {images.length > 1 && (
            <div className="image-thumbnails">
              {images.map((img, index) => (
                <button
                  key={index}
                  className={`thumbnail-btn ${index === selectedImageIndex ? 'active' : ''}`}
                  onClick={() => setSelectedImageIndex(index)}
                >
                  <img src={img} alt={`${product.name} ${index + 1}`} />
                </button>
              ))}
            </div>
          )}
          {product.requires_prescription && (
            <div className="prescription-badge">
              <span>⚕️ Prescription Required</span>
            </div>
          )}
        </div>

        {/* Product Info */}
        <div className="product-info-section">
          <h1 className="product-title">{product.name}</h1>

          {product.brand_name && (
            <p className="product-manufacturer">by {product.brand_name}</p>
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

          {product.show_price ? (
            <div className="product-price">
              <span className="price-label">Price:</span>
              <span className="price-amount">AED {parseFloat(product.price).toFixed(2)}</span>
            </div>
          ) : (
            <a
              className="detail-inquire-link"
              href={`https://wa.me/971505456388?text=${encodeURIComponent(`Hi, I'd like to inquire about the price of: ${product.name}`)}`}
              target="_blank"
              rel="noopener noreferrer"
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
                <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/>
              </svg>
              Inquire on WhatsApp for pricing
            </a>
          )}

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
            <p>{product.short_description}</p>
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
