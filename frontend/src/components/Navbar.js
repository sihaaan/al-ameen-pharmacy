// frontend/src/components/Navbar.js
import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useCart } from "../context/CartContext";
import { useAuth } from "../context/AuthContext";
import Cart from "./Cart";
import "../styles/Navbar.css";

const Navbar = () => {
  const { totalItems, totalPrice } = useCart();
  const { user, logout } = useAuth();
  const [showCart, setShowCart] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate('/');
  };

  const handleSearch = (e) => {
    e.preventDefault();
    if (searchQuery.trim()) {
      navigate(`/?search=${encodeURIComponent(searchQuery.trim())}`);
    }
  };

  return (
    <>
      <nav className="navbar">
        <div className="nav-brand">
          <Link to="/" style={{ textDecoration: 'none', color: 'inherit' }}>
            <div className="brand-container">
              <div className="brand-arabic">صيدلية الأمين</div>
              <div className="brand-english">AL AMEEN PHARMACY</div>
            </div>
          </Link>
        </div>

        <form className="search-bar" onSubmit={handleSearch}>
          <input
            type="text"
            placeholder="Search for medicines..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="search-input"
          />
          <button type="submit" className="search-button">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="11" cy="11" r="8"></circle>
              <path d="m21 21-4.35-4.35"></path>
            </svg>
          </button>
        </form>

        <div className="nav-links">
          <Link to="/" className="nav-link">Home</Link>
          <Link to="/about" className="nav-link">About</Link>

          {user ? (
            <>
              <span className="nav-link nav-username">
                Hello, {user.username}!
              </span>

              {user.is_staff && (
                <Link to="/admin" className="nav-link admin-link">
                  Admin
                </Link>
              )}

              <button
                className="cart-button"
                onClick={() => setShowCart(!showCart)}
              >
                🛒 Cart ({totalItems}) - AED {totalPrice.toFixed(2)}
              </button>

              <button
                className="logout-button"
                onClick={handleLogout}
              >
                Logout
              </button>
            </>
          ) : (
            <>
              <Link to="/login" className="nav-link login-link">
                Login
              </Link>
              <Link to="/register" className="nav-link register-link">
                Register
              </Link>
            </>
          )}
        </div>
      </nav>

      {showCart && <Cart onClose={() => setShowCart(false)} />}
    </>
  );
};

export default Navbar;
