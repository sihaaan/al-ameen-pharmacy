import axiosInstance from '../utils/axios';
import { describeQuotationError } from './quotations';

export const describeAccountingError = describeQuotationError;

const accountingAPI = {
  dashboard: {
    retrieve: () => axiosInstance.get('/accounting/dashboard/'),
  },
  imports: {
    list: (params = {}) => axiosInstance.get('/accounting/imports/', { params }),
    retrieve: (id) => axiosInstance.get(`/accounting/imports/${id}/`),
    upload: (formData) => axiosInstance.post('/accounting/imports/upload/', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),
    applyCategories: (id, formData) => axiosInstance.post(`/accounting/imports/${id}/apply_categories/`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }),
    statementsZip: (id, style = 'professional', customerIds = [], extraParams = {}) => axiosInstance.get(`/accounting/imports/${id}/statements_zip/`, {
      params: { style, customer_ids: customerIds.join(','), ...extraParams },
      responseType: 'blob',
    }),
  },
  importCustomers: {
    list: (params = {}) => axiosInstance.get('/accounting/import-customers/', { params }),
    retrieve: (id, params = {}) => axiosInstance.get(`/accounting/import-customers/${id}/`, { params }),
    update: (id, data) => axiosInstance.patch(`/accounting/import-customers/${id}/`, data),
    statementPdf: (id, style = 'professional', extraParams = {}) => axiosInstance.get(`/accounting/import-customers/${id}/statement_pdf/`, {
      params: { style, ...extraParams },
      responseType: 'blob',
    }),
  },
  customers: {
    list: (params = {}) => axiosInstance.get('/accounting/customers/', { params }),
    update: (id, data) => axiosInstance.patch(`/accounting/customers/${id}/`, data),
  },
};

export default accountingAPI;
