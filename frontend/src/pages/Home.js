import React, { useState, useEffect } from "react";
import { productsAPI } from "../api";
import ProductGrid from "../components/ProductGrid";

function Home() {
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
        <div className="loading-container">
          <div className="loading-spinner"></div>
          <p>Loading products...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="App">
        <div className="error-container">
          <p style={{ color: "red" }}>{error}</p>
          <p>Make sure your backend is running on port 8000!</p>
        </div>
      </div>
    );
  }

  return (
    <>
      <header className="App-header">
        <div className="header-content">
          <div className="brand-header">
            <h2 className="brand-arabic-home">صيدلية الأمين</h2>
            <h1 className="brand-english-home">AL AMEEN PHARMACY</h1>
          </div>
          <p className="header-tagline">Your Trusted Healthcare Partner in Dubai</p>
        </div>
      </header>

      <main className="products-section">
        <div className="section-header-home">
          <h2>Our Products</h2>
          <p className="products-count">{products.length} Premium Products Available</p>
        </div>
        <ProductGrid products={products} />
      </main>
    </>
  );
}

export default Home;
