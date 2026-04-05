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
  // GET single product
  getOne: (id) => api.get(`/products/${id}/`),
};

export default api;
