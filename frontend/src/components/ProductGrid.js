// frontend/src/components/ProductGrid.js
import React, { useState } from "react";
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

  // Get current quantity in cart for a product
  const getCartQuantity = (productId) => {
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
    const cartItemId = getCartItemId(product.id);

    // Check if we can increment
    if (currentQty >= product.stock_quantity) return;

    // Fire and forget - CartContext handles optimistic updates
    if (currentQty === 0) {
      // Not in cart yet, add it
      addToCart(product, 1);
    } else {
      // Already in cart, increment
      updateQuantity(cartItemId, currentQty + 1);
    }
  };

  const decrementQuantity = (e, product) => {
    e.stopPropagation();

    if (!user) {
      return;
    }

    const currentQty = getCartQuantity(product.id);
    const cartItemId = getCartItemId(product.id);

    if (currentQty <= 0) return;

    // Fire and forget - CartContext handles optimistic updates
    updateQuantity(cartItemId, currentQty - 1);
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
