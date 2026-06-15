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
  const [analysis, setAnalysis] = useState(null);
  const [followups, setFollowups] = useState(null);
  const [loading, setLoading] = useState(true);
  const [errorInfo, setErrorInfo] = useState(null);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      setErrorInfo(null);
      try {
        const [statsResponse, analysisResponse, followupResponse] = await Promise.all([
          quotationAPI.dashboard.retrieve(),
          quotationAPI.dashboard.analysis(),
          quotationAPI.followups.list(),
        ]);
        setStats(statsResponse.data);
        setAnalysis(analysisResponse.data);
        setFollowups(followupResponse.data);
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
      {analysis && (
        <>
          <div className="qm-panel">
            <div className="qm-panel-heading">
              <div>
                <h3>Revenue analysis</h3>
                <p>Outcome tracking shows what was quoted, accepted, lost, and still pending.</p>
              </div>
            </div>
            <div className="qm-stat-grid">
              <div className="qm-stat"><span>AED {Number(analysis.cards.total_quoted_value || 0).toFixed(2)}</span><p>Total quoted</p></div>
              <div className="qm-stat success"><span>AED {Number(analysis.cards.accepted_value || 0).toFixed(2)}</span><p>Accepted value</p></div>
              <div className="qm-stat warning"><span>AED {Number(analysis.cards.lost_value || 0).toFixed(2)}</span><p>Lost value</p></div>
              <div className="qm-stat"><span>{Number(analysis.cards.value_win_rate || 0).toFixed(1)}%</span><p>Value win rate</p></div>
              <div className="qm-stat"><span>{Number(analysis.cards.line_win_rate || 0).toFixed(1)}%</span><p>Line win rate</p></div>
              <div className="qm-stat warning"><span>{analysis.cards.overdue_followups || 0}</span><p>Overdue follow-ups</p></div>
            </div>
          </div>
          <div className="qm-grid-two bottom-panels">
            <div className="qm-panel">
              <h3>Lost value by reason</h3>
              <div className="qm-table-wrap compact">
                <table className="qm-table">
                  <thead><tr><th>Reason</th><th>Lines</th><th>Lost</th></tr></thead>
                  <tbody>
                    {(analysis.tables.lost_value_by_reason || []).slice(0, 8).map((row) => (
                      <tr key={row.reason || 'blank'}><td>{row.reason_display}</td><td>{row.lines}</td><td>AED {Number(row.lost_value || 0).toFixed(2)}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
            <div className="qm-panel">
              <h3>Pending value by customer</h3>
              <div className="qm-table-wrap compact">
                <table className="qm-table">
                  <thead><tr><th>Customer</th><th>Quotes</th><th>Pending</th></tr></thead>
                  <tbody>
                    {(analysis.tables.pending_value_by_customer || []).slice(0, 8).map((row) => (
                      <tr key={row.company_id || row.company_name}><td>{row.company_name}</td><td>{row.quotes}</td><td>AED {Number(row.pending_value || 0).toFixed(2)}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </>
      )}
      {followups && (
        <div className="qm-panel">
          <div className="qm-panel-heading">
            <div>
              <h3>Follow-up priorities</h3>
              <p>Sent quotations with no outcome, due reminders, and high-value pending work.</p>
            </div>
          </div>
          <div className="qm-grid-two bottom-panels">
            <div>
              <h4>Due or overdue</h4>
              <ul className="qm-compact-list">
                {[...(followups.overdue || []), ...(followups.due_today || [])].slice(0, 8).map((quote) => (
                  <li key={quote.id}>{quote.quotation_number} - {quote.company_name} - AED {Number(quote.total || 0).toFixed(2)}</li>
                ))}
              </ul>
            </div>
            <div>
              <h4>No outcome after 7 days</h4>
              <ul className="qm-compact-list">
                {(followups.sent_no_outcome_after_7_days || []).slice(0, 8).map((quote) => (
                  <li key={quote.id}>{quote.quotation_number} - {quote.company_name} - AED {Number(quote.total || 0).toFixed(2)}</li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      )}
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
