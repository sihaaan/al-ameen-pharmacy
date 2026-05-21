import React, { useEffect, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import QuotationErrorNotice from './QuotationErrorNotice';

const AuditLogPanel = ({ quotationId = '', companyId = '' }) => {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [errorInfo, setErrorInfo] = useState(null);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      setErrorInfo(null);
      try {
        const response = await quotationAPI.auditLogs.list({
          quotation: quotationId || undefined,
          company: companyId || undefined,
        });
        setLogs(response.data);
      } catch (error) {
        const details = await describeQuotationError(error, 'Load audit logs', 'GET /quotations/audit-logs/');
        setErrorInfo(details);
        console.error(formatQuotationError(details), error);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [quotationId, companyId]);

  return (
    <div className="qm-panel">
      <h3>Audit Logs</h3>
      <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
      {loading ? (
        <div className="qm-loading">Loading audit logs...</div>
      ) : (
        <div className="qm-table-wrap">
          <table className="qm-table">
            <thead><tr><th>When</th><th>User</th><th>Action</th><th>Target</th><th>Message</th></tr></thead>
            <tbody>
              {logs.map((log) => (
                <tr key={log.id}>
                  <td>{new Date(log.created_at).toLocaleString('en-AE')}</td>
                  <td>{log.actor_username || 'System'}</td>
                  <td>{log.action}</td>
                  <td>{log.quotation_number || log.company_name || `${log.target_type} ${log.target_id || ''}`}</td>
                  <td>{log.message || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default AuditLogPanel;
