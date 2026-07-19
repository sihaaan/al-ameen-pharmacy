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

  afterEach(() => {
    jest.useRealTimers();
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

  test('debounces optional remote searches and only sends the latest term', () => {
    jest.useFakeTimers();
    const onSearch = jest.fn();
    render(
      <CompanySelectWithCreate companies={[]} value="" onChange={jest.fn()} onSearch={onSearch} />
    );

    fireEvent.change(screen.getByPlaceholderText('Search companies'), { target: { value: 'Al' } });
    act(() => jest.advanceTimersByTime(200));
    fireEvent.change(screen.getByPlaceholderText('Search companies'), { target: { value: 'Al Ameen' } });
    act(() => jest.advanceTimersByTime(249));
    expect(onSearch).not.toHaveBeenCalled();

    act(() => jest.advanceTimersByTime(1));
    expect(onSearch).toHaveBeenCalledTimes(1);
    expect(onSearch).toHaveBeenCalledWith('Al Ameen');
  });

  test('caps rendered options without dropping the selected company', () => {
    const companies = [
      { id: 1, name: 'Alpha' },
      { id: 2, name: 'Beta' },
      { id: 3, name: 'Gamma' },
    ];
    render(
      <CompanySelectWithCreate
        companies={companies}
        value="3"
        onChange={jest.fn()}
        maxRenderedCompanies={2}
      />
    );

    const select = screen.getByRole('combobox');
    expect(select).toHaveValue('3');
    expect(screen.getByRole('option', { name: 'Gamma' })).toBeInTheDocument();
    expect(screen.getByText('Showing the first 2 of 3 matches. Type to narrow the list.')).toBeInTheDocument();
  });

  test('shows an explicit loading state while the first company results load', () => {
    render(
      <CompanySelectWithCreate companies={[]} value="" onChange={jest.fn()} loading />
    );

    expect(screen.getByRole('combobox')).toBeDisabled();
    expect(screen.getByRole('option', { name: 'Loading companies...' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Loading company results...');
  });

  test('keeps a backend TRN search result visible in the local picker filter', () => {
    render(
      <CompanySelectWithCreate
        companies={[{ id: 7, name: 'Customer Company', trn: '100200300400005' }]}
        value=""
        onChange={jest.fn()}
      />
    );

    fireEvent.change(screen.getByPlaceholderText('Search companies'), {
      target: { value: '100200300400005' },
    });
    expect(screen.getByRole('option', { name: 'Customer Company' })).toBeInTheDocument();
  });
});
