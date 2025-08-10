// frontend/src/App.js
import React, { useState, useEffect } from "react";
import { productsAPI } from "./api";
import "./App.css";

function App() {
  // State to store products from your backend
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Fetch products when component loads
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
  }, []); // Empty array = run once when component mounts

  // Loading state
  if (loading) {
    return (
      <div className="App">
        <h1>Al Ameen Pharmacy</h1>
        <p>Loading products...</p>
      </div>
    );
  }

  // Error state
  if (error) {
    return (
      <div className="App">
        <h1>Al Ameen Pharmacy</h1>
        <p style={{ color: "red" }}>{error}</p>
        <p>Make sure your backend is running on port 5000!</p>
      </div>
    );
  }

  // Success state - show products
  return (
    <div className="App">
      <header className="App-header">
        <h1>🏥 Al Ameen Pharmacy</h1>
        <p>Your trusted online pharmacy</p>
      </header>

      <main className="products-section">
        <h2>Our Products ({products.length})</h2>

        <div className="products-grid">
          {products.map((product) => (
            <div key={product.id} className="product-card">
              <h3>{product.name}</h3>
              <p className="product-description">{product.description}</p>
              <p className="product-category">Category: {product.category}</p>
              <div className="product-details">
                <span className="product-price">AED {product.price}</span>
                <span className="product-stock">
                  Stock: {product.stock_quantity}
                </span>
              </div>
              <button className="add-to-cart-btn">Add to Cart</button>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}

export default App;
