// frontend/src/App.js
import React from "react";
import lazyWorkspace from "./components/WorkspaceLoader";
import { BrowserRouter as Router, Routes, Route } from "react-router-dom";
import { CartProvider } from "./context/CartContext";
import { AuthProvider } from "./context/AuthContext";
import Navbar from "./components/Navbar";
import WhatsAppButton from "./components/WhatsAppButton";
import ScrollToTop from "./components/ScrollToTop";
import Home from "./pages/Home";
import Login from "./pages/Login";
import Register from "./pages/Register";
import ForgotPassword from "./pages/ForgotPassword";
import ResetPassword from "./pages/ResetPassword";
import About from "./pages/About";
import ProductDetail from "./pages/ProductDetail";
import Checkout from "./pages/Checkout";
import OrderConfirmation from "./pages/OrderConfirmation";
import Profile from "./pages/Profile";
import "./App.css";

const AdminDashboard = lazyWorkspace(() => {
  const dashboard = import("./pages/AdminDashboard");
  const params = new URLSearchParams(window.location.search);
  const tab = params.get('admin_tab');
  const pending = [];
  if (tab === 'products') pending.push(import('./components/ProductManagement'));
  if (tab === 'orders') pending.push(import('./components/OrderManagement'));
  if (tab === 'accounting') pending.push(import('./components/accounting/AccountingModule'));
  const quotationLink = tab === 'quotations' || (!tab && ['quotation_tab', 'quote_id', 'gmail_import', 'gmail_import_id'].some((key) => params.has(key)));
  if (quotationLink) {
    pending.push(import('./components/quotations/QuotationModule'));
    if (params.has('gmail_import') || params.has('gmail_import_id')) {
      pending.push(import('./components/quotations/GmailInquiryReview'));
    } else if (params.has('quote_id')) {
      pending.push(params.get('quotation_mode') === 'review'
        ? import('./components/quotations/QuotationOutcomeReview')
        : import('./components/quotations/QuotationEditor'));
    } else if (params.get('quotation_tab') === 'quotes') {
      pending.push(import('./components/quotations/QuotationList'));
    } else if (!params.has('quotation_tab') || params.get('quotation_tab') === 'dashboard') {
      pending.push(import('./components/quotations/QuotationDashboard'));
    }
  }
  // Download the selected workspace concurrently. Components and their requests
  // still mount only after the dashboard's authentication/permission gates.
  void Promise.allSettled(pending);
  return dashboard;
}, 'admin workspace');

function App() {
  return (
    <Router future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AuthProvider>
        <CartProvider>
          <div className="App">
            <ScrollToTop />
            <Navbar />
            <WhatsAppButton />
            <Routes>
              <Route path="/" element={<Home />} />
              <Route path="/about" element={<About />} />
              <Route path="/login" element={<Login />} />
              <Route path="/register" element={<Register />} />
              <Route path="/forgot-password" element={<ForgotPassword />} />
              <Route path="/reset-password/:token" element={<ResetPassword />} />
              <Route path="/admin" element={<AdminDashboard />} />
              <Route path="/profile" element={<Profile />} />
              <Route path="/product/:id" element={<ProductDetail />} />
              <Route path="/checkout" element={<Checkout />} />
              <Route path="/order-confirmation" element={<OrderConfirmation />} />
            </Routes>
          </div>
        </CartProvider>
      </AuthProvider>
    </Router>
  );
}

export default App;
