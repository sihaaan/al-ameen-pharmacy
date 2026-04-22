// frontend/src/components/ProductModal.js
import React, { useState, useEffect } from "react";
import { useCart } from "../context/CartContext";
import { useAuth } from "../context/AuthContext";
import { useNavigate } from "react-router-dom";
import axiosInstance from "../utils/axios";
import "../styles/ProductModal.css";

const ProductModal = ({ productId, onClose, onViewFullPage }) => {
  const { addToCart } = useCart();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [product, setProduct] = useState(null);
  const [loading, setLoading] = useState(true);
  const [quantity, setQuantity] = useState(1);
  const [addingToCart, setAddingToCart] = useState(false);
  const [addedSuccess, setAddedSuccess] = useState(false);
  const [selectedImageIndex, setSelectedImageIndex] = useState(0);

  useEffect(() => {
    const fetchProduct = async () => {
      try {
        setLoading(true);
        const response = await axiosInstance.get(`/products/${productId}/`);
        setProduct(response.data);
        setSelectedImageIndex(0);
      } catch (error) {
        console.error("Error fetching product:", error);
      } finally {
        setLoading(false);
      }
    };

    if (productId) {
      fetchProduct();
    }
  }, [productId]);

  // Get all images (from images array or fall back to primary_image_url)
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

  const nextImage = (e) => {
    e.stopPropagation();
    if (images.length > 1) {
      setSelectedImageIndex((prev) => (prev + 1) % images.length);
    }
  };

  const prevImage = (e) => {
    e.stopPropagation();
    if (images.length > 1) {
      setSelectedImageIndex((prev) => (prev - 1 + images.length) % images.length);
    }
  };

  useEffect(() => {
    // Prevent body scroll when modal is open
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "unset";
    };
  }, []);

  const handleAddToCart = async () => {
    if (!user) {
      alert("Please login to add items to cart");
      navigate("/login");
      onClose();
      return;
    }

    setAddingToCart(true);
    const result = await addToCart(product, quantity);
    setAddingToCart(false);

    if (result.success) {
      setAddedSuccess(true);
      setTimeout(() => {
        setAddedSuccess(false);
      }, 2000);
    } else {
      alert(result.error || "Failed to add to cart");
    }
  };

  const incrementQuantity = () => {
    if (quantity < product.stock_quantity) {
      setQuantity(quantity + 1);
    }
  };

  const decrementQuantity = () => {
    if (quantity > 1) {
      setQuantity(quantity - 1);
    }
  };

  const handleBackdropClick = (e) => {
    if (e.target.className === "modal-backdrop") {
      onClose();
    }
  };

  if (!productId) return null;

  return (
    <div className="modal-backdrop" onClick={handleBackdropClick}>
      <div className="modal-container">
        <button className="modal-close" onClick={onClose}>
          <svg
            width="24"
            height="24"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>

        {loading ? (
          <div className="modal-loading">
            <div className="modal-spinner"></div>
            <p>Loading product details...</p>
          </div>
        ) : product ? (
          <>
            {/* View Full Details Link - Top Right */}
            {/* <button
              className="view-full-details-link-top"
              onClick={() => {
                onViewFullPage(productId);
                onClose();
              }}
            >
              View Full Product Details
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="M5 12h14M12 5l7 7-7 7"></path>
              </svg>
            </button> */}

            <div className="product-modal-content">
              {/* Left Side - Images */}
              <div className="modal-image-section">
                <div className="main-image-container">
                  {selectedImage ? (
                    <>
                      <img
                        src={selectedImage}
                        alt={product.name}
                        className="main-product-image"
                      />
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
                          {/* Image Counter */}
                          <div className="image-counter">
                            {selectedImageIndex + 1} / {images.length}
                          </div>
                        </>
                      )}
                    </>
                  ) : (
                    <div className="no-image-modal">
                      <svg
                        width="80"
                        height="80"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1"
                      >
                        <rect
                          x="3"
                          y="3"
                          width="18"
                          height="18"
                          rx="2"
                          ry="2"
                        ></rect>
                        <circle cx="8.5" cy="8.5" r="1.5"></circle>
                        <polyline points="21 15 16 10 5 21"></polyline>
                      </svg>
                      <p>No image available</p>
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
                {/* View Full Product Details — under the image */}
                <button
                  className="view-full-details-below-image"
                  onClick={() => {
                    onViewFullPage(productId);
                    onClose();
                  }}
                  aria-label="View full product details"
                >
                  View Full Product Details
                  <svg
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <path d="M5 12h14M12 5l7 7-7 7"></path>
                  </svg>
                </button>

                {product.requires_prescription && (
                  <div className="modal-prescription-badge">
                    <svg
                      width="20"
                      height="20"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                    >
                      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
                      <polyline points="22 4 12 14.01 9 11.01"></polyline>
                    </svg>
                    <span>Prescription Required</span>
                  </div>
                )}
              </div>

              {/* Right Side - Product Info */}
              <div className="modal-info-section">
                {/* Product Title & Category */}
                <div className="modal-header">
                  {product.category_name && (
                    <span className="modal-category">
                      {product.category_name}
                    </span>
                  )}
                  <h2 className="modal-title">{product.name}</h2>
                  {product.brand_name && (
                    <p className="modal-manufacturer">
                      by {product.brand_name}
                    </p>
                  )}
                </div>

                {/* Price & Stock */}
                <div className="modal-price-section">
                  <div className="modal-price-row">
                    {product.show_price ? (
                      <span className="modal-price">
                        AED {parseFloat(product.price).toFixed(2)}
                      </span>
                    ) : (
                      <a
                        className="modal-inquire-link"
                        href={`https://wa.me/971505456388?text=${encodeURIComponent(`Hi, I'd like to inquire about the price of: ${product.name}`)}`}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
                          <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/>
                        </svg>
                        Inquire on WhatsApp
                      </a>
                    )}
                    {product.stock_quantity > 0 ? (
                      <span className="modal-stock in-stock">
                        <svg
                          width="16"
                          height="16"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                        >
                          <polyline points="20 6 9 17 4 12"></polyline>
                        </svg>
                        In Stock ({product.stock_quantity} available)
                      </span>
                    ) : (
                      <span className="modal-stock out-of-stock">
                        Out of Stock
                      </span>
                    )}
                  </div>
                </div>

                {/* Product Meta */}
                {(product.dosage || product.pack_size) && (
                  <div className="modal-meta">
                    {product.dosage && (
                      <div className="meta-badge">
                        <svg
                          width="16"
                          height="16"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                        >
                          <circle cx="12" cy="12" r="10"></circle>
                          <line x1="12" y1="8" x2="12" y2="16"></line>
                          <line x1="8" y1="12" x2="16" y2="12"></line>
                        </svg>
                        <span>{product.dosage}</span>
                      </div>
                    )}
                    {product.pack_size && (
                      <div className="meta-badge">
                        <svg
                          width="16"
                          height="16"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                        >
                          <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path>
                        </svg>
                        <span>{product.pack_size}</span>
                      </div>
                    )}
                  </div>
                )}

                {/* Description */}
                {product.short_description && (
                  <div className="modal-description">
                    <p>{product.short_description}</p>
                  </div>
                )}

                {/* Quantity Selector */}
                {product.stock_quantity > 0 && (
                  <div className="modal-quantity-section">
                    <label>Quantity</label>
                    <div className="modal-quantity-controls">
                      <button
                        className="qty-btn-modal"
                        onClick={decrementQuantity}
                        disabled={quantity <= 1}
                      >
                        <svg
                          width="16"
                          height="16"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                        >
                          <line x1="5" y1="12" x2="19" y2="12"></line>
                        </svg>
                      </button>
                      <span className="qty-display-modal">{quantity}</span>
                      <button
                        className="qty-btn-modal"
                        onClick={incrementQuantity}
                        disabled={quantity >= product.stock_quantity}
                      >
                        <svg
                          width="16"
                          height="16"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                        >
                          <line x1="12" y1="5" x2="12" y2="19"></line>
                          <line x1="5" y1="12" x2="19" y2="12"></line>
                        </svg>
                      </button>
                    </div>
                  </div>
                )}

                {/* Add to Cart Button */}
                <div className="modal-actions">
                  <button
                    className={`modal-add-to-cart ${
                      addedSuccess ? "success" : ""
                    }`}
                    onClick={handleAddToCart}
                    disabled={product.stock_quantity === 0 || addingToCart}
                  >
                    {addingToCart ? (
                      <>
                        <div className="button-spinner"></div>
                        Adding...
                      </>
                    ) : addedSuccess ? (
                      <>
                        <svg
                          width="20"
                          height="20"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                        >
                          <polyline points="20 6 9 17 4 12"></polyline>
                        </svg>
                        Added to Cart!
                      </>
                    ) : product.stock_quantity === 0 ? (
                      "Out of Stock"
                    ) : (
                      <>
                        <svg
                          width="20"
                          height="20"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                        >
                          <circle cx="9" cy="21" r="1"></circle>
                          <circle cx="20" cy="21" r="1"></circle>
                          <path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"></path>
                        </svg>
                        Add to Cart
                      </>
                    )}
                  </button>
                </div>
              </div>
            </div>
          </>
        ) : (
          <div className="modal-error">
            <p>Product not found</p>
          </div>
        )}
      </div>
    </div>
  );
};

export default ProductModal;
