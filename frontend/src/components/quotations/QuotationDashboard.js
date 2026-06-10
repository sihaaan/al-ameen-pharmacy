import React, { useEffect, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import QuotationErrorNotice from './QuotationErrorNotice';

const QuotationDashboard = ({ onOpenQuotes }) => {
  const [stats, setStats] = useState({
    companies: 0,
    items: 0,
    inquiries: 0,
    quotes: 0,
    pending: 0,
    finalized: 0,
  });
  const [loading, setLoading] = useState(true);
  const [errorInfo, setErrorInfo] = useState(null);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      setErrorInfo(null);
      try {
        const response = await quotationAPI.dashboard.retrieve();
        setStats(response.data);
      } catch (error) {
        const details = await describeQuotationError(
          error,
          'Load quotation dashboard',
          'GET /quotations/dashboard/'
        );
        setErrorInfo(details);
        console.error(formatQuotationError(details), error);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  if (loading) return <div className="qm-loading">Loading quotation dashboard...</div>;

  return (
    <div className="qm-dashboard">
      <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
      <div className="qm-stat-grid">
        <div className="qm-stat"><span>{stats.companies}</span><p>Companies</p></div>
        <div className="qm-stat"><span>{stats.items}</span><p>Products / Items</p></div>
        <div className="qm-stat"><span>{stats.inquiries}</span><p>Inquiries</p></div>
        <div className="qm-stat"><span>{stats.quotes}</span><p>Quotations</p></div>
        <div className="qm-stat warning"><span>{stats.pending}</span><p>Needs Action</p></div>
        <div className="qm-stat success"><span>{stats.finalized}</span><p>Finalized/Sent</p></div>
      </div>
      <div className="qm-panel">
        <div>
          <h3>Daily workflow</h3>
          <p>Create or select a company, enter a manual inquiry, match each line to a product, review previous prices, then finalize and download the PDF.</p>
        </div>
        <button type="button" className="qm-primary" onClick={onOpenQuotes}>Open Quotations</button>
      </div>
    </div>
  );
};

export default QuotationDashboard;
