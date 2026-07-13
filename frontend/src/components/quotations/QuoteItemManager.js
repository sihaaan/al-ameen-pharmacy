import React, { useEffect, useMemo, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import QuotationErrorNotice from './QuotationErrorNotice';
import ProductFormModal from '../ProductFormModal';

const QuoteItemManager = () => {
  const [items, setItems] = useState([]);
  const [selectedItem, setSelectedItem] = useState(null);
  const [editingProduct, setEditingProduct] = useState(null);
  const [showProductModal, setShowProductModal] = useState(false);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState(null);
  const [errorInfo, setErrorInfo] = useState(null);

  const load = async (selectedId = null) => {
    setLoading(true);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.items.list();
      setItems(response.data);
      if (selectedId) {
        setSelectedItem(response.data.find((item) => String(item.id) === String(selectedId)) || null);
      }
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

  const openNewProduct = () => {
    setEditingProduct(null);
    setShowProductModal(true);
  };

  const editItem = (item) => {
    setEditingProduct(item);
    setSelectedItem(item);
    setNotice(null);
    setShowProductModal(true);
  };

  const closeProductModal = () => {
    setShowProductModal(false);
    setEditingProduct(null);
  };

  const handleProductSaved = async (product) => {
    setNotice({ type: 'success', message: product?.status === 'active' ? 'Product saved and visible publicly.' : 'Draft/internal product saved for quotations.' });
    await load(product?.id || null);
  };

  const deleteOrArchive = async (item = selectedItem) => {
    if (!item || saving) return;
    if (!window.confirm(`Delete or archive "${item.name}"? Used products are archived so old quotations remain readable.`)) return;
    setSaving(true);
    setNotice(null);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.items.delete(item.id);
      const archived = response.status === 200;
      setNotice({
        type: 'success',
        message: archived ? 'Product was archived because it has history.' : 'Unused product was deleted.',
      });
      setSelectedItem(null);
      await load();
    } catch (error) {
      const details = await describeQuotationError(error, 'Delete/archive quotation product', `DELETE /quotations/items/${item.id}/`);
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
            <div className="qm-controls">
              <input className="qm-input" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search product items" />
              <button type="button" className="qm-primary" onClick={openNewProduct}>Add Internal Product</button>
            </div>
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
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredItems.map((item) => (
                    <tr key={item.id} className={selectedItem?.id === item.id ? 'selected' : ''} onClick={() => setSelectedItem(item)}>
                      <td>{item.name}</td>
                      <td>{item.sku || '-'}</td>
                      <td>{item.dosage || '-'}</td>
                      <td>{item.pack_size || '-'}</td>
                      <td><span className={`qm-badge status-${item.status}`}>{item.status}</span></td>
                      <td className="qm-row-actions" onClick={(event) => event.stopPropagation()}>
                        <button type="button" className="qm-secondary small" onClick={() => editItem(item)}>Edit</button>
                        <button type="button" className="qm-secondary small danger" disabled={saving} onClick={() => deleteOrArchive(item)}>Delete / Archive</button>
                      </td>
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
              <h3>Product Actions</h3>
              <p>Use the shared product form here. Draft products can be used in quotations but are hidden from the public website.</p>
            </div>
          </div>
          <div className="qm-subpanel">
            {selectedItem ? (
              <>
                <h4>{selectedItem.name}</h4>
                <p>{selectedItem.short_description || 'No short description entered.'}</p>
                <div className="qm-action-row">
                  <button type="button" className="qm-primary" onClick={() => editItem(selectedItem)}>Edit Product</button>
                  <button type="button" className="qm-secondary danger" disabled={saving} onClick={deleteOrArchive}>
                    {saving ? 'Working...' : 'Delete / Archive'}
                  </button>
                </div>
              </>
            ) : (
              <div className="qm-empty compact">Select a product to edit, or add a new internal product for quotation use.</div>
            )}
            <div className="qm-action-row">
              <button type="button" className="qm-secondary" onClick={openNewProduct}>Add Internal Product</button>
            </div>
          </div>
        </div>
      </div>
      <ProductFormModal
        isOpen={showProductModal}
        product={editingProduct}
        onClose={closeProductModal}
        onSaved={handleProductSaved}
        defaultStatus="draft"
        createTitle="Add Internal Product"
        contextHelpText="Draft products can be used in quotations but are hidden from the public website. Change status to Active only when this product should appear publicly."
      />
    </div>
  );
};

export default QuoteItemManager;
