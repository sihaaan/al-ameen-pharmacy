import React, { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import './QuotationModule.css';
import './QuotationOutcomeReview.css';

const CompanyManager = lazy(() => import('./CompanyManager'));
const QuoteItemManager = lazy(() => import('./QuoteItemManager'));
const InquiryManager = lazy(() => import('./InquiryManager'));
const GmailInquiryReview = lazy(() => import('./GmailInquiryReview'));
const QuotationList = lazy(() => import('./QuotationList'));
const QuotationEditor = lazy(() => import('./QuotationEditor'));
const QuotationOutcomeReview = lazy(() => import('./QuotationOutcomeReview'));
const QuotationDashboard = lazy(() => import('./QuotationDashboard'));
const ProformaInvoiceManager = lazy(() => import('./ProformaInvoiceManager'));
const DeliveryNoteManager = lazy(() => import('./DeliveryNoteManager'));
const PriceHistoryPanel = lazy(() => import('./PriceHistoryPanel'));
const AuditLogPanel = lazy(() => import('./AuditLogPanel'));
const QuotationSettings = lazy(() => import('./QuotationSettings'));
const HistoricalImportManager = lazy(() => import('./HistoricalImportManager'));
const ContractIntelligenceManager = lazy(() => import('./ContractIntelligenceManager'));

const tabs = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'companies', label: 'Companies' },
  { id: 'items', label: 'Products / Items' },
  { id: 'inquiries', label: 'Inquiries' },
  { id: 'quotes', label: 'Quotations' },
  { id: 'proformas', label: 'Proforma Tax Invoices' },
  { id: 'deliveries', label: 'Orders & Delivery Notes' },
  { id: 'history', label: 'Price History' },
  { id: 'historical-imports', label: 'Historical Imports' },
  { id: 'contract-intelligence', label: 'Contract Intelligence' },
  { id: 'audit', label: 'Audit Logs', ownerOnly: true },
  { id: 'settings', label: 'Settings' },
];

const positiveId = (value) => {
  const normalized = String(value ?? '').trim();
  if (!/^\d+$/.test(normalized)) return null;
  const parsed = Number(normalized);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
};

const sha256Fingerprint = (value) => /^[0-9a-f]{64}$/.test(String(value || ''));

export const quotationRouteFromSearch = (search, canManageMailboxAudit = false) => {
  const params = new URLSearchParams(search || '');
  const gmailToken = params.get('gmail_import') || '';
  const parsedGmailImportId = positiveId(params.get('gmail_import_id'));
  const gmailImportId = parsedGmailImportId ? String(parsedGmailImportId) : '';
  const quoteId = positiveId(params.get('quote_id'));
  const requestedTab = params.get('quotation_tab');

  if (gmailToken || gmailImportId) {
    return {
      activeTab: 'quotes',
      gmailToken,
      gmailImportId,
      quoteId: null,
    };
  }
  if (quoteId) {
    return {
      activeTab: 'quotes',
      gmailToken: '',
      gmailImportId: '',
      quoteId,
    };
  }
  return {
    activeTab: tabs.some((candidate) => (
      candidate.id === requestedTab
      && (!candidate.ownerOnly || canManageMailboxAudit === true)
    )) ? requestedTab : 'dashboard',
    gmailToken: '',
    gmailImportId: '',
    quoteId: null,
  };
};

