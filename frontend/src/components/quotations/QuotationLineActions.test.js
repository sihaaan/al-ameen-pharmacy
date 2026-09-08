import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import QuotationLineActions from './QuotationLineActions';

const props = { label: 'Gloves', isDirty: false, canSave: true, canEditImage: true, canUpload: true, canDelete: true,
  hasImage: true, includeImage: false, onSave: jest.fn(), onToggleImage: jest.fn(), onUpload: jest.fn(), onDelete: jest.fn() };
beforeEach(() => jest.clearAllMocks());

test('keeps saved rows compact and exposes keyboard accessible image and delete actions', () => {
  render(<QuotationLineActions {...props} />);
  expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument();
  expect(screen.getByText('Saved')).toBeInTheDocument();
  const trigger = screen.getByRole('button', { name: 'Actions for Gloves' });
  fireEvent.keyDown(trigger, { key: 'ArrowDown' });
  const image = screen.getByRole('menuitemcheckbox', { name: 'Include product photo in PDF' });
  expect(image).toHaveFocus();
  fireEvent.click(image);
  expect(props.onToggleImage).toHaveBeenCalledTimes(1);
  fireEvent.keyDown(image, { key: 'ArrowUp' });
  expect(screen.getByRole('menuitem', { name: 'Delete line' })).toHaveFocus();
  fireEvent.keyDown(document.activeElement, { key: 'Home' });
  expect(image).toHaveFocus();
  fireEvent.keyDown(image, { key: 'Escape' });
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
  fireEvent.click(trigger);
  fireEvent.click(screen.getByRole('menuitem', { name: 'Delete line' }));
  expect(props.onDelete).toHaveBeenCalledTimes(1);
  expect(trigger).toHaveFocus();
});

test('save, upload, outside dismissal and locked-row permissions stay intact', () => {
  const { rerender } = render(<QuotationLineActions {...props} isDirty />);
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  expect(props.onSave).toHaveBeenCalledTimes(1);
  const file = new File(['image'], 'gloves.png', { type: 'image/png' });
  const fileInput = screen.getByLabelText('Upload image for Gloves');
  const picker = jest.spyOn(fileInput, 'click');
  fireEvent.click(screen.getByRole('button', { name: 'Actions for Gloves' }));
  fireEvent.click(screen.getByRole('menuitem', { name: 'Upload image' }));
  expect(picker).toHaveBeenCalledTimes(1);
  fireEvent.change(fileInput, { target: { files: [file] } });
  expect(props.onUpload).toHaveBeenCalledWith(file);
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Actions for Gloves' }));
  fireEvent.pointerDown(document.body);
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  rerender(<QuotationLineActions {...props} canSave={false} canEditImage={false} canUpload={false} canDelete={false} />);
  fireEvent.click(screen.getByRole('button', { name: 'Actions for Gloves' }));
  expect(screen.getByRole('menuitemcheckbox')).toBeDisabled();
  screen.getAllByRole('menuitem').forEach((item) => expect(item).toBeDisabled());
});

test('Tab dismisses the portal and resumes the row tab order', () => {
  render(<><QuotationLineActions {...props} /><button>Next control</button></>);
  fireEvent.click(screen.getByRole('button', { name: 'Actions for Gloves' }));
  userEvent.tab();
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Actions for Gloves' })).toHaveFocus();
  userEvent.tab();
  expect(screen.getByRole('button', { name: 'Next control' })).toHaveFocus();
});
