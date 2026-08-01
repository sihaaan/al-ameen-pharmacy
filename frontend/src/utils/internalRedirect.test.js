import {
  internalPathOrHome,
  normalizeSameOriginInternalPath,
} from './internalRedirect';

const ORIGIN = 'https://app.example';

describe('normalizeSameOriginInternalPath', () => {
  test.each([
    ['/', '/'],
    ['/admin', '/admin'],
    [
      '/admin/quotations?quote_id=42&tab=editor#lines',
      '/admin/quotations?quote_id=42&tab=editor#lines',
    ],
    [
      '/admin?gmail_import=opaque%2Ftoken#review',
      '/admin?gmail_import=opaque%2Ftoken#review',
    ],
    [
      '/admin?source=https%3A%2F%2Fcustomer.example%2Frfq',
      '/admin?source=https%3A%2F%2Fcustomer.example%2Frfq',
    ],
    ['/admin?discount=100%25', '/admin?discount=100%25'],
    ['/admin?token=opaque%2525value', '/admin?token=opaque%2525value'],
    ['/products/first%20aid?unit=box#details', '/products/first%20aid?unit=box#details'],
  ])('allows same-origin application target %s', (candidate, expected) => {
    expect(
      normalizeSameOriginInternalPath(candidate, { origin: ORIGIN })
    ).toBe(expected);
  });

  test.each([
    null,
    undefined,
    '',
    42,
    ' /admin',
    '/admin ',
    'https://evil.example/steal',
    'http://evil.example/steal',
    '//evil.example/steal',
    '///evil.example/steal',
    'javascript:alert(1)',
    'data:text/html,owned',
    'file:///etc/passwd',
    'mailto:attacker@example.com',
    '\\evil.example',
    '\\/evil.example',
    '/\\evil.example',
    '/admin\\evil',
    '/%5cevil.example',
    '/%255cevil.example',
    '/%255c%255cevil.example?discount=100%25',
    '/%255c%252fevil.example?discount=100%25',
    '/%EF%BC%BCevil.example',
    '/%EF%BC%8Fevil.example',
    '/%2f%2fevil.example',
    '/%252f%252fevil.example',
    '/%252f%252fevil.example?discount=100%25',
    '/%2e%2e//evil.example',
    '/admin\n/quotation',
    '/admin\u0000/quotation',
    '/admin%0d%0aLocation%3a%20https%3a%2f%2fevil.example',
    '/admin%250d%250aLocation%253a%2520https%253a%252f%252fevil.example',
    '/%2500evil?discount=100%25',
    '/admin%',
    '/admin%ZZ',
    '/admin\uD800',
  ])('rejects unsafe redirect target %p', (candidate) => {
    expect(
      normalizeSameOriginInternalPath(candidate, { origin: ORIGIN })
    ).toBeNull();
  });

  test('rejects targets when the configured base is malformed or non-HTTP', () => {
    expect(
      normalizeSameOriginInternalPath('/admin', { origin: 'not a URL' })
    ).toBeNull();
    expect(
      normalizeSameOriginInternalPath('/admin', { origin: 'data:text/plain,base' })
    ).toBeNull();
    expect(
      normalizeSameOriginInternalPath('/admin', {
        origin: 'https://user:pass@app.example',
      })
    ).toBeNull();
  });

  test('rejects encodings nested beyond the inspected normalization limit', () => {
    let nested = '%2f%2fevil.example';
    for (let pass = 0; pass < 10; pass += 1) {
      nested = encodeURIComponent(nested);
    }
    expect(
      normalizeSameOriginInternalPath(`/safe/${nested}`, { origin: ORIGIN })
    ).toBeNull();
  });

  test('falls back to the application home route for an unsafe target', () => {
    expect(internalPathOrHome('//evil.example', { origin: ORIGIN })).toBe('/');
  });
});
