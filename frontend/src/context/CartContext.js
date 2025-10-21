// frontend/src/context/CartContext.js
import React, { createContext, useContext, useState, useEffect, useCallback } from "react";
import axios from "axios";
import { useAuth } from "./AuthContext";

// Create the context
const CartContext = createContext();

// Custom hook to use cart context
export const useCart = () => {
  const context = useContext(CartContext);
  if (!context) {
    throw new Error("useCart must be used within a CartProvider");
  }
  return context;
};

// Cart Provider Component
export const CartProvider = ({ children }) => {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const { user } = useAuth();

  // Fetch cart from backend when user logs in
  const fetchCart = useCallback(async () => {
    if (!user) {
      setItems([]);
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const response = await axios.get('http://localhost:8000/api/cart/');
      // The backend returns cart with items array
      setItems(response.data.items || []);
    } catch (err) {
      console.error('Error fetching cart:', err);
      setError('Failed to load cart');
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [user]);

  // Load cart when user logs in
  useEffect(() => {
    fetchCart();
  }, [fetchCart]);

  // Add item to cart (calls Django API)
  const addToCart = async (product, quantity = 1) => {
    if (!user) {
      setError('Please login to add items to cart');
      return { success: false, error: 'Please login to add items to cart' };
    }

    setLoading(true);
    setError(null);
    try {
      const response = await axios.post('http://localhost:8000/api/cart/add_item/', {
        product_id: product.id,
        quantity: quantity
      });

      // Refresh cart after adding
      await fetchCart();

      return { success: true, data: response.data };
    } catch (err) {
      console.error('Error adding to cart:', err);
      const errorMessage = err.response?.data?.error || 'Failed to add item to cart';
      setError(errorMessage);
      return { success: false, error: errorMessage };
    } finally {
      setLoading(false);
    }
  };

  // Remove item from cart (calls Django API)
  const removeFromCart = async (cartItemId) => {
    if (!user) {
      setError('Please login first');
      return { success: false, error: 'Please login first' };
    }

    setLoading(true);
    setError(null);
    try {
      await axios.delete('http://localhost:8000/api/cart/remove_item/', {
        data: { cart_item_id: cartItemId }
      });

      // Refresh cart after removing
      await fetchCart();

      return { success: true };
    } catch (err) {
      console.error('Error removing from cart:', err);
      const errorMessage = err.response?.data?.error || 'Failed to remove item';
      setError(errorMessage);
      return { success: false, error: errorMessage };
    } finally {
      setLoading(false);
    }
  };

  // Update quantity (calls Django API)
  const updateQuantity = async (cartItemId, quantity) => {
    if (!user) {
      setError('Please login first');
      return { success: false, error: 'Please login first' };
    }

    setLoading(true);
    setError(null);
    try {
      if (quantity <= 0) {
        // If quantity is 0 or less, remove the item
        return await removeFromCart(cartItemId);
      }

      await axios.patch('http://localhost:8000/api/cart/update_item/', {
        cart_item_id: cartItemId,
        quantity: quantity
      });

      // Refresh cart after updating
      await fetchCart();

      return { success: true };
    } catch (err) {
      console.error('Error updating quantity:', err);
      const errorMessage = err.response?.data?.error || 'Failed to update quantity';
      setError(errorMessage);
      return { success: false, error: errorMessage };
    } finally {
      setLoading(false);
    }
  };

  // Clear cart (calls Django API)
  const clearCart = async () => {
    if (!user) {
      setItems([]);
      return { success: true };
    }

    setLoading(true);
    setError(null);
    try {
      await axios.delete('http://localhost:8000/api/cart/clear/');
      setItems([]);
      return { success: true };
    } catch (err) {
      console.error('Error clearing cart:', err);
      const errorMessage = err.response?.data?.error || 'Failed to clear cart';
      setError(errorMessage);
      return { success: false, error: errorMessage };
    } finally {
      setLoading(false);
    }
  };

  // Calculate totals from items
  const totalItems = items.reduce((sum, item) => sum + item.quantity, 0);
  const totalPrice = items.reduce(
    (sum, item) => sum + parseFloat(item.product.price) * item.quantity,
    0
  );

  const value = {
    items,
    totalItems,
    totalPrice,
    loading,
    error,
    addToCart,
    removeFromCart,
    updateQuantity,
    clearCart,
    fetchCart,
  };

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
};
