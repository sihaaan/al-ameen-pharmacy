// frontend/src/components/Navbar.js
import React, { useState, useEffect, useRef } from "react";
import { Link, useNavigate, useLocation } from "react-router-dom";
import { useCart } from "../context/CartContext";
import { useAuth } from "../context/AuthContext";
import Cart from "./Cart";
import axiosInstance from "../utils/axios";
import "../styles/Navbar.css";

const Navbar = () => {
  const { totalItems, totalPrice } = useCart();
  const { user, logout } = useAuth();
  const [showCart, setShowCart] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [suggestions, setSuggestions] = useState([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(-1);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const searchRef = useRef(null);

  // Only show search bar on home page
  const showSearchBar = location.pathname === '/';

  // Fetch search suggestions with debouncing and request cancellation
  useEffect(() => {
    if (searchQuery.trim().length < 2) {
      setSuggestions([]);
      setShowSuggestions(false);
      return;
    }

    // Create abort controller for request cancellation
    const abortController = new AbortController();

    const timer = setTimeout(async () => {
      try {
        const response = await axiosInstance.get(
          `/products/?search=${encodeURIComponent(searchQuery.trim())}`,
          { signal: abortController.signal }
        );
        setSuggestions(response.data.slice(0, 5)); // Show top 5 results
        setShowSuggestions(true);
      } catch (error) {
        if (error.name !== 'CanceledError') {
          console.error('Error fetching suggestions:', error);
        }
      }
    }, 150); // Reduced from 300ms to 150ms for faster response

    return () => {
      clearTimeout(timer);
      abortController.abort(); // Cancel pending request when user types again
    };
  }, [searchQuery]);

  // Close suggestions when clicking outside
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (searchRef.current && !searchRef.current.contains(event.target)) {
        setShowSuggestions(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleLogout = () => {
    logout();
    navigate('/');
    setMobileMenuOpen(false);
  };

  const closeMobileMenu = () => {
    setMobileMenuOpen(false);
  };

  const handleSearch = (e) => {
    e.preventDefault();
    if (searchQuery.trim()) {
      navigate(`/?search=${encodeURIComponent(searchQuery.trim())}`);
      setShowSuggestions(false);
    }
  };

  const handleSuggestionClick = (product) => {
    setSearchQuery(product.name);
    navigate(`/?search=${encodeURIComponent(product.name)}`);
    setShowSuggestions(false);
  };

  const handleKeyDown = (e) => {
    if (!showSuggestions || suggestions.length === 0) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex(prev =>
        prev < suggestions.length - 1 ? prev + 1 : prev
      );
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex(prev => prev > 0 ? prev - 1 : -1);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (selectedIndex >= 0) {
        handleSuggestionClick(suggestions[selectedIndex]);
      } else {
        handleSearch(e);
      }
    } else if (e.key === 'Escape') {
      setShowSuggestions(false);
      setSelectedIndex(-1);
    }
  };

  return (
    <>
      <nav className="navbar">
        <div className="nav-brand">
          <Link to="/" style={{ textDecoration: 'none', color: 'inherit' }} onClick={closeMobileMenu}>
            <div className="brand-container">
              <div className="brand-arabic">صيدلية الأمين</div>
              <div className="brand-english">AL AMEEN PHARMACY</div>
            </div>
          </Link>
        </div>

        {showSearchBar && (
          <div className="search-container" ref={searchRef}>
            <form className="search-bar" onSubmit={handleSearch}>
              <input
                type="text"
                placeholder="Search for medicines..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={handleKeyDown}
                onFocus={() => suggestions.length > 0 && setShowSuggestions(true)}
                className="search-input"
                autoComplete="off"
              />
              <button type="submit" className="search-button">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="11" cy="11" r="8"></circle>
                  <path d="m21 21-4.35-4.35"></path>
                </svg>
              </button>
            </form>

            {showSuggestions && suggestions.length > 0 && (
              <div className="search-suggestions">
                {suggestions.map((product, index) => (
                  <div
                    key={product.id}
                    className={`suggestion-item ${index === selectedIndex ? 'selected' : ''}`}
                    onClick={() => handleSuggestionClick(product)}
                    onMouseEnter={() => setSelectedIndex(index)}
                  >
                    <div className="suggestion-image">
                      {product.image_display ? (
                        <img src={product.image_display} alt={product.name} />
                      ) : (
                        <div className="no-image">💊</div>
                      )}
                    </div>
                    <div className="suggestion-info">
                      <div className="suggestion-name">{product.name}</div>
                      <div className="suggestion-price">AED {product.price}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Hamburger Menu Button (Mobile Only) */}
        <button
          className="hamburger-menu"
          onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
          aria-label="Toggle menu"
        >
          <span className={mobileMenuOpen ? "hamburger-line open" : "hamburger-line"}></span>
          <span className={mobileMenuOpen ? "hamburger-line open" : "hamburger-line"}></span>
          <span className={mobileMenuOpen ? "hamburger-line open" : "hamburger-line"}></span>
        </button>

        <div className={`nav-links ${mobileMenuOpen ? 'mobile-open' : ''}`}>
          <Link to="/" className="nav-link" onClick={closeMobileMenu}>Home</Link>
          <Link to="/about" className="nav-link" onClick={closeMobileMenu}>About</Link>

          {user ? (
            <>
              <span className="nav-link nav-username">
                Hello, {user.username}!
              </span>

              <Link to="/profile" className="nav-link" onClick={closeMobileMenu}>
                Profile
              </Link>

              {user.is_staff && (
                <Link to="/admin" className="nav-link admin-link" onClick={closeMobileMenu}>
                  Admin
                </Link>
              )}

              <button
                className="cart-button"
                onClick={() => { setShowCart(!showCart); closeMobileMenu(); }}
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
              <Link to="/login" className="nav-link login-link" onClick={closeMobileMenu}>
                Login
              </Link>
              <Link to="/register" className="nav-link register-link" onClick={closeMobileMenu}>
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
