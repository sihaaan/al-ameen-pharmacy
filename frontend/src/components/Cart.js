// frontend/src/components/Cart.js
import React from "react";
import { useCart } from "../context/CartContext";
import "../styles/Cart.css";

const Cart = ({ onClose }) => {
  const {
    items,
    totalItems,
    totalPrice,
    updateQuantity,
    removeFromCart,
    clearCart,
  } = useCart();

  const handleQuantityChange = (productId, newQuantity) => {
    if (newQuantity < 0) return;
    updateQuantity(productId, newQuantity);
  };

  const handleRemoveItem = (productId) => {
    removeFromCart(productId);
  };

  return (
    <div className="cart-overlay" onClick={onClose}>
      <div className="cart-modal" onClick={(e) => e.stopPropagation()}>
        <div className="cart-header">
          <h2>Shopping Cart ({totalItems} items)</h2>
          <button className="close-btn" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="cart-content">
          {items.length === 0 ? (
            <p className="empty-cart">Your cart is empty</p>
          ) : (
            <>
              <div className="cart-items">
                {items.map((item) => (
                  <div key={item.id} className="cart-item">
                    <div className="item-info">
                      <h4>{item.name}</h4>
                      <p className="item-price">AED {item.price} each</p>
                    </div>

                    <div className="quantity-controls">
                      <button
                        onClick={() =>
                          handleQuantityChange(item.id, item.quantity - 1)
                        }
                        className="qty-btn"
                      >
                        -
                      </button>
                      <span className="quantity">{item.quantity}</span>
                      <button
                        onClick={() =>
                          handleQuantityChange(item.id, item.quantity + 1)
                        }
                        className="qty-btn"
                      >
                        +
                      </button>
                    </div>

                    <div className="item-total">
                      <p>AED {(item.price * item.quantity).toFixed(2)}</p>
                      <button
                        onClick={() => handleRemoveItem(item.id)}
                        className="remove-btn"
                      >
                        Remove
                      </button>
                    </div>
                  </div>
                ))}
              </div>

              <div className="cart-footer">
                <div className="cart-total">
                  <h3>Total: AED {totalPrice.toFixed(2)}</h3>
                </div>
                <div className="cart-actions">
                  <button onClick={clearCart} className="clear-cart-btn">
                    Clear Cart
                  </button>
                  <button className="checkout-btn">Proceed to Checkout</button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

export default Cart;
