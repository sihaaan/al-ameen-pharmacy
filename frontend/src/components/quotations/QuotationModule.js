import React, { useState } from 'react';
import CompanyManager from './CompanyManager';
import QuoteItemManager from './QuoteItemManager';
import InquiryManager from './InquiryManager';
import QuotationList from './QuotationList';
import QuotationEditor from './QuotationEditor';
import QuotationDashboard from './QuotationDashboard';
import PriceHistoryPanel from './PriceHistoryPanel';
import AuditLogPanel from './AuditLogPanel';
import QuotationSettings from './QuotationSettings';
import './QuotationModule.css';

const tabs = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'companies', label: 'Companies' },
  { id: 'items', label: 'Quote Items' },
  { id: 'inquiries', label: 'Inquiries' },
  { id: 'quotes', label: 'Quotations' },
  { id: 'history', label: 'Price History' },
  { id: 'audit', label: 'Audit Logs' },
  { id: 'settings', label: 'Settings' },
];

const QuotationModule = () => {
  const [activeTab, setActiveTab] = useState('dashboard');
  const [editingQuoteId, setEditingQuoteId] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const refresh = () => setRefreshKey((value) => value + 1);

  const openQuote = (quoteId) => {
    setEditingQuoteId(quoteId);
    setActiveTab('quotes');
  };

  const closeQuote = () => {
    setEditingQuoteId(null);
    refresh();
  };

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
            onClick={() => {
              setActiveTab(tab.id);
              if (tab.id !== 'quotes') setEditingQuoteId(null);
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="qm-body">
        {activeTab === 'dashboard' && <QuotationDashboard key={refreshKey} onOpenQuotes={() => setActiveTab('quotes')} />}
        {activeTab === 'companies' && <CompanyManager />}
        {activeTab === 'items' && <QuoteItemManager />}
        {activeTab === 'inquiries' && <InquiryManager onOpenQuote={openQuote} />}
        {activeTab === 'quotes' && (
          editingQuoteId ? (
            <QuotationEditor quoteId={editingQuoteId} onClose={closeQuote} />
          ) : (
            <QuotationList key={refreshKey} onOpenQuote={openQuote} />
          )
        )}
        {activeTab === 'history' && <PriceHistoryPanel />}
        {activeTab === 'audit' && <AuditLogPanel />}
        {activeTab === 'settings' && <QuotationSettings />}
      </div>
    </div>
  );
};

export default QuotationModule;
