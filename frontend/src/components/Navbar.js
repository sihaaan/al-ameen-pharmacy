// frontend/src/components/Navbar.js
import React, { useState } from "react";
import { useCart } from "../context/CartContext";
import Cart from "./Cart";
import "../styles/Navbar.css";

const Navbar = () => {
  const { totalItems, totalPrice } = useCart();
  const [showCart, setShowCart] = useState(false);

  return (
    <>
      <nav className="navbar">
        <div className="nav-brand">
          <h2>Al Ameen Pharmacy</h2>
        </div>

        <div className="nav-links">
          <span className="nav-link">Home</span>
          <span className="nav-link">Products</span>
          <span className="nav-link">About</span>

          <button
            className="cart-button"
            onClick={() => setShowCart(!showCart)}
          >
            🛒 Cart ({totalItems}) - AED {totalPrice.toFixed(2)}
          </button>
        </div>
      </nav>

      {showCart && <Cart onClose={() => setShowCart(false)} />}
    </>
  );
};

export default Navbar;
