// Tests du moteur de calcul Nelyqor — lancer avec : node --test tests/
import test from 'node:test';
import assert from 'node:assert/strict';
import { parseAmountToCents, parseRateToBp, contributionFor, grossNeededForNet, percentOf, formatCents, formatPercent } from '../js/core/money.js';
import { computeSnapshot, computePremium, countActivityDays, revenueSeries, buildRevenueAmounts } from '../js/core/calc.js';
import { weekday, startOfWeek, addMonths } from '../js/core/dates.js';

const baseSettings = (over = {}) => ({
  goalAmount: 1800000, targetDate: '2027-12-31', initialSavings: 300000,
  hasContributions: true, contributionRate: 2100, plannedActivityDays: [0, 1, 3, 4],
  displayName: '', setupDate: '2026-11-09', createdAt: '2026-11-09T10:00:00Z', ...over,
});
const st = (over = {}) => ({
  settings: baseSettings(over.settings), revenues: over.revenues || [], transfers: over.transfers || [],
  withdrawals: over.withdrawals || [], currentWeekState: { restDays: over.restDays || {} },
});
let n = 0;
const rev = (date, gross, rate = 2100) => ({ id: 'r' + n++, date, ...buildRevenueAmounts(gross, rate), label: '', createdAt: '2026-11-15T00:00:00Z' });
const amt = (date, amount) => ({ id: 'x' + n++, date, amount, label: '', createdAt: '2026-11-15T00:00:00Z' });

test('parsing des montants et taux', () => {
  assert.equal(parseAmountToCents('10,50'), 1050);
  assert.equal(parseAmountToCents('18 000'), 1800000);
  assert.equal(parseAmountToCents('0.1'), 10);
  assert.equal(parseAmountToCents('abc'), null);
  assert.equal(parseAmountToCents('1,234'), null);
  assert.equal(parseRateToBp('21'), 2100);
  assert.equal(parseRateToBp('21,5'), 2150);
});

test('TEST 3 — revenu 100 € brut à 21 % : 21 € cotisations, 79 € net, solde +79 €', () => {
  assert.equal(contributionFor(10000, 2100), 2100);
  const before = computeSnapshot(st(), '2026-11-15').balance;
  const after = computeSnapshot(st({ revenues: [rev('2026-11-15', 10000)] }), '2026-11-15').balance;
  assert.equal(after - before, 7900);
  assert.equal(before, 300000);
});

test('TEST 4 / 5 — virement +100 €, retrait −50 €', () => {
  const s0 = computeSnapshot(st(), '2026-11-15').balance;
  assert.equal(computeSnapshot(st({ transfers: [amt('2026-11-15', 10000)] }), '2026-11-15').balance - s0, 10000);
  assert.equal(computeSnapshot(st({ withdrawals: [amt('2026-11-15', 5000)] }), '2026-11-15').balance - s0, -5000);
});

test('brut nécessaire pour 79 € net à 21 % = 100 €', () => {
  assert.equal(grossNeededForNet(7900, 2100), 10000);
  assert.equal(grossNeededForNet(7900, 0), 7900);
});

test('TEST 6 / 7 — pourcentages de la semaine (non plafonnés)', () => {
  assert.equal(formatPercent(percentOf(18900, 29668)), '63,7 %');
  assert.equal(Math.round(percentOf(31900, 22000)), 145);
  assert.equal(formatCents(31900 - 22000, { sign: true }), '+99 €');
});

test('objectif semaine = objectif journalier × jours prévus, stable quand on ajoute un revenu', () => {
  // Lundi 16 nov. 2026, 4 jours prévus (lun, mar, jeu, ven), setup antérieur
  const today = '2026-11-16';
  const a = computeSnapshot(st(), today);
  assert.equal(a.weekActivityDays, 4);
  assert.equal(a.weeklyTarget, a.dailyGrossTarget * 4);
  const b = computeSnapshot(st({ revenues: [rev(today, 18900)] }), today);
  assert.equal(b.weeklyTarget, a.weeklyTarget, "l'objectif semaine ne baisse pas");
  assert.equal(b.weeklyRealized, 18900);
  assert.equal(b.weeklyRemaining, Math.max(0, a.weeklyTarget - 18900));
  assert.ok(b.weeklyTarget !== b.dailyGrossTarget, 'jamais objectif journalier = objectif semaine');
});

test('dépassement : % réel affiché, barre plafonnée à 100', () => {
  const today = '2026-11-16';
  const t = computeSnapshot(st(), today).weeklyTarget;
  const s = computeSnapshot(st({ revenues: [rev(today, Math.round(t * 1.45))] }), today);
  assert.ok(s.weeklyProgress > 144 && s.weeklyProgress < 146);
  assert.equal(s.weeklyBarPct, 100);
  assert.ok(s.weeklyExceeded);
  // exactement 100 % : atteint mais pas dépassé
  const e = computeSnapshot(st({ revenues: [rev(today, t)] }), today);
  assert.equal(e.weeklyProgress, 100);
  assert.ok(e.weeklyReached && !e.weeklyExceeded);
});

test('« Je ne travaille pas aujourd’hui » recalcule la semaine', () => {
  const today = '2026-11-16';
  const a = computeSnapshot(st(), today);
  const b = computeSnapshot(st({ restDays: { [today]: true } }), today);
  assert.equal(b.weekActivityDays, 3);
  assert.equal(b.todayIsActivity, false);
  assert.ok(b.weeklyTarget < a.weeklyTarget);
});

