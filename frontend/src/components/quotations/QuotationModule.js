import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import CompanyManager from './CompanyManager';
import QuoteItemManager from './QuoteItemManager';
import InquiryManager from './InquiryManager';
import GmailInquiryReview from './GmailInquiryReview';
import QuotationList from './QuotationList';
import QuotationEditor from './QuotationEditor';
import QuotationOutcomeReview from './QuotationOutcomeReview';
import QuotationDashboard from './QuotationDashboard';
import ProformaInvoiceManager from './ProformaInvoiceManager';
import PriceHistoryPanel from './PriceHistoryPanel';
import AuditLogPanel from './AuditLogPanel';
import QuotationSettings from './QuotationSettings';
import HistoricalImportManager from './HistoricalImportManager';
import ContractIntelligenceManager from './ContractIntelligenceManager';
import './QuotationModule.css';

const tabs = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'companies', label: 'Companies' },
  { id: 'items', label: 'Products / Items' },
  { id: 'inquiries', label: 'Inquiries' },
  { id: 'quotes', label: 'Quotations' },
  { id: 'proformas', label: 'Proforma Tax Invoices' },
  { id: 'history', label: 'Price History' },
  { id: 'historical-imports', label: 'Historical Imports' },
  { id: 'contract-intelligence', label: 'Contract Intelligence' },
  { id: 'audit', label: 'Audit Logs' },
  { id: 'settings', label: 'Settings' },
];

const positiveId = (value) => {
  const normalized = String(value ?? '').trim();
  if (!/^\d+$/.test(normalized)) return null;
  const parsed = Number(normalized);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
};

const sha256Fingerprint = (value) => /^[0-9a-f]{64}$/.test(String(value || ''));

export const quotationRouteFromSearch = (search) => {
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
    activeTab: tabs.some((candidate) => candidate.id === requestedTab) ? requestedTab : 'dashboard',
    gmailToken: '',
    gmailImportId: '',
    quoteId: null,
  };
};

const QuotationModule = () => {
  const location = useLocation();
  const navigate = useNavigate();
  const route = useMemo(() => quotationRouteFromSearch(location.search), [location.search]);
  const gmailReturnQuoteId = useMemo(() => positiveId(
    new URLSearchParams(location.search).get('gmail_return_quote_id')
  ), [location.search]);
  const [activeTab, setActiveTab] = useState(route.activeTab);
  const [editingQuoteId, setEditingQuoteId] = useState(route.quoteId);
  const [reviewingOutcomeQuoteId, setReviewingOutcomeQuoteId] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [pendingEmailReview, setPendingEmailReview] = useState(null);

  useEffect(() => {
    setActiveTab(route.activeTab);
    if (route.quoteId) {
      setEditingQuoteId(route.quoteId);
      setReviewingOutcomeQuoteId(null);
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
      params.delete('gmail_import');
      params.delete('gmail_import_id');
      params.delete('gmail_return_quote_id');
    });
  }, [updateLocation]);

  const openOutcome = useCallback((quoteId) => {
    setPendingEmailReview(null);
    setReviewingOutcomeQuoteId(quoteId);
    setEditingQuoteId(null);
    setActiveTab('quotes');
  }, []);

  const closeQuote = useCallback(() => {
    setPendingEmailReview(null);
    setEditingQuoteId(null);
    setReviewingOutcomeQuoteId(null);
    refresh();
    updateLocation((params) => {
      params.set('quotation_tab', 'quotes');
      params.delete('quote_id');
      params.delete('gmail_return_quote_id');
    }, { replace: true });
  }, [refresh, updateLocation]);

  const selectTab = useCallback((tabId) => {
    setPendingEmailReview(null);
    setActiveTab(tabId);
    setEditingQuoteId(null);
    setReviewingOutcomeQuoteId(null);
    updateLocation((params) => {
      params.set('quotation_tab', tabId);
      params.delete('gmail_import');
      params.delete('gmail_import_id');
      params.delete('quote_id');
      params.delete('gmail_return_quote_id');
    });
  }, [updateLocation]);

  const rememberClaimedImport = useCallback((claimedImportId) => {
    const normalizedId = positiveId(claimedImportId);
    if (!normalizedId) return;
    updateLocation((params) => {
      params.set('quotation_tab', 'quotes');
      params.set('gmail_import_id', String(normalizedId));
      params.delete('gmail_import');
      params.delete('quote_id');
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
        {tabs.map((tab) => (
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
            />
          ) : reviewingOutcomeQuoteId ? (
            <QuotationOutcomeReview quoteId={reviewingOutcomeQuoteId} onBack={closeQuote} />
          ) : editingQuoteId ? (
            <QuotationEditor
              quoteId={editingQuoteId}
              onClose={closeQuote}
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
            <QuotationList key={refreshKey} onOpenQuote={openQuote} onReviewOutcome={openOutcome} />
          )
        )}
        {activeTab === 'proformas' && <ProformaInvoiceManager />}
        {activeTab === 'history' && <PriceHistoryPanel />}
        {activeTab === 'historical-imports' && <HistoricalImportManager />}
        {activeTab === 'contract-intelligence' && <ContractIntelligenceManager />}
        {activeTab === 'audit' && <AuditLogPanel />}
        {activeTab === 'settings' && <QuotationSettings />}
      </div>
    </div>
  );
};

export default QuotationModule;
