const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const handler = require('serve-handler');
const config = require('../serve.json');

let directory;
let server;
let base;
before(async () => {
  directory = await fs.mkdtemp(path.join(os.tmpdir(), 'pharmacy-serve-test-'));
  await fs.mkdir(path.join(directory, 'static/js'), { recursive: true });
  await fs.writeFile(path.join(directory, 'index.html'), '<html>Pharmacy app entry</html>');
  await fs.writeFile(path.join(directory, 'asset-manifest.json'), '{"files":{}}');
  await fs.writeFile(path.join(directory, 'static/js/main.123abc.js'), 'window.appLoaded = true;');
  await fs.copyFile(path.join(__dirname, '../public/404.html'), path.join(directory, '404.html'));
  server = http.createServer((req, res) => handler(req, res, { ...config, public: directory }));
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
});
after(async () => {
  if (server) await new Promise((resolve) => server.close(resolve));
  if (directory) {
    const resolved = path.resolve(directory);
    assert.equal(path.dirname(resolved), path.resolve(os.tmpdir()));
    assert.ok(path.basename(resolved).startsWith('pharmacy-serve-test-'));
    await fs.rm(resolved, { recursive: true, force: true });
  }
});

test('SPA routes load the app without caching the entry document', async () => {
  for (const route of ['/', '/admin?tab=quotations&quote=21', '/admin/orders', '/about', '/login', '/register', '/forgot-password', '/reset-password/token', '/profile', '/product/123', '/checkout', '/order-confirmation']) {
    const response = await fetch(`${base}${route}`);
    assert.equal(response.status, 200, route);
    assert.match(response.headers.get('content-type'), /text\/html/, route);
    assert.equal(response.headers.get('cache-control'), 'no-store', route);
    assert.equal(response.headers.get('x-content-type-options'), 'nosniff', route);
    assert.match(await response.text(), /Pharmacy app entry/, route);
  }
});

test('missing code and styles return an uncached 404 instead of the app HTML', async () => {
  for (const route of ['/static/js/old.chunk.js', '/static/css/old.chunk.css']) {
    const response = await fetch(`${base}${route}`);
    assert.equal(response.status, 404, route);
    assert.equal(response.headers.get('cache-control'), 'no-store', route);
    assert.equal(response.headers.get('x-content-type-options'), 'nosniff', route);
    assert.doesNotMatch(await response.text(), /Pharmacy app entry/, route);
  }
});

test('real hashed assets retain long caching while the manifest stays fresh', async () => {
  const asset = await fetch(`${base}/static/js/main.123abc.js`);
  assert.equal(asset.status, 200);
  assert.match(asset.headers.get('content-type'), /javascript/);
  assert.equal(asset.headers.get('cache-control'), 'public, max-age=31536000, immutable');
  assert.match(await asset.text(), /appLoaded/);
  const manifest = await fetch(`${base}/asset-manifest.json`);
  assert.equal(manifest.status, 200);
  assert.equal(manifest.headers.get('cache-control'), 'no-store');
});
