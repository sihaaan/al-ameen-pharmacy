// frontend/src/api.js
import axios from "axios";

// Base URL for your Django backend API
// Use environment variable or fallback to localhost for development
const API_URL = process.env.REACT_APP_API_URL || "http://localhost:8000/api";

// Create axios instance with default settings
// Timeout set to 30s to handle Neon database cold starts (free tier can take 15-30s to wake)
const api = axios.create({
  baseURL: API_URL,
  timeout: 30000,
});

// Products API functions
export const productsAPI = {
  // GET all products from Django backend
  getAll: () => api.get("/products/"),
  // GET single product by slug or ID
  // Backend uses slug-based lookup, but ID still works via redirect
  getOne: (slugOrId) => api.get(`/products/${slugOrId}/`),
  // GET featured products
  getFeatured: () => api.get("/products/?featured=true"),
  // Search products
  search: (query) => api.get(`/products/?search=${encodeURIComponent(query)}`),
  // Filter by category slug
  getByCategory: (categorySlug) => api.get(`/products/?category=${categorySlug}`),
  // Filter by brand slug
  getByBrand: (brandSlug) => api.get(`/products/?brand=${brandSlug}`),
};

// Categories API
export const categoriesAPI = {
  getAll: () => api.get("/categories/"),
  getRootOnly: () => api.get("/categories/?root=true"),
  getFlat: () => api.get("/categories/?flat=true"),
  getOne: (slug) => api.get(`/categories/${slug}/`),
};

// Brands API
export const brandsAPI = {
  getAll: () => api.get("/brands/"),
  getOne: (slug) => api.get(`/brands/${slug}/`),
};

export default api;
