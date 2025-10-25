// frontend/src/components/ProductGrid.js
import React, { useState, useRef } from "react";
import { useCart } from "../context/CartContext";
import { useAuth } from "../context/AuthContext";
import { useNavigate } from "react-router-dom";
import ProductModal from "./ProductModal";
import "../styles/ProductGrid.css";

const ProductGrid = ({ products }) => {
  const { addToCart, updateQuantity, items } = useCart();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [selectedProductId, setSelectedProductId] = useState(null);
  const [localQuantities, setLocalQuantities] = useState({});
  const debounceTimers = useRef({});
  const pendingQuantities = useRef({}); // Track target quantities (not affected by closure)

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

    // Clear existing debounce timer
    if (debounceTimers.current[product.id]) {
      clearTimeout(debounceTimers.current[product.id]);
    }

    // Debounce the API call - batch rapid clicks
    debounceTimers.current[product.id] = setTimeout(async () => {
      // Read from ref, not from state closure
      const finalQty = pendingQuantities.current[product.id];
      const cartItemId = getCartItemId(product.id);

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

    // Clear existing debounce timer
    if (debounceTimers.current[product.id]) {
      clearTimeout(debounceTimers.current[product.id]);
    }

    // Debounce the API call
    debounceTimers.current[product.id] = setTimeout(async () => {
      // Read from ref, not from state closure
      const finalQty = pendingQuantities.current[product.id];
      const cartItemId = getCartItemId(product.id);

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
      delete debounceTimers.current[product.id];
    }, 500);
  };

  const handleCardClick = (e, productId) => {
    // Check if click was on the button
    if (e.target.closest('button')) {
      return;
    }
    // Open quick view modal
    setSelectedProductId(productId);
  };

  const handleViewFullPage = (productId) => {
    navigate(`/product/${productId}`);
  };

  return (
    <>
      <div className="products-grid">
        {products.map((product) => (
          <div
            key={product.id}
            className="product-card"
            onClick={(e) => handleCardClick(e, product.id)}
          >
            <div className="product-image">
              {product.image ? (
                <img src={product.image} alt={product.name} />
              ) : product.image_url ? (
                <img src={product.image_url} alt={product.name} />
              ) : (
                <div className="no-image">No Image</div>
              )}
            </div>
            <div className="product-card-content">
              <h3>{product.name}</h3>
              <p className="product-description">{product.description}</p>
              {product.category_name && (
                <p className="product-category">Category: {product.category_name}</p>
              )}

              <div className="card-cart-controls">
                <div className="price-and-quantity">
                  <span className="product-price">AED {parseFloat(product.price).toFixed(2)}</span>
                  <div className="card-quantity-controls">
                    <button
                      className="card-qty-btn"
                      onClick={(e) => decrementQuantity(e, product)}
                      disabled={getCartQuantity(product.id) <= 0}
                    >
                      −
                    </button>
                    <span className="card-qty-display">{getCartQuantity(product.id)}</span>
                    <button
                      className="card-qty-btn"
                      onClick={(e) => incrementQuantity(e, product)}
                      disabled={getCartQuantity(product.id) >= product.stock_quantity}
                    >
                      +
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>

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