const QuotationModule = ({ canManageMailboxAudit }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const route = useMemo(
    () => quotationRouteFromSearch(location.search, canManageMailboxAudit),
    [canManageMailboxAudit, location.search]
  );
  const visibleTabs = useMemo(
    () => tabs.filter((tab) => !tab.ownerOnly || canManageMailboxAudit === true),
    [canManageMailboxAudit]
  );
  const gmailReturnQuoteId = useMemo(() => positiveId(
    new URLSearchParams(location.search).get('gmail_return_quote_id')
  ), [location.search]);
  const isReviewRoute = Boolean(route.quoteId && new URLSearchParams(location.search).get('quotation_mode') === 'review');
  const [activeTab, setActiveTab] = useState(route.activeTab);
  const [editingQuoteId, setEditingQuoteId] = useState(isReviewRoute ? null : route.quoteId);
  const [reviewingOutcomeQuoteId, setReviewingOutcomeQuoteId] = useState(isReviewRoute ? route.quoteId : null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [quotationListState, setQuotationListState] = useState({ search: '', statusFilter: '', scrollY: 0 });
  const [pendingEmailReview, setPendingEmailReview] = useState(null);
  const [preparedDeliveryNote, setPreparedDeliveryNote] = useState(null);

  useEffect(() => {
    setActiveTab(route.activeTab);
    if (route.quoteId) {
      setEditingQuoteId(isReviewRoute ? null : route.quoteId);
      setReviewingOutcomeQuoteId(isReviewRoute ? route.quoteId : null);
      setPendingEmailReview((current) => (
        current && Number(current.quoteId) === Number(route.quoteId) ? current : null
      ));
    } else {
      setEditingQuoteId(null);
      setReviewingOutcomeQuoteId(null);
      setPendingEmailReview(null);
    }
  }, [
    route.activeTab,
    route.gmailImportId,
    route.gmailToken,
    route.quoteId,
    isReviewRoute,
  ]);

  const updateLocation = useCallback((mutate, { replace = false } = {}) => {
    const params = new URLSearchParams(location.search);
    params.set('admin_tab', 'quotations');
    mutate(params);
    const query = params.toString();
    navigate(`${location.pathname}${query ? `?${query}` : ''}${location.hash || ''}`, { replace });
  }, [location.hash, location.pathname, location.search, navigate]);

  const refresh = useCallback(() => setRefreshKey((value) => value + 1), []);

  const openQuote = useCallback((quoteId, options = {}) => {
    const exactQuoteId = positiveId(quoteId);
    if (!exactQuoteId) return;
    const exactReviewFingerprint = String(options.quotationReviewFingerprint || '');
    setPendingEmailReview(
      options.reviewEmail === true && sha256Fingerprint(exactReviewFingerprint)
        ? {
          quoteId: exactQuoteId,
          quotationReviewFingerprint: exactReviewFingerprint,
          requestKey: `${exactQuoteId}:${exactReviewFingerprint}`,
        }
        : null
    );
    setEditingQuoteId(exactQuoteId);
    setReviewingOutcomeQuoteId(null);
    setActiveTab('quotes');
    updateLocation((params) => {
      params.set('quotation_tab', 'quotes');
      params.set('quote_id', String(exactQuoteId));
      params.delete('quotation_mode');
      params.delete('gmail_import');
      params.delete('gmail_import_id');
      params.delete('gmail_return_quote_id');
    });
  }, [updateLocation]);

  const openOutcome = useCallback((quoteId) => {
    const exactQuoteId = positiveId(quoteId);
    if (!exactQuoteId) return;
    setPendingEmailReview(null);
    setReviewingOutcomeQuoteId(exactQuoteId);
    setEditingQuoteId(null);
    setActiveTab('quotes');
    updateLocation((params) => {
      params.set('quotation_tab', 'quotes');
      params.set('quote_id', String(exactQuoteId));
      params.set('quotation_mode', 'review');
      params.delete('gmail_import');
      params.delete('gmail_import_id');
      params.delete('gmail_return_quote_id');
    });
  }, [updateLocation]);

  const closeQuote = useCallback(() => {
    setPendingEmailReview(null);
    setEditingQuoteId(null);
    setReviewingOutcomeQuoteId(null);
    refresh();
    updateLocation((params) => {
      params.set('quotation_tab', 'quotes');
      params.delete('quote_id');
      params.delete('quotation_mode');
      params.delete('gmail_return_quote_id');
    }, { replace: true });
  }, [refresh, updateLocation]);

  const selectTab = useCallback((tabId) => {
    if (tabId === 'audit' && canManageMailboxAudit !== true) return;
    setPendingEmailReview(null);
    setPreparedDeliveryNote(null);
    setActiveTab(tabId);
    setEditingQuoteId(null);
    setReviewingOutcomeQuoteId(null);
    updateLocation((params) => {
      params.set('quotation_tab', tabId);
      params.delete('gmail_import');
      params.delete('gmail_import_id');
      params.delete('quote_id');
      params.delete('quotation_mode');
      params.delete('gmail_return_quote_id');
    });
  }, [canManageMailboxAudit, updateLocation]);

  const openPreparedDeliveryNote = useCallback((note) => {
    selectTab('deliveries');
    setPreparedDeliveryNote(note);
  }, [selectTab]);

  const rememberClaimedImport = useCallback((claimedImportId) => {
    const normalizedId = positiveId(claimedImportId);
    if (!normalizedId) return;
    updateLocation((params) => {
      params.set('quotation_tab', 'quotes');
      params.set('gmail_import_id', String(normalizedId));
      params.delete('gmail_import');
      params.delete('quote_id');
      params.delete('quotation_mode');
    }, { replace: true });
  }, [updateLocation]);

  const openGmailImport = useCallback((gmailImportId) => {
    const normalizedId = positiveId(gmailImportId);
    if (!normalizedId) return;
    setPendingEmailReview(null);
    setEditingQuoteId(null);
    setReviewingOutcomeQuoteId(null);
    setActiveTab('quotes');
    updateLocation((params) => {
      params.set('quotation_tab', 'quotes');
      params.set('gmail_import_id', String(normalizedId));
      if (editingQuoteId) params.set('gmail_return_quote_id', String(editingQuoteId));
      else params.delete('gmail_return_quote_id');
      params.delete('gmail_import');
      params.delete('quote_id');
      params.delete('quotation_mode');
    });
  }, [editingQuoteId, updateLocation]);

  const closeGmailReview = useCallback(() => {
    if (gmailReturnQuoteId) {
      openQuote(gmailReturnQuoteId);
      return;
    }
    setPendingEmailReview(null);
    setActiveTab('quotes');
    updateLocation((params) => {
      params.set('quotation_tab', 'quotes');
      params.delete('gmail_import');
      params.delete('gmail_import_id');
      params.delete('quote_id');
      params.delete('quotation_mode');
      params.delete('gmail_return_quote_id');
    }, { replace: true });
  }, [gmailReturnQuoteId, openQuote, updateLocation]);

  return (
    <div className="quotation-module">
      <div className="qm-header">
        <div>
          <h2>Quotations</h2>
          <p>Staff-only company quotation workflow</p>
        </div>
      </div>

      <div className="qm-tabs">
        {visibleTabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={`qm-tab ${activeTab === tab.id ? 'active' : ''}`}
            onClick={() => selectTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="qm-body">
        <Suspense fallback={<p role="status">Loading quotation workspace…</p>}>
        {activeTab === 'dashboard' && <QuotationDashboard key={refreshKey} onOpenQuotes={() => selectTab('quotes')} />}
        {activeTab === 'companies' && <CompanyManager />}
        {activeTab === 'items' && <QuoteItemManager />}
        {activeTab === 'inquiries' && <InquiryManager onOpenQuote={openQuote} />}
        {activeTab === 'quotes' && (
          route.gmailToken || route.gmailImportId ? (
            <GmailInquiryReview
              key={route.gmailToken ? `token:${route.gmailToken}` : `import:${route.gmailImportId}`}
              token={route.gmailToken}
              importId={route.gmailImportId}
              onClaimed={rememberClaimedImport}
              onOpenQuote={openQuote}
              onBack={closeGmailReview}
              backLabel={gmailReturnQuoteId ? 'Back to quotation' : 'Back to quotations'}
              initialShowEvidence={Boolean(gmailReturnQuoteId)}
            />
          ) : reviewingOutcomeQuoteId ? (
            <QuotationOutcomeReview quoteId={reviewingOutcomeQuoteId} onBack={closeQuote} onDeliveryNoteCreated={openPreparedDeliveryNote} />
          ) : editingQuoteId ? (
            <QuotationEditor
              quoteId={editingQuoteId}
              onClose={closeQuote}
              onOpenQuote={openQuote}
              onReviewOutcome={openOutcome}
              initialEmailReviewFingerprint={
                pendingEmailReview?.quoteId === editingQuoteId
                  ? pendingEmailReview.quotationReviewFingerprint
                  : ''
              }
              onInitialEmailReviewHandled={() => {
                const handledKey = pendingEmailReview?.requestKey;
                setPendingEmailReview((current) => (
                  current?.requestKey === handledKey ? null : current
                ));
              }}
              onOpenGmailImport={openGmailImport}
            />
          ) : (
            <QuotationList
              key={refreshKey}
              viewState={quotationListState}
              onViewStateChange={setQuotationListState}
              onOpenQuote={openQuote}
              onReviewOutcome={openOutcome}
              canManageMailboxAudit={canManageMailboxAudit}
            />
          )
        )}
        {activeTab === 'proformas' && <ProformaInvoiceManager />}
        {activeTab === 'deliveries' && <DeliveryNoteManager onReviewOutcome={openOutcome} initialNote={preparedDeliveryNote} />}
        {activeTab === 'history' && <PriceHistoryPanel />}
        {activeTab === 'historical-imports' && <HistoricalImportManager />}
        {activeTab === 'contract-intelligence' && <ContractIntelligenceManager />}
        {activeTab === 'audit' && canManageMailboxAudit === true && <AuditLogPanel />}
        {activeTab === 'settings' && <QuotationSettings />}
        </Suspense>
      </div>
    </div>
  );
};

export default QuotationModule;
