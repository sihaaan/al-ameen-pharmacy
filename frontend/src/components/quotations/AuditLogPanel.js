import React, { useEffect, useMemo, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import QuotationErrorNotice from './QuotationErrorNotice';

const actionOptions = [
  ['created', 'Created'],
  ['updated', 'Updated'],
  ['deleted', 'Deleted'],
  ['status_changed', 'Status Changed'],
  ['finalized', 'Finalized'],
  ['revised', 'Revised'],
  ['pdf_downloaded', 'PDF Downloaded'],
  ['imported', 'Imported'],
];

const formatDateTime = (value) => {
  if (!value) return '-';
  return new Date(value).toLocaleString('en-AE', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
};

const titleizeTarget = (targetType = '') => (
  targetType
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/^Quotation$/, 'Quotation')
    .trim() || 'Record'
);

const targetLabel = (log) => {
  if (log.quotation_number) return log.quotation_number;
  if (log.company_name) return log.company_name;
  const target = titleizeTarget(log.target_type);
  return log.target_id ? `${target} #${log.target_id}` : target;
};

const compactMessage = (message = '') => {
  const cleaned = message.trim();
  if (!cleaned) return '-';
  return cleaned.length > 160 ? `${cleaned.slice(0, 157)}...` : cleaned;
};

const AuditLogPanel = ({ quotationId = '', companyId = '' }) => {
  const [logs, setLogs] = useState([]);
  const [search, setSearch] = useState('');
  const [actionFilter, setActionFilter] = useState('');
  const [importantOnly, setImportantOnly] = useState(true);
  const [loading, setLoading] = useState(true);
  const [errorInfo, setErrorInfo] = useState(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      setErrorInfo(null);
      try {
        const response = await quotationAPI.auditLogs.list({
          quotation: quotationId || undefined,
          company: companyId || undefined,
          action: actionFilter || undefined,
          search: search.trim() || undefined,
          important: importantOnly ? 'true' : undefined,
          limit: importantOnly ? 150 : 300,
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
  }, [quotationId, companyId, actionFilter, importantOnly, reloadKey, search]);

  const visibleLogs = useMemo(() => {
    const seen = new Set();
    return logs.filter((log) => {
      const minute = log.created_at ? new Date(log.created_at).toISOString().slice(0, 16) : '';
      const key = [
        minute,
        log.actor_username || 'System',
        log.action,
        log.target_type,
        log.target_id || '',
        log.quotation_number || '',
        log.company_name || '',
        log.message || '',
      ].join('|');
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [logs]);

  return (
    <div className="qm-panel">
      <div className="qm-panel-heading">
        <div>
          <h3>Audit Logs</h3>
          <p>Important activity is shown by default. Turn off the filter to inspect every technical event.</p>
        </div>
        <button type="button" className="qm-secondary small" onClick={() => setReloadKey((value) => value + 1)}>
          Refresh
        </button>
      </div>
      <div className="qm-audit-filters">
        <input
          className="qm-input"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search user, company, quote, message"
        />
        <select className="qm-input compact" value={actionFilter} onChange={(event) => setActionFilter(event.target.value)}>
          <option value="">All actions</option>
          {actionOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <label className="qm-audit-toggle">
          <input type="checkbox" checked={importantOnly} onChange={(event) => setImportantOnly(event.target.checked)} />
          Important activity only
        </label>
      </div>
      <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
      {loading ? (
        <div className="qm-loading">Loading audit logs...</div>
      ) : visibleLogs.length === 0 ? (
        <div className="qm-empty">No audit activity matches these filters.</div>
      ) : (
        <div className="qm-table-wrap compact">
          <table className="qm-table qm-audit-table">
            <thead><tr><th>When</th><th>User</th><th>Action</th><th>Record</th><th>Summary</th></tr></thead>
            <tbody>
              {visibleLogs.map((log) => (
                <tr key={log.id}>
                  <td className="qm-nowrap">{formatDateTime(log.created_at)}</td>
                  <td>{log.actor_username || 'System'}</td>
                  <td><span className="qm-badge muted">{log.action_display || log.action}</span></td>
                  <td>
                    <strong>{targetLabel(log)}</strong>
                    {log.target_type && <small>{titleizeTarget(log.target_type)}</small>}
                  </td>
                  <td>{compactMessage(log.message)}</td>
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
