// frontend/src/components/ProductGrid.js
import React, { useState } from "react";
import { useCart } from "../context/CartContext";
import { useAuth } from "../context/AuthContext";
import { useNavigate } from "react-router-dom";
import "../styles/ProductGrid.css";

const ProductGrid = ({ products }) => {
  const { addToCart } = useCart();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [addingToCart, setAddingToCart] = useState(null);
  const [feedback, setFeedback] = useState({});

  const handleAddToCart = async (product) => {
    // Check if user is logged in
    if (!user) {
      alert('Please login to add items to cart');
      navigate('/login');
      return;
    }

    setAddingToCart(product.id);
    const result = await addToCart(product, 1);
    setAddingToCart(null);

    if (result.success) {
      // Show success feedback
      setFeedback({ [product.id]: 'Added!' });
      setTimeout(() => {
        setFeedback(prev => ({ ...prev, [product.id]: null }));
      }, 2000);
    } else {
      // Show error feedback
      alert(result.error || 'Failed to add to cart');
    }
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
            disabled={product.stock_quantity === 0 || addingToCart === product.id}
          >
            {addingToCart === product.id
              ? "Adding..."
              : feedback[product.id]
              ? "✓ Added!"
              : product.stock_quantity === 0
              ? "Out of Stock"
              : "Add to Cart"}
          </button>
        </div>
      ))}
    </div>
  );
};

export default ProductGrid;
