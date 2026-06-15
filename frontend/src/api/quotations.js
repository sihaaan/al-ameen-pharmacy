import axiosInstance from '../utils/axios';

const apiBaseURL = process.env.REACT_APP_API_URL || 'http://localhost:8000/api';

const stringifyBackendData = (data) => {
  if (!data) return '';
  if (typeof data === 'string') return data.trim();
  if (Array.isArray(data)) return data.map((item) => stringifyBackendData(item)).filter(Boolean).join(', ');
  if (typeof data === 'object') {
    if (data.detail) return stringifyBackendData(data.detail);
    if (data.non_field_errors) return stringifyBackendData(data.non_field_errors);
    return Object.entries(data)
      .map(([key, value]) => `${key}: ${stringifyBackendData(value)}`)
      .filter((value) => !value.endsWith(': '))
      .join('; ');
  }
  return String(data);
};

const truncateDetail = (detail) => {
  if (!detail || detail.length <= 800) return detail;
  return `${detail.slice(0, 800)}...`;
};

const readBackendData = async (data) => {
  if (typeof Blob !== 'undefined' && data instanceof Blob) {
    const text = await data.text();
    if (!text) return '';
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  }
  return data;
};

export const describeQuotationError = async (error, action, endpoint) => {
  const config = error?.config || {};
  const method = (config.method || '').toUpperCase();
  const configEndpoint = config.url ? `${method || 'GET'} ${config.url}` : '';
  const responseData = await readBackendData(error?.response?.data);
  const backendDetail = truncateDetail(stringifyBackendData(responseData));

  return {
    action,
    endpoint: endpoint || configEndpoint || 'Unknown endpoint',
    status: error?.response?.status || 'Network error',
    baseURL: config.baseURL || apiBaseURL,
    detail: backendDetail || error?.message || 'No backend detail was returned.',
  };
};

export const formatQuotationError = (errorInfo) => (
  `${errorInfo.action} failed. Endpoint: ${errorInfo.endpoint}. ` +
  `Status: ${errorInfo.status}. Detail: ${errorInfo.detail}`
);

