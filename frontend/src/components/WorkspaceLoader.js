import React, { Suspense, lazy, useState } from 'react';
import './WorkspaceLoader.css';

export const isWorkspaceLoadError = (error) => (
  /ChunkLoadError|CSS_CHUNK_LOAD_FAILED|Loading (?:CSS )?chunk .+ failed|Failed to fetch dynamically imported module|Importing a module script failed/i
    .test(`${error?.name || ''} ${error?.code || ''} ${error?.message || ''}`)
);

export async function loadWorkspace(importer) {
  // Only retry downloading code. Never repeat a mounted workspace's actions.
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await importer();
    } catch (error) {
      if (!isWorkspaceLoadError(error) || attempt >= 2) throw error;
      await new Promise((resolve) => setTimeout(resolve, 400 * (attempt + 1)));
    }
  }
}

export class WorkspaceErrorBoundary extends React.Component {
  state = { error: null };
  static getDerivedStateFromError(error) { return { error }; }
  componentDidCatch(error) { console.error('Workspace could not open:', error); }
  render() {
    if (!this.state.error) return this.props.children;
    const loadingFailed = isWorkspaceLoadError(this.state.error);
    return <section className="workspace-recovery" role="alert" aria-label="Workspace recovery">
      <span className="workspace-recovery-icon" aria-hidden="true">↻</span>
      <div>
        <h2>{loadingFailed ? 'This workspace needs to reconnect' : 'This workspace ran into a problem'}</h2>
        <p>{loadingFailed
          ? 'A part of the page could not download. This can happen after a website update or a connection interruption.'
          : 'Try opening this workspace again. If the problem continues, reload the page.'}</p>
        <p className="workspace-recovery-note">Your saved records remain available. Reloading clears unsaved changes on this page.</p>
        <div className="workspace-recovery-actions">
          <button type="button" onClick={this.props.onRetry}>Try again</button>
          <button type="button" className="workspace-recovery-reload" onClick={this.props.onReload || (() => window.location.reload())}>Reload latest version</button>
        </div>
        <details><summary>Error details</summary><code>{this.state.error?.name || 'Workspace error'}: {this.state.error?.message || 'Unknown error'}</code></details>
      </div>
    </section>;
  }
}

export default function lazyWorkspace(importer, label = 'workspace') {
  let Workspace = lazy(() => loadWorkspace(importer));
  return function RecoverableWorkspace(props) {
    const [attempt, setAttempt] = useState(0);
    const retry = () => {
      // React.lazy retains rejected promises; resetting only the error boundary
      // would immediately throw the same cached failure again.
      Workspace = lazy(() => loadWorkspace(importer));
      setAttempt((value) => value + 1);
    };
    return <WorkspaceErrorBoundary key={attempt} onRetry={retry}>
      <Suspense fallback={<div className="workspace-loading" role="status">Loading {label}…</div>}>
        <Workspace {...props} />
      </Suspense>
    </WorkspaceErrorBoundary>;
  };
}
