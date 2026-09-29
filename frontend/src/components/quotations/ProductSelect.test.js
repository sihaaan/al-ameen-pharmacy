import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import ProductSelect, { buildProductCatalogue } from './ProductSelect';

const products = Array.from({ length: 5400 }, (_, index) => ({
  id: index + 1, name: `Product ${String(index + 1).padStart(4, '0')}`,
}));
products[5399] = { id: 5400, name: 'Nitrile gloves', brand_name: 'Medline', sku: 'GLV-5400', barcode: '6291234567890', unit: 'box' };
const defaultProps = { catalogue: buildProductCatalogue(products, []), label: 'Product', value: '', onChange: jest.fn() };

function ControlledPicker({ value = '', onChange = jest.fn(), ...props }) {
  const [selected, setSelected] = useState(value);
  return <ProductSelect {...defaultProps} {...props} value={selected} onChange={(id) => {
    if (id !== '__create__') setSelected(id);
    onChange(id);
  }} />;
}

test('renders one field with no initial options, waits for two characters, and caps suggestions at ten', () => {
  const { container } = render(<ControlledPicker />);
  const input = screen.getByRole('combobox', { name: 'Product' });
  expect(container.querySelectorAll('input')).toHaveLength(1);
  expect(container.querySelector('select')).toBeNull();
  expect(screen.queryByRole('option')).not.toBeInTheDocument();
  fireEvent.focus(input);
  expect(screen.getByText('Type at least 2 characters to search.')).toBeInTheDocument();
  fireEvent.change(input, { target: { value: 'p' } });
  expect(screen.queryByRole('option')).not.toBeInTheDocument();
  fireEvent.change(input, { target: { value: 'pr' } });
  expect(screen.getAllByRole('option')).toHaveLength(10);
  expect(screen.getByText('10 suggestions. Keep typing to narrow the results.')).toBeInTheDocument();
  fireEvent.change(input, { target: { value: ' p ' } });
  expect(screen.queryByRole('option')).not.toBeInTheDocument();
});

test('searches the full catalogue by name, brand, SKU, barcode and unit; clicking explicitly chooses a match', () => {
  const onChange = jest.fn();
  render(<ControlledPicker onChange={onChange} />);
  const input = screen.getByRole('combobox');
  fireEvent.focus(input);
  ['GLOVES', 'medline', 'GLV-5400', '6291234567890', 'medline box'].forEach((query) => {
    fireEvent.change(input, { target: { value: query } });
    expect(screen.getAllByRole('option')).toHaveLength(1);
    expect(screen.getByRole('option', { name: /Nitrile gloves/ })).toBeInTheDocument();
  });
  expect(onChange).not.toHaveBeenCalled();
  userEvent.click(screen.getByRole('option', { name: /Nitrile gloves/ }));
  expect(onChange).toHaveBeenCalledWith('5400');
  expect(input).toHaveValue('Nitrile gloves');
  expect(input).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
});

test('arrows navigate suggestions and Enter selects without submitting the form', () => {
  const onSubmit = jest.fn((event) => event.preventDefault());
  const onChange = jest.fn();
  render(<form onSubmit={onSubmit}><ControlledPicker onChange={onChange} /></form>);
  const input = screen.getByRole('combobox');
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: 'Product 00' } });
  fireEvent.keyDown(input, { key: 'ArrowDown' });
  fireEvent.keyDown(input, { key: 'ArrowDown' });
  expect(screen.getByRole('option', { name: 'Product 0002' })).toHaveAttribute('aria-selected', 'true');
  fireEvent.keyDown(input, { key: 'ArrowUp' });
  expect(input).toHaveAttribute('aria-activedescendant', screen.getByRole('option', { name: 'Product 0001' }).id);
  expect(fireEvent.keyDown(input, { key: 'Enter' })).toBe(false);
  expect(onChange).toHaveBeenCalledWith('1');
  expect(input).toHaveValue('Product 0001');
  expect(onSubmit).not.toHaveBeenCalled();
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: 'not a real item' } });
  fireEvent.keyDown(input, { key: 'Enter' });
  expect(onChange).toHaveBeenCalledTimes(1);
  expect(onSubmit).not.toHaveBeenCalled();
});

