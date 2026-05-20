// frontend/src/pages/About.js
import React from 'react';
import { Link } from 'react-router-dom';
import './About.css';
import '../styles/Home.css';

// SVG Icons (shared with footer)
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

const About = () => {
  return (
    <div className="about-container">
      {/* Hero Section */}
      <section className="hero-section">
        <div className="hero-content">
          <div className="logo-container">
            <div className="about-brand-mark">
              <div className="about-brand-icon">
                <svg className="about-brand-icon-svg" viewBox="0 0 100 120" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                  {/* Layer 1 — snake segment BEHIND staff (lower wrap) */}
                  <path d="M43 58 C54 62, 60 72, 54 82 C49 90, 40 94, 37 102"
                    stroke="white" strokeWidth="5" fill="none" strokeLinecap="round" strokeLinejoin="round"/>
                  {/* Layer 2 — staff rectangle, overlaps lower snake segment */}
                  <rect x="38" y="10" width="10" height="96" rx="5" fill="white"/>
                  {/* Layer 3 — snake segment IN FRONT of staff (upper wrap + head connection) */}
                  <path d="M56 28 C60 36, 56 44, 48 48 C40 52, 32 54, 28 62 C24 70, 28 80, 38 84"
                    stroke="white" strokeWidth="5" fill="none" strokeLinecap="round" strokeLinejoin="round"/>
                  {/* Snake head — solid circle upper-right, naturally attached to body start */}
                  <circle cx="56" cy="22" r="8" fill="white"/>
                  {/* Gold accent — filled crescent shape on right edge */}
                  <path d="M74 20 C88 36, 92 58, 88 82 C86 92, 80 100, 74 104 C78 96, 80 84, 78 68 C76 52, 72 38, 74 20Z"
                    fill="rgba(212,175,55,0.75)"/>
                  {/* Gold accent — inner highlight stroke for depth */}
                  <path d="M70 28 C80 44, 82 66, 76 88"
                    stroke="rgba(212,175,55,0.4)" strokeWidth="2" fill="none" strokeLinecap="round"/>
                </svg>
              </div>
              <div className="about-brand-text">
                <span className="about-brand-arabic">صيدلية الأمين</span>
                <span className="about-brand-english">AL AMEEN PHARMACY</span>
                <span className="about-brand-subline">
                  <span className="about-brand-divider" aria-hidden="true" />
                  PHARMACY LLC
                  <span className="about-brand-divider" aria-hidden="true" />
                </span>
              </div>
            </div>
            <div className="hero-supporting-text">
              <p className="company-tagline">Pharmaceutical &amp; Medical Equipment Trading</p>
              <p className="hero-description">
                Trusted pharmacy and medical equipment supplier serving Dubai with quality healthcare products and reliable service.
              </p>
            </div>
          </div>
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
              <strong>Al Ameen Pharmacy</strong> is a leading pharmaceutical and medical equipment
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

      {/* Footer — same as Home page */}
      <footer className="site-footer">
        <div className="footer-inner">
          {/* Brand & About */}
          <div className="footer-section footer-brand-section">
            <div className="footer-brand">
              <div className="footer-brand-mark">
                <div className="footer-brand-icon">
                  <svg className="footer-brand-icon-svg" viewBox="0 0 100 120" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                    <path d="M43 58 C54 62, 60 72, 54 82 C49 90, 40 94, 37 102"
                      stroke="white" strokeWidth="5" fill="none" strokeLinecap="round" strokeLinejoin="round"/>
                    <rect x="38" y="10" width="10" height="96" rx="5" fill="white"/>
                    <path d="M56 28 C60 36, 56 44, 48 48 C40 52, 32 54, 28 62 C24 70, 28 80, 38 84"
                      stroke="white" strokeWidth="5" fill="none" strokeLinecap="round" strokeLinejoin="round"/>
                    <circle cx="56" cy="22" r="8" fill="white"/>
                    <path d="M74 20 C88 36, 92 58, 88 82 C86 92, 80 100, 74 104 C78 96, 80 84, 78 68 C76 52, 72 38, 74 20Z"
                      fill="rgba(212,175,55,0.75)"/>
                    <path d="M70 28 C80 44, 82 66, 76 88"
                      stroke="rgba(212,175,55,0.4)" strokeWidth="2" fill="none" strokeLinecap="round"/>
                  </svg>
                </div>
                <div className="footer-brand-text">
                  <span className="footer-brand-arabic">صيدلية الأمين</span>
                  <span className="footer-brand-english">AL AMEEN PHARMACY</span>
                  <span className="footer-brand-subline">
                    <span className="footer-brand-divider" aria-hidden="true" />
                    PHARMACY LLC
                    <span className="footer-brand-divider" aria-hidden="true" />
                  </span>
                </div>
              </div>
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
};

export default About;
