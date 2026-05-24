import React, { useEffect, useMemo, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import QuotationErrorNotice from './QuotationErrorNotice';

const emptyItem = {
  name: '',
  sku: '',
  barcode: '',
  dosage: '',
  pack_size: '',
  active_ingredient: '',
  short_description: '',
  price: '0.01',
  stock_quantity: '0',
  status: 'draft',
  show_price: false,
};

const QuoteItemManager = () => {
  const [items, setItems] = useState([]);
  const [selectedItem, setSelectedItem] = useState(null);
  const [form, setForm] = useState(emptyItem);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState(null);
  const [errorInfo, setErrorInfo] = useState(null);

  const load = async () => {
    setLoading(true);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.items.list();
      setItems(response.data);
    } catch (error) {
      const details = await describeQuotationError(error, 'Load quotation products', 'GET /quotations/items/');
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const filteredItems = useMemo(() => {
    const term = search.toLowerCase();
    return items.filter((item) =>
      item.name.toLowerCase().includes(term) ||
      (item.sku || '').toLowerCase().includes(term) ||
      (item.barcode || '').toLowerCase().includes(term) ||
      (item.active_ingredient || '').toLowerCase().includes(term)
    );
  }, [items, search]);

  const reset = () => {
    setSelectedItem(null);
    setForm(emptyItem);
  };

  const editItem = (item) => {
    setSelectedItem(item);
    setNotice(null);
    setForm({
      name: item.name || '',
      sku: item.sku || '',
      barcode: item.barcode || '',
      dosage: item.dosage || '',
      pack_size: item.pack_size || '',
      active_ingredient: item.active_ingredient || '',
      short_description: item.short_description || '',
      price: item.price || '0.01',
      stock_quantity: item.stock_quantity ?? '0',
      status: item.status || 'draft',
      show_price: Boolean(item.show_price),
    });
  };

  const saveItem = async (event) => {
    event.preventDefault();
    if (saving) return;
    setSaving(true);
    setNotice(null);
    setErrorInfo(null);
    const payload = {
      ...form,
      price: form.price || '0.01',
      stock_quantity: form.stock_quantity || 0,
    };
    try {
      if (selectedItem) {
        await quotationAPI.items.update(selectedItem.id, payload);
      } else {
        await quotationAPI.items.create(payload);
      }
      setNotice({ type: 'success', message: selectedItem ? 'Product item updated.' : 'Internal quotation product created.' });
      reset();
      await load();
    } catch (error) {
      const details = await describeQuotationError(
        error,
        selectedItem ? 'Update quotation product' : 'Create quotation product',
        selectedItem ? `PATCH /quotations/items/${selectedItem.id}/` : 'POST /quotations/items/'
      );
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  const deleteOrArchive = async () => {
    if (!selectedItem || saving) return;
    if (!window.confirm(`Delete or archive "${selectedItem.name}"? Used products are archived so old quotations remain readable.`)) return;
    setSaving(true);
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.items.delete(selectedItem.id);
      const archived = response.status === 200;
      setNotice({
        type: 'success',
        message: archived ? 'Product was archived because it has history.' : 'Unused product was deleted.',
      });
      reset();
      await load();
    } catch (error) {
      const details = await describeQuotationError(error, 'Delete/archive quotation product', `DELETE /quotations/items/${selectedItem.id}/`);
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="qm-section">
      <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
      {notice && <div className={`qm-feedback ${notice.type}`}>{notice.message}</div>}
      <div className="qm-helper">
        Products are now the master item catalog. Draft products are internal quotation items and are hidden from the public website.
      </div>
      <div className="qm-split">
        <div className="qm-panel">
          <div className="qm-panel-heading">
            <h3>Products / Items</h3>
            <input className="qm-input" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search product items" />
          </div>
          {loading ? (
            <div className="qm-loading">Loading products...</div>
          ) : (
            <div className="qm-table-wrap">
              <table className="qm-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>SKU</th>
                    <th>Dosage</th>
                    <th>Pack</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredItems.map((item) => (
                    <tr key={item.id} className={selectedItem?.id === item.id ? 'selected' : ''} onClick={() => editItem(item)}>
                      <td>{item.name}</td>
                      <td>{item.sku || '-'}</td>
                      <td>{item.dosage || '-'}</td>
                      <td>{item.pack_size || '-'}</td>
                      <td><span className={`qm-badge status-${item.status}`}>{item.status}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="qm-panel">
          <div className="qm-panel-heading">
            <div>
              <h3>{selectedItem ? 'Edit Product Item' : 'New Internal Product Item'}</h3>
              <p>Use Draft for quotation-only/internal items. Use Active only when it should appear publicly.</p>
            </div>
            {selectedItem && <button type="button" className="qm-secondary" onClick={reset}>New</button>}
          </div>
          <form onSubmit={saveItem} className="qm-form">
            <label>Product Name<input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
            <div className="qm-grid-two">
              <label>SKU<input value={form.sku} onChange={(event) => setForm({ ...form, sku: event.target.value })} /></label>
              <label>Barcode<input value={form.barcode} onChange={(event) => setForm({ ...form, barcode: event.target.value })} /></label>
              <label>Dosage / Strength<input value={form.dosage} onChange={(event) => setForm({ ...form, dosage: event.target.value })} /></label>
              <label>Pack / Unit<input value={form.pack_size} onChange={(event) => setForm({ ...form, pack_size: event.target.value })} /></label>
              <label>Active Ingredient<input value={form.active_ingredient} onChange={(event) => setForm({ ...form, active_ingredient: event.target.value })} /></label>
              <label>Status
                <select value={form.status} onChange={(event) => setForm({ ...form, status: event.target.value })}>
                  <option value="draft">Draft / Internal</option>
                  <option value="active">Active / Public</option>
                  <option value="archived">Archived</option>
                </select>
              </label>
              <label>Base Public Price<input type="number" min="0.01" step="0.01" value={form.price} onChange={(event) => setForm({ ...form, price: event.target.value })} /></label>
              <label>Stock<input type="number" min="0" step="1" value={form.stock_quantity} onChange={(event) => setForm({ ...form, stock_quantity: event.target.value })} /></label>
            </div>
            <label>Description<textarea rows="3" value={form.short_description} onChange={(event) => setForm({ ...form, short_description: event.target.value })} /></label>
            <label className="qm-checkbox"><input type="checkbox" checked={form.show_price} onChange={(event) => setForm({ ...form, show_price: event.target.checked })} /> Show public price when active</label>
            <div className="qm-action-row">
              <button type="submit" className="qm-primary" disabled={saving}>{saving ? 'Saving...' : 'Save Product Item'}</button>
              {selectedItem && (
                <button type="button" className="qm-secondary danger" disabled={saving} onClick={deleteOrArchive}>
                  Delete / Archive
                </button>
              )}
            </div>
          </form>
        </div>
      </div>
    </div>
  );
};

export default QuoteItemManager;
