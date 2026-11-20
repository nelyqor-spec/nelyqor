import test from 'node:test';
import assert from 'node:assert/strict';
import type { AddressInfo } from 'node:net';
import { authorized, createApp } from '../src/server.js';

const TOKEN = 'x'.repeat(32);
async function withServer(cfg: Partial<Parameters<typeof createApp>[0]>, fn: (url: string, logs: string[]) => Promise<void>) {
  const logs: string[] = [];
  const srv = createApp({ token: TOKEN, mock: true, ratePerMinute: 100, dailyCap: 100, log: l => logs.push(l), ...cfg });
  await new Promise<void>(r => srv.listen(0, '127.0.0.1', r));
  try { await fn(`http://127.0.0.1:${(srv.address() as AddressInfo).port}`, logs); } finally { srv.close(); }
}
const body = () => JSON.stringify({
  requestId: 'req-12345678',
  restaurantName: 'Pizza Cosy - Aix en Provence',
  customerAddress: '230 avenue de Bagatelle, 13090 Aix-en-Provence, France',
  origin: { latitude: 43.52, longitude: 5.44, accuracyMeters: 10, capturedAt: new Date().toISOString() }
});
const post = (url: string, b: string, token = TOKEN) =>
  fetch(`${url}/v1/estimate`, { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: b });

test('auth en temps constant', () => {
  assert.equal(authorized(`Bearer ${TOKEN}`, TOKEN), true);
  assert.equal(authorized('Bearer nope', TOKEN), false);
  assert.equal(authorized(undefined, TOKEN), false);
});
test('POST /v1/estimate (mock) : 200 + bannière DÉMO ; journaux sans adresse', async () => {
  await withServer({}, async (url, logs) => {
    const r = await post(url, body());
    assert.equal(r.status, 200);
    const j = await r.json();
    assert.equal(j.mock, true);
    assert.equal(j.requestId, 'req-12345678');
    assert.match(j.banner, /^\[DÉMO/);
    assert.equal(r.headers.get('cache-control'), 'no-store');
    const all = logs.join('\n');
    assert.ok(!/Bagatelle|Pizza|43\.52/.test(all), all);
  });
});
test('401 sans jeton, 400 JSON invalide, 413 trop gros, 409 needs_confirmation', async () => {
  await withServer({}, async url => {
    assert.equal((await post(url, body(), 'bad')).status, 401);
    assert.equal((await post(url, '{')).status, 400);
    assert.equal((await post(url, 'x'.repeat(9000))).status, 413);
    const r = await post(url, body().replace('Pizza Cosy - Aix en Provence', 'Resto ambigu'));
    assert.equal(r.status, 409);
    const j = await r.json();
    assert.equal(j.status, 'needs_confirmation');
    assert.equal(j.candidates.length, 2);
  });
});
test('limitation de débit', async () => {
  await withServer({ ratePerMinute: 2 }, async url => {
    assert.equal((await post(url, body())).status, 200);
    assert.equal((await post(url, body())).status, 200);
    assert.equal((await post(url, body())).status, 429);
  });
});
