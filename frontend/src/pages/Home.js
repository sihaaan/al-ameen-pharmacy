import React, { useState, useEffect } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { productsAPI } from "../api";
import ProductGrid from "../components/ProductGrid";
import "../styles/Home.css";

// SVG Icons
const WhatsAppIcon = ({ size = 24 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
    <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/>
  </svg>
);

const PhoneIcon = ({ size = 24 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>
  </svg>
);

const CheckIcon = ({ size = 20 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="20 6 9 17 4 12"/>
  </svg>
);

const ClockIcon = ({ size = 24 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10"/>
    <polyline points="12 6 12 12 16 14"/>
  </svg>
);

const MapPinIcon = ({ size = 24 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/>
    <circle cx="12" cy="10" r="3"/>
  </svg>
);

const ShieldIcon = ({ size = 24 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
  </svg>
);

const TruckIcon = ({ size = 24 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="1" y="3" width="15" height="13"/>
    <polygon points="16 8 20 8 23 11 23 16 16 16 16 8"/>
    <circle cx="5.5" cy="18.5" r="2.5"/>
    <circle cx="18.5" cy="18.5" r="2.5"/>
  </svg>
);

const BuildingIcon = ({ size = 24 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="4" y="2" width="16" height="20" rx="2" ry="2"/>
    <path d="M9 22v-4h6v4"/>
    <path d="M8 6h.01"/>
    <path d="M16 6h.01"/>
    <path d="M12 6h.01"/>
    <path d="M12 10h.01"/>
    <path d="M12 14h.01"/>
    <path d="M16 10h.01"/>
    <path d="M16 14h.01"/>
    <path d="M8 10h.01"/>
    <path d="M8 14h.01"/>
  </svg>
);

const PillIcon = ({ size = 24 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="m10.5 20.5 10-10a4.95 4.95 0 1 0-7-7l-10 10a4.95 4.95 0 1 0 7 7Z"/>
    <path d="m8.5 8.5 7 7"/>
  </svg>
);

const UsersIcon = ({ size = 24 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>
    <circle cx="9" cy="7" r="4"/>
    <path d="M22 21v-2a4 4 0 0 0-3-3.87"/>
    <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
  </svg>
);

const PRODUCTS_PER_PAGE = 12;

// Category order based on pharmacy conversion psychology (high-demand/urgent first)
const CATEGORY_ORDER = [
  { slug: 'pain-relief', name: 'Pain Relief' },
  { slug: 'cold-flu-allergy', name: 'Cold, Flu & Allergy' },
  { slug: 'vitamins-supplements', name: 'Vitamins & Supplements' },
  { slug: 'digestive-health', name: 'Digestive Health' },
  { slug: 'baby-care', name: 'Baby Care' },
  { slug: 'skincare', name: 'Skincare' },
  { slug: 'first-aid', name: 'First Aid' },
  { slug: 'oral-care', name: 'Oral Care' },
  { slug: 'eye-ear-care', name: 'Eye & Ear Care' },
  { slug: 'personal-care', name: 'Personal Care' },
];

const ChevronDownIcon = ({ size = 16 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <polyline points="6 9 12 15 18 9"/>
  </svg>
);

const XIcon = ({ size = 14 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="18" y1="6" x2="6" y2="18"/>
    <line x1="6" y1="6" x2="18" y2="18"/>
  </svg>
);

function Home() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [products, setProducts] = useState([]);
  const [filteredProducts, setFilteredProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [sortBy, setSortBy] = useState('newest');
  const [displayCount, setDisplayCount] = useState(PRODUCTS_PER_PAGE);
  const [selectedCategory, setSelectedCategory] = useState('');
  const [priceRange, setPriceRange] = useState('all');
  const [showMobileFilters, setShowMobileFilters] = useState(false);

  useEffect(() => {
    const fetchProducts = async () => {
      try {
        const response = await productsAPI.getAll();
        setProducts(response.data);
      } catch (err) {
        console.error("Error fetching products:", err);
        setError("Failed to load products");
      } finally {
        setLoading(false);
      }
    };
    fetchProducts();
  }, []);

  // Apply filters
  useEffect(() => {
    let result = [...products];

    // Search filter
    const searchQuery = searchParams.get('search');
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      result = result.filter(product =>
        product.name.toLowerCase().includes(query) ||
        product.short_description?.toLowerCase().includes(query) ||
        product.category_name?.toLowerCase().includes(query) ||
        product.brand_name?.toLowerCase().includes(query)
      );
    }

    // Category filter
    if (selectedCategory) {
      result = result.filter(product =>
        product.category_slug === selectedCategory ||
        product.category_name?.toLowerCase().replace(/[^a-z0-9]+/g, '-') === selectedCategory
      );
    }

    // Price range filter
    if (priceRange !== 'all') {
      const getPrice = (p) => parseFloat(p.price);
      switch (priceRange) {
        case 'under-25':
          result = result.filter(p => getPrice(p) < 25);
          break;
        case '25-50':
          result = result.filter(p => getPrice(p) >= 25 && getPrice(p) <= 50);
          break;
        case '50-100':
          result = result.filter(p => getPrice(p) >= 50 && getPrice(p) <= 100);
          break;
        case 'over-100':
          result = result.filter(p => getPrice(p) > 100);
          break;
        default:
          break;
      }
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
      default:
        result.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
    }

    setFilteredProducts(result);
    setDisplayCount(PRODUCTS_PER_PAGE);
  }, [products, searchParams, sortBy, selectedCategory, priceRange]);

  const loadMore = () => {
    setDisplayCount(prev => prev + PRODUCTS_PER_PAGE);
  };

  const clearFilters = () => {
    setSelectedCategory('');
    setPriceRange('all');
    setSearchParams({});
  };

  const displayedProducts = filteredProducts.slice(0, displayCount);
  const hasMore = displayCount < filteredProducts.length;

  // Count products per category
  const categoryCounts = products.reduce((acc, product) => {
    const slug = product.category_slug || 'other';
    acc[slug] = (acc[slug] || 0) + 1;
    return acc;
  }, {});

  // Check if any filters are active
  const hasActiveFilters = selectedCategory || priceRange !== 'all' || searchParams.get('search');
  const activeFilterCount = [selectedCategory, priceRange !== 'all' ? priceRange : null, searchParams.get('search')].filter(Boolean).length;

  if (loading) {
    return (
      <div className="home-loading">
        <div className="loading-spinner-home"></div>
        <p>Loading...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="home-error">
        <h2>{error}</h2>
        <p>Please try refreshing the page</p>
      </div>
    );
  }

  return (
    <div className="home-container">
      {/* ===== HERO SECTION ===== */}
      <section className="hero-section">
        <div className="hero-background"></div>
        <div className="hero-content">
          <div className="hero-inner">
            <div className="hero-badge">
              <ClockIcon size={16} />
              <span>Open Late Until 2AM</span>
            </div>

            <div className="hero-brand">
              <span className="hero-brand-arabic">صيدلية الأمين</span>
              <span className="hero-brand-english">Al Ameen Pharmacy</span>
            </div>

            <h1 className="hero-headline">
              Your Trusted<br />
              <span className="hero-accent">Pharmacy</span> in Dubai
            </h1>

            <p className="hero-subtext">
              DHA licensed pharmacy with 4 branches across Dubai.
              Quality medicines, competitive prices, and late-night service when you need it most.
            </p>

            <div className="hero-ctas">
              <a
                href="https://wa.me/971505456388"
                className="hero-cta-primary"
                target="_blank"
                rel="noopener noreferrer"
              >
                <WhatsAppIcon size={22} />
                <span>Order on WhatsApp</span>
              </a>

              <a href="tel:+97142713695" className="hero-cta-secondary">
                <PhoneIcon size={18} />
                <span>Call Now</span>
              </a>
            </div>
          </div>
        </div>
      </section>

      {/* ===== TRUST SIGNALS BANNER ===== */}
      <section className="trust-banner">
        <div className="trust-banner-inner">
          <div className="trust-item">
            <ShieldIcon size={22} />
            <span>DHA Licensed</span>
          </div>
          <div className="trust-item">
            <BuildingIcon size={22} />
            <span>4 Branches</span>
          </div>
          <div className="trust-item">
            <ClockIcon size={22} />
            <span>Open Until 2AM</span>
          </div>
          <div className="trust-item">
            <TruckIcon size={22} />
            <span>Fast Delivery UAE</span>
          </div>
        </div>
      </section>

      {/* ===== BRANCH LOCATIONS ===== */}
      <section className="branches-section">
        <div className="branches-inner">
          <h2 className="section-title">Our Locations</h2>
          <p className="section-subtitle">Find your nearest Al Ameen Pharmacy branch</p>

          <div className="branches-grid">
            <div className="branch-card">
              <div className="branch-icon">
                <MapPinIcon size={24} />
              </div>
              <h3>Frij Murar</h3>
              <p className="branch-address">Frij Murar, Deira, Dubai</p>
              <p className="branch-hours">
                <ClockIcon size={14} />
                <span>Sat-Thu: 9AM - 2AM</span>
              </p>
              <a href="tel:+97142713695" className="branch-phone">
                <PhoneIcon size={14} />
                +971-4-271-3695
              </a>
            </div>

            <div className="branch-card">
              <div className="branch-icon">
                <MapPinIcon size={24} />
              </div>
              <h3>Al Muteena</h3>
              <p className="branch-address">Al Muteena Street, Deira, Dubai</p>
              <p className="branch-hours">
                <ClockIcon size={14} />
                <span>Sat-Thu: 9AM - 2AM</span>
              </p>
              <a href="tel:+97142713695" className="branch-phone">
                <PhoneIcon size={14} />
                +971-4-271-3695
              </a>
            </div>

            <div className="branch-card">
              <div className="branch-icon">
                <MapPinIcon size={24} />
              </div>
              <h3>Naif Road</h3>
              <p className="branch-address">Naif Road, Deira, Dubai</p>
              <p className="branch-hours">
                <ClockIcon size={14} />
                <span>Sat-Thu: 9AM - 2AM</span>
              </p>
              <a href="tel:+97142713695" className="branch-phone">
                <PhoneIcon size={14} />
                +971-4-271-3695
              </a>
            </div>

            <div className="branch-card">
              <div className="branch-icon">
                <MapPinIcon size={24} />
              </div>
              <h3>Abu Hail</h3>
              <p className="branch-address">Abu Hail, Deira, Dubai</p>
              <p className="branch-hours">
                <ClockIcon size={14} />
                <span>Sat-Thu: 9AM - 2AM</span>
              </p>
              <a href="tel:+97142713695" className="branch-phone">
                <PhoneIcon size={14} />
                +971-4-271-3695
              </a>
            </div>
          </div>
        </div>
      </section>

      {/* ===== PRODUCTS SECTION WITH SIDEBAR ===== */}
      <section className="shop-section" id="products">
        <div className="shop-container">
          {/* Filter Sidebar */}
          <aside className={`filter-sidebar ${showMobileFilters ? 'show' : ''}`}>
            <div className="filter-sidebar-header">
              <h3>Filters</h3>
              {hasActiveFilters && (
                <button className="clear-all-filters" onClick={clearFilters}>
                  Clear All
                </button>
              )}
              <button
                className="close-filters-mobile"
                onClick={() => setShowMobileFilters(false)}
              >
                <XIcon size={20} />
              </button>
            </div>

            {/* Category Filter */}
            <div className="filter-group">
              <h4 className="filter-group-title">
                <ChevronDownIcon size={16} />
                Categories
              </h4>
              <div className="filter-options">
                <label className={`filter-option ${!selectedCategory ? 'active' : ''}`}>
                  <input
                    type="radio"
                    name="category"
                    checked={!selectedCategory}
                    onChange={() => setSelectedCategory('')}
                  />
                  <span className="filter-label">All Categories</span>
                  <span className="filter-count">{products.length}</span>
                </label>
                {CATEGORY_ORDER.map((cat) => (
                  <label
                    key={cat.slug}
                    className={`filter-option ${selectedCategory === cat.slug ? 'active' : ''}`}
                  >
                    <input
                      type="radio"
                      name="category"
                      checked={selectedCategory === cat.slug}
                      onChange={() => setSelectedCategory(cat.slug)}
                    />
                    <span className="filter-label">{cat.name}</span>
                    <span className="filter-count">{categoryCounts[cat.slug] || 0}</span>
                  </label>
                ))}
              </div>
            </div>

            {/* Price Filter */}
            <div className="filter-group">
              <h4 className="filter-group-title">
                <ChevronDownIcon size={16} />
                Price Range
              </h4>
              <div className="filter-options">
                {[
                  { value: 'all', label: 'All Prices' },
                  { value: 'under-25', label: 'Under AED 25' },
                  { value: '25-50', label: 'AED 25 - 50' },
                  { value: '50-100', label: 'AED 50 - 100' },
                  { value: 'over-100', label: 'Over AED 100' },
                ].map((option) => (
                  <label
                    key={option.value}
                    className={`filter-option ${priceRange === option.value ? 'active' : ''}`}
                  >
                    <input
                      type="radio"
                      name="price"
                      checked={priceRange === option.value}
                      onChange={() => setPriceRange(option.value)}
                    />
                    <span className="filter-label">{option.label}</span>
                  </label>
                ))}
              </div>
            </div>
          </aside>

          {/* Main Products Area */}
          <div className="products-main">
            {/* Products Header */}
            <div className="products-header">
              <div className="products-header-left">
                <button
                  className="mobile-filter-btn"
                  onClick={() => setShowMobileFilters(true)}
                >
                  Filters
                  {activeFilterCount > 0 && (
                    <span className="filter-badge">{activeFilterCount}</span>
                  )}
                </button>
                <h2 className="products-title">
                  {searchParams.get('search')
                    ? `Results for "${searchParams.get('search')}"`
                    : selectedCategory
                      ? CATEGORY_ORDER.find(c => c.slug === selectedCategory)?.name || 'Products'
                      : 'All Products'
                  }
                </h2>
                <span className="products-count">{filteredProducts.length} products</span>
              </div>
              <div className="products-header-right">
                <select
                  value={sortBy}
                  onChange={(e) => setSortBy(e.target.value)}
                  className="sort-select"
                >
                  <option value="newest">Newest</option>
                  <option value="name">Name A-Z</option>
                  <option value="price-low">Price: Low to High</option>
                  <option value="price-high">Price: High to Low</option>
                </select>
              </div>
            </div>

            {/* Active Filters */}
            {hasActiveFilters && (
              <div className="active-filters">
                {searchParams.get('search') && (
                  <span className="active-filter-tag">
                    Search: {searchParams.get('search')}
                    <button onClick={() => setSearchParams({})}>
                      <XIcon size={12} />
                    </button>
                  </span>
                )}
                {selectedCategory && (
                  <span className="active-filter-tag">
                    {CATEGORY_ORDER.find(c => c.slug === selectedCategory)?.name}
                    <button onClick={() => setSelectedCategory('')}>
                      <XIcon size={12} />
                    </button>
                  </span>
                )}
                {priceRange !== 'all' && (
                  <span className="active-filter-tag">
                    {priceRange.replace('-', ' - ').replace('under', 'Under').replace('over', 'Over')} AED
                    <button onClick={() => setPriceRange('all')}>
                      <XIcon size={12} />
                    </button>
                  </span>
                )}
                <button className="clear-filters-link" onClick={clearFilters}>
                  Clear all filters
                </button>
              </div>
            )}

            {/* Products Grid */}
            {filteredProducts.length === 0 ? (
              <div className="no-results">
                <div className="no-results-icon">🔍</div>
                <h3>No products found</h3>
                <p>Try adjusting your filters or search terms</p>
                <button className="clear-filters-btn" onClick={clearFilters}>
                  Clear all filters
                </button>
              </div>
            ) : (
              <>
                <ProductGrid products={displayedProducts} />
                {hasMore && (
                  <div className="load-more-container">
                    <button className="load-more-btn" onClick={loadMore}>
                      Load More Products
                      <span className="load-more-count">
                        (Showing {displayCount} of {filteredProducts.length})
                      </span>
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </section>

      {/* ===== WHY CHOOSE US / STATS ===== */}
      <section className="stats-section">
        <div className="stats-inner">
          <h2 className="section-title">Why Choose Al Ameen?</h2>
          <p className="section-subtitle">Trusted by thousands of customers across Dubai</p>

          <div className="stats-grid">
            <div className="stat-card">
              <div className="stat-icon">
                <PillIcon size={32} />
              </div>
              <div className="stat-number">500+</div>
              <div className="stat-label">Medicines in Stock</div>
            </div>

            <div className="stat-card">
              <div className="stat-icon">
                <BuildingIcon size={32} />
              </div>
              <div className="stat-number">4</div>
              <div className="stat-label">Branches in Dubai</div>
            </div>

            <div className="stat-card">
              <div className="stat-icon">
                <UsersIcon size={32} />
              </div>
              <div className="stat-number">10K+</div>
              <div className="stat-label">Happy Customers</div>
            </div>

            <div className="stat-card">
              <div className="stat-icon">
                <TruckIcon size={32} />
              </div>
              <div className="stat-number">Same Day</div>
              <div className="stat-label">Delivery Available</div>
            </div>
          </div>
        </div>
      </section>

      {/* ===== WHOLESALE SECTION ===== */}
      <section className="wholesale-section">
        <div className="wholesale-inner">
          <span className="wholesale-label">For Businesses</span>

          <h2 className="wholesale-headline">
            Wholesale & Bulk Supply
          </h2>

          <p className="wholesale-text">
            Get wholesale pricing instantly via WhatsApp for clinics,
            pharmacies, and hospitals. Competitive rates, reliable stock,
            fast delivery across UAE.
          </p>

          <ul className="wholesale-list">
            <li><CheckIcon size={18} /> Competitive bulk pricing</li>
            <li><CheckIcon size={18} /> Reliable stock availability</li>
            <li><CheckIcon size={18} /> Fast delivery across UAE</li>
          </ul>

          <a
            href="https://wa.me/971505456388?text=Hi,%20I'm%20interested%20in%20wholesale%20pricing."
            className="wholesale-cta"
            target="_blank"
            rel="noopener noreferrer"
          >
            <WhatsAppIcon size={22} />
            <span>Get Wholesale Pricing</span>
          </a>
          <p className="wholesale-response">Quick response on WhatsApp for bulk pricing</p>
        </div>
      </section>

      {/* ===== FOOTER ===== */}
      <footer className="site-footer">
        <div className="footer-inner">
          {/* Brand & About */}
          <div className="footer-section footer-brand-section">
            <div className="footer-brand">
              <span className="footer-arabic">صيدلية الأمين</span>
              <span className="footer-name">Al Ameen Pharmacy</span>
            </div>
            <p className="footer-about">
              DHA licensed pharmacy serving Dubai with quality medicines,
              competitive prices, and late-night service across 4 branches.
            </p>
            <div className="footer-contact-main">
              <a href="https://wa.me/971505456388" className="footer-whatsapp" target="_blank" rel="noopener noreferrer">
                <WhatsAppIcon size={20} />
                <span>+971-50-545-6388</span>
              </a>
              <a href="tel:+97142713695" className="footer-phone">
                <PhoneIcon size={18} />
                <span>+971-4-271-3695</span>
              </a>
            </div>
          </div>

          {/* Quick Links */}
          <div className="footer-section">
            <h4>Quick Links</h4>
            <ul className="footer-links">
              <li><Link to="/">Home</Link></li>
              <li><Link to="/about">About Us</Link></li>
              <li><a href="#products">Popular Medicines</a></li>
              <li><a href="https://wa.me/971505456388?text=Hi,%20I'm%20interested%20in%20wholesale%20pricing." target="_blank" rel="noopener noreferrer">Wholesale</a></li>
            </ul>
          </div>

          {/* Branch Locations */}
          <div className="footer-section">
            <h4>Our Branches</h4>
            <ul className="footer-branches">
              <li>
                <MapPinIcon size={14} />
                <span>Frij Murar, Deira</span>
              </li>
              <li>
                <MapPinIcon size={14} />
                <span>Al Muteena, Deira</span>
              </li>
              <li>
                <MapPinIcon size={14} />
                <span>Naif Road, Deira</span>
              </li>
              <li>
                <MapPinIcon size={14} />
                <span>Abu Hail, Deira</span>
              </li>
            </ul>
          </div>

          {/* Hours */}
          <div className="footer-section">
            <h4>Opening Hours</h4>
            <div className="footer-hours-info">
              <p><strong>Saturday - Thursday</strong></p>
              <p className="hours-time">9:00 AM - 2:00 AM</p>
              <p className="hours-note">Open late for your convenience!</p>
            </div>
          </div>
        </div>

        <div className="footer-bottom">
          <p className="footer-copy">
            © {new Date().getFullYear()} Al Ameen Pharmacy LLC. All rights reserved.
          </p>
          <p className="footer-dha">
            <ShieldIcon size={14} />
            <span>Licensed by Dubai Health Authority</span>
          </p>
        </div>
      </footer>
    </div>
  );
}

export default Home;
