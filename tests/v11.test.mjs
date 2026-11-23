// Tests des nouveautés 1.1 — lancer avec : node --test tests/v11.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { computeSnapshot, computePremium, buildRevenueAmounts, plannedTotalUntil, contributionSchedule, upcomingPlanned, simulate, microStatus } from '../js/core/calc.js';
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

test('cotisations : prélèvement à la date indiquée par l’utilisateur', () => {
  const revs = [rev('2026-10-05', 100000), rev('2026-11-02', 50000), rev('2026-09-20', 20000)];
  // Mensuel, prélevé le 5 : le 5 déc. porte sur novembre.
  const m = contributionSchedule(st({ settings: { contributionFrequency: 'monthly', contributionDueDate: '2026-12-05' }, revenues: revs }), T);
  assert.equal(m.needsDueDate, false);
  assert.equal(m.next.due, '2026-12-05');
  assert.equal(m.next.from, '2026-11-01');
  assert.equal(m.next.amount, 10500);
  assert.equal(m.following.due, '2027-01-05');
  // Le 3 nov. : le prélèvement du 5 nov. (octobre) n'est pas encore passé.
  const m2 = contributionSchedule(st({ settings: { contributionFrequency: 'monthly', contributionDueDate: '2026-12-05' }, revenues: revs }), '2026-11-03');
  assert.equal(m2.next.due, '2026-11-05');
  assert.equal(m2.next.amount, 21000);
  assert.ok(m2.next.complete);
  assert.equal(m2.following.amount, 10500, 'novembre déjà commencé : à garder pour le suivant');
  assert.equal(m2.toSetAside, 31500);
  // Trimestriel, date ancienne (l'app retrouve les échéances suivantes) : 31 janv. → oct.–déc.
  const q = contributionSchedule(st({ settings: { contributionFrequency: 'quarterly', contributionDueDate: '2025-01-31' }, revenues: revs }), T);
  assert.equal(q.next.due, '2027-01-31');
  assert.equal(q.next.from, '2026-10-01');
  assert.equal(q.toSetAside, 21000 + 10500);
  // Pas de date renseignée (version 1.1) : on le signale.
  assert.equal(contributionSchedule(st({ revenues: revs }), T).needsDueDate, true);
  assert.equal(computeSnapshot(st({ settings: { hasContributions: false, contributionRate: 0 } }), T).contributions, null);
});

test('calendrier : un dimanche travaillé exceptionnellement ne change que ce dimanche', () => {
  const base = st({ revenues: regular() });
  const a = computeSnapshot(base, T);
  const withSunday = { ...base, calendar: { '2026-11-22': 'work' } };
  const b = computeSnapshot(withSunday, T);
  assert.equal(b.remainingActivityDays, a.remainingActivityDays + 1, 'un seul jour en plus');
  assert.equal(b.weekActivityDays, a.weekActivityDays + 1);
  assert.ok(b.dailyNetTarget < a.dailyNetTarget, 'objectif du jour réparti sur un jour de plus');
  assert.ok(b.simpleProjection > a.simpleProjection, 'la projection gagne un jour de travail');
  // Les autres dimanches restent libres.
  assert.equal(computeSnapshot(withSunday, '2026-11-29').todayIsActivity, false);
  assert.equal(computeSnapshot(withSunday, '2026-11-22').todayIsActivity, true);
  assert.equal(computeSnapshot(withSunday, '2026-11-22').todayExtraWork, true);
  // Repos exceptionnel un mardi.
  const c = computeSnapshot({ ...base, calendar: { '2026-11-17': 'off' } }, T);
  assert.equal(c.remainingActivityDays, a.remainingActivityDays - 1);
});

