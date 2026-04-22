// frontend/src/components/ProductGrid.js
import React, { useState, useRef } from "react";
import { useCart } from "../context/CartContext";
import { useAuth } from "../context/AuthContext";
import { useNavigate } from "react-router-dom";
import ProductModal from "./ProductModal";
import "../styles/ProductGrid.css";

const ProductGrid = ({ products, limit, showViewAll, onViewAll, viewAllText = "View All" }) => {
  // Apply limit if specified
  const displayProducts = limit ? products.slice(0, limit) : products;
  const { addToCart, updateQuantity, items } = useCart();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [selectedProductId, setSelectedProductId] = useState(null);
  const [localQuantities, setLocalQuantities] = useState({});
  const debounceTimers = useRef({});
  const pendingQuantities = useRef({}); // Track target quantities (not affected by closure)
  const pendingCartItemIds = useRef({}); // Store cart item IDs at click time

  // Get current quantity - use local state if available, otherwise cart
  const getCartQuantity = (productId) => {
    if (localQuantities[productId] !== undefined) {
      return localQuantities[productId];
    }
    const cartItem = items.find(item => item.product.id === productId);
    return cartItem ? cartItem.quantity : 0;
  };

  // Get cart item ID for a product
  const getCartItemId = (productId) => {
    const cartItem = items.find(item => item.product.id === productId);
    return cartItem?.id;
  };

  const incrementQuantity = (e, product) => {
    e.stopPropagation();

    if (!user) {
      alert('Please login to add items to cart');
      navigate('/login');
      return;
    }

    const currentQty = getCartQuantity(product.id);

    // Check stock limit
    if (currentQty >= product.stock_quantity) return;

    const newQty = currentQty + 1;

    // Update UI immediately with local state
    setLocalQuantities(prev => ({
      ...prev,
      [product.id]: newQty
    }));

    // Store target quantity in ref (survives closure)
    pendingQuantities.current[product.id] = newQty;

    // Store cart item ID NOW (before CartContext optimistic updates remove it)
    if (!pendingCartItemIds.current[product.id]) {
      pendingCartItemIds.current[product.id] = getCartItemId(product.id);
    }

    // Clear existing debounce timer
    if (debounceTimers.current[product.id]) {
      clearTimeout(debounceTimers.current[product.id]);
    }

    // Debounce the API call - batch rapid clicks
    debounceTimers.current[product.id] = setTimeout(async () => {
      // Read from ref, not from state closure
      const finalQty = pendingQuantities.current[product.id];
      const cartItemId = pendingCartItemIds.current[product.id];

      if (!cartItemId) {
        // Not in cart yet, add it
        await addToCart(product, finalQty);
      } else {
        // Already in cart, update it
        await updateQuantity(cartItemId, finalQty);
      }

      // Clear local state after API call completes
      setLocalQuantities(prev => {
        const next = { ...prev };
        delete next[product.id];
        return next;
      });
      delete pendingQuantities.current[product.id];
      delete pendingCartItemIds.current[product.id];
      delete debounceTimers.current[product.id];
    }, 500);
  };

  const decrementQuantity = (e, product) => {
    e.stopPropagation();

    if (!user) {
      return;
    }

    const currentQty = getCartQuantity(product.id);

    if (currentQty <= 0) return;

    const newQty = currentQty - 1;

    // Update UI immediately with local state
    setLocalQuantities(prev => ({
      ...prev,
      [product.id]: newQty
    }));

    // Store target quantity in ref
    pendingQuantities.current[product.id] = newQty;

    // Store cart item ID NOW (before CartContext optimistic updates remove it)
    if (!pendingCartItemIds.current[product.id]) {
      pendingCartItemIds.current[product.id] = getCartItemId(product.id);
    }

    // Clear existing debounce timer
    if (debounceTimers.current[product.id]) {
      clearTimeout(debounceTimers.current[product.id]);
    }

    // Debounce the API call
    debounceTimers.current[product.id] = setTimeout(async () => {
      // Read from ref, not from state closure
      const finalQty = pendingQuantities.current[product.id];
      const cartItemId = pendingCartItemIds.current[product.id];

      if (cartItemId) {
        await updateQuantity(cartItemId, finalQty);
      }

      // Clear local state after API call completes
      setLocalQuantities(prev => {
        const next = { ...prev };
        delete next[product.id];
        return next;
      });
      delete pendingQuantities.current[product.id];
      delete pendingCartItemIds.current[product.id];
      delete debounceTimers.current[product.id];
    }, 500);
  };

  const handleCardClick = (e, product) => {
    // Check if click was on the button
    if (e.target.closest('button')) {
      return;
    }
    // Open quick view modal - pass slug for API, id for cart
    setSelectedProductId(product.slug || product.id);
  };

  const handleViewFullPage = (slugOrId) => {
    navigate(`/product/${slugOrId}`);
  };

  return (
    <>
      <div className="products-grid">
        {displayProducts.map((product) => {
          const qty = getCartQuantity(product.id);
          const isInCart = qty > 0;

          return (
            <div
              key={product.id}
              className="product-card"
              onClick={(e) => handleCardClick(e, product)}
            >
              <div className="product-image">
                {product.primary_image_url ? (
                  <img src={product.primary_image_url} alt={product.name} />
                ) : (
                  <div className="no-image">No Image</div>
                )}
              </div>
              <div className="product-card-content">
                <h3>{product.name}</h3>
                <div className="card-bottom-row">
                  {product.show_price ? (
                    <span className="product-price">AED {parseFloat(product.price).toFixed(2)}</span>
                  ) : (
                    <a
                      className="product-inquire-link"
                      href="https://wa.me/971505456388?text=Hi%2C%20I%27d%20like%20to%20inquire%20about%20the%20price%20of%3A%20"
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={(e) => {
                        e.stopPropagation();
                        const msg = encodeURIComponent(`Hi, I'd like to inquire about the price of: ${product.name}`);
                        e.currentTarget.href = `https://wa.me/971505456388?text=${msg}`;
                      }}
                    >
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" style={{flexShrink:0}}>
                        <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/>
                      </svg>
                      Inquire
                    </a>
                  )}
                  <div className="card-cart-controls">
                    {isInCart ? (
                      <div className="card-quantity-controls">
                        <button
                          className="card-qty-btn"
                          onClick={(e) => decrementQuantity(e, product)}
                        >
                          −
                        </button>
                        <span className="card-qty-display">{qty}</span>
                        <button
                          className="card-qty-btn"
                          onClick={(e) => incrementQuantity(e, product)}
                          disabled={qty >= product.stock_quantity}
                        >
                          +
                        </button>
                      </div>
                    ) : (
                      <button
                        className="card-add-btn"
                        onClick={(e) => incrementQuantity(e, product)}
                        disabled={product.stock_quantity <= 0}
                      >
                        Add
                      </button>
                    )}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {showViewAll && products.length > (limit || 0) && (
        <div className="view-all-container">
          <button className="view-all-btn" onClick={onViewAll}>
            {viewAllText}
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="9 18 15 12 9 6"/>
            </svg>
          </button>
        </div>
      )}

      {selectedProductId && (
        <ProductModal
          productId={selectedProductId}
          onClose={() => setSelectedProductId(null)}
          onViewFullPage={handleViewFullPage}
        />
      )}
    </>
  );
};

export default ProductGrid;
