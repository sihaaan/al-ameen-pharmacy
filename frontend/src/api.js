// frontend/src/api.js
import axios from "axios";

// Base URL for your backend API
const API_URL = "http://localhost:5000/api";

// Create axios instance with default settings
const api = axios.create({
  baseURL: API_URL,
  timeout: 10000, // 10 second timeout
});

// Products API functions
export const productsAPI = {
  // GET all products from your backend
  getAll: () => api.get("/products"),
  // Later we'll add: create, update, delete
};

export default api;
