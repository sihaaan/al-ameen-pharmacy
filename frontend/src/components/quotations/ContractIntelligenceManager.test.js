import React from 'react';
import { render, screen } from '@testing-library/react';
import { ContractWarningReview } from './ContractIntelligenceManager';

describe('ContractWarningReview', () => {
  test('keeps later attachment warnings discoverable instead of silently dropping them', () => {
    render(
      <ContractWarningReview
        warnings={[
          'First warning',
          'Second warning',
          'Third warning',
          'Fourth attachment warning',
          'Fifth attachment warning',
        ]}
      />,
    );

    expect(screen.getByText('First warning')).toBeInTheDocument();
    expect(screen.getByText('Third warning')).toBeInTheDocument();
    expect(screen.getByText('Show 2 more warnings')).toBeInTheDocument();
    expect(screen.getByText('Fourth attachment warning')).toBeInTheDocument();
    expect(screen.getByText('Fifth attachment warning')).toBeInTheDocument();
  });
});
