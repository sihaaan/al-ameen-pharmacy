import React from 'react';

const MATERIAL_LPO_WARNING_PATTERNS = [
  /stopp(?:ed|ing)?\s+reading/i,
  /truncat/i,
  /\blimit(?:ed|s)?\b/i,
  /\bfallback\b/i,
  /\bpartial(?:ly)?\b/i,
  /no cached result/i,
  /\bnot (?:parsed|imported|refreshed)\b/i,
  /\bcould not\b/i,
  /cannot be fully inspected/i,
  /may appear blank|missing values/i,
  /not used for row extraction/i,
  /active[- ]content|embedded|macro|vba|external links?|highly compressed/i,
];

const isMaterialLpoWarning = (warning) => MATERIAL_LPO_WARNING_PATTERNS.some(
  (pattern) => pattern.test(String(warning)),
);

const LpoWarningReview = ({ warnings = [] }) => {
  const reviewWarnings = Array.isArray(warnings)
    ? warnings.filter((warning) => String(warning || '').trim())
    : [];
  if (!reviewWarnings.length) return null;
  const alwaysVisibleIndexes = new Set(
    reviewWarnings.reduce((indexes, warning, index) => {
      if (index < 3 || isMaterialLpoWarning(warning)) indexes.push(index);
      return indexes;
    }, []),
  );
  const alwaysVisible = reviewWarnings.filter((warning, index) => alwaysVisibleIndexes.has(index));
  const remaining = reviewWarnings.filter((warning, index) => !alwaysVisibleIndexes.has(index));
  return (
    <div className="qm-lpo-warning" role="alert" aria-label="LPO attachment warnings">
      <strong>Review attachment warnings</strong>
      {alwaysVisible.map((warning, index) => (
        <p key={`lpo-warning-${index}`}>{warning}</p>
      ))}
      {remaining.length > 0 && (
        <details>
          <summary>
            Show {remaining.length} more {remaining.length === 1 ? 'warning' : 'warnings'}
          </summary>
          {remaining.map((warning, index) => (
            <p key={`lpo-warning-more-${index}`}>{warning}</p>
          ))}
        </details>
      )}
    </div>
  );
};


export default LpoWarningReview;