test('TEST 8 — modifier les jours recalcule', () => {
  const today = '2026-11-16';
  const a = computeSnapshot(st(), today);
  const b = computeSnapshot(st({ settings: { plannedActivityDays: [0, 1, 2, 3, 4] } }), today);
  assert.equal(b.weekActivityDays, 5);
  assert.ok(b.remainingActivityDays > a.remainingActivityDays);
  assert.ok(b.dailyGrossTarget < a.dailyGrossTarget);
});

test('0 jour d’activité : pas de division par zéro', () => {
  const s = computeSnapshot(st({ settings: { plannedActivityDays: [] } }), '2026-11-16');
  assert.equal(s.dailyGrossTarget, 0);
  assert.equal(s.weeklyTarget, 0);
  assert.equal(s.weeklyProgress, null);
  assert.ok(Number.isFinite(s.simpleProjection));
});

test('date cible dépassée : aucune valeur infinie', () => {
  const s = computeSnapshot(st({ settings: { targetDate: '2026-11-10' } }), '2026-11-16');
  assert.ok(s.targetReached);
  assert.equal(s.remainingActivityDays, 0);
  assert.ok(Number.isFinite(s.simpleProjection));
});

test('objectif atteint : restant = 0, solde réel conservé', () => {
  const s = computeSnapshot(st({ transfers: [amt('2026-11-10', 2000000)] }), '2026-11-16');
  assert.equal(s.remaining, 0);
  assert.ok(s.goalReached);
  assert.ok(s.globalPct > 100);
});

test('cotisations historiques conservées si le taux change', () => {
  const r = rev('2026-11-10', 10000, 2100);
  const s = st({ revenues: [r], settings: { contributionRate: 1500 } });
  assert.equal(s.revenues[0].netAmount, 7900);
  assert.equal(computeSnapshot(s, '2026-11-16').balance, 300000 + 7900);
});

test('projection : aucun revenu -> rythme nécessaire uniquement (atteint l’objectif pile)', () => {
  const s = computeSnapshot(st(), '2026-11-16');
  assert.equal(s.pace.status, 'none');
  assert.ok(Math.abs(s.simpleProjection - 1800000) <= 2);
});

test('projection : semaine entière saisie le 1er jour -> pas d’explosion', () => {
  // Setup jeudi 19/11, revenus réalisés lun-mar-mer-jeu (dates de réalisation, pas createdAt)
  const revenues = ['2026-11-16', '2026-11-17', '2026-11-18', '2026-11-19'].map((d) => rev(d, 40000));
  const s = computeSnapshot(st({ revenues, settings: { setupDate: '2026-11-19', plannedActivityDays: [0, 1, 2, 3, 4] } }), '2026-11-19');
  assert.equal(s.pace.observedActivityDays, 4, '4 jours différents observés');
  assert.ok(Math.abs(s.pace.projectionConfidence - 0.2) < 1e-9);
  const expected = s.pace.requiredNetPerActivityDay * 0.8 + s.pace.observedNetPerActivityDay * 0.2;
  assert.ok(Math.abs(s.pace.projectionDailyPace - expected) < 1e-6);
  assert.equal(s.pace.status, 'refining');
  assert.ok(s.simpleProjection < 4000000, `projection raisonnable : ${s.simpleProjection}`);
});

test('projection : exemple du cahier des charges 60 € / 120 € / 4 jours = 72 €', () => {
  const required = 6000, observed = 12000, conf = 4 / 20;
  assert.equal(required * (1 - conf) + observed * conf, 7200);
});

test('projection : virements et fonds initiaux exclus du rythme observé', () => {
  const revenues = [rev('2026-11-16', 10000)];
  const a = computeSnapshot(st({ revenues }), '2026-11-17');
  const b = computeSnapshot(st({ revenues, transfers: [amt('2026-11-16', 500000)] }), '2026-11-17');
  assert.equal(a.pace.observedNetPerActivityDay, b.pace.observedNetPerActivityDay);
});

test('projection : confiance 100 % après 20 jours', () => {
  const revenues = [];
  for (let i = 0; i < 40; i++) {
    const d = new Date(Date.UTC(2026, 9, 1 + i)).toISOString().slice(0, 10);
    revenues.push(rev(d, 10000));
  }
  const s = computeSnapshot(st({ revenues, settings: { setupDate: '2026-10-01' } }), '2026-11-16');
  assert.equal(s.pace.projectionConfidence, 1);
  assert.equal(s.pace.status, 'personal');
});

test('paliers Nelyqor+ : 6, 12, 18 mois et date cible', () => {
  const p = computePremium(st({ revenues: [rev('2026-11-16', 10000)] }), '2026-11-16');
  assert.deepEqual(p.milestones.map((m) => m.key), ['6m', '12m', '18m', 'target']);
  assert.equal(p.milestones[0].date, addMonths('2026-11-16', 6));
});

test('graphique : filtres S / M / 6M / 1A', () => {
  const s = st({ revenues: [rev('2026-11-16', 10000), rev('2026-06-01', 5000)] });
  assert.equal(revenueSeries(s, '2026-11-16', 'S').length, 7);
  assert.equal(revenueSeries(s, '2026-11-16', 'M').length, 30);
  assert.equal(revenueSeries(s, '2026-11-16', '6M').length, 26);
  const y = revenueSeries(s, '2026-11-16', '1A');
  assert.equal(y.length, 12);
  assert.equal(y.reduce((a, b) => a + b.value, 0), 15000);
});

test('dates : semaine lundi -> dimanche', () => {
  assert.equal(weekday('2026-11-16'), 0);
  assert.equal(startOfWeek('2026-11-22'), '2026-11-16');
  assert.equal(countActivityDays(baseSettings(), {}, '2026-11-16', '2026-11-22'), 4);
});
