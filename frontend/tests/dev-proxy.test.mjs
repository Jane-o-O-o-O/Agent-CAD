import assert from 'node:assert/strict';
import { createServer, get } from 'node:http';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { createServer as createViteServer } from 'vite';

const frontendRoot = fileURLToPath(new URL('../', import.meta.url));

function request(port, host) {
  return new Promise((resolve, reject) => {
    get({ hostname: '127.0.0.1', port, path: '/api/v1/auth/status', headers: { Host: host } }, response => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', chunk => { body += chunk; });
      response.on('end', () => resolve({ status: response.statusCode, body }));
    }).on('error', reject);
  });
}

test('Compose frontend hostname proxies API requests and unrelated hosts stay blocked', async () => {
  const received = [];
  const backend = createServer((req, res) => {
    received.push(req.url);
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ data: { auth_provider: 'none' } }));
  });
  await new Promise(resolve => backend.listen(0, '127.0.0.1', resolve));
  const oldBackendUrl = process.env.BACKEND_URL;
  process.env.BACKEND_URL = `http://127.0.0.1:${backend.address().port}`;
  let vite;
  try {
    vite = await createViteServer({
      root: frontendRoot,
      configFile: `${frontendRoot}vite.config.ts`,
      logLevel: 'silent',
      server: { host: '127.0.0.1', port: 0, hmr: false },
    });
    await vite.listen();
    const port = vite.httpServer.address().port;
    const proxied = await request(port, `frontend-dev:${port}`);
    assert.equal(proxied.status, 200, proxied.body);
    assert.equal(JSON.parse(proxied.body).data.auth_provider, 'none');
    assert.deepEqual(received, ['/api/v1/auth/status']);

    const blocked = await request(port, 'untrusted.example');
    assert.equal(blocked.status, 403);
    assert.match(blocked.body, /Blocked request/);
    assert.equal(received.length, 1, 'An untrusted Host must not reach the backend');
  } finally {
    if (oldBackendUrl === undefined) delete process.env.BACKEND_URL;
    else process.env.BACKEND_URL = oldBackendUrl;
    await vite?.close();
    await new Promise(resolve => backend.close(resolve));
  }
});
