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
  const [selectedImage, setSelectedImage] = useState(null);

  useEffect(() => {
    const fetchProduct = async () => {
      try {
        setLoading(true);
        const response = await axiosInstance.get(`/products/${productId}/`);
        setProduct(response.data);
        setSelectedImage(response.data.image || response.data.image_url);
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
                    <img
                      src={selectedImage}
                      alt={product.name}
                      className="main-product-image"
                    />
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
                  {product.manufacturer && (
                    <p className="modal-manufacturer">
                      by {product.manufacturer}
                    </p>
                  )}
                </div>

                {/* Price & Stock */}
                <div className="modal-price-section">
                  <div className="modal-price-row">
                    <span className="modal-price">
                      AED {parseFloat(product.price).toFixed(2)}
                    </span>
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
                {product.description && (
                  <div className="modal-description">
                    <p>{product.description}</p>
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
