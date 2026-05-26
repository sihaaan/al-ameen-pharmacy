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
    statementsZip: (id, style = 'professional', customerIds = []) => axiosInstance.get(`/accounting/imports/${id}/statements_zip/`, {
      params: { style, customer_ids: customerIds.join(',') },
      responseType: 'blob',
    }),
  },
  importCustomers: {
    list: (params = {}) => axiosInstance.get('/accounting/import-customers/', { params }),
    retrieve: (id) => axiosInstance.get(`/accounting/import-customers/${id}/`),
    update: (id, data) => axiosInstance.patch(`/accounting/import-customers/${id}/`, data),
    statementPdf: (id, style = 'professional') => axiosInstance.get(`/accounting/import-customers/${id}/statement_pdf/`, {
      params: { style },
      responseType: 'blob',
    }),
  },
  customers: {
    list: (params = {}) => axiosInstance.get('/accounting/customers/', { params }),
    update: (id, data) => axiosInstance.patch(`/accounting/customers/${id}/`, data),
  },
};

export default accountingAPI;
