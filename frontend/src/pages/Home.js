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
