const DEFAULT_BROWSER_ORIGIN = 'http://localhost';
const MAX_DECODE_PASSES = 8;
const PERCENT_ESCAPE_PATTERN = /%[0-9a-f]{2}/i;

const browserOrigin = () => (
  typeof window !== 'undefined' && window.location?.origin
    ? window.location.origin
    : DEFAULT_BROWSER_ORIGIN
);

const containsControlCharacter = (value) => Array.from(value).some((character) => {
  const codePoint = character.codePointAt(0);
  return (
    codePoint <= 0x1f
    || (codePoint >= 0x7f && codePoint <= 0x9f)
    || (codePoint >= 0xd800 && codePoint <= 0xdfff)
    || codePoint === 0x2028
    || codePoint === 0x2029
  );
});

const safeBaseUrl = (origin) => {
  try {
    const base = new URL(origin || browserOrigin());
    if (
      !['http:', 'https:'].includes(base.protocol)
      || base.username
      || base.password
      || base.origin === 'null'
    ) {
      return null;
    }
    return new URL('/', base.origin);
  } catch {
    return null;
  }
};

const inspectRedirectLayer = (value, base) => {
  let normalized;
  try {
    normalized = value.normalize('NFKC');
  } catch {
    return null;
  }

  if (
    !normalized.startsWith('/')
    || normalized.startsWith('//')
    || normalized.includes('\\')
    || containsControlCharacter(normalized)
  ) {
    return null;
  }

  try {
    const resolved = new URL(normalized, base);
    if (
      resolved.origin !== base.origin
      || !['http:', 'https:'].includes(resolved.protocol)
      || resolved.username
      || resolved.password
      || resolved.pathname.startsWith('//')
    ) {
      return null;
    }
    return { normalized, resolved };
  } catch {
    return null;
  }
};

/**
 * Return a canonical same-origin application path, or null when the value is
 * not safe to use as a client-side redirect target.
 *
 * Every percent-decoding layer is inspected so encoded authority markers,
 * backslashes, and control characters cannot become dangerous after a router
 * or browser performs additional normalization.
 */
export const normalizeSameOriginInternalPath = (candidate, { origin } = {}) => {
  if (
    typeof candidate !== 'string'
    || !candidate
    || candidate !== candidate.trim()
  ) {
    return null;
  }

  const base = safeBaseUrl(origin);
  if (!base) return null;

  let layer = candidate;
  let canonical = null;

  for (let pass = 0; pass < MAX_DECODE_PASSES; pass += 1) {
    const inspected = inspectRedirectLayer(layer, base);
    if (!inspected) return null;
    if (pass === 0) {
      canonical = `${inspected.resolved.pathname}${inspected.resolved.search}${inspected.resolved.hash}`;
    }

    let decoded;
    try {
      decoded = decodeURIComponent(inspected.normalized);
    } catch {
      // The submitted URL itself must be well formed. A later pass may expose
      // a literal percent from a legitimate `%25` query value; browsers do not
      // decode that value again, and the already-inspected target is still
      // same-origin.
      return pass > 0 && !PERCENT_ESCAPE_PATTERN.test(inspected.normalized)
        ? canonical
        : null;
    }
    if (decoded === inspected.normalized) return canonical;
    layer = decoded;
  }

  // Reject excessively nested encodings instead of accepting a value whose
  // eventual browser/router interpretation was not inspected.
  try {
    return decodeURIComponent(layer) === layer ? canonical : null;
  } catch {
    return PERCENT_ESCAPE_PATTERN.test(layer) ? null : canonical;
  }
};

export const internalPathOrHome = (candidate, options) => (
  normalizeSameOriginInternalPath(candidate, options) || '/'
);
