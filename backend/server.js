const express = require("express");
const cors = require("cors");
require("dotenv").config();

const app = express();
const PORT = process.env.PORT || 5000;

// Middleware
app.use(cors());
app.use(express.json());

// Test route
app.get("/", (req, res) => {
  res.json({ message: "Dubai Pharmacy API is running! 🚀" });
});

// Temporary products route (we'll improve this)
app.get("/api/products", (req, res) => {
  // Fake data for now - we'll connect to real database later
  const products = [
    {
      id: 1,
      name: "Paracetamol 500mg",
      description: "Pain relief and fever reducer",
      price: 12.5,
      stock_quantity: 100,
      category: "Pain Relief",
    },
    {
      id: 2,
      name: "Vitamin D3 1000IU",
      description: "Daily vitamin supplement",
      price: 25.0,
      stock_quantity: 50,
      category: "Vitamins",
    },
  ];
  res.json(products);
});

app.listen(PORT, () => {
  console.log(`🚀 Server running on port ${PORT}`);
});
