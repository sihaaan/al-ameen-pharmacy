// frontend/src/App.js
import React, { useState, useEffect } from "react";
import { CartProvider } from "./context/CartContext";
import { productsAPI } from "./api";
import Navbar from "./components/Navbar";
import ProductGrid from "./components/ProductGrid";
import "./App.css";

function App() {
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchProducts = async () => {
      try {
        console.log("Fetching products from backend...");
        const response = await productsAPI.getAll();
        console.log("Products received:", response.data);
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

  if (loading) {
    return (
      <div className="App">
        <h1>Al Ameen Pharmacy</h1>
        <p>Loading products...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="App">
        <h1>Al Ameen Pharmacy</h1>
        <p style={{ color: "red" }}>{error}</p>
        <p>Make sure your backend is running on port 5000!</p>
      </div>
    );
  }

  return (
    <CartProvider>
      <div className="App">
        <Navbar />

        <header className="App-header">
          <h1>🏥 Al Ameen Pharmacy</h1>
          <p>Your trusted online pharmacy</p>
        </header>

        <main className="products-section">
          <h2>Our Products ({products.length})</h2>
          <ProductGrid products={products} />
        </main>
      </div>
    </CartProvider>
  );
}

export default App;
