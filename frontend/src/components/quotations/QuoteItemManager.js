import React, { useEffect, useMemo, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import axiosInstance from '../../utils/axios';
import QuotationErrorNotice from './QuotationErrorNotice';

const emptyItem = {
  product: '',
  name: '',
  internal_code: '',
  brand_text: '',
  generic_name: '',
  strength: '',
  dosage_form: '',
  pack_size: '',
  unit: '',
  notes: '',
  is_active: true,
};

const QuoteItemManager = () => {
  const [items, setItems] = useState([]);
  const [products, setProducts] = useState([]);
  const [selectedItem, setSelectedItem] = useState(null);
  const [form, setForm] = useState(emptyItem);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [productsLoading, setProductsLoading] = useState(true);
  const [productErrorInfo, setProductErrorInfo] = useState(null);
  const [saving, setSaving] = useState(false);
  const [errorInfo, setErrorInfo] = useState(null);

  const load = async () => {
    setLoading(true);
    setProductsLoading(true);
    setErrorInfo(null);
    setProductErrorInfo(null);

    const itemsPromise = quotationAPI.items.list()
      .then((itemsRes) => {
        setItems(itemsRes.data);
      })
      .catch(async (error) => {
        const details = await describeQuotationError(error, 'Load quote items', 'GET /quotations/items/');
        setErrorInfo(details);
        console.error(formatQuotationError(details), error);
      })
      .finally(() => {
        setLoading(false);
      });

    const productsPromise = axiosInstance.get('/products/?compact=true&limit=200')
      .then((productsRes) => {
        setProducts(productsRes.data);
      })
      .catch(async (error) => {
        const details = await describeQuotationError(error, 'Load optional public products', 'GET /products/?compact=true&limit=200');
        setProductErrorInfo(details);
        console.warn(formatQuotationError(details), error);
      })
      .finally(() => {
        setProductsLoading(false);
      });

    await Promise.allSettled([itemsPromise, productsPromise]);
  };

  const refreshProducts = async () => {
    setProductsLoading(true);
    setProductErrorInfo(null);
    try {
      const productsRes = await axiosInstance.get('/products/?compact=true&limit=200');
      setProducts(productsRes.data);
    } catch (error) {
      const details = await describeQuotationError(error, 'Load optional public products', 'GET /products/?compact=true&limit=200');
      setProductErrorInfo(details);
      console.warn(formatQuotationError(details), error);
    } finally {
      setProductsLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const filteredItems = useMemo(() => {
    const term = search.toLowerCase();
    return items.filter((item) =>
      item.name.toLowerCase().includes(term) ||
      (item.internal_code || '').toLowerCase().includes(term) ||
      (item.brand_text || '').toLowerCase().includes(term) ||
      (item.generic_name || '').toLowerCase().includes(term)
    );
  }, [items, search]);

  const reset = () => {
    setSelectedItem(null);
    setForm(emptyItem);
  };

  const editItem = (item) => {
    setSelectedItem(item);
    setForm({
      product: item.product || '',
      name: item.name || '',
      internal_code: item.internal_code || '',
      brand_text: item.brand_text || '',
      generic_name: item.generic_name || '',
      strength: item.strength || '',
      dosage_form: item.dosage_form || '',
      pack_size: item.pack_size || '',
      unit: item.unit || '',
      notes: item.notes || '',
      is_active: item.is_active,
    });
  };

  const saveItem = async (event) => {
    event.preventDefault();
    setSaving(true);
    setErrorInfo(null);
    const payload = {
      ...form,
      product: form.product || null,
    };
    try {
      if (selectedItem) {
        await quotationAPI.items.update(selectedItem.id, payload);
      } else {
        await quotationAPI.items.create(payload);
      }
      reset();
      await load();
    } catch (error) {
      const details = await describeQuotationError(
        error,
        selectedItem ? 'Update quote item' : 'Create quote item',
        selectedItem ? `PATCH /quotations/items/${selectedItem.id}/` : 'POST /quotations/items/'
      );
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="qm-section">
      <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
      <div className="qm-split">
        <div className="qm-panel">
        <div className="qm-panel-heading">
          <h3>Quote Items</h3>
          <input className="qm-input" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search quote items" />
        </div>
        {loading ? (
          <div className="qm-loading">Loading quote items...</div>
        ) : (
          <div className="qm-table-wrap">
            <table className="qm-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Code</th>
                  <th>Brand</th>
                  <th>Strength</th>
                  <th>Pack</th>
                </tr>
              </thead>
              <tbody>
                {filteredItems.map((item) => (
                  <tr key={item.id} className={selectedItem?.id === item.id ? 'selected' : ''} onClick={() => editItem(item)}>
                    <td>{item.name}</td>
                    <td>{item.internal_code || '-'}</td>
                    <td>{item.brand_text || item.product_name || '-'}</td>
                    <td>{item.strength || '-'}</td>
                    <td>{item.pack_size || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        </div>

        <div className="qm-panel">
        <div className="qm-panel-heading">
          <h3>{selectedItem ? 'Edit Quote Item' : 'New Quote Item'}</h3>
          {selectedItem && <button type="button" className="qm-secondary" onClick={reset}>New</button>}
        </div>
        <form onSubmit={saveItem} className="qm-form">
          <label>Quote Item Name<input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
          <label>Optional Public Product
            <select value={form.product} onChange={(event) => setForm({ ...form, product: event.target.value })}>
              <option value="">{productsLoading ? 'No public product link (products loading...)' : 'No public product link'}</option>
              {productsLoading && <option value="" disabled>Loading public products...</option>}
              {products.map((product) => (
                <option key={product.id} value={product.id}>{product.name}</option>
              ))}
            </select>
          </label>
          {productsLoading && (
            <div className="qm-notice">
              Public products are still loading. You can save a private quote item now by leaving this optional field blank.
            </div>
          )}
          {productErrorInfo && (
            <div className="qm-notice">
              Public products could not load. You can still save a private quote item without linking a public product.
              <button type="button" className="qm-secondary small" onClick={refreshProducts}>Retry Products</button>
            </div>
          )}
          <div className="qm-grid-two">
            <label>Internal Code<input value={form.internal_code} onChange={(event) => setForm({ ...form, internal_code: event.target.value })} /></label>
            <label>Brand<input value={form.brand_text} onChange={(event) => setForm({ ...form, brand_text: event.target.value })} /></label>
            <label>Generic Name<input value={form.generic_name} onChange={(event) => setForm({ ...form, generic_name: event.target.value })} /></label>
            <label>Strength<input value={form.strength} onChange={(event) => setForm({ ...form, strength: event.target.value })} /></label>
            <label>Dosage Form<input value={form.dosage_form} onChange={(event) => setForm({ ...form, dosage_form: event.target.value })} /></label>
            <label>Pack Size<input value={form.pack_size} onChange={(event) => setForm({ ...form, pack_size: event.target.value })} /></label>
            <label>Unit<input value={form.unit} onChange={(event) => setForm({ ...form, unit: event.target.value })} /></label>
          </div>
          <label>Notes<textarea rows="3" value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} /></label>
          <label className="qm-checkbox"><input type="checkbox" checked={form.is_active} onChange={(event) => setForm({ ...form, is_active: event.target.checked })} /> Active</label>
          <button type="submit" className="qm-primary" disabled={saving}>{saving ? 'Saving...' : 'Save Quote Item'}</button>
        </form>
        </div>
      </div>
    </div>
  );
};

export default QuoteItemManager;
