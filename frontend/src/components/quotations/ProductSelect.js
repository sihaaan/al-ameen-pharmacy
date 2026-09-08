import React, { useId, useMemo, useState } from 'react';
import './ProductSelect.css';

const MAX_RESULTS = 20;

// Build and sort once per catalogue/customer change, shared by every line picker.
export const buildProductCatalogue = (items, companyItems) => {
  const preferredIds = new Set(companyItems.map((item) => String(item.id)));
  const byId = new Map(items.map((item) => [String(item.id), item]));
  const options = Array.from(byId.values()).map((item) => ({
    ...item,
    previouslyUsed: preferredIds.has(String(item.id)),
    searchText: [item.name, item.brand_name, item.sku, item.barcode, item.unit, item.pack_size]
      .filter(Boolean).join(' ').toLowerCase(),
  })).sort((a, b) => (
    Number(b.previouslyUsed) - Number(a.previouslyUsed) || a.name.localeCompare(b.name)
  ));
  return { options, byId };
};

const ProductSelect = ({
  catalogue,
  value,
  fallbackName = '',
  label,
  searchLabel,
  placeholder = 'Unmatched',
  disabled = false,
  loading = false,
  allowCreate = false,
  onChange,
}) => {
  const [search, setSearch] = useState('');
  const helpId = useId();
  const selectedId = String(value || '');
  const selectedProduct = catalogue.byId.get(selectedId);
  const { matches, count } = useMemo(() => {
    const terms = search.toLowerCase().trim().split(/\s+/).filter(Boolean);
    const filtered = catalogue.options.filter((item) => terms.every((term) => item.searchText.includes(term)));
    return { matches: filtered.slice(0, MAX_RESULTS), count: filtered.length };
  }, [catalogue, search]);
  const keepSelected = selectedId && !matches.some((item) => String(item.id) === selectedId);
  const previouslyUsed = matches.filter((item) => item.previouslyUsed);
  const remaining = matches.filter((item) => !item.previouslyUsed);
  const renderOptions = (options) => options.map((item) => (
    <option key={item.id} value={item.id}>{item.name}</option>
  ));

  return (
    <div className="qm-product-picker">
      <input
        type="search"
        aria-label={searchLabel}
        aria-describedby={helpId}
        aria-busy={loading}
        placeholder="Search products…"
        value={search}
        disabled={disabled}
        onChange={(event) => setSearch(event.target.value)}
        onKeyDown={(event) => {
          // Searching inside the add-line form must not submit a new line.
          if (event.key === 'Enter') event.preventDefault();
          if (event.key === 'Escape') {
            event.preventDefault();
            setSearch('');
          }
        }}
      />
      <select
        aria-label={label}
        aria-describedby={helpId}
        aria-busy={loading}
        value={selectedId}
        disabled={disabled}
        onChange={(event) => {
          if (disabled) return;
          onChange(event.target.value);
          setSearch('');
        }}
      >
        <option value="">{placeholder}</option>
        {allowCreate && <option value="__create__">+ Create a new Product…</option>}
        {keepSelected && (
          <optgroup label="Selected product">
            <option value={selectedId}>{selectedProduct?.name || fallbackName || `Product ${selectedId}`}</option>
          </optgroup>
        )}
        {previouslyUsed.length > 0 && (
          <optgroup label="Previously quoted for this customer">{renderOptions(previouslyUsed)}</optgroup>
        )}
        {remaining.length > 0 && (
          <optgroup label="All other Products">{renderOptions(remaining)}</optgroup>
        )}
      </select>
      <small id={helpId} className="qm-product-picker-help" role="status">
        {loading ? 'Loading products…' : count === 0
          ? (search.trim() ? 'No matching products. Try another search.' : 'No products available.')
          : count > MAX_RESULTS
            ? `Showing ${MAX_RESULTS} of ${count} matches. Type to narrow the list.`
            : search.trim() ? `${count} matching product${count === 1 ? '' : 's'}.` : ''}
      </small>
    </div>
  );
};

export default ProductSelect;
