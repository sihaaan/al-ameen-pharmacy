// frontend/src/api.js
import axios from "axios";

// Base URL for your Django backend API
const API_URL = "http://localhost:8000/api";

// Create axios instance with default settings
const api = axios.create({
  baseURL: API_URL,
  timeout: 10000, // 10 second timeout
});

// Products API functions
export const productsAPI = {
  // GET all products from Django backend
  getAll: () => api.get("/products/"),
  // GET single product
  getOne: (id) => api.get(`/products/${id}/`),
};

export default api;
