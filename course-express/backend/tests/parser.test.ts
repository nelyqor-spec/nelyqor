import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { normalizeAddress, OfferParseError, parseOffer } from '../src/parser.js';

const fx = (name: string) => readFileSync(fileURLToPath(new URL(`../../../fixtures/ocr/${name}`, import.meta.url)), 'utf8');

test('Uber Eats Pizza Cosy : restaurant et adresse client', () => {
  const r = parseOffer(fx('uber_pizza_cosy.txt'));
  assert.equal(r.platform, 'uber_eats');
  assert.equal(r.restaurantName, 'Pizza Cosy - Aix en Provence');
  assert.equal(r.customerAddressRaw, '230 Av. de Bagatelle, 13090, Aix-en-Provence');
  assert.equal(r.customerAddress, '230 avenue de Bagatelle, 13090 Aix-en-Provence, France');
});
test('restaurant jamais confondu avec l\'adresse client ni avec un libellé de carte', () => {
  const r = parseOffer(fx('uber_pizza_cosy.txt'));
  assert.notEqual(r.restaurantName, r.customerAddressRaw);
  assert.notEqual(r.restaurantName, 'Pizza'); // libellé de la carte en arrière-plan
});
test('0.9 km n\'est jamais repris ; aucune adresse de restaurant déduite de la capture', () => {
  const r = parseOffer(fx('uber_pizza_cosy.txt'));
  const json = JSON.stringify(r);
  assert.ok(!json.includes('0.9'));
  assert.ok(!/route de berre/i.test(json));
  assert.deepEqual(Object.keys(r).sort(), ['customerAddress', 'customerAddressRaw', 'platform', 'restaurantName']);
});
test('adresse sur une seule ligne ou coupée sur deux lignes => même résultat', () => {
  assert.equal(parseOffer(fx('uber_pizza_cosy_one_line.txt')).customerAddress, parseOffer(fx('uber_pizza_cosy.txt')).customerAddress);
});
test('adresse client absente => erreur explicite', () => {
  assert.throws(() => parseOffer(fx('uber_no_address.txt')), (e: unknown) => e instanceof OfferParseError && e.code === 'missing_address');
  assert.throws(() => parseOffer(''), (e: unknown) => e instanceof OfferParseError && e.code === 'no_text');
});
test('Deliveroo (SYNTHÉTIQUE, à valider sur vraie capture) : libellés sur lignes séparées', () => {
  const r = parseOffer(fx('SYNTHETIQUE_deliveroo_1.txt'));
  assert.equal(r.platform, 'deliveroo');
  assert.equal(r.restaurantName, 'Le Petit Café Crème');
  assert.equal(r.customerAddress, '12 boulevard du Roi René, 13100 Aix-en-Provence, France');
});
test('Deliveroo (SYNTHÉTIQUE) : libellés en ligne', () => {
  const r = parseOffer(fx('SYNTHETIQUE_deliveroo_2.txt'));
  assert.equal(r.restaurantName, 'Sushi Shop - Aix Sextius');
  assert.equal(r.customerAddress, '4 route de Galice, 13090 Aix-en-Provence, France');
});
test('normalisation : abréviations, accents conservés, Aix-en-Provence, pays', () => {
  assert.equal(normalizeAddress('230 Av. de Bagatelle, 13090, Aix en Provence'), '230 avenue de Bagatelle, 13090 Aix-en-Provence, France');
  assert.equal(normalizeAddress('5 bis Bd   Carnot 13100 Aix-en-Provence'), '5 bis boulevard Carnot 13100 Aix-en-Provence, France');
  assert.equal(normalizeAddress('7 Ch. des Pinchinats, 13100 Aix-en-Provence'), '7 chemin des Pinchinats, 13100 Aix-en-Provence, France');
  assert.equal(normalizeAddress('3 Rue St Jean, 13002 Marseille, France'), '3 Rue Saint Jean, 13002 Marseille, France');
  assert.equal(normalizeAddress('1 Pl. de l\'Hôtel de Ville, 13100 Aix-en-Provence'), '1 place de l\'Hôtel de Ville, 13100 Aix-en-Provence, France');
});
