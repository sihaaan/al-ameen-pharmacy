// frontend/src/context/CartContext.js
import React, { createContext, useContext, useState, useEffect, useCallback } from "react";
import axiosInstance from "../utils/axios";
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
      const response = await axiosInstance.get('/cart/');
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

    // Optimistic update - immediately update UI
    const existingItemIndex = items.findIndex(
      item => item.product.id === product.id
    );

    let optimisticItems;
    if (existingItemIndex >= 0) {
      // Item already exists, increase quantity
      optimisticItems = [...items];
      optimisticItems[existingItemIndex] = {
        ...optimisticItems[existingItemIndex],
        quantity: optimisticItems[existingItemIndex].quantity + quantity
      };
    } else {
      // New item, add to cart
      optimisticItems = [
        ...items,
        {
          id: Date.now(), // temporary ID
          product: product,
          quantity: quantity
        }
      ];
    }

    // Update UI immediately
    setItems(optimisticItems);

    // Then make API call in background
    setError(null);
    try {
      await axiosInstance.post('/cart/add_item/', {
        product_id: product.id,
        quantity: quantity
      });

      // Sync with backend to get correct cart state (including IDs)
      // Do this without showing loading state
      const response = await axiosInstance.get('/cart/');
      setItems(response.data.items || []);

      return { success: true };
    } catch (err) {
      console.error('Error adding to cart:', err);
      const errorMessage = err.response?.data?.error || 'Failed to add item to cart';
      setError(errorMessage);

      // Revert optimistic update on error
      setItems(items);

      return { success: false, error: errorMessage };
    }
  };

  // Remove item from cart (calls Django API)
  const removeFromCart = async (cartItemId) => {
    if (!user) {
      setError('Please login first');
      return { success: false, error: 'Please login first' };
    }

    // Optimistic update - immediately remove from UI
    const previousItems = items;
    const optimisticItems = items.filter(item => item.id !== cartItemId);
    setItems(optimisticItems);

    setError(null);
    try {
      await axiosInstance.delete('/cart/remove_item/', {
        data: { cart_item_id: cartItemId }
      });

      return { success: true };
    } catch (err) {
      console.error('Error removing from cart:', err);
      const errorMessage = err.response?.data?.error || 'Failed to remove item';
      setError(errorMessage);

      // Revert optimistic update on error
      setItems(previousItems);

      return { success: false, error: errorMessage };
    }
  };

  // Update quantity (calls Django API)
  const updateQuantity = async (cartItemId, quantity) => {
    if (!user) {
      setError('Please login first');
      return { success: false, error: 'Please login first' };
    }

    if (quantity <= 0) {
      // If quantity is 0 or less, remove the item
      return await removeFromCart(cartItemId);
    }

    // Optimistic update - immediately update UI
    const previousItems = items;
    const optimisticItems = items.map(item =>
      item.id === cartItemId ? { ...item, quantity } : item
    );
    setItems(optimisticItems);

    setError(null);
    try {
      await axiosInstance.patch('/cart/update_item/', {
        cart_item_id: cartItemId,
        quantity: quantity
      });

      return { success: true };
    } catch (err) {
      console.error('Error updating quantity:', err);
      const errorMessage = err.response?.data?.error || 'Failed to update quantity';
      setError(errorMessage);

      // Revert optimistic update on error
      setItems(previousItems);

      return { success: false, error: errorMessage };
    }
  };

  // Clear cart (calls Django API)
  const clearCart = async () => {
    if (!user) {
      setItems([]);
      return { success: true };
    }

    // Optimistic update - immediately clear UI
    const previousItems = items;
    setItems([]);

    setError(null);
    try {
      await axiosInstance.delete('/cart/clear/');
      return { success: true };
    } catch (err) {
      console.error('Error clearing cart:', err);
      const errorMessage = err.response?.data?.error || 'Failed to clear cart';
      setError(errorMessage);

      // Revert optimistic update on error
      setItems(previousItems);

      return { success: false, error: errorMessage };
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
