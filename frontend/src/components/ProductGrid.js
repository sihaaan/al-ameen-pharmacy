// frontend/src/components/ProductGrid.js
import React, { useState } from "react";
import { useCart } from "../context/CartContext";
import { useAuth } from "../context/AuthContext";
import { useNavigate } from "react-router-dom";
import ProductModal from "./ProductModal";
import "../styles/ProductGrid.css";

const ProductGrid = ({ products }) => {
  const { addToCart } = useCart();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [addingToCart, setAddingToCart] = useState(null);
  const [feedback, setFeedback] = useState({});
  const [selectedProductId, setSelectedProductId] = useState(null);

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

  const handleCardClick = (e, productId) => {
    // Check if click was on the button
    if (e.target.closest('button')) {
      return;
    }
    // Open quick view modal
    setSelectedProductId(productId);
  };

  const handleViewFullPage = (productId) => {
    navigate(`/product/${productId}`);
  };

  return (
    <>
      <div className="products-grid">
        {products.map((product) => (
          <div
            key={product.id}
            className="product-card"
            onClick={(e) => handleCardClick(e, product.id)}
          >
            <div className="product-image">
              {product.image ? (
                <img src={product.image} alt={product.name} />
              ) : product.image_url ? (
                <img src={product.image_url} alt={product.name} />
              ) : (
                <div className="no-image">No Image</div>
              )}
            </div>
            <div className="product-card-content">
              <h3>{product.name}</h3>
              <p className="product-description">{product.description}</p>
              {product.category_name && (
                <p className="product-category">Category: {product.category_name}</p>
              )}
              <div className="product-details">
                <span className="product-price">AED {parseFloat(product.price).toFixed(2)}</span>
                <span className="product-stock">
                  Stock: {product.stock_quantity}
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>

      {selectedProductId && (
        <ProductModal
          productId={selectedProductId}
          onClose={() => setSelectedProductId(null)}
          onViewFullPage={handleViewFullPage}
        />
      )}
    </>
  );
};

export default ProductGrid;
