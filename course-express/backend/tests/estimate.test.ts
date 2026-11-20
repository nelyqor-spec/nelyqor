import test from 'node:test';
import assert from 'node:assert/strict';
import {
  displayMinutes, estimate, EstimationError, secondsFromDuration, selectPlace, shortBanner, validateRequest, type Deps
} from '../src/estimate.js';

const NOW = Date.parse('2026-11-20T13:30:00Z');
const base = {
  restaurantName: 'Pizza Cosy - Aix en Provence',
  customerAddress: '230 avenue de Bagatelle, 13090 Aix-en-Provence, France',
  origin: { latitude: 43.52, longitude: 5.44, accuracyMeters: 10, capturedAt: new Date(NOW - 5000).toISOString() },
  platform: 'uber_eats'
};
const isCode = (code: string) => (e: unknown) => e instanceof EstimationError && e.code === code;

const PIZZA_COSY = {
  id: 'ChIJ_fixture_pizza_cosy', displayName: { text: 'Pizza Cosy' },
  formattedAddress: '475 Rte de Berre, 13090 Aix-en-Provence, France', location: { latitude: 43.515, longitude: 5.42 }
};

type Call = { url: string; body: any; headers: Record<string, string> };
function fakeFetch(handler: (c: Call) => { status: number; json?: unknown } | Promise<never>) {
  const calls: Call[] = [];
  const f = (async (url: string, init: RequestInit) => {
    const c = { url, body: init.body ? JSON.parse(String(init.body)) : undefined, headers: init.headers as Record<string, string> };
    calls.push(c);
    const r = await handler(c);
    return { ok: r.status >= 200 && r.status < 300, status: r.status, json: async () => r.json } as Response;
  }) as unknown as typeof fetch;
  return { calls, deps: { fetch: f, now: () => NOW, timeoutMs: 50 } satisfies Deps };
}
const happy = (c: Call) =>
  c.url.includes('searchText') ? { status: 200, json: { places: [PIZZA_COSY] } }
  : c.body.destination.address ? { status: 200, json: { routes: [{ duration: '421.4s', distanceMeters: 3900 }] } }
  : { status: 200, json: { routes: [{ duration: '119s', distanceMeters: 1500 }] } };