test('searching, Escape, blur and Tab preserve an existing match; only Clear unlinks it', () => {
  const onChange = jest.fn();
  render(<><ControlledPicker value="5399" onChange={onChange} /><button>Next field</button></>);
  const input = screen.getByRole('combobox');
  expect(input).toHaveValue('Product 5399');
  ['Escape', 'Tab'].forEach((key) => {
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'unmatched search' } });
    expect(screen.getByText('No matching products. Try another search.')).toBeInTheDocument();
    fireEvent.keyDown(input, { key });
    expect(input).toHaveValue('Product 5399');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: '' } });
  fireEvent.blur(input);
  expect(input).toHaveValue('Product 5399');
  expect(onChange).not.toHaveBeenCalled();
  userEvent.click(screen.getByRole('button', { name: 'Clear Product' }));
  expect(onChange).toHaveBeenCalledWith('');
  expect(input).toHaveValue('');
});

test('prioritizes this customer’s products and keeps product creation available', () => {
  const onChange = jest.fn();
  render(<ControlledPicker catalogue={buildProductCatalogue(products, [products[5398]])} allowCreate onChange={onChange} />);
  const input = screen.getByRole('combobox');
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: 'Product' } });
  expect(screen.getAllByRole('option')[0]).toHaveTextContent('Product 5399');
  expect(screen.getAllByRole('option')[0]).toHaveTextContent('Previously quoted for this customer');
  userEvent.click(screen.getByRole('option', { name: '+ Create a new product…' }));
  expect(onChange).toHaveBeenCalledWith('__create__');
});

test('retains a saved name while loading and closes stale suggestions when the parent changes the product', () => {
  const onChange = jest.fn();
  const { rerender } = render(<ProductSelect {...defaultProps} value="5399" onChange={onChange} />);
  const input = screen.getByRole('combobox');
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: 'gloves' } });
  rerender(<ProductSelect {...defaultProps} value="9999" fallbackName="Saved gloves" catalogue={buildProductCatalogue([], [])} loading onChange={onChange} />);
  expect(input).toHaveValue('Saved gloves');
  expect(input).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Clear Product' })).toBeDisabled();
  expect(screen.queryByRole('option')).not.toBeInTheDocument();
  expect(onChange).not.toHaveBeenCalled();
  rerender(<ProductSelect {...defaultProps} value="" onChange={onChange} />);
  expect(input).toHaveValue('');
  expect(input).toBeEnabled();
});

test('uses a portal to escape table clipping and follows the field when its table scrolls', () => {
  const { container } = render(<div style={{ overflow: 'auto' }}><ControlledPicker /></div>);
  const input = screen.getByRole('combobox');
  let top = 100;
  jest.spyOn(input, 'getBoundingClientRect').mockImplementation(() => ({ left: 100, right: 300, top, bottom: top + 40, width: 200 }));
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: 'Product' } });
  const popup = screen.getByRole('listbox').parentElement;
  expect(container.contains(popup)).toBe(false);
  expect(popup).toHaveStyle({ top: '144px' });
  top = 160;
  fireEvent.scroll(container.firstChild);
  expect(popup).toHaveStyle({ top: '204px' });
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: 'Product' } });
  jest.spyOn(container.firstChild, 'getBoundingClientRect').mockReturnValue({
    left: 0, right: 500, top: 250, bottom: 500, width: 500, height: 250,
  });
  fireEvent.scroll(container.firstChild);
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  expect(input).toHaveValue('');
});

test('a disabled parent fieldset also prevents selection from an already-open portal', () => {
  const onChange = jest.fn();
  const { rerender } = render(<fieldset><ControlledPicker onChange={onChange} /></fieldset>);
  const input = screen.getByRole('combobox');
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: 'gloves' } });
  rerender(<fieldset disabled><ControlledPicker onChange={onChange} /></fieldset>);
  fireEvent.click(screen.getByRole('option', { name: /Nitrile gloves/ }));
  expect(onChange).not.toHaveBeenCalled();
});
