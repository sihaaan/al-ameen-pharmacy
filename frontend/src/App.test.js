import { render, screen } from '@testing-library/react';
import App from './App';

jest.mock('./pages/Home', () => () => <main>Home</main>);

jest.mock('./api', () => ({
  productsAPI: {
    getAll: jest.fn().mockResolvedValue({ data: [] }),
    getFeatured: jest.fn().mockResolvedValue({ data: [] }),
    search: jest.fn().mockResolvedValue({ data: [] }),
    getByCategory: jest.fn().mockResolvedValue({ data: [] }),
    getByBrand: jest.fn().mockResolvedValue({ data: [] }),
  },
  categoriesAPI: {
    getAll: jest.fn().mockResolvedValue({ data: [] }),
    getRootOnly: jest.fn().mockResolvedValue({ data: [] }),
  },
  brandsAPI: {
    getAll: jest.fn().mockResolvedValue({ data: [] }),
  },
}));

test('renders the pharmacy application shell', () => {
  render(<App />);
  expect(screen.getByRole('link', { name: /al ameen pharmacy llc/i })).toBeInTheDocument();
  expect(screen.getByPlaceholderText(/search for medicines/i)).toBeInTheDocument();
});
