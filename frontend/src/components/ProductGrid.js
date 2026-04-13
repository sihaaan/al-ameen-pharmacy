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
                  <span className="product-price">AED {parseFloat(product.price).toFixed(2)}</span>
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
