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

// Pill/Capsule icon for hero visual
const PillIcon = ({ size = 48 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M10.5 20.5L3.5 13.5C1.5 11.5 1.5 8.5 3.5 6.5C5.5 4.5 8.5 4.5 10.5 6.5L17.5 13.5C19.5 15.5 19.5 18.5 17.5 20.5C15.5 22.5 12.5 22.5 10.5 20.5Z"/>
    <path d="M7 10L14 17"/>
  </svg>
);

const HeartPulseIcon = ({ size = 48 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M19 14C20.49 12.54 22 10.79 22 8.5C22 7.04 21.47 5.64 20.5 4.64C19.53 3.65 18.21 3.1 16.8 3.1C15.03 3.1 13.64 3.99 12.75 5C12.38 5.43 12.13 5.8 12 6C11.87 5.8 11.62 5.43 11.25 5C10.36 3.99 8.97 3.1 7.2 3.1C5.79 3.1 4.47 3.65 3.5 4.64C2.53 5.64 2 7.04 2 8.5C2 10.79 3.51 12.54 5 14"/>
    <path d="M3 15H7L9 12L12 18L15 15H21"/>
  </svg>
);

const ShieldIcon = ({ size = 48 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 22S20 18 20 12V5L12 2L4 5V12C4 18 12 22 12 22Z"/>
    <path d="M9 12L11 14L15 10"/>
  </svg>
);

function Home() {
  const [searchParams] = useSearchParams();
  const [products, setProducts] = useState([]);
  const [filteredProducts, setFilteredProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [sortBy, setSortBy] = useState('newest');

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

    const searchQuery = searchParams.get('search');
    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      result = result.filter(product =>
        product.name.toLowerCase().includes(query) ||
        product.description?.toLowerCase().includes(query) ||
        product.category_name?.toLowerCase().includes(query)
      );
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
  }, [products, searchParams, sortBy]);

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
        <div className="hero-content">
          <div className="hero-inner">
            <span className="hero-arabic">صيدلية الأمين</span>

            <h1 className="hero-headline">
              Fast Access to<br />
              <span className="hero-accent">Medicines</span> Across Dubai
            </h1>

            <p className="hero-subtext">
              Serving Dubai with 4 branches, offering fast access to medicines
              for both retail customers and bulk orders.
            </p>

            {/* Primary CTA - WhatsApp */}
            <a
              href="https://wa.me/971505456388"
              className="hero-cta-primary"
              target="_blank"
              rel="noopener noreferrer"
            >
              <WhatsAppIcon size={24} />
              <span>Order Medicines on WhatsApp</span>
            </a>

            {/* Secondary - Call */}
            <a href="tel:+97142713695" className="hero-cta-secondary">
              <PhoneIcon size={18} />
              <span>+971-4-271-3695</span>
            </a>
          </div>

          {/* Hero Visual - Subtle icons */}
          <div className="hero-visual">
            <div className="hero-icon-group">
              <div className="hero-icon hero-icon-1">
                <PillIcon size={40} />
              </div>
              <div className="hero-icon hero-icon-2">
                <HeartPulseIcon size={36} />
              </div>
              <div className="hero-icon hero-icon-3">
                <ShieldIcon size={32} />
              </div>
            </div>
          </div>
        </div>

        {/* Trust Line */}
        <p className="hero-trust-line">
          4 branches across Dubai • Licensed DHA pharmacy • Open late daily
        </p>
      </section>

      {/* ===== PRODUCTS SECTION ===== */}
      <section className="products-section" id="products">
        <div className="products-header">
          <h2 className="products-title">
            {searchParams.get('search')
              ? `Results for "${searchParams.get('search')}"`
              : 'Popular Medicines'}
          </h2>

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

        {filteredProducts.length === 0 ? (
          <div className="no-results">
            <p>No products found</p>
          </div>
        ) : (
          <ProductGrid products={filteredProducts} />
        )}
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
          <div className="footer-brand">
            <span className="footer-arabic">صيدلية الأمين</span>
            <span className="footer-name">Al Ameen Pharmacy</span>
          </div>

          <div className="footer-contact">
            <p>Frij Murar, 8th Street, Deira, Dubai</p>
            <p>P.O. Box: 39547</p>
            <p>
              <a href="tel:+97142713695">+971-4-271-3695</a>
              {' '}&nbsp;•&nbsp;{' '}
              <a href="https://wa.me/971505456388" target="_blank" rel="noopener noreferrer">
                WhatsApp: +971-50-545-6388
              </a>
            </p>
            <p className="footer-hours">Saturday – Thursday, 9AM – 2AM</p>
          </div>

          <p className="footer-copy">
            © {new Date().getFullYear()} Al Ameen Pharmacy. All rights reserved.
          </p>
        </div>
      </footer>
    </div>
  );
}

export default Home;
