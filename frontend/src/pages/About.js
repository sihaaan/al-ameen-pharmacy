// frontend/src/pages/About.js
import React from 'react';
import { Link } from 'react-router-dom';
import './About.css';

const About = () => {
  return (
    <div className="about-container">
      {/* Hero Section */}
      <section className="hero-section">
        <div className="hero-content">
          <div className="logo-container">
            <div className="company-branding">
              <h1 className="company-name-arabic">صيدلية الأمين</h1>
              <h1 className="company-name-english">AL AMEEN PHARMACY LLC</h1>
              <p className="company-tagline">Pharmaceutical & Medical Equipment Trading</p>
            </div>
          </div>
          <p className="hero-description">
            Your trusted partner in healthcare, serving Dubai with quality pharmaceuticals
            and medical equipment since our establishment
          </p>
        </div>
      </section>

      {/* About Section */}
      <section className="about-section content-section">
        <div className="section-header">
          <h2 className="section-title">About Al Ameen Pharmacy</h2>
          <p className="section-subtitle">
            Committed to excellence in pharmaceutical care and medical equipment trading
          </p>
        </div>
        <div className="about-content">
          <div className="about-text">
            <p>
              <strong>Al Ameen Pharmacy LLC</strong> is a leading pharmaceutical and medical equipment
              trading company based in the heart of <strong>Deira, Dubai</strong>. We are dedicated to
              providing our community with access to high-quality medications, healthcare products,
              and professional pharmaceutical services.
            </p>
            <p>
              Located in one of Dubai's most vibrant and accessible districts, we serve a diverse
              community with a comprehensive range of prescription medications, over-the-counter
              drugs, vitamins, supplements, and medical equipment. Our strategic location in
              Frij Murar ensures we're easily reachable for all your healthcare needs.
            </p>
            <p>
              At Al Ameen Pharmacy, your health and well-being are our top priorities. Our team
              of experienced pharmacists and healthcare professionals is committed to providing
              expert advice, personalized service, and ensuring the highest standards of
              pharmaceutical care for every customer.
            </p>
          </div>
          <div className="about-image">
            <div className="about-image-icon">⚕️</div>
            <h3>Quality Healthcare</h3>
            <p>Professional pharmaceutical services you can trust</p>
          </div>
        </div>
      </section>

      {/* Services Section */}
      <section className="services-section content-section">
        <div className="section-header">
          <h2 className="section-title">Our Services</h2>
          <p className="section-subtitle">
            Comprehensive pharmaceutical solutions for all your healthcare needs
          </p>
        </div>
        <div className="services-grid">
          <div className="service-card">
            <span className="service-icon">💊</span>
            <h3>Prescription Medications</h3>
            <p>Wide selection of prescription drugs with expert pharmacist consultation and guidance</p>
          </div>
          <div className="service-card">
            <span className="service-icon">🏥</span>
            <h3>Medical Equipment</h3>
            <p>Quality medical devices and equipment for both home and clinical use</p>
          </div>
          <div className="service-card">
            <span className="service-icon">💪</span>
            <h3>Vitamins & Supplements</h3>
            <p>Essential vitamins and dietary supplements for optimal health and wellness</p>
          </div>
          <div className="service-card">
            <span className="service-icon">🩺</span>
            <h3>Healthcare Consultation</h3>
            <p>Professional advice from experienced healthcare professionals</p>
          </div>
        </div>
      </section>

      {/* Contact Section */}
      <section className="contact-section content-section">
        <div className="section-header">
          <h2 className="section-title">Get In Touch</h2>
          <p className="section-subtitle">
            We're here to help with all your pharmaceutical needs
          </p>
        </div>
        <div className="contact-grid">
          <div className="contact-card">
            <span className="contact-icon">📧</span>
            <h3>Email Us</h3>
            <p>For inquiries and orders</p>
            <a href="mailto:pharmacydxb@gmail.com" className="contact-link">
              pharmacydxb@gmail.com
            </a>
          </div>
          <div className="contact-card">
            <span className="contact-icon">📞</span>
            <h3>Call Us</h3>
            <p>Speak with our team</p>
            <a href="tel:+97142713695" className="contact-link">
              +971-4-2713695
            </a>
          </div>
          <div className="contact-card">
            <span className="contact-icon">⏰</span>
            <h3>Working Hours</h3>
            <p>Saturday - Thursday</p>
            <p style={{ color: '#3b82f6', fontWeight: 600, marginTop: '10px' }}>9:00 AM - 10:00 PM</p>
          </div>
        </div>
      </section>

      {/* Location Section */}
      <section className="location-section content-section">
        <div className="section-header">
          <h2 className="section-title">Visit Our Store</h2>
          <p className="section-subtitle">
            Conveniently located in the heart of Deira, Dubai
          </p>
        </div>
        <div className="location-info">
          <div className="location-icon">📍</div>
          <h3>Al Ameen Pharmacy LLC</h3>
          <p className="location-address">
            P.O. Box 39547<br />
            Frij Murar, Somali Street<br />
            Deira, Dubai<br />
            United Arab Emirates
          </p>
          <p className="location-details">
            Easily accessible in one of Dubai's most central locations
          </p>
        </div>
      </section>

      {/* CTA Section */}
      <section className="cta-section">
        <div className="cta-content">
          <h2>Ready to Shop?</h2>
          <p>Browse our extensive selection of pharmaceutical products and medical equipment</p>
          <Link to="/" className="cta-button">
            Explore Our Products
          </Link>
        </div>
      </section>
    </div>
  );
};

export default About;
