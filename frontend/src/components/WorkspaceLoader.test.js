import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import lazyWorkspace, { isWorkspaceLoadError, loadWorkspace, WorkspaceErrorBoundary } from './WorkspaceLoader';

const chunkError = () => Object.assign(new Error('Loading chunk 12 failed.'), { name: 'ChunkLoadError' });

describe('workspace recovery', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    jest.spyOn(console, 'error').mockImplementation(() => {});
  });
  afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
  });
  const retryDelay = async (delay) => {
    await act(async () => { jest.advanceTimersByTime(delay); });
  };

  test('recovers a temporary code download failure automatically', async () => {
    const importer = jest.fn().mockRejectedValueOnce(chunkError()).mockResolvedValue({ default: () => <h2>Quotation editor</h2> });
    const Workspace = lazyWorkspace(importer, 'quotations');
    await act(async () => { render(<Workspace />); });
    expect(screen.getByRole('status')).toHaveTextContent('Loading quotations');
    await retryDelay(400);
    expect(screen.getByRole('heading', { name: 'Quotation editor' })).toBeVisible();
    expect(importer).toHaveBeenCalledTimes(2);
  });

  test('keeps the surrounding navigation available and retries a rejected lazy module', async () => {
    const importer = jest.fn().mockRejectedValue(chunkError());
    const Workspace = lazyWorkspace(importer);
    await act(async () => { render(<><nav>Admin navigation</nav><Workspace /></>); });
    await retryDelay(400);
    await retryDelay(800);
    expect(importer).toHaveBeenCalledTimes(3);
    expect(screen.getByRole('alert')).toHaveTextContent('This workspace needs to reconnect');
    expect(screen.getByRole('navigation')).toBeVisible();
    importer.mockResolvedValue({ default: () => <h2>Recovered workspace</h2> });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Try again' })); });
    expect(screen.getByRole('heading', { name: 'Recovered workspace' })).toBeVisible();
    expect(importer).toHaveBeenCalledTimes(4);
  });

  test('does not automatically replay runtime failures or reload the page', async () => {
    const importer = jest.fn().mockRejectedValue(new TypeError('Invalid workspace data'));
    await expect(loadWorkspace(importer)).rejects.toThrow('Invalid workspace data');
    expect(importer).toHaveBeenCalledTimes(1);
    const onReload = jest.fn();
    function BrokenWorkspace() { throw new TypeError('Invalid workspace data'); }
    render(<WorkspaceErrorBoundary onReload={onReload} onRetry={jest.fn()}><BrokenWorkspace /></WorkspaceErrorBoundary>);
    expect(screen.getByRole('alert')).toHaveTextContent('This workspace ran into a problem');
    expect(onReload).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Reload latest version' }));
    expect(onReload).toHaveBeenCalledTimes(1);
  });

  test('only classifies module download failures for automatic retries', () => {
    expect(isWorkspaceLoadError(chunkError())).toBe(true);
    expect(isWorkspaceLoadError({ code: 'CSS_CHUNK_LOAD_FAILED' })).toBe(true);
    expect(isWorkspaceLoadError(new TypeError('Failed to fetch dynamically imported module'))).toBe(true);
    expect(isWorkspaceLoadError(new Error('Network error saving quotation'))).toBe(false);
    expect(isWorkspaceLoadError(new TypeError('Cannot read properties of undefined'))).toBe(false);
  });
});