test('micro-entreprise : plafond et franchise de TVA', () => {
  const revs = Array.from({ length: 10 }, (_, i) => rev(`2026-0${(i % 9) + 1}-10`, 350000));
  const s0 = st({ settings: { microActivity: 'services' }, revenues: revs });
  const snap = computeSnapshot(s0, T);
  const m = microStatus(s0, T, snap);
  assert.equal(m.revenue, 3500000);
  assert.equal(m.year, 2026);
  assert.equal(m.tvaLevel, 'near'); // 35 000 € sur 37 500 €
  assert.equal(m.plafondLevel, 'ok');
  assert.equal(microStatus(st(), T, snap), null);
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
  assert.equal(s.calendar['2026-11-03'], 'off', 'anciens jours de repos conservés');
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

test('projection par jour de la semaine (tous les revenus) : un lundi qui rapporte plus pèse plus', () => {
  // 12 semaines d'historique : lundis à 200 € brut, autres jours à 100 € brut.
  const revs = [];
  for (let i = 1; i <= 84; i++) {
    const d = new Date(Date.UTC(2026, 10, 16) - i * 864e5).toISOString().slice(0, 10);
    const w = (new Date(d + 'T12:00:00Z').getUTCDay() + 6) % 7;
    if (w > 4) continue;
    revs.push(rev(d, w === 0 ? 20000 : 10000));
  }
  const base = st({ settings: { setupDate: '2026-08-01' }, revenues: revs });
  const s0 = computeSnapshot(base, T);
  const p = s0.pace;
  assert.equal(p.status, 'personal');
  assert.ok(p.weekdayObservedNet[0] > p.weekdayObservedNet[1] * 1.9, 'le lundi rapporte environ deux fois plus');
  assert.equal(p.weekdaySamples[0], 12, 'tous les lundis avec revenu (12 semaines)');
  assert.equal(Math.round(p.weekdayObservedGross[0]), 20000, 'moyenne brute des lundis');
  // Retirer un lundi fait perdre plus que retirer un mardi.
  const offMonday = computeSnapshot({ ...base, calendar: { '2026-11-23': 'off' } }, T);
  const offTuesday = computeSnapshot({ ...base, calendar: { '2026-11-24': 'off' } }, T);
  const lossMon = s0.simpleProjection - offMonday.simpleProjection;
  const lossTue = s0.simpleProjection - offTuesday.simpleProjection;
  assert.ok(lossMon > lossTue * 1.6, `lundi ${lossMon} vs mardi ${lossTue}`);
  // Un samedi jamais travaillé compte pour la moyenne générale (ni 0, ni le rythme du lundi).
  const sat = computeSnapshot({ ...base, calendar: { '2026-11-21': 'work', '2026-11-28': 'work' } }, T);
  const gainSat = (sat.simpleProjection - s0.simpleProjection) / 2;
  assert.ok(gainSat > lossTue * 0.9 && gainSat < lossMon, `samedi ${gainSat}`);
});

test('rythme par jour : un jour prévu sans revenu ne fait pas baisser la moyenne du jour', () => {
  // Mardis : un à 100 €, un sans revenu (jour prévu non saisi) → moyenne du mardi = 100 €, pas 50 €.
  const s0 = st({ settings: { setupDate: '2026-11-01', plannedActivityDays: [1] }, revenues: [rev('2026-11-03', 10000)] });
  const snap = computeSnapshot(s0, '2026-11-12');
  assert.equal(snap.pace.weekdaySamples[1], 1);
  assert.equal(snap.pace.weekdayObservedGross[1], 10000);
});

test('historique importé : compte dans les moyennes, pas dans l’objectif ni les cotisations', () => {
  const now = [rev('2026-11-10', 6379), rev('2026-11-12', 11056)];
  const hist = [{ ...rev('2026-10-01', 7974), history: true }, { ...rev('2026-10-08', 7318), history: true }];
  const a = computeSnapshot(st({ settings: { setupDate: '2026-11-10' }, revenues: now }), T);
  const b = computeSnapshot(st({ settings: { setupDate: '2026-11-10' }, revenues: [...now, ...hist] }), T);
  assert.equal(b.balance, a.balance, 'le solde ne bouge pas');
  assert.equal(b.pace.weekdaySamples[3], 3, 'jeudis : 12 nov. + 1er et 8 oct.');
  assert.ok(b.pace.weekdayObservedGross[3] !== a.pace.weekdayObservedGross[3], 'la moyenne du jeudi change');
  const c = contributionSchedule(st({ settings: { contributionFrequency: 'monthly', contributionDueDate: '2026-11-05' }, revenues: hist }), '2026-11-03');
  assert.equal(c.toSetAside, 0, 'pas de cotisations sur l’historique');
  assert.equal(sanitize({ revenues: hist }).revenues.every((r) => r.history), true);
});
