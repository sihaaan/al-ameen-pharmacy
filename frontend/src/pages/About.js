// frontend/src/pages/About.js
import React from 'react';
import './About.css';

const About = () => {
  return (
    <div className="about-container">
      {/* Hero Section with Logo */}
      <section className="hero-section">
        <div className="hero-content">
          <div className="logo-container">
            <img
              src="/images/al-ameen-logo.png"
              alt="Al Ameen Pharmacy LLC"
              className="company-logo"
              onError={(e) => {
                // Fallback if logo image not found
                e.target.style.display = 'none';
                e.target.nextSibling.style.display = 'block';
              }}
            />
            <div className="logo-fallback" style={{ display: 'none' }}>
              <div className="medical-symbol">⚕️</div>
              <h1 className="company-name-fallback">AL AMEEN PHARMACY LLC</h1>
              <p className="tagline-fallback">Pharmaceutical & Medical Equipment Trading</p>
            </div>
          </div>
          <h1 className="hero-title">Welcome to Al Ameen Pharmacy</h1>
          <p className="hero-subtitle">Your Trusted Partner in Healthcare Since Establishment</p>
          <p className="hero-description">
            Pharmaceutical & Medical Equipment Trading
          </p>
        </div>
      </section>

      {/* About Section */}
      <section className="about-section">
        <div className="section-content">
          <h2 className="section-title">About Us</h2>
          <div className="about-text">
            <p>
              Al Ameen Pharmacy LLC is a leading pharmaceutical and medical equipment
              trading company based in the heart of Dubai, United Arab Emirates. We are
              committed to providing high-quality medications, healthcare products, and
              medical equipment to our valued customers.
            </p>
            <p>
              Located in the vibrant Deira district, we serve the community with dedication,
              ensuring accessibility to essential medicines and healthcare solutions. Our
              extensive range includes prescription medications, over-the-counter drugs,
              vitamins, supplements, and medical supplies.
            </p>
            <p>
              At Al Ameen Pharmacy, we prioritize your health and well-being. Our team of
              experienced pharmacists and healthcare professionals is always ready to assist
              you with expert advice and personalized service.
            </p>
          </div>
        </div>
      </section>

      {/* Services Section */}
      <section className="services-section">
        <div className="section-content">
          <h2 className="section-title">Our Services</h2>
          <div className="services-grid">
            <div className="service-card">
              <div className="service-icon">💊</div>
              <h3>Prescription Medications</h3>
              <p>Wide range of prescription drugs with expert pharmacist consultation</p>
            </div>
            <div className="service-card">
              <div className="service-icon">🏥</div>
              <h3>Medical Equipment</h3>
              <p>Quality medical devices and equipment for home and clinical use</p>
            </div>
            <div className="service-card">
              <div className="service-icon">💪</div>
              <h3>Vitamins & Supplements</h3>
              <p>Essential vitamins and dietary supplements for optimal health</p>
            </div>
            <div className="service-card">
              <div className="service-icon">🩺</div>
              <h3>Healthcare Consultation</h3>
              <p>Professional advice from experienced healthcare professionals</p>
            </div>
          </div>
        </div>
      </section>

      {/* Contact Section */}
      <section className="contact-section">
        <div className="section-content">
          <h2 className="section-title">Contact Us</h2>
          <div className="contact-grid">
            <div className="contact-card">
              <div className="contact-icon">📍</div>
              <h3>Address</h3>
              <p>P.O. Box: 39547</p>
              <p>Frij Murar, Somali Street</p>
              <p>Deira, Dubai</p>
              <p>United Arab Emirates</p>
            </div>
            <div className="contact-card">
              <div className="contact-icon">📧</div>
              <h3>Email</h3>
              <a href="mailto:pharmacydxb@gmail.com" className="contact-link">
                pharmacydxb@gmail.com
              </a>
            </div>
            <div className="contact-card">
              <div className="contact-icon">📞</div>
              <h3>Phone</h3>
              <a href="tel:+97142713695" className="contact-link">
                +971-4-2713695
              </a>
            </div>
          </div>
        </div>
      </section>

      {/* Map Section (Placeholder) */}
      <section className="map-section">
        <div className="section-content">
          <h2 className="section-title">Visit Us</h2>
          <div className="map-container">
            <div className="map-placeholder">
              <p>📍 Frij Murar, Somali Street, Deira, Dubai, UAE</p>
              <p className="map-note">We are conveniently located in Deira, one of Dubai's most accessible areas</p>
            </div>
          </div>
        </div>
      </section>

      {/* Footer CTA */}
      <section className="cta-section">
        <div className="section-content">
          <h2>Ready to Shop?</h2>
          <p>Browse our wide selection of pharmaceutical products and medical equipment</p>
          <a href="/" className="cta-button">Explore Our Products</a>
        </div>
      </section>
    </div>
  );
};

export default About;
