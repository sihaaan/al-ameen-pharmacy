import React, { useEffect, useMemo, useState } from 'react';
import quotationAPI, { describeQuotationError, formatQuotationError } from '../../api/quotations';
import QuotationErrorNotice from './QuotationErrorNotice';

const emptyRunForm = {
  company: '',
  target_company_name: 'ALEC',
  gmail_query: '',
  sender_domain_hint: 'alec.ae',
  date_from: '',
  date_to: '',
  max_messages: 5000,
  discovery_batch_size: 25,
  include_attachments: true,
};

const statusLabel = {
  draft: 'Draft',
  discovering: 'Discovering',
  ready: 'Ready',
  analyzing: 'Analyzing',
  review: 'Review',
  failed: 'Failed',
};

const classificationLabel = {
  inquiry: 'Inquiry',
  quotation: 'Quotation',
  lpo: 'LPO',
  followup: 'Follow-up',
  irrelevant: 'Irrelevant',
  unknown: 'Unknown',
};

const itemStatusLabel = {
  suggested: 'Suggested',
  approved: 'Approved',
  rejected: 'Rejected',
  needs_review: 'Needs review',
};

const discoveryStopLabel = {
  can_continue: 'Discovery can continue',
  message_cap_reached: 'Message cap reached',
  gmail_exhausted: 'Gmail has no more matching messages',
};

const formatDate = (value) => {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString('en-AE');
};

const formatDateTime = (value) => {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('en-AE');
};

const percent = (value) => `${Math.round(Number(value || 0) * 100)}%`;

