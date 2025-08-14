// frontend/src/components/ProductGrid.js
import React from "react";
import { useCart } from "../context/CartContext";
import "../styles/ProductGrid.css";

const ProductGrid = ({ products }) => {
  const { addToCart } = useCart();

  const handleAddToCart = (product) => {
    addToCart(product, 1);
    // Optional: Show a success message
    console.log(`Added ${product.name} to cart!`);
  };

  return (
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
          <button
            className="add-to-cart-btn"
            onClick={() => handleAddToCart(product)}
            disabled={product.stock_quantity === 0}
          >
            {product.stock_quantity === 0 ? "Out of Stock" : "Add to Cart"}
          </button>
        </div>
      ))}
    </div>
  );
};

export default ProductGrid;
