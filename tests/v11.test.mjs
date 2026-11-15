// Tests des nouveautés 1.1 — lancer avec : node --test tests/v11.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { computeSnapshot, computePremium, buildRevenueAmounts, plannedTotalUntil, contributionSchedule, upcomingPlanned, simulate } from '../js/core/calc.js';
import { sanitize, emptyState } from '../js/core/store.js';
import { mergeStates } from '../js/core/sync.js';
import { buildCSV } from '../js/ui/premium.js';

const T = '2026-11-16'; // lundi
let n = 0;
const settings = (over = {}) => ({
  goalAmount: 1800000, targetDate: '2027-09-11', initialSavings: 0, hasContributions: true, contributionRate: 2100,
  contributionFrequency: 'quarterly', plannedActivityDays: [0, 1, 2, 3, 4], displayName: '', setupDate: '2026-10-01', createdAt: '2026-10-01T00:00:00Z', ...over,
});
const st = (over = {}) => ({
  settings: settings(over.settings), revenues: over.revenues || [], transfers: over.transfers || [], withdrawals: over.withdrawals || [],
  plannedExpenses: over.plannedExpenses || [], categories: [], currentWeekState: { restDays: {} },
});
const rev = (date, gross, extra = {}) => ({ id: 'r' + n++, date, ...buildRevenueAmounts(gross, 2100), contributionRate: 2100, label: '', createdAt: '2026-11-01T00:00:00Z', ...extra });
const tr = (date, amount) => ({ id: 't' + n++, date, amount, label: '', createdAt: '2026-11-01T00:00:00Z' });
const regular = () => Array.from({ length: 30 }, (_, i) => rev(`2026-10-${String(i + 1).padStart(2, '0')}`, 15000));

test('un virement est compté une seule fois : la projection augmente exactement de son montant', () => {
  const a = computeSnapshot(st({ revenues: regular() }), T);
  const b = computeSnapshot(st({ revenues: regular(), transfers: [tr('2026-11-10', 300000)] }), T);
  assert.equal(b.pace.status, 'personal');
  assert.equal(b.simpleProjection - a.simpleProjection, 300000);
  const pa = computePremium(st({ revenues: regular() }), T, a);
  const pb = computePremium(st({ revenues: regular(), transfers: [tr('2026-11-10', 300000)] }), T, b);
  pa.milestones.forEach((m, i) => assert.equal(pb.milestones[i].amount - m.amount, 300000, m.key));
});

test('un revenu exceptionnel n’est pas extrapolé (il ne « gonfle » pas la projection)', () => {
  const base = st({ revenues: regular() });
  const withNormal = st({ revenues: [...regular(), rev('2026-11-13', 300000)] });
  const withExc = st({ revenues: [...regular(), rev('2026-11-13', 300000, { exceptional: true })] });
  const s0 = computeSnapshot(base, T);
  const sN = computeSnapshot(withNormal, T);
  const sE = computeSnapshot(withExc, T);
  const net = buildRevenueAmounts(300000, 2100).netAmount;
  // Exceptionnel : +net une seule fois.
  assert.equal(sE.simpleProjection - s0.simpleProjection, net);
  // Revenu normal du même montant : extrapolé, donc bien plus.
  assert.ok(sN.simpleProjection - s0.simpleProjection > net * 3);
});

test('dépenses prévues récurrentes : déduites de la projection et de l’objectif du jour', () => {
  const plan = { id: 'p1', label: 'Assurance', amount: 200000, categoryId: null, startDate: '2026-12-01', everyMonths: 3, done: 0, createdAt: 'x' };
  // 1 déc. 2026, 1 mars, 1 juin, 1 sept. 2027 → 4 échéances avant le 11/09/2027
  assert.equal(plannedTotalUntil(st({ plannedExpenses: [plan] }), '2027-09-11'), 800000);
  const a = computeSnapshot(st({ revenues: regular() }), T);
  const b = computeSnapshot(st({ revenues: regular(), plannedExpenses: [plan] }), T);
  assert.equal(a.simpleProjection - b.simpleProjection, 800000);
  assert.ok(b.dailyNetTarget > a.dailyNetTarget, 'il faut gagner plus par jour pour couvrir la dépense');
  assert.equal(b.balance, a.balance, 'le solde actuel ne change pas');
});