const ContractIntelligenceManager = () => {
  const [gmail, setGmail] = useState(null);
  const [companies, setCompanies] = useState([]);
  const [runs, setRuns] = useState([]);
  const [selectedRun, setSelectedRun] = useState(null);
  const [sources, setSources] = useState([]);
  const [items, setItems] = useState([]);
  const [form, setForm] = useState(emptyRunForm);
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState('');
  const [errorInfo, setErrorInfo] = useState(null);
  const [notice, setNotice] = useState('');
  const [itemFilter, setItemFilter] = useState('');
  const [sourceSort, setSourceSort] = useState('newest');
  const [deleteConfirmRun, setDeleteConfirmRun] = useState(null);

  const selectedSummary = selectedRun?.summary || {};
  const selectedBatchSize = Number(selectedRun?.discovery_batch_size || 25);
  const selectedMaxMessages = Number(selectedRun?.max_messages || 0);

  const visibleItems = useMemo(() => {
    const term = itemFilter.trim().toLowerCase();
    if (!term) return items;
    return items.filter((item) => (
      (item.suggested_item_name || '').toLowerCase().includes(term) ||
      (item.original_item_name || '').toLowerCase().includes(term) ||
      (item.product_name || '').toLowerCase().includes(term) ||
      (item.source_subject || '').toLowerCase().includes(term)
    ));
  }, [items, itemFilter]);

  const visibleSources = useMemo(() => (
    [...sources].sort((left, right) => {
      const leftTime = new Date(left.sent_at || left.created_at || 0).getTime() || 0;
      const rightTime = new Date(right.sent_at || right.created_at || 0).getTime() || 0;
      return sourceSort === 'oldest' ? leftTime - rightTime : rightTime - leftTime;
    })
  ), [sources, sourceSort]);

  const handleError = async (error, action, endpoint) => {
    const details = await describeQuotationError(error, action, endpoint);
    setErrorInfo(details);
    console.error(formatQuotationError(details), error);
  };

  const loadRuns = async () => {
    const response = await quotationAPI.contractIntelligence.runs();
    setRuns(response.data);
    return response.data;
  };

  const loadGmail = async () => {
    const response = await quotationAPI.gmail.status();
    setGmail(response.data);
    return response.data;
  };

  const loadInitial = async () => {
    setLoading(true);
    setErrorInfo(null);
    try {
      const [gmailRes, companiesRes, runsRes] = await Promise.all([
        loadGmail(),
        quotationAPI.companies.list({ active: 'true' }),
        loadRuns(),
      ]);
      setGmail(gmailRes);
      setCompanies(companiesRes.data);
      if (!selectedRun && runsRes.length) {
        await openRun(runsRes[0].id);
      }
    } catch (error) {
      await handleError(error, 'Load contract intelligence', 'GET Gmail status, companies, and contract intelligence runs');
    } finally {
      setLoading(false);
    }
  };

  const openRun = async (runId) => {
    setBusyAction(`open-${runId}`);
    setErrorInfo(null);
    try {
      const [runRes, sourceRes, itemRes] = await Promise.all([
        quotationAPI.contractIntelligence.retrieveRun(runId),
        quotationAPI.contractIntelligence.sources(runId),
        quotationAPI.contractIntelligence.items(runId),
      ]);
      setSelectedRun(runRes.data);
      setSources(sourceRes.data);
      setItems(itemRes.data);
    } catch (error) {
      await handleError(error, 'Open contract intelligence run', `GET /quotations/contract-intelligence-runs/${runId}/`);
    } finally {
      setBusyAction('');
    }
  };

  const refreshSelectedRun = async (runId = selectedRun?.id) => {
    if (!runId) return;
    await openRun(runId);
    await loadRuns();
  };

  useEffect(() => {
    loadInitial();
    const params = new URLSearchParams(window.location.search);
    const gmailStatus = params.get('gmail');
    if (gmailStatus) {
      setNotice(gmailStatus === 'connected' ? 'Gmail connected successfully.' : `Gmail connection status: ${gmailStatus}`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const createRun = async (event) => {
    event.preventDefault();
    setBusyAction('create-run');
    setNotice('');
    setErrorInfo(null);
    try {
      const response = await quotationAPI.contractIntelligence.createRun({
        ...form,
        company: form.company || null,
      });
      setNotice('Contract intelligence run created. Use Discover Gmail to collect matching emails.');
      setForm(emptyRunForm);
      await loadRuns();
      await openRun(response.data.id);
    } catch (error) {
      await handleError(error, 'Create contract intelligence run', 'POST /quotations/contract-intelligence-runs/');
    } finally {
      setBusyAction('');
    }
  };

  const connectGmail = async () => {
    setBusyAction('gmail-connect');
    setErrorInfo(null);
    try {
      const response = await quotationAPI.gmail.connectUrl();
      window.location.href = response.data.auth_url;
    } catch (error) {
      await handleError(error, 'Start Gmail connection', 'POST /quotations/gmail/connection/');
      setBusyAction('');
    }
  };

  const disconnectGmail = async () => {
    setBusyAction('gmail-disconnect');
    setErrorInfo(null);
    try {
      await quotationAPI.gmail.disconnect();
      setNotice('Gmail disconnected.');
      await loadGmail();
    } catch (error) {
      await handleError(error, 'Disconnect Gmail', 'DELETE /quotations/gmail/connection/');
    } finally {
      setBusyAction('');
    }
  };

  const discover = async ({ resetCursor = false } = {}) => {
    if (!selectedRun) return;
    setBusyAction('discover');
    setNotice('');
    setErrorInfo(null);
    try {
      const response = await quotationAPI.contractIntelligence.discover(selectedRun.id, {
        batch_size: selectedRun.discovery_batch_size || 25,
        reset_cursor: resetCursor,
      });
      const exhaustedText = response.data.result.discovery_exhausted
        ? ' Gmail has no more matching messages for this run.'
        : ' More matching messages may be available; discover the next batch when ready.';
      setNotice(`Discovery batch complete: ${response.data.result.created} new candidate email(s), ${response.data.result.reused} already tracked, ${response.data.result.failed} failed.${exhaustedText}`);
      await refreshSelectedRun(selectedRun.id);
    } catch (error) {
      await handleError(error, 'Discover Gmail contract emails', `POST /quotations/contract-intelligence-runs/${selectedRun.id}/discover/`);
    } finally {
      setBusyAction('');
    }
  };

  const discoverAll = async ({ resetCursor = false } = {}) => {
    if (!selectedRun) return;
    setBusyAction('discover-all');
    setNotice('');
    setErrorInfo(null);
    try {
      const knownSources = Number(selectedSummary.sources || 0);
      const remainingMessages = selectedMaxMessages ? Math.max(selectedMaxMessages - knownSources, selectedBatchSize) : selectedBatchSize * 50;
      const maxBatches = Math.min(Math.ceil(remainingMessages / selectedBatchSize) || 1, 100);
      const totals = {
        batches: 0,
        created: 0,
        reused: 0,
        failed: 0,
        discovery_exhausted: false,
      };
      for (let batchIndex = 0; batchIndex < maxBatches; batchIndex += 1) {
        setNotice(`Discovering Gmail batch ${batchIndex + 1} of up to ${maxBatches}...`);
        const response = await quotationAPI.contractIntelligence.discover(selectedRun.id, {
          batch_size: selectedBatchSize,
          reset_cursor: resetCursor && batchIndex === 0,
        });
        const result = response.data.result || {};
        totals.batches += 1;
        totals.created += Number(result.created || 0);
        totals.reused += Number(result.reused || 0);
        totals.failed += Number(result.failed || 0);
        totals.discovery_exhausted = Boolean(result.discovery_exhausted);
        if (totals.discovery_exhausted || !result.next_page_token) {
          break;
        }
      }
      const exhaustedText = totals.discovery_exhausted
        ? ' Gmail has no more matching messages for this run.'
        : ' Discovery stopped at the safety cap; run it again to continue if needed.';
      setNotice(`Full discovery complete: ${totals.batches} batch(es), ${totals.created} new candidate email(s), ${totals.reused} already tracked, ${totals.failed} failed.${exhaustedText}`);
      await refreshSelectedRun(selectedRun.id);
    } catch (error) {
      await handleError(error, 'Run full Gmail discovery', `POST /quotations/contract-intelligence-runs/${selectedRun.id}/discover/`);
    } finally {
      setBusyAction('');
    }
  };

  const analyze = async (useAI = true) => {
    if (!selectedRun) return;
    setBusyAction(useAI ? 'analyze-ai' : 'analyze-basic');
    setNotice('');
    setErrorInfo(null);
    try {
      const response = await quotationAPI.contractIntelligence.analyze(selectedRun.id, {
        use_ai: useAI,
        source_limit: selectedRun.discovery_batch_size || 25,
      });
      setNotice(`Analysis batch complete: ${response.data.result.items_created} item row(s) extracted from ${response.data.result.sources_analyzed} source(s). ${response.data.result.pending_sources} source(s) still waiting.`);
      await refreshSelectedRun(selectedRun.id);
    } catch (error) {
      await handleError(error, 'Analyze contract emails', `POST /quotations/contract-intelligence-runs/${selectedRun.id}/analyze/`);
    } finally {
      setBusyAction('');
    }
  };

  const analyzeAll = async (useAI = true) => {
    if (!selectedRun) return;
    const action = useAI ? 'analyze-all-ai' : 'analyze-all-basic';
    setBusyAction(action);
    setNotice('');
    setErrorInfo(null);
    try {
      const pendingSources = Math.max(Number(selectedSummary.sources_candidate || 0), Number(sources.filter((source) => source.status !== 'analyzed').length || 0));
      const maxBatches = Math.min(Math.ceil((pendingSources || selectedBatchSize) / selectedBatchSize) || 1, 100);
      const totals = {
        batches: 0,
        sources_analyzed: 0,
        items_created: 0,
        pending_sources: pendingSources,
      };
      for (let batchIndex = 0; batchIndex < maxBatches; batchIndex += 1) {
        setNotice(`${useAI ? 'AI analyzing' : 'Analyzing'} batch ${batchIndex + 1} of up to ${maxBatches}...`);
        const response = await quotationAPI.contractIntelligence.analyze(selectedRun.id, {
          use_ai: useAI,
          source_limit: selectedBatchSize,
        });
        const result = response.data.result || {};
        const analyzedThisBatch = Number(result.sources_analyzed || 0);
        totals.batches += 1;
        totals.sources_analyzed += analyzedThisBatch;
        totals.items_created += Number(result.items_created || 0);
        totals.pending_sources = Number(result.pending_sources || 0);
        if (totals.pending_sources <= 0 || analyzedThisBatch <= 0) {
          break;
        }
      }
      setNotice(`Full analysis complete: ${totals.items_created} item row(s) extracted from ${totals.sources_analyzed} source(s) across ${totals.batches} batch(es). ${totals.pending_sources || 0} source(s) still waiting.`);
      await refreshSelectedRun(selectedRun.id);
    } catch (error) {
      await handleError(error, 'Run full contract email analysis', `POST /quotations/contract-intelligence-runs/${selectedRun.id}/analyze/`);
    } finally {
      setBusyAction('');
    }
  };

  const cleanExtractedRows = async () => {
    if (!selectedRun) return;
    const runId = selectedRun.id;
    setBusyAction('clean-items');
    setNotice('');
    setErrorInfo(null);
    try {
      const response = await quotationAPI.contractIntelligence.cleanItems(runId);
      const result = response.data.result || {};
      setNotice(
        `Cleaned extracted rows: ${result.updated || 0} item name(s) improved, `
        + `${result.noise_rejected || 0} metadata/noise row(s) hidden, `
        + `${result.skipped_approved || 0} approved row(s) left untouched.`
      );
      await refreshSelectedRun(runId);
    } catch (error) {
      await handleError(error, 'Clean contract intelligence rows', `POST /quotations/contract-intelligence-runs/${runId}/clean_items/`);
    } finally {
      setBusyAction('');
    }
  };

  const deleteRun = async () => {
    if (!deleteConfirmRun) return;
    setBusyAction('delete-run');
    setNotice('');
    setErrorInfo(null);
    try {
      await quotationAPI.contractIntelligence.deleteRun(deleteConfirmRun.id);
      setDeleteConfirmRun(null);
      setSelectedRun(null);
      setSources([]);
      setItems([]);
      const nextRuns = await loadRuns();
      if (nextRuns.length) {
        await openRun(nextRuns[0].id);
      } else {
        setNotice('Research run deleted. Start a fresh run when you are ready.');
      }
    } catch (error) {
      await handleError(error, 'Delete contract intelligence run', `DELETE /quotations/contract-intelligence-runs/${deleteConfirmRun.id}/`);
    } finally {
      setBusyAction('');
    }
  };

  const exportRun = async () => {
    if (!selectedRun) return;
    setBusyAction('export');
    setErrorInfo(null);
    try {
      const response = await quotationAPI.contractIntelligence.export(selectedRun.id);
      const blob = new Blob([response.data], {
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `${(selectedRun.target_company_name || 'contract').replace(/[^a-z0-9]+/gi, '_')}_contract_intelligence.xlsx`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      await handleError(error, 'Export contract intelligence', `GET /quotations/contract-intelligence-runs/${selectedRun.id}/export/`);
    } finally {
      setBusyAction('');
    }
  };

  const updateItem = async (itemId, changes) => {
    setBusyAction(`item-${itemId}`);
    setErrorInfo(null);
    try {
      const response = await quotationAPI.contractIntelligence.updateItem(itemId, changes);
      setItems((current) => current.map((item) => (item.id === itemId ? response.data : item)));
    } catch (error) {
      await handleError(error, 'Update contract intelligence item', `PATCH /quotations/contract-intelligence-items/${itemId}/`);
    } finally {
      setBusyAction('');
    }
  };

  if (loading) return <div className="qm-loading">Loading contract intelligence...</div>;

  const gmailConnected = gmail?.connection?.is_connected;
  const gmailConfigured = gmail?.configured;

  return (
    <div className="qm-section qm-contract-intel">
      <QuotationErrorNotice error={errorInfo} onDismiss={() => setErrorInfo(null)} />
      {notice && <div className="qm-helper">{notice}</div>}

      <section className="qm-panel qm-contract-hero">
        <div>
          <span className="qm-step-kicker">Gmail Read-Only + AI</span>
          <h3>ALEC Yearly Contract Intelligence</h3>
          <p>
            Search old pharmacy emails, extract inquiry/quotation/LPO product demand, and export the item intelligence Dad needs for yearly contract pricing.
          </p>
        </div>
        <div className="qm-contract-gmail-card">
          <span>Gmail</span>
          <strong>{gmailConnected ? gmail.connection.email : gmailConfigured ? 'Ready to connect' : 'Not configured'}</strong>
          <small>Scope: gmail.readonly. AI suggestions are review-only.</small>
          <div className="qm-actions">
            {gmailConnected ? (
              <button type="button" className="qm-secondary small" onClick={disconnectGmail} disabled={busyAction === 'gmail-disconnect'}>
                {busyAction === 'gmail-disconnect' ? 'Disconnecting...' : 'Disconnect'}
              </button>
            ) : (
              <button type="button" className="qm-primary small" onClick={connectGmail} disabled={!gmailConfigured || busyAction === 'gmail-connect'}>
                {busyAction === 'gmail-connect' ? 'Opening Google...' : 'Connect Gmail'}
              </button>
            )}
          </div>
          {!gmailConfigured && (
            <p className="qm-field-warning">
              Add GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET, and GOOGLE_OAUTH_REDIRECT_URI on the Railway backend.
            </p>
          )}
        </div>
      </section>

      <div className="qm-contract-layout">
        <aside className="qm-panel qm-contract-sidebar">
          <div className="qm-panel-heading">
            <div>
              <h3>Research Runs</h3>
              <p>Create a focused search session for ALEC or another customer.</p>
            </div>
          </div>

          <form className="qm-form qm-contract-run-form" onSubmit={createRun}>
            <label>
              Customer
              <select
                value={form.company}
                onChange={(event) => {
                  const companyId = event.target.value;
                  const selected = companies.find((company) => String(company.id) === String(companyId));
                  setForm((current) => ({
                    ...current,
                    company: companyId,
                    target_company_name: selected?.name || current.target_company_name,
                  }));
                }}
              >
                <option value="">No linked company</option>
                {companies.map((company) => (
                  <option key={company.id} value={company.id}>{company.name}</option>
                ))}
              </select>
            </label>
            <label>
              Search name
              <input
                value={form.target_company_name}
                onChange={(event) => setForm((current) => ({ ...current, target_company_name: event.target.value }))}
                placeholder="ALEC"
                required
              />
            </label>
            <label>
              Domain hint
              <input
                value={form.sender_domain_hint}
                onChange={(event) => setForm((current) => ({ ...current, sender_domain_hint: event.target.value }))}
                placeholder="alec.ae"
              />
              <small>Optional. Speeds up ALEC searches but does not exclude forwarded or non-domain emails.</small>
            </label>
            <label>
              Gmail query override
              <textarea
                value={form.gmail_query}
                onChange={(event) => setForm((current) => ({ ...current, gmail_query: event.target.value }))}
                placeholder='Optional: from:alec "quotation" after:2018/01/01'
              />
            </label>
            <div className="qm-grid-two">
              <label>
                From
                <input
                  type="date"
                  value={form.date_from}
                  onChange={(event) => setForm((current) => ({ ...current, date_from: event.target.value }))}
                />
              </label>
              <label>
                To
                <input
                  type="date"
                  value={form.date_to}
                  onChange={(event) => setForm((current) => ({ ...current, date_to: event.target.value }))}
                />
              </label>
            </div>
            <div className="qm-grid-two">
              <label>
                Max emails in run
                <input
                  type="number"
                  min="1"
                  max="5000"
                  value={form.max_messages}
                  onChange={(event) => setForm((current) => ({ ...current, max_messages: event.target.value }))}
                />
              </label>
              <label>
                Batch size
                <input
                  type="number"
                  min="1"
                  max="100"
                  value={form.discovery_batch_size}
                  onChange={(event) => setForm((current) => ({ ...current, discovery_batch_size: event.target.value }))}
                />
              </label>
            </div>
            <div className="qm-grid-two">
              <label className="qm-checkbox contract-checkbox">
                <input
                  type="checkbox"
                  checked={form.include_attachments}
                  onChange={(event) => setForm((current) => ({ ...current, include_attachments: event.target.checked }))}
                />
                Parse attachments
              </label>
            </div>
            <button type="submit" className="qm-primary" disabled={busyAction === 'create-run'}>
              {busyAction === 'create-run' ? 'Creating...' : 'Create Research Run'}
            </button>
          </form>

          <div className="qm-contract-run-list">
            {runs.length === 0 && <div className="qm-empty compact">No research runs yet.</div>}
            {runs.map((run) => (
              <button
                type="button"
                key={run.id}
                className={`qm-contract-run-item ${selectedRun?.id === run.id ? 'active' : ''}`}
                onClick={() => openRun(run.id)}
              >
                <strong>{run.target_company_name}</strong>
                <span>{statusLabel[run.status] || run.status} - {run.item_count} rows</span>
                <small>{formatDateTime(run.updated_at)}</small>
              </button>
            ))}
          </div>
        </aside>

        <main className="qm-contract-workspace">
          {!selectedRun ? (
            <section className="qm-panel qm-empty-state">
              <h3>Select or create a research run</h3>
              <p>Connect Gmail, create an ALEC run, then discover and analyze matching emails.</p>
            </section>
          ) : (
            <>
              <section className="qm-panel qm-contract-run-header">
                <div>
                  <span className={`qm-badge status-${selectedRun.status}`}>{statusLabel[selectedRun.status] || selectedRun.status}</span>
                  <h3>{selectedRun.target_company_name}</h3>
                  <p>{selectedRun.gmail_query ? 'Custom Gmail query override is active.' : 'Smart Gmail query uses the customer name, optional domain hint, inquiry/quotation/LPO terms, and date range.'}</p>
                  <div className="qm-contract-query-box">
                    <strong>Effective Gmail query</strong>
                    <code>{selectedRun.effective_gmail_query || selectedRun.gmail_query || 'No Gmail query available yet.'}</code>
                  </div>
                  <div className="qm-contract-progress-line">
                    <span>Batch size: {selectedRun.discovery_batch_size || 25}</span>
                    <span>Message cap: {selectedRun.max_messages || 0}</span>
                    <span>{discoveryStopLabel[selectedRun.discovery_stop_reason] || (selectedRun.discovery_exhausted ? 'Discovery complete' : 'Discovery can continue')}</span>
                    {selectedRun.sender_domain_hint && <span>Domain hint: {selectedRun.sender_domain_hint}</span>}
                  </div>
                </div>
                <div className="qm-contract-actions">
                  <button type="button" className="qm-primary" onClick={() => discoverAll()} disabled={!gmailConnected || busyAction === 'discover-all' || selectedRun.discovery_exhausted}>
                    {busyAction === 'discover-all' ? 'Discovering all...' : selectedRun.discovery_exhausted ? 'Discovery Complete' : 'Run Full Discovery'}
                  </button>
                  <button type="button" className="qm-secondary" onClick={discover} disabled={!gmailConnected || busyAction === 'discover' || selectedRun.discovery_exhausted}>
                    {busyAction === 'discover' ? 'Discovering...' : selectedSummary.sources ? 'Discover One Batch' : 'Discover First Batch'}
                  </button>
                  {!!selectedSummary.sources && (
                    <button type="button" className="qm-secondary" onClick={() => discoverAll({ resetCursor: true })} disabled={!gmailConnected || busyAction === 'discover-all'}>
                      Restart Full Discovery
                    </button>
                  )}
                  <button type="button" className="qm-secondary" onClick={() => analyzeAll(true)} disabled={!sources.length || busyAction === 'analyze-all-ai'}>
                    {busyAction === 'analyze-all-ai' ? 'AI analyzing...' : 'Run Full AI Analysis'}
                  </button>
                  <button type="button" className="qm-secondary" onClick={() => analyzeAll(false)} disabled={!sources.length || busyAction === 'analyze-all-basic'}>
                    {busyAction === 'analyze-all-basic' ? 'Analyzing...' : 'Run Full Basic Analysis'}
                  </button>
                  <button type="button" className="qm-secondary" onClick={() => analyze(true)} disabled={!sources.length || busyAction === 'analyze-ai'}>
                    AI Analyze One Batch
                  </button>
                  <button type="button" className="qm-secondary" onClick={cleanExtractedRows} disabled={!items.length || busyAction === 'clean-items'}>
                    {busyAction === 'clean-items' ? 'Cleaning...' : 'Clean Extracted Rows'}
                  </button>
                  <button type="button" className="qm-secondary" onClick={exportRun} disabled={!items.length || busyAction === 'export'}>
                    Export Excel
                  </button>
                  <button type="button" className="qm-secondary danger" onClick={() => setDeleteConfirmRun(selectedRun)} disabled={busyAction === 'delete-run'}>
                    Delete Run
                  </button>
                </div>
              </section>

              <section className="qm-summary-banner qm-contract-summary">
                <div className="qm-summary-stat">
                  <span>Sources</span>
                  <strong>{selectedSummary.sources || 0}</strong>
                </div>
                <div className="qm-summary-stat">
                  <span>Candidates</span>
                  <strong>{selectedSummary.sources_candidate || 0}</strong>
                </div>
                <div className="qm-summary-stat">
                  <span>Analyzed</span>
                  <strong>{selectedSummary.sources_analyzed || 0}</strong>
                </div>
                <div className="qm-summary-stat success">
                  <span>Unique Items</span>
                  <strong>{selectedSummary.unique_items || 0}</strong>
                </div>
                <div className="qm-summary-stat">
                  <span>Matched Products</span>
                  <strong>{selectedSummary.matched_products || 0}</strong>
                </div>
                <div className="qm-summary-stat">
                  <span>Items</span>
                  <strong>{selectedSummary.items || 0}</strong>
                </div>
                <div className="qm-summary-stat">
                  <span>Gmail Estimate</span>
                  <strong>{selectedSummary.discovery_result_estimate || '-'}</strong>
                </div>
              </section>

              {!!(selectedRun.warnings || []).length && (
                <section className="qm-helper warning">
                  {(selectedRun.warnings || []).slice(0, 3).map((warning, index) => (
                    <div key={`${warning}-${index}`}>{warning}</div>
                  ))}
                </section>
              )}

              <section className="qm-grid-two qm-contract-panels">
                <div className="qm-panel">
                  <div className="qm-panel-heading">
                    <div>
                      <h3>
                        Sources
                        <span className="qm-heading-count">{selectedSummary.sources || 0} emails found</span>
                      </h3>
                      <p>Emails and attachments found for this run. Showing all discovered sources.</p>
                    </div>
                    <select
                      className="qm-input compact"
                      value={sourceSort}
                      onChange={(event) => setSourceSort(event.target.value)}
                      aria-label="Sort discovered sources"
                    >
                      <option value="newest">Newest first</option>
                      <option value="oldest">Oldest first</option>
                    </select>
                  </div>
                  <div className="qm-contract-source-list">
                    {sources.length === 0 && <div className="qm-empty compact">No Gmail sources discovered yet.</div>}
                    {visibleSources.map((source) => (
                      <article key={source.id} className="qm-contract-source">
                        <div>
                          <strong>{source.subject || '(no subject)'}</strong>
                          <span>{source.sender}</span>
                          <small>{formatDateTime(source.sent_at)}</small>
                        </div>
                        <div className="qm-contract-source-meta">
                          <span className={`qm-badge status-${source.status}`}>{source.status || 'tracked'}</span>
                          <span className={`qm-badge status-${source.classification}`}>{classificationLabel[source.classification] || source.classification}</span>
                          <span>{percent(source.confidence)}</span>
                          <span>{source.item_count || 0} items</span>
                        </div>
                      </article>
                    ))}
                  </div>
                </div>

                <div className="qm-panel">
                  <div className="qm-panel-heading">
                    <div>
                      <h3>Top Items</h3>
                      <p>Grouped demand signals across emails and attachments.</p>
                    </div>
                  </div>
                  <div className="qm-contract-top-items">
                    {(selectedSummary.top_items || []).length === 0 && <div className="qm-empty compact">Analyze sources to see grouped items.</div>}
                    {(selectedSummary.top_items || []).map((item) => (
                      <div key={item.normalized} className="qm-contract-top-item">
                        <strong>{item.item_name}</strong>
                        <span>{item.count} mention(s) from {item.source_count} source(s)</span>
                        <small>{item.latest_date || '-'} {item.last_price ? `- AED ${item.last_price}` : ''}</small>
                      </div>
                    ))}
                  </div>
                </div>
              </section>

              <section className="qm-panel">
                <div className="qm-panel-heading">
                  <div>
                    <h3>Extracted Item Review</h3>
                    <p>Review-only intelligence. Editing rows here does not create Products, aliases, quotations, or orders.</p>
                  </div>
                  <input
                    className="qm-input compact"
                    value={itemFilter}
                    onChange={(event) => setItemFilter(event.target.value)}
                    placeholder="Search items"
                  />
                </div>
                <div className="qm-table-wrap">
                  <table className="qm-table qm-contract-items-table">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>Item</th>
                        <th>Qty</th>
                        <th>Unit</th>
                        <th>Price</th>
                        <th>Date</th>
                        <th>Product Match</th>
                        <th>Confidence</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleItems.map((item, index) => (
                        <tr key={item.id}>
                          <td>{index + 1}</td>
                          <td>
                            <input
                              value={item.suggested_item_name || ''}
                              onChange={(event) => setItems((current) => current.map((row) => (
                                row.id === item.id ? { ...row, suggested_item_name: event.target.value } : row
                              )))}
                              onBlur={(event) => updateItem(item.id, { suggested_item_name: event.target.value })}
                            />
                            <small>{item.source_subject || item.source_filename || item.original_item_name}</small>
                          </td>
                          <td>{item.quantity || '-'}</td>
                          <td>{item.unit || '-'}</td>
                          <td>{item.unit_price ? `${item.currency || 'AED'} ${Number(item.unit_price).toFixed(2)}` : '-'}</td>
                          <td>{item.requested_date ? formatDate(item.requested_date) : formatDate(item.source_sent_at)}</td>
                          <td>{item.product_name || <span className="qm-muted-line">No catalog match</span>}</td>
                          <td><span className="qm-confidence">{percent(item.confidence)}</span></td>
                          <td>
                            <select
                              value={item.status}
                              onChange={(event) => updateItem(item.id, { status: event.target.value })}
                              disabled={busyAction === `item-${item.id}`}
                            >
                              {Object.entries(itemStatusLabel).map(([value, label]) => (
                                <option key={value} value={value}>{label}</option>
                              ))}
                            </select>
                          </td>
                        </tr>
                      ))}
                      {!visibleItems.length && (
                        <tr>
                          <td colSpan="9" className="qm-empty">No extracted items yet.</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </section>
            </>
          )}
        </main>
      </div>
      {deleteConfirmRun && (
        <div className="qm-modal-backdrop" role="presentation">
          <div className="qm-modal qm-contract-delete-modal" role="dialog" aria-modal="true" aria-labelledby="delete-contract-run-title">
            <h3 id="delete-contract-run-title">Delete this research run?</h3>
            <p>
              This removes the run, discovered Gmail sources, and extracted review items for
              <strong> {deleteConfirmRun.target_company_name}</strong>. Gmail messages are not deleted.
            </p>
            <div className="qm-helper warning">
              Use this when a run was started with the wrong settings and you want a clean slate.
            </div>
            <div className="qm-actions">
              <button type="button" className="qm-secondary" onClick={() => setDeleteConfirmRun(null)} disabled={busyAction === 'delete-run'}>
                Cancel
              </button>
              <button type="button" className="qm-secondary danger" onClick={deleteRun} disabled={busyAction === 'delete-run'}>
                {busyAction === 'delete-run' ? 'Deleting...' : 'Delete Research Run'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default ContractIntelligenceManager;
