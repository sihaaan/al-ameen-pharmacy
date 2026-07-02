import React, { useCallback, useEffect, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import CompanySelectWithCreate from './CompanySelectWithCreate';
import QuotationErrorNotice from './QuotationErrorNotice';

const formatUnitMoney = (value, currency = 'AED') => `${currency || 'AED'} ${Number(value || 0).toLocaleString(undefined, {
  minimumFractionDigits: 2,
  maximumFractionDigits: 3,
})}`;

const PriceHistoryPanel = ({ companyId = '', itemId = '' }) => {
  const [history, setHistory] = useState([]);
  const [companies, setCompanies] = useState([]);
  const [items, setItems] = useState([]);
  const [filters, setFilters] = useState({ company: companyId, item: itemId });
  const [loading, setLoading] = useState(true);
  const [errorInfo, setErrorInfo] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErrorInfo(null);
    try {
      const [historyRes, companiesRes, itemsRes] = await Promise.all([
        quotationAPI.priceHistory.list({
          company: filters.company || undefined,
          item: filters.item || undefined,
        }),
        quotationAPI.companies.list(),
        quotationAPI.items.list(),
      ]);
      setHistory(historyRes.data);
      setCompanies(companiesRes.data);
      setItems(itemsRes.data);
    } catch (error) {
      const details = await describeQuotationError(
        error,
        'Load price history',
        'GET /quotations/price-history/, /quotations/companies/, /quotations/items/'
      );
      setErrorInfo(details);
      console.error(formatQuotationError(details), error);
    } finally {
      setLoading(false);
    }
  }, [filters.company, filters.item]);

  useEffect(() => {
    load();
  }, [load]);

  const rememberCompany = (company) => {
    setCompanies((current) => {
      const withoutDuplicate = current.filter((candidate) => candidate.id !== company.id);
      return [...withoutDuplicate, company].sort((a, b) => a.name.localeCompare(b.name));
    });
  };

  return (
    <div className="qm-panel">
      <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
      <div className="qm-panel-heading">
        <h3>Price History</h3>
        <div className="qm-controls">
          <CompanySelectWithCreate
            companies={companies}
            value={filters.company}
            label="Company filter"
            placeholder="All companies"
            onChange={(companyId) => setFilters({ ...filters, company: companyId })}
            onCreated={(company) => {
              rememberCompany(company);
              setFilters((current) => ({ ...current, company: String(company.id) }));
            }}
          />
          <select className="qm-input" value={filters.item} onChange={(event) => setFilters({ ...filters, item: event.target.value })}>
            <option value="">All items</option>
            {items.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </div>
      </div>
      {loading ? (
        <div className="qm-loading">Loading price history...</div>
      ) : (
        <div className="qm-table-wrap">
          <table className="qm-table">
            <thead><tr><th>Company</th><th>Item</th><th>Price</th><th>Qty</th><th>Quote</th><th>Date</th></tr></thead>
            <tbody>
              {history.map((row) => (
                <tr key={row.id}>
                  <td>{row.company_name}</td>
                  <td>{row.product_name || row.quote_item_name}</td>
                  <td>{formatUnitMoney(row.unit_price, row.currency)}</td>
                  <td>{parseFloat(row.quantity).toString()} {row.unit}</td>
                  <td>{row.quotation_number}</td>
                  <td>{new Date(row.quoted_at).toLocaleDateString('en-AE')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default PriceHistoryPanel;