const quotationAPI = {
  dashboard: {
    retrieve: () => axiosInstance.get('/quotations/dashboard/'),
    analysis: (params = {}) => axiosInstance.get('/quotations/dashboard/analysis/', { params }),
  },
  followups: {
    list: (params = {}) => axiosInstance.get('/quotations/followups/', { params }),
  },
  companies: {
    list: (params = {}) => axiosInstance.get('/quotations/companies/', { params }),
    similar: (params = {}) => axiosInstance.get('/quotations/companies/similar/', { params }),
    retrieve: (id) => axiosInstance.get(`/quotations/companies/${id}/`),
    create: (data) => axiosInstance.post('/quotations/companies/', data),
    update: (id, data) => axiosInstance.patch(`/quotations/companies/${id}/`, data),
    delete: (id) => axiosInstance.delete(`/quotations/companies/${id}/`),
    priceHistory: (id, params = {}) => axiosInstance.get(`/quotations/companies/${id}/price_history/`, { params }),
  },
  contacts: {
    list: (params = {}) => axiosInstance.get('/quotations/contacts/', { params }),
    create: (data) => axiosInstance.post('/quotations/contacts/', data),
    update: (id, data) => axiosInstance.patch(`/quotations/contacts/${id}/`, data),
  },
  items: {
    list: (params = {}) => axiosInstance.get('/quotations/items/', { params }),
    create: (data) => axiosInstance.post('/quotations/items/', data),
    update: (id, data) => axiosInstance.patch(`/quotations/items/${id}/`, data),
    delete: (id) => axiosInstance.delete(`/quotations/items/${id}/`),
  },
  aliases: {
    list: (params = {}) => axiosInstance.get('/quotations/aliases/', { params }),
    create: (data) => axiosInstance.post('/quotations/aliases/', data),
    update: (id, data) => axiosInstance.patch(`/quotations/aliases/${id}/`, data),
    delete: (id) => axiosInstance.delete(`/quotations/aliases/${id}/`),
  },
  inquiries: {
    list: (params = {}) => axiosInstance.get('/quotations/inquiries/', { params }),
    create: (data) => axiosInstance.post('/quotations/inquiries/', data),
    update: (id, data) => axiosInstance.patch(`/quotations/inquiries/${id}/`, data),
    parseText: (data) => axiosInstance.post('/quotations/inquiries/parse_text/', data),
    parseFile: (formData) => axiosInstance.post('/quotations/inquiries/parse_file/', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),
    applyPriceReference: (formData) => axiosInstance.post('/quotations/inquiries/apply_price_reference/', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),
    aiCleanParse: (data) => axiosInstance.post('/quotations/inquiries/ai_clean_parse/', data),
    createImported: (data) => axiosInstance.post('/quotations/inquiries/create_imported/', data),
    createQuote: (id) => axiosInstance.post(`/quotations/inquiries/${id}/create_quote/`),
  },
  inquiryLines: {
    list: (params = {}) => axiosInstance.get('/quotations/inquiry-lines/', { params }),
    create: (data) => axiosInstance.post('/quotations/inquiry-lines/', data),
    update: (id, data) => axiosInstance.patch(`/quotations/inquiry-lines/${id}/`, data),
    rememberAlias: (id) => axiosInstance.post(`/quotations/inquiry-lines/${id}/remember_alias/`),
  },
  historicalImports: {
    list: (params = {}) => axiosInstance.get('/quotations/historical-imports/', { params }),
    retrieve: (id) => axiosInstance.get(`/quotations/historical-imports/${id}/`),
    update: (id, data) => axiosInstance.patch(`/quotations/historical-imports/${id}/`, data),
    parseFile: (formData) => axiosInstance.post('/quotations/historical-imports/parse_file/', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),
    commit: (id) => axiosInstance.post(`/quotations/historical-imports/${id}/commit/`),
    bulkCreateQuoteItems: (id, data) => axiosInstance.post(`/quotations/historical-imports/${id}/bulk_create_quote_items/`, data),
    bulkUpdateRows: (id, data) => axiosInstance.post(`/quotations/historical-imports/${id}/bulk_update_rows/`, data),
    bulkSkipRows: (id, data) => axiosInstance.post(`/quotations/historical-imports/${id}/bulk_skip_rows/`, data),
    aiCleanRows: (id, data) => axiosInstance.post(`/quotations/historical-imports/${id}/ai_clean_rows/`, data),
    applyAiCleanRows: (id, data) => axiosInstance.post(`/quotations/historical-imports/${id}/apply_ai_clean_rows/`, data),
    runAiSuggestions: (id, data = {}) => axiosInstance.post(`/quotations/historical-imports/${id}/run_ai_suggestions/`, data),
    previewPage: (id, params = {}) => axiosInstance.get(`/quotations/historical-imports/${id}/preview_page/`, { params, responseType: 'blob' }),
    removeFromBatch: (id) => axiosInstance.post(`/quotations/historical-imports/${id}/remove_from_batch/`),
  },
  historicalImportBatches: {
    list: (params = {}) => axiosInstance.get('/quotations/historical-import-batches/', { params }),
    retrieve: (id) => axiosInstance.get(`/quotations/historical-import-batches/${id}/`),
    create: (data) => axiosInstance.post('/quotations/historical-import-batches/', data),
    uploadFile: (id, formData) => axiosInstance.post(`/quotations/historical-import-batches/${id}/upload_file/`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),
    runAiSuggestions: (id, data = {}) => axiosInstance.post(`/quotations/historical-import-batches/${id}/run_ai_suggestions/`, data),
    applyAiSuggestions: (id, data = {}) => axiosInstance.post(`/quotations/historical-import-batches/${id}/apply_ai_suggestions/`, data),
    commitReadyImports: (id, data = {}) => axiosInstance.post(`/quotations/historical-import-batches/${id}/commit_ready_imports/`, data),
  },
  historicalImportAiSuggestions: {
    list: (params = {}) => axiosInstance.get('/quotations/historical-import-ai-suggestions/', { params }),
    update: (id, data) => axiosInstance.patch(`/quotations/historical-import-ai-suggestions/${id}/`, data),
    apply: (data = {}) => axiosInstance.post('/quotations/historical-import-ai-suggestions/apply/', data),
    reject: (id, data = {}) => axiosInstance.post(`/quotations/historical-import-ai-suggestions/${id}/reject/`, data),
    sourceContext: (id) => axiosInstance.get(`/quotations/historical-import-ai-suggestions/${id}/source_context/`),
  },
  historicalImportLines: {
    list: (params = {}) => axiosInstance.get('/quotations/historical-import-lines/', { params }),
    update: (id, data) => axiosInstance.patch(`/quotations/historical-import-lines/${id}/`, data),
    rememberAlias: (id) => axiosInstance.post(`/quotations/historical-import-lines/${id}/remember_alias/`),
  },
  quotes: {
    list: (params = {}) => axiosInstance.get('/quotations/quotes/', { params }),
    retrieve: (id) => axiosInstance.get(`/quotations/quotes/${id}/`),
    create: (data) => axiosInstance.post('/quotations/quotes/', data),
    update: (id, data) => axiosInstance.patch(`/quotations/quotes/${id}/`, data),
    submitReview: (id) => axiosInstance.post(`/quotations/quotes/${id}/submit_review/`),
    approve: (id) => axiosInstance.post(`/quotations/quotes/${id}/approve/`),
    finalize: (id) => axiosInstance.post(`/quotations/quotes/${id}/finalize/`),
    bulkUpdateLines: (id, data) => axiosInstance.post(`/quotations/quotes/${id}/bulk_update_lines/`, data),
    bulkCreateProductsForLines: (id, data) => axiosInstance.post(`/quotations/quotes/${id}/bulk_create_products_for_lines/`, data),
    productPrice: (id, params = {}) => axiosInstance.get(`/quotations/quotes/${id}/product_price/`, { params }),
    outcome: (id) => axiosInstance.get(`/quotations/quotes/${id}/outcome/`),
    updateOutcome: (id, data) => axiosInstance.patch(`/quotations/quotes/${id}/outcome/`, data),
    parseOutcomePO: (id, data, isMultipart = false) => axiosInstance.post(
      `/quotations/quotes/${id}/parse_outcome_po/`,
      data,
      isMultipart ? { headers: { 'Content-Type': 'multipart/form-data' } } : undefined
    ),
    markSent: (id) => axiosInstance.post(`/quotations/quotes/${id}/mark_sent/`),
    revise: (id) => axiosInstance.post(`/quotations/quotes/${id}/revise/`),
    cancel: (id) => axiosInstance.post(`/quotations/quotes/${id}/cancel/`),
    pdf: (id) => axiosInstance.get(`/quotations/quotes/${id}/pdf/`, { responseType: 'blob' }),
    excel: (id) => axiosInstance.get(`/quotations/quotes/${id}/excel/`, { responseType: 'blob' }),
  },
  lines: {
    create: (data) => axiosInstance.post('/quotations/quote-lines/', data),
    update: (id, data) => axiosInstance.patch(`/quotations/quote-lines/${id}/`, data),
    delete: (id) => axiosInstance.delete(`/quotations/quote-lines/${id}/`),
    createProduct: (id, data = {}) => axiosInstance.post(`/quotations/quote-lines/${id}/create_product/`, data),
    uploadProductImage: (id, formData) => axiosInstance.post(`/quotations/quote-lines/${id}/upload_product_image/`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),
    rememberAlias: (id) => axiosInstance.post(`/quotations/quote-lines/${id}/remember_alias/`),
  },
  priceHistory: {
    list: (params = {}) => axiosInstance.get('/quotations/price-history/', { params }),
  },
  auditLogs: {
    list: (params = {}) => axiosInstance.get('/quotations/audit-logs/', { params }),
  },
  settings: {
    retrieve: () => axiosInstance.get('/quotations/settings/'),
    update: (data, isMultipart = false) => axiosInstance.patch('/quotations/settings/', data, isMultipart ? {
      headers: { 'Content-Type': 'multipart/form-data' },
    } : undefined),
  },
  userSignature: {
    retrieve: () => axiosInstance.get('/quotations/my-signature/'),
    update: (data, isMultipart = false) => axiosInstance.patch('/quotations/my-signature/', data, isMultipart ? {
      headers: { 'Content-Type': 'multipart/form-data' },
    } : undefined),
  },
};

export default quotationAPI;