test('entrée valide', () => {
  assert.equal(validateRequest(base, NOW).restaurantName, 'Pizza Cosy - Aix en Provence');
});
test('adresse client absente ou incomplète => missing_address', () => {
  assert.throws(() => validateRequest({ ...base, customerAddress: '' }, NOW), isCode('missing_address'));
  assert.throws(() => validateRequest({ ...base, customerAddress: 'avenue de Bagatelle Aix' }, NOW), isCode('missing_address'));
});
test('GPS ancien, absent, imprécis ou 0,0 => location_unavailable', () => {
  assert.throws(() => validateRequest({ ...base, origin: { ...base.origin, capturedAt: '2020-01-01T00:00:00Z' } }, NOW), isCode('location_unavailable'));
  assert.throws(() => validateRequest({ ...base, origin: undefined }, NOW), isCode('location_unavailable'));
  assert.throws(() => validateRequest({ ...base, origin: { ...base.origin, accuracyMeters: 2000 } }, NOW), isCode('location_unavailable'));
  assert.throws(() => validateRequest({ ...base, origin: { ...base.origin, latitude: 0, longitude: 0 } }, NOW), isCode('location_unavailable'));
});
test('conversion de durée Google', () => {
  assert.equal(secondsFromDuration('121.8s'), 122);
  assert.throws(() => secondsFromDuration(undefined), isCode('route_unavailable'));
});
test('minutes cohérentes : a + b = total, total issu de la somme brute', () => {
  for (const [a, b] of [[120, 420], [89, 89], [61, 61], [29, 29], [150, 150], [0, 0], [3599, 1]]) {
    const d = displayMinutes(a, b);
    assert.equal(d.toRestaurantMinutes + d.restaurantToCustomerMinutes, d.totalMinutes);
    assert.equal(d.totalMinutes, Math.round((a + b) / 60));
    assert.ok(d.restaurantToCustomerMinutes >= 0);
  }
});
test('mock strictement identifié, somme exacte, bannière marquée DÉMO', async () => {
  const r = await estimate(validateRequest(base, NOW), undefined, true);
  assert.equal(r.travelMode, 'DRIVE');
  assert.equal(r.totalSeconds, r.toRestaurantSeconds + r.restaurantToCustomerSeconds);
  assert.equal(r.mock, true);
  assert.equal(r.trafficUsed, false);
  assert.equal(shortBanner(r), '[DÉMO – durées fictives] 🚗 Restaurant 2 min · Client 7 min · Total 9 min');
});
test('réel : Places puis 2 routes DRIVE/TRAFFIC_AWARE en parallèle, clé dans l\'en-tête uniquement', async () => {
  const { calls, deps } = fakeFetch(happy);
  const r = await estimate(validateRequest(base, NOW), 'KEY', false, deps);
  assert.equal(calls.length, 3);
  assert.match(calls[0].url, /places:searchText/);
  assert.equal(calls[0].body.languageCode, 'fr');
  assert.equal(calls[0].body.regionCode, 'FR');
  assert.ok(calls[0].body.locationBias.circle);
  const routes = calls.slice(1);
  for (const c of routes) {
    assert.match(c.url, /computeRoutes/);
    assert.equal(c.body.travelMode, 'DRIVE');
    assert.equal(c.body.routingPreference, 'TRAFFIC_AWARE');
    assert.equal(c.headers['X-Goog-FieldMask'], 'routes.duration,routes.distanceMeters');
    assert.equal(c.headers['X-Goog-Api-Key'], 'KEY');
    assert.ok(!c.url.includes('KEY'));
  }
  assert.deepEqual(routes[0].body.destination.location.latLng, { latitude: 43.515, longitude: 5.42 });
  assert.equal(routes[1].body.destination.address, base.customerAddress);
  assert.equal(r.toRestaurantSeconds, 119);
  assert.equal(r.restaurantToCustomerSeconds, 421);
  assert.equal(r.totalSeconds, 540);
  assert.equal(r.restaurant.address, PIZZA_COSY.formattedAddress);
  assert.equal(r.mock, false);
  assert.equal(shortBanner(r), '🚗 Restaurant 2 min · Client 7 min · Total 9 min');
});
test('restaurant introuvable => aucune route appelée', async () => {
  const { calls, deps } = fakeFetch(() => ({ status: 200, json: { places: [{ ...PIZZA_COSY, displayName: { text: 'Pizza Hut' } }] } }));
  await assert.rejects(estimate(validateRequest(base, NOW), 'K', false, deps), isCode('restaurant_not_found'));
  assert.equal(calls.length, 1);
});
test('homonymes => needs_confirmation avec choix triés par distance, aucune route', async () => {
  const other = { ...PIZZA_COSY, id: 'ChIJ_other_pizza_cosy', formattedAddress: '1 rue X, 13100 Marseille', location: { latitude: 43.3, longitude: 5.4 } };
  const { calls, deps } = fakeFetch(() => ({ status: 200, json: { places: [other, PIZZA_COSY] } }));
  const input = validateRequest({ ...base, restaurantName: 'Pizza Cosy' }, NOW);
  await assert.rejects(estimate(input, 'K', false, deps), (e: unknown) =>
    e instanceof EstimationError && e.code === 'needs_confirmation' && e.candidates![0].placeId === PIZZA_COSY.id);
  assert.equal(calls.length, 1);
});
test('homonymes départagés par le suffixe de ville', () => {
  const other = { ...PIZZA_COSY, id: 'ChIJ_other', formattedAddress: '1 rue X, 13100 Marseille', location: { latitude: 43.3, longitude: 5.4 } };
  assert.equal(selectPlace([other, PIZZA_COSY], 'Pizza Cosy - Aix en Provence', base.origin).id, PIZZA_COSY.id);
});
test('choix confirmé par l\'utilisateur (placeId) => Place Details puis routes', async () => {
  const { calls, deps } = fakeFetch(c => c.url.includes('/places/') ? { status: 200, json: PIZZA_COSY } : happy(c));
  const r = await estimate(validateRequest({ ...base, restaurantPlaceId: PIZZA_COSY.id }, NOW), 'K', false, deps);
  assert.match(calls[0].url, /places\/ChIJ_fixture_pizza_cosy/);
  assert.equal(r.restaurant.confidence, 'user_confirmed');
});
test('Google 429 => rate_limited ; 5xx => network_error ; timeout => network_error', async () => {
  const input = validateRequest(base, NOW);
  await assert.rejects(estimate(input, 'K', false, fakeFetch(() => ({ status: 429 })).deps), isCode('rate_limited'));
  await assert.rejects(estimate(input, 'K', false, fakeFetch(() => ({ status: 503 })).deps), isCode('network_error'));
  const hang = fakeFetch(c => c.url.includes('searchText') ? happy(c) : Promise.reject(new DOMException('aborted', 'AbortError')));
  await assert.rejects(estimate(input, 'K', false, hang.deps), isCode('network_error'));
});
test('pas d\'itinéraire => route_unavailable, jamais de durée inventée', async () => {
  const { deps } = fakeFetch(c => c.url.includes('searchText') ? happy(c) : { status: 200, json: {} });
  await assert.rejects(estimate(validateRequest(base, NOW), 'K', false, deps), isCode('route_unavailable'));
});
test('pas de clé et pas de mock => maps_not_configured', async () => {
  await assert.rejects(estimate(validateRequest(base, NOW), undefined, false), isCode('maps_not_configured'));
});