test('dépense ponctuelle et échéance validée', () => {
  const once = { id: 'p2', label: 'Ordinateur', amount: 120000, categoryId: null, startDate: '2027-01-15', everyMonths: 0, done: 0, createdAt: 'x' };
  assert.equal(plannedTotalUntil(st({ plannedExpenses: [once] }), '2027-09-11'), 120000);
  assert.equal(plannedTotalUntil(st({ plannedExpenses: [{ ...once, done: 1 }] }), '2027-09-11'), 0);
  const monthly = { id: 'p3', label: 'Essence', amount: 7000, categoryId: null, startDate: '2026-11-10', everyMonths: 1, done: 0, createdAt: 'x' };
  const due = upcomingPlanned(st({ plannedExpenses: [monthly] }), T, 0);
  assert.equal(due.length, 1);
  assert.ok(due[0].overdue);
});

test('objectif de la semaine stable quand on valide une dépense prévue de la semaine', () => {
  const plan = { id: 'p4', label: 'Pneu', amount: 30000, categoryId: null, startDate: '2026-11-17', everyMonths: 0, done: 0, createdAt: 'x' };
  const before = computeSnapshot(st({ plannedExpenses: [plan] }), T);
  const after = computeSnapshot(st({
    plannedExpenses: [{ ...plan, done: 1 }],
    withdrawals: [{ id: 'w1', date: '2026-11-17', amount: 30000, label: 'Pneu', createdAt: 'x' }],
  }), '2026-11-17');
  assert.equal(after.weeklyTarget, before.weeklyTarget);
});

test('cotisations trimestrielles : montant à garder et échéance', () => {
  const s = st({ revenues: [rev('2026-10-05', 100000), rev('2026-11-02', 50000), rev('2026-09-20', 20000)] });
  // T = 16 nov. : le T3 (juil.–sept.) est dû le 31 oct. → déjà passé. Trimestre en cours : oct.–déc., dû le 31 janv.
  const c = contributionSchedule(s, T);
  assert.equal(c.next.due, '2027-01-31');
  assert.equal(c.toSetAside, 21000 + 10500);
  // Le 15 oct., le trimestre précédent (avec la cotisation de septembre) est encore dû.
  const c2 = contributionSchedule(s, '2026-10-15');
  assert.equal(c2.next.due, '2026-10-31');
  assert.ok(c2.next.closed);
  assert.equal(c2.toSetAside, 4200 + 21000);
  // Mensuel
  const m = contributionSchedule(st({ settings: { contributionFrequency: 'monthly' }, revenues: [rev('2026-11-02', 50000)] }), T);
  assert.equal(m.next.due, '2026-12-31');
  assert.equal(computeSnapshot(st({ settings: { hasContributions: false, contributionRate: 0 } }), T).contributions, null);
});

test('simulateur : tient compte des dépenses prévues', () => {
  const plan = { id: 'p5', label: 'X', amount: 100000, categoryId: null, startDate: '2027-01-01', everyMonths: 0, done: 0, createdAt: 'x' };
  const a = simulate(st(), T, { dailyGross: 10000, daysPerWeek: 5 });
  const b = simulate(st({ plannedExpenses: [plan] }), T, { dailyGross: 10000, daysPerWeek: 5 });
  assert.equal(a.projectedAtTarget - b.projectedAtTarget, 100000);
});

