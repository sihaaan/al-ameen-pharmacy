import React, { useState, useEffect } from "react";
import { useSearchParams } from "react-router-dom";
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

const LocationIcon = ({ size = 20 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/>
    <circle cx="12" cy="10" r="3"/>
  </svg>
);

// Category icons mapping
const categoryIcons = {
  'Pain Relief': '💊',
  'Vitamins': '💪',
  'Baby Care': '👶',
  'Medical Supplies': '🏥',
  'Skin Care': '🧴',
  'First Aid': '🩹',
  'Supplements': '💪',
  'Personal Care': '🧴',
};

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

  useEffect(() => {
    const fetchProducts = async () => {
      try {
        const response = await productsAPI.getAll();
        setProducts(response.data);
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

  // Apply filters
  useEffect(() => {
    let result = [...products];

    const searchQuery = searchParams.get('search');
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      result = result.filter(product =>
        product.name.toLowerCase().includes(query) ||
        product.description?.toLowerCase().includes(query) ||
        product.category_name?.toLowerCase().includes(query)
      );
    }

    if (selectedCategory !== 'all') {
      result = result.filter(product => product.category_name === selectedCategory);
    }

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
  }, [products, searchParams, selectedCategory, sortBy]);

  const scrollToProducts = () => {
    document.getElementById('products')?.scrollIntoView({ behavior: 'smooth' });
  };

  const handleCategoryClick = (categoryName) => {
    setSelectedCategory(categoryName);
    scrollToProducts();
  };

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
        <p>Please try refreshing the page</p>
      </div>
    );
  }

  return (
    <div className="home-container">
      {/* ===== HERO SECTION ===== */}
      <section className="hero-section-premium">
        <div className="hero-grid">
          {/* Left Content */}
          <div className="hero-content-left">
            <span className="hero-arabic-badge">صيدلية الأمين</span>

            <h1 className="hero-headline-premium">
              YOUR TRUSTED<br />
              <span className="hero-headline-accent">PHARMACY</span> IN DUBAI
            </h1>

            <p className="hero-subtext">
              Licensed pharmacy with 4 branches across Dubai. Quality medications
              available 7 days a week, open until 2AM.
            </p>

            {/* Primary CTA - WhatsApp */}
            <a
              href="https://wa.me/971505456388"
              className="hero-whatsapp-btn"
              target="_blank"
              rel="noopener noreferrer"
            >
              <WhatsAppIcon size={28} />
              <span>WhatsApp Us Now</span>
            </a>

            {/* Secondary Contact */}
            <div className="hero-contact-row">
              <a href="tel:+97142713695" className="hero-contact-link">
                <PhoneIcon size={18} />
                <span>+971-4-271-3695</span>
              </a>
              <span className="hero-contact-divider">•</span>
              <span className="hero-contact-text">
                <LocationIcon size={18} />
                Deira, Dubai
              </span>
            </div>
          </div>

          {/* Right Visual */}
          <div className="hero-visual">
            <div className="hero-visual-blob"></div>
            <div className="hero-trust-badges">
              <div className="hero-badge-item">Licensed</div>
              <div className="hero-badge-item">4 Branches</div>
              <div className="hero-badge-item">Open Late</div>
            </div>
          </div>
        </div>
      </section>

      {/* ===== TRUST SECTION ===== */}
      <section className="trust-section-premium">
        <div className="trust-grid-premium">
          <div className="trust-headline-area">
            <h2 className="trust-headline-large">
              WHY UAE TRUSTS<br />
              <span className="trust-headline-accent">AL AMEEN</span>
            </h2>
          </div>

          <div className="trust-points-area">
            <div className="trust-point">
              <div className="trust-point-icon">
                <CheckIcon size={24} />
              </div>
              <div className="trust-point-content">
                <h3>Licensed by Dubai Health Authority</h3>
                <p>Fully registered and regulated pharmacy serving Dubai since establishment</p>
              </div>
            </div>

            <div className="trust-point">
              <div className="trust-point-icon">
                <CheckIcon size={24} />
              </div>
              <div className="trust-point-content">
                <h3>4 Convenient Locations Across Dubai</h3>
                <p>Multiple branches in Deira and surrounding areas for easy pickup</p>
              </div>
            </div>

            <div className="trust-point">
              <div className="trust-point-icon">
                <CheckIcon size={24} />
              </div>
              <div className="trust-point-content">
                <h3>Fast Availability of Medicines</h3>
                <p>Thousands of products in stock from trusted international brands</p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ===== WHOLESALE SECTION ===== */}
      <section className="wholesale-section-premium">
        <div className="wholesale-grid">
          <div className="wholesale-content">
            <span className="wholesale-badge">For Clinics, Hospitals & Pharmacies</span>

            <h2 className="wholesale-headline">
              WHOLESALE &<br />
              <span className="wholesale-headline-accent">BULK SUPPLY</span>
            </h2>

            <p className="wholesale-description">
              Partner with Al Ameen Pharmacy for reliable pharmaceutical supply.
              We serve clinics, hospitals, and pharmacies across the UAE with
              competitive pricing and consistent stock availability.
            </p>

            <ul className="wholesale-benefits">
              <li>
                <CheckIcon size={20} />
                <span>Competitive bulk pricing</span>
              </li>
              <li>
                <CheckIcon size={20} />
                <span>Reliable stock levels</span>
              </li>
              <li>
                <CheckIcon size={20} />
                <span>Fast delivery across UAE</span>
              </li>
              <li>
                <CheckIcon size={20} />
                <span>Dedicated account support</span>
              </li>
            </ul>

            <a
              href="https://wa.me/971505456388?text=Hi,%20I'm%20interested%20in%20wholesale%20pricing%20for%20my%20business."
              className="wholesale-cta"
              target="_blank"
              rel="noopener noreferrer"
            >
              <WhatsAppIcon size={24} />
              <span>WhatsApp for Wholesale Inquiry</span>
            </a>

            <p className="wholesale-phone">
              Or call: <a href="tel:+97142713695">+971-4-271-3695</a>
            </p>
          </div>

          <div className="wholesale-visual">
            <div className="wholesale-icon-container">
              <span className="wholesale-icon">📦</span>
              <span className="wholesale-icon-sub">🏥</span>
            </div>
          </div>
        </div>
      </section>

      {/* ===== CATEGORY NAVIGATION ===== */}
      <section className="categories-section-minimal">
        <div className="categories-container">
          <h2 className="categories-title">Shop by Category</h2>

          <div className="category-pills-row">
            <button
              className={`category-pill-premium ${selectedCategory === 'all' ? 'active' : ''}`}
              onClick={() => handleCategoryClick('all')}
            >
              All Products
            </button>
            {categories.slice(0, 6).map(cat => (
              <button
                key={cat}
                className={`category-pill-premium ${selectedCategory === cat ? 'active' : ''}`}
                onClick={() => handleCategoryClick(cat)}
              >
                <span className="category-pill-icon">{categoryIcons[cat] || '💊'}</span>
                <span>{cat}</span>
              </button>
            ))}
          </div>
        </div>
      </section>

      {/* ===== PRODUCTS SECTION ===== */}
      <section className="products-section-premium" id="products">
        <div className="products-header-premium">
          <div className="products-header-left">
            <h2 className="products-title-premium">
              {searchParams.get('search')
                ? `Results for "${searchParams.get('search')}"`
                : 'Our Products'}
            </h2>
            <span className="products-count-premium">
              {filteredProducts.length} {filteredProducts.length === 1 ? 'product' : 'products'}
            </span>
          </div>

          <div className="products-filters-minimal">
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value)}
              className="sort-select-premium"
            >
              <option value="newest">Newest</option>
              <option value="name">Name A-Z</option>
              <option value="price-low">Price: Low to High</option>
              <option value="price-high">Price: High to Low</option>
            </select>
          </div>
        </div>

        {filteredProducts.length === 0 ? (
          <div className="no-results-premium">
            <span className="no-results-icon">🔍</span>
            <h3>No products found</h3>
            <p>Try adjusting your filters or search query</p>
            <button
              className="reset-filters-btn"
              onClick={() => {
                setSelectedCategory('all');
                setSortBy('newest');
                window.history.pushState({}, '', '/');
              }}
            >
              Reset Filters
            </button>
          </div>
        ) : (
          <ProductGrid products={filteredProducts} />
        )}
      </section>

      {/* ===== STICKY CONTACT BAR ===== */}
      <div className="sticky-contact-bar">
        <div className="sticky-contact-content">
          <span className="sticky-contact-text">Need help finding something?</span>
          <div className="sticky-contact-buttons">
            <a
              href="https://wa.me/971505456388"
              className="sticky-whatsapp-btn"
              target="_blank"
              rel="noopener noreferrer"
            >
              <WhatsAppIcon size={18} />
              <span>WhatsApp</span>
            </a>
            <a href="tel:+97142713695" className="sticky-call-btn">
              <PhoneIcon size={18} />
              <span>+971-4-271-3695</span>
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}

export default Home;
