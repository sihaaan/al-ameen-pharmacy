import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import CompanySelectWithCreate from './CompanySelectWithCreate';
import quotationAPI from '../../api/quotations';

jest.mock('../../api/quotations', () => ({
  __esModule: true,
  default: {
    companies: {
      create: jest.fn(),
      similar: jest.fn(),
    },
  },
  describeQuotationError: jest.fn(async () => ({ detail: 'Request failed' })),
  formatQuotationError: jest.fn(() => 'Request failed'),
}));

describe('CompanySelectWithCreate external locking', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    quotationAPI.companies.similar.mockResolvedValue({ data: { suggestions: [] } });
  });

  test('does not select a company whose create request finishes after the picker is disabled', async () => {
    let resolveCreate;
    quotationAPI.companies.create.mockReturnValue(new Promise((resolve) => {
      resolveCreate = resolve;
    }));
    const onCreated = jest.fn();
    const onChange = jest.fn();
    const { rerender } = render(
      <CompanySelectWithCreate companies={[]} value="" onCreated={onCreated} onChange={onChange} />
    );

    fireEvent.click(screen.getByRole('button', { name: '+ New Company' }));
    fireEvent.change(screen.getByPlaceholderText('Company name'), { target: { value: 'Created During Save' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create Company' }));

    rerender(
      <CompanySelectWithCreate companies={[]} value="" onCreated={onCreated} onChange={onChange} disabled />
    );
    expect(screen.getByPlaceholderText('Company name')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Creating...' })).toBeDisabled();

    await act(async () => {
      resolveCreate({ data: { id: 91, name: 'Created During Save' } });
    });

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith({ id: 91, name: 'Created During Save' }));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText('Company created. Select it after the current operation finishes.')).toBeInTheDocument();
  });
});