test('validation : données corrompues ou piégées nettoyées', () => {
  const s = sanitize({
    onboardingCompleted: true,
    settings: { ...settings(), contributionRate: 99999, plannedActivityDays: [1, 1, 9, 'x'], displayName: 'A'.repeat(500) },
    revenues: [{ id: '<x>', date: '2026-01-01', grossAmount: 10 }, { id: 'ok', date: '2026-01-01', grossAmount: 100, contributionAmount: 500 }, { id: 'ok2', date: '2026-01-01', grossAmount: 1.5, contributionAmount: 0 }],
    withdrawals: [{ id: 'w', date: '2026-02-30', amount: 100 }],
    preferences: { accentColor: 'red;background:url(x)' },
    profile: { avatar: 'data:text/html;base64,PHNjcmlwdD4=' },
    subscription: { status: 'god-mode' },
    deleted: { 'a b': 'x' },
  });
  assert.equal(s.revenues.length, 0);
  assert.equal(s.withdrawals.length, 0);
  assert.equal(s.settings.contributionRate, 0);
  assert.deepEqual(s.settings.plannedActivityDays, [1]);
  assert.equal(s.settings.displayName.length, 30);
  assert.equal(s.preferences.accentColor, '#2ED3B7');
  assert.equal(s.profile.avatar, null);
  assert.equal(s.subscription.status, 'free');
  assert.deepEqual(s.deleted, {});
  assert.deepEqual(sanitize('pas un objet'), emptyState());
});

test('migration : les données de la version 1.0 sont conservées', () => {
  const v1 = {
    version: 1, onboardingCompleted: true,
    settings: { goalAmount: 1800000, targetDate: '2027-09-11', initialSavings: 250000, hasContributions: true, contributionRate: 2100, plannedActivityDays: [0, 1], displayName: 'Léa', setupDate: '2026-11-01', createdAt: '2026-11-01T00:00:00Z' },
    revenues: [{ id: 'abc', date: '2026-11-02', grossAmount: 10000, contributionAmount: 2100, netAmount: 7900, contributionRate: 2100, label: 'Mission', createdAt: '2026-11-02T10:00:00Z' }],
    transfers: [], withdrawals: [], subscription: { status: 'trial', trialEndsAt: '2026-11-08T00:00:00Z', trialUsed: true },
    preferences: { dynamicBackgroundEnabled: false, dailyQuoteEnabled: true }, currentWeekState: { restDays: { '2026-11-03': true } },
  };
  const s = sanitize(v1);
  assert.equal(s.version, 2);
  assert.equal(s.revenues[0].label, 'Mission');
  assert.equal(s.revenues[0].exceptional, false);
  assert.equal(s.settings.contributionFrequency, 'quarterly');
  assert.equal(s.settings.displayName, 'Léa');
  assert.equal(s.preferences.dynamicBackgroundEnabled, false);
  assert.ok(s.currentWeekState.restDays['2026-11-03']);
});

test('fusion cloud : union, suppressions, rubrique la plus récente', () => {
  const base = sanitize({ onboardingCompleted: true, settings: settings(), revenues: [] });
  const r1 = rev('2026-11-02', 1000);
  const r2 = rev('2026-11-03', 2000);
  const local = { ...base, revenues: [r1, r2], preferences: { ...base.preferences, accentColor: '#4ADE80' }, updatedAt: { preferences: '2026-11-10T00:00:00Z' } };
  const remote = { ...base, revenues: [r1], deleted: { [r2.id]: '2026-11-11T00:00:00Z' }, settings: { ...base.settings, goalAmount: 2000000 }, updatedAt: { settings: '2026-11-12T00:00:00Z', preferences: '2026-11-01T00:00:00Z' } };
  const m = mergeStates(local, remote);
  assert.deepEqual(m.revenues.map((r) => r.id), [r1.id], 'supprimé sur un appareil = supprimé partout');
  assert.equal(m.settings.goalAmount, 2000000, 'réglages les plus récents');
  assert.equal(m.preferences.accentColor, '#4ADE80', 'préférences les plus récentes');
  // Appareil neuf (pas d'onboarding) : on prend le compte tel quel.
  assert.equal(mergeStates(emptyState(), remote).settings.goalAmount, 2000000);
  // Compte vide : on garde l'appareil.
  assert.equal(mergeStates(local, emptyState()).revenues.length, 2);
});

test('export CSV : injection de formules neutralisée', () => {
  const s = sanitize({ onboardingCompleted: true, settings: settings(), revenues: [{ ...rev('2026-11-02', 1000), label: '=HYPERLINK("http://x")' }] });
  const csv = buildCSV(s);
  assert.ok(csv.includes(`"'=HYPERLINK(""http://x"")"`));
  assert.ok(!/;"=/.test(csv));
});
