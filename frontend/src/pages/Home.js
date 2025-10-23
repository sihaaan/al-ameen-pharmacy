import React, { useState, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { productsAPI } from "../api";
import ProductGrid from "../components/ProductGrid";
import "../styles/Home.css";

function Home() {
  const [searchParams] = useSearchParams();
  const [products, setProducts] = useState([]);
  const [filteredProducts, setFilteredProducts] = useState([]);
  const [categories, setCategories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Filter states
  const [selectedCategory, setSelectedCategory] = useState('all');
  const [sortBy, setSortBy] = useState('newest');
  const [showInStockOnly, setShowInStockOnly] = useState(false);

  useEffect(() => {
    const fetchProducts = async () => {
      try {
        console.log("Fetching products from backend...");
        const response = await productsAPI.getAll();
        console.log("Products received:", response.data);
        setProducts(response.data);

        // Extract unique categories
        const uniqueCategories = [...new Set(response.data.map(p => p.category_name).filter(Boolean))];
        setCategories(uniqueCategories);
      } catch (err) {
        console.error("Error fetching products:", err);
        setError("Failed to load products");
      } finally {
        setLoading(false);
      }
    };

    fetchProducts();
  }, []);

  // Apply filters and search whenever products or filters change
  useEffect(() => {
    let result = [...products];

    // Search filter
    const searchQuery = searchParams.get('search');
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      result = result.filter(product =>
        product.name.toLowerCase().includes(query) ||
        product.description?.toLowerCase().includes(query) ||
        product.category_name?.toLowerCase().includes(query)
      );
    }

    // Category filter
    if (selectedCategory !== 'all') {
      result = result.filter(product => product.category_name === selectedCategory);
    }

    // In stock filter
    if (showInStockOnly) {
      result = result.filter(product => product.stock_quantity > 0);
    }

    // Sorting
    switch (sortBy) {
      case 'price-low':
        result.sort((a, b) => parseFloat(a.price) - parseFloat(b.price));
        break;
      case 'price-high':
        result.sort((a, b) => parseFloat(b.price) - parseFloat(a.price));
        break;
      case 'name':
        result.sort((a, b) => a.name.localeCompare(b.name));
        break;
      case 'newest':
      default:
        result.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
        break;
    }

    setFilteredProducts(result);
  }, [products, searchParams, selectedCategory, sortBy, showInStockOnly]);

  if (loading) {
    return (
      <div className="home-loading">
        <div className="loading-spinner-home"></div>
        <p>Loading products...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="home-error">
        <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="12" r="10"></circle>
          <line x1="12" y1="8" x2="12" y2="12"></line>
          <line x1="12" y1="16" x2="12.01" y2="16"></line>
        </svg>
        <h2>{error}</h2>
        <p>Make sure your backend is running on port 8000!</p>
      </div>
    );
  }

  return (
    <div className="home-container">
      {/* Hero Section */}
      <section className="hero-section">
        <div className="hero-content">
          <div className="hero-badge">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
              <polyline points="22 4 12 14.01 9 11.01"></polyline>
            </svg>
            <span>Trusted Healthcare Provider</span>
          </div>

          <h1 className="hero-title">
            <span className="hero-arabic">صيدلية الأمين</span>
            <span className="hero-english">AL AMEEN PHARMACY</span>
          </h1>

          <p className="hero-description">
            Your Premier Healthcare Partner in Dubai. Quality medications and healthcare products
            delivered with care and professionalism.
          </p>

          <div className="hero-features">
            <div className="feature-item">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
                <polyline points="22 4 12 14.01 9 11.01"></polyline>
              </svg>
              <span>Certified Products</span>
            </div>
            <div className="feature-item">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
              </svg>
              <span>Licensed Pharmacy</span>
            </div>
            <div className="feature-item">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10"></circle>
                <polyline points="12 6 12 12 16 14"></polyline>
              </svg>
              <span>Fast Service</span>
            </div>
          </div>
        </div>

        <div className="hero-decoration">
          <div className="decoration-circle decoration-1"></div>
          <div className="decoration-circle decoration-2"></div>
          <div className="decoration-circle decoration-3"></div>
        </div>
      </section>

      {/* Products Section */}
      <section className="products-section">
        <div className="section-header">
          <h2 className="section-title">
            {searchParams.get('search') ? `Search Results for "${searchParams.get('search')}"` : 'Our Products'}
          </h2>
          <div className="products-count-badge">
            {filteredProducts.length} {filteredProducts.length === 1 ? 'Product' : 'Products'}
          </div>
        </div>

        {/* Filters Bar */}
        <div className="filters-bar">
          <div className="filter-group">
            <label>Category:</label>
            <select
              value={selectedCategory}
              onChange={(e) => setSelectedCategory(e.target.value)}
              className="filter-select"
            >
              <option value="all">All Categories</option>
              {categories.map(cat => (
                <option key={cat} value={cat}>{cat}</option>
              ))}
            </select>
          </div>

          <div className="filter-group">
            <label>Sort By:</label>
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value)}
              className="filter-select"
            >
              <option value="newest">Newest First</option>
              <option value="name">Name (A-Z)</option>
              <option value="price-low">Price (Low to High)</option>
              <option value="price-high">Price (High to Low)</option>
            </select>
          </div>

          <div className="filter-group checkbox-group">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={showInStockOnly}
                onChange={(e) => setShowInStockOnly(e.target.checked)}
              />
              <span>In Stock Only</span>
            </label>
          </div>

          {(selectedCategory !== 'all' || showInStockOnly || searchParams.get('search')) && (
            <button
              className="clear-filters-btn"
              onClick={() => {
                setSelectedCategory('all');
                setSortBy('newest');
                setShowInStockOnly(false);
                window.history.pushState({}, '', '/');
              }}
            >
              Clear Filters
            </button>
          )}
        </div>

        {filteredProducts.length === 0 ? (
          <div className="no-results">
            <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="11" cy="11" r="8"></circle>
              <path d="m21 21-4.35-4.35"></path>
            </svg>
            <h3>No products found</h3>
            <p>Try adjusting your filters or search query</p>
          </div>
        ) : (
          <ProductGrid products={filteredProducts} />
        )}
      </section>
    </div>
  );
}

export default Home;
