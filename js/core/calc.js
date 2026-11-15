// Nelyqor — SOURCE UNIQUE DES CALCULS.
// Aucun composant d'interface ne refait un calcul financier : tout passe par
// computeSnapshot() / computePremium() / simulate() / revenueSeries().
// Tous les montants sont en centimes (entiers). Taux en points de base.

import { contributionFor, grossNeededForNet, percentOf, netFor } from './money.js';
import {
  addDays, addMonths, diffDays, weekday, startOfWeek, endOfWeek,
  startOfMonth, endOfMonth, minKey, maxKey,
} from './dates.js';

/** Nombre de jours d'activité observés pour une confiance de 100 % dans le rythme réel. */
export const PROJECTION_FULL_CONFIDENCE_DAYS = 20;
/** Fenêtre récente (en jours d'activité observés) utilisée pour le rythme réel. */
export const PROJECTION_RECENT_WINDOW_DAYS = 30;
/** Seuil sous lequel la projection est « en cours d'affinage ». */
export const PROJECTION_REFINING_DAYS = 5;
/** Limite de recherche en arrière pour l'historique observé (3 ans). */
const OBSERVATION_LOOKBACK_DAYS = 365 * 3;

const sum = (arr, fn) => arr.reduce((acc, x) => acc + fn(x), 0);
const restDaysOf = (state) => (state.currentWeekState && state.currentWeekState.restDays) || {};

// ---------------------------------------------------------------------------
// Jours d'activité
// ---------------------------------------------------------------------------

/** Le jour `k` est-il un jour d'activité ? (planning habituel + journées passées en repos) */
export function isActivityDay(settings, restDays, k) {
  if (restDays && restDays[k]) return false;
  return settings.plannedActivityDays.includes(weekday(k));
}

/** Nombre de jours d'activité entre from et to inclus. */
export function countActivityDays(settings, restDays, from, to) {
  if (!from || !to || from > to) return 0;
  const planned = new Set(settings.plannedActivityDays);
  if (planned.size === 0) return 0;
  const total = diffDays(from, to) + 1;
  let count = Math.floor(total / 7) * planned.size;
  const startWd = weekday(from);
  for (let i = 0; i < total % 7; i++) if (planned.has((startWd + i) % 7)) count++;
  for (const [k, isRest] of Object.entries(restDays || {})) {
    if (isRest && k >= from && k <= to && planned.has(weekday(k))) count--;
  }
  return count;
}

/** Date du n-ième jour d'activité à partir de `from` (inclus). null si impossible. */
export function nthActivityDayFrom(settings, restDays, from, n, maxDays = 365 * 30) {
  if (n <= 0) return from;
  if (!settings.plannedActivityDays.length) return null;
  let k = from;
  let c = 0;
  for (let i = 0; i < maxDays; i++) {
    if (isActivityDay(settings, restDays, k) && ++c >= n) return k;
    k = addDays(k, 1);
  }
  return null;
}

// ---------------------------------------------------------------------------
// Opérations
// ---------------------------------------------------------------------------

/** Montants d'un revenu, calculés avec le taux EN VIGUEUR au moment de la saisie. */
export function buildRevenueAmounts(grossAmount, rateBp) {
  const contributionAmount = contributionFor(grossAmount, rateBp);
  return { grossAmount, contributionAmount, netAmount: grossAmount - contributionAmount };
}

function flow(state, keep = () => true) {
  return (
    sum(state.revenues.filter((r) => keep(r.date)), (r) => r.netAmount) +
    sum(state.transfers.filter((t) => keep(t.date)), (t) => t.amount) -
    sum(state.withdrawals.filter((w) => keep(w.date)), (w) => w.amount)
  );
}

/** currentSavings = fonds initiaux + revenus nets + virements − retraits. */
export function currentSavings(state) {
  return state.settings.initialSavings + flow(state);
}

/** Solde sans les opérations datées à partir de `beforeKey`. */
export function savingsBefore(state, beforeKey) {
  return state.settings.initialSavings + flow(state, (d) => d < beforeKey);
}

export function grossRevenueBetween(state, from, to) {
  return sum(state.revenues.filter((r) => r.date >= from && r.date <= to), (r) => r.grossAmount);
}

export function netRevenueBetween(state, from, to) {
  return sum(state.revenues.filter((r) => r.date >= from && r.date <= to), (r) => r.netAmount);
}

// ---------------------------------------------------------------------------
// Moteur de projection (confiance progressive)
// ---------------------------------------------------------------------------
// Au démarrage : rythme NÉCESSAIRE pour atteindre l'objectif.
// Puis, à mesure que l'historique grandit : de plus en plus de poids au rythme OBSERVÉ.
//   projectionConfidence = min(1, joursObservés / 20)
//   projectionDailyPace  = nécessaire × (1 − confiance) + observé × confiance
// Le rythme observé n'utilise QUE les revenus nets, à la date où ils ont été réalisés
// (jamais createdAt), jamais les fonds initiaux, virements ou retraits.

export function computeProjectionPace(state, today, { remaining, futureStart }) {
  const s = state.settings;
  const rd = restDaysOf(state);
  const remainingPlannedDays = countActivityDays(s, rd, futureStart, s.targetDate);
  const requiredNetPerActivityDay = remainingPlannedDays > 0 ? remaining / remainingPlannedDays : 0;

  const pastRevenues = state.revenues.filter((r) => r.date <= today);
  if (pastRevenues.length === 0) {
    return {
      requiredNetPerActivityDay, observedNetPerActivityDay: null,
      observedActivityDays: 0, projectionConfidence: 0,
      projectionDailyPace: requiredNetPerActivityDay, status: 'none', remainingPlannedDays,
    };
  }

  // Jours d'activité observés : jours prévus déjà terminés + tout jour où un revenu a été réalisé
  // (aujourd'hui ne compte que s'il contient déjà un revenu).
  const revenueDays = new Set(pastRevenues.map((r) => r.date));
  const firstRevenue = pastRevenues.reduce((m, r) => (r.date < m ? r.date : m), today);
  const start = maxKey(minKey(s.setupDate, firstRevenue), addDays(today, -OBSERVATION_LOOKBACK_DAYS));
  const observed = []; // du plus récent au plus ancien
  for (let k = today; k >= start; k = addDays(k, -1)) {
    const hasRevenue = revenueDays.has(k);
    if (k === today ? hasRevenue : hasRevenue || isActivityDay(s, rd, k)) observed.push(k);
  }

  const observedActivityDays = observed.length;
  const windowDays = observed.slice(0, PROJECTION_RECENT_WINDOW_DAYS);
  const windowStart = windowDays[windowDays.length - 1];
  const windowNet = netRevenueBetween(state, windowStart, today);
  const observedNetPerActivityDay = windowDays.length ? windowNet / windowDays.length : 0;

  const projectionConfidence = Math.min(1, observedActivityDays / PROJECTION_FULL_CONFIDENCE_DAYS);
  const projectionDailyPace =
    requiredNetPerActivityDay * (1 - projectionConfidence) + observedNetPerActivityDay * projectionConfidence;

  const status = observedActivityDays < PROJECTION_REFINING_DAYS
    ? 'refining'
    : observedActivityDays < PROJECTION_FULL_CONFIDENCE_DAYS ? 'provisional' : 'personal';

  return {
    requiredNetPerActivityDay, observedNetPerActivityDay, observedActivityDays,
    projectionConfidence, projectionDailyPace, status, remainingPlannedDays,
  };
}

export const PROJECTION_STATUS_LABEL = {
  none: 'Ajoute tes premiers revenus pour affiner ta projection.',
  refining: 'Projection en cours d’affinage',
  provisional: 'Projection provisoire',
  personal: 'Projection basée sur ton rythme',
};

// ---------------------------------------------------------------------------
// Instantané complet (utilisé par toutes les pages)
// ---------------------------------------------------------------------------

export function computeSnapshot(state, today) {
  const s = state.settings;
  const rd = restDaysOf(state);
  const rate = s.hasContributions ? s.contributionRate : 0;
  const goal = s.goalAmount;
  const setup = s.setupDate;
  const target = s.targetDate;
  const yesterday = addDays(today, -1);
  const tomorrow = addDays(today, 1);
  const hasActivityPlan = s.plannedActivityDays.length > 0;

  // --- Objectif global -----------------------------------------------------
  const balance = currentSavings(state);
  const remaining = Math.max(0, goal - balance); // jamais négatif à l'affichage
  const globalPct = percentOf(balance, goal) ?? 0; // non plafonné
  const goalReached = balance >= goal;
  const targetReached = today >= target; // « Date cible atteinte »
  const remainingActivityDays = countActivityDays(s, rd, today, target);

  // --- Semaine (lundi 00:00 -> dimanche 23:59:59) ----------------------------
  // L'objectif de la semaine se calcule à partir du solde AU DÉBUT de la semaine :
  // un revenu fait progresser vers le cap sans faire baisser le cap.
  // Il est recalculé si planning, journée de repos, objectif, date ou taux changent.
  const weekStart = startOfWeek(today);
  const weekEnd = endOfWeek(today);
  const planFrom = maxKey(weekStart, setup);
  const weekPlanEnd = minKey(weekEnd, target);

  const remainingAtWeekStart = Math.max(0, goal - savingsBefore(state, planFrom));
  const activityDaysFromWeekStart = countActivityDays(s, rd, planFrom, target);
  const dailyNetTarget = activityDaysFromWeekStart > 0 ? Math.ceil(remainingAtWeekStart / activityDaysFromWeekStart) : 0;
  const dailyGrossTarget = grossNeededForNet(dailyNetTarget, rate);

  const weekActivityDays = countActivityDays(s, rd, planFrom, weekPlanEnd);
  const weeklyTarget = dailyGrossTarget * weekActivityDays; // = somme des objectifs journaliers planifiés
  const weeklyRealized = grossRevenueBetween(state, weekStart, weekEnd);
  const weeklyRemaining = Math.max(0, weeklyTarget - weeklyRealized);
  const weeklyProgress = percentOf(weeklyRealized, weeklyTarget); // NON plafonné (null si objectif = 0)
  const weeklyBarPct = weeklyProgress === null ? 0 : Math.min(100, Math.max(0, weeklyProgress));
  const weeklyReached = weeklyTarget > 0 && weeklyRealized >= weeklyTarget;
  const weeklyExceeded = weeklyTarget > 0 && weeklyRealized > weeklyTarget;
  const weeklySurplus = weeklyExceeded ? weeklyRealized - weeklyTarget : 0;
  const weekDaysElapsed = countActivityDays(s, rd, planFrom, minKey(yesterday, weekPlanEnd));
  const remainingWeekActivityDays = countActivityDays(s, rd, maxKey(today, planFrom), weekPlanEnd);
  const remainingPerActivityDay = remainingWeekActivityDays > 0 ? Math.ceil(weeklyRemaining / remainingWeekActivityDays) : 0;

  // --- Aujourd'hui ---------------------------------------------------------
  const todayPlanned = s.plannedActivityDays.includes(weekday(today));
  const todayMarkedRest = Boolean(rd[today]);
  const todayIsActivity = todayPlanned && !todayMarkedRest && today <= target;
  const todayRealized = grossRevenueBetween(state, today, today);

  // Avance de la semaine : réalisé − objectif cumulé des jours d'activité déjà passés
  // (+ aujourd'hui s'il contient déjà un revenu).
  const daysDueSoFar = weekDaysElapsed + (todayIsActivity && todayRealized > 0 && today >= planFrom ? 1 : 0);
  const weeklyAdvance = weeklyRealized - dailyGrossTarget * daysDueSoFar;

  // --- Totaux ----------------------------------------------------------------
  const monthStart = startOfMonth(today);
  const monthEnd = endOfMonth(today);
  const monthRevenueGross = grossRevenueBetween(state, monthStart, monthEnd);
  const monthRevenueNet = netRevenueBetween(state, monthStart, monthEnd);
  const weekRevenueNet = netRevenueBetween(state, weekStart, weekEnd);
  const transfersTotal = sum(state.transfers, (t) => t.amount);
  const withdrawalsTotal = sum(state.withdrawals, (w) => w.amount);

  // --- Projection simple ----------------------------------------------------
  const futureStart = todayIsActivity && todayRealized === 0 ? today : tomorrow;
  const pace = computeProjectionPace(state, today, { remaining, futureStart });
  const projectAt = (dateKey) =>
    Math.round(balance + pace.projectionDailyPace * countActivityDays(s, rd, futureStart, dateKey));
  const simpleProjection = projectAt(target);

  return {
    today, rate, goal, target, setup, hasActivityPlan,
    balance, remaining, globalPct, goalReached, targetReached, remainingActivityDays,
    weekStart, weekEnd, dailyNetTarget, dailyGrossTarget,
    weekActivityDays, weeklyTarget, weeklyRealized, weeklyRemaining,
    weeklyProgress, weeklyBarPct, weeklyReached, weeklyExceeded, weeklySurplus, weeklyAdvance,
    weekDaysElapsed, remainingWeekActivityDays, remainingPerActivityDay,
    todayPlanned, todayMarkedRest, todayIsActivity, todayRealized,
    weekRevenueGross: weeklyRealized, weekRevenueNet, monthRevenueGross, monthRevenueNet,
    transfersTotal, withdrawalsTotal,
    futureStart, pace, simpleProjection, projectAt,
  };
}

// ---------------------------------------------------------------------------
// Nelyqor+ — analyses avancées
// ---------------------------------------------------------------------------

export function computePremium(state, today, snap = computeSnapshot(state, today)) {
  const s = state.settings;
  const rd = restDaysOf(state);
  const yesterday = addDays(today, -1);

  // Avance / retard vs une progression régulière depuis la création de l'objectif.
  const totalPlanDays = countActivityDays(s, rd, s.setupDate, s.targetDate);
  const elapsed = countActivityDays(s, rd, s.setupDate, minKey(yesterday, s.targetDate));
  const expected = totalPlanDays > 0
    ? Math.round(s.initialSavings + (s.goalAmount - s.initialSavings) * Math.min(1, elapsed / totalPlanDays))
    : s.initialSavings;
  const gap = snap.balance - expected; // > 0 avance, < 0 retard

  // Date estimée d'atteinte avec le rythme de projection.
  let estimatedReachDate = null;
  if (snap.goalReached) estimatedReachDate = today;
  else if (snap.pace.projectionDailyPace > 0) {
    estimatedReachDate = nthActivityDayFrom(s, rd, snap.futureStart, Math.ceil(snap.remaining / snap.pace.projectionDailyPace));
  }

  // Paliers 6 / 12 / 18 mois + date cible.
  const milestones = [6, 12, 18].map((months) => {
    const date = addMonths(today, months);
    const amount = snap.projectAt(date);
    return { key: `${months}m`, label: `Dans ${months} mois`, date, amount, pct: percentOf(amount, s.goalAmount) };
  });
  milestones.push({ key: 'target', label: 'Date cible', date: s.targetDate, amount: snap.simpleProjection, pct: percentOf(snap.simpleProjection, s.goalAmount) });

  // Comparaisons à période équivalente (semaine / mois).
  const thisWeek = grossRevenueBetween(state, snap.weekStart, today);
  const lastWeek = grossRevenueBetween(state, addDays(snap.weekStart, -7), addDays(today, -7));
  const ms = startOfMonth(today);
  const prevMonthStart = startOfMonth(addDays(ms, -1));
  const prevMonthSameDay = minKey(addDays(prevMonthStart, diffDays(ms, today)), endOfMonth(prevMonthStart));
  const thisMonth = grossRevenueBetween(state, ms, today);
  const lastMonth = grossRevenueBetween(state, prevMonthStart, prevMonthSameDay);

  // Statistiques avancées.
  const revs = state.revenues;
  const byDay = new Map();
  revs.forEach((r) => byDay.set(r.date, (byDay.get(r.date) || 0) + r.grossAmount));
  let bestDay = null;
  for (const [date, amount] of byDay) if (!bestDay || amount > bestDay.amount) bestDay = { date, amount };
  const totalGross = sum(revs, (r) => r.grossAmount);

  return {
    expected, gap, estimatedReachDate, milestones,
    comparison: {
      week: { current: thisWeek, previous: lastWeek, deltaPct: percentDelta(thisWeek, lastWeek) },
      month: { current: thisMonth, previous: lastMonth, deltaPct: percentDelta(thisMonth, lastMonth) },
    },
    stats: {
      totalGross,
      totalContrib: sum(revs, (r) => r.contributionAmount),
      totalNet: sum(revs, (r) => r.netAmount),
      bestDay,
      daysWithRevenue: byDay.size,
      avgPerRevenueDay: byDay.size ? Math.round(totalGross / byDay.size) : null,
    },
  };
}

function percentDelta(current, previous) {
  if (!previous) return null;
  return ((current - previous) / previous) * 100;
}

/** Simulateur « Et si… » : X € brut par jour d'activité, N jours par semaine. */
export function simulate(state, today, { dailyGross, daysPerWeek }) {
  const s = state.settings;
  const rate = s.hasContributions ? s.contributionRate : 0;
  const balance = currentSavings(state);
  const weeklyNet = netFor(dailyGross, rate) * daysPerWeek;
  const calendarDaysLeft = Math.max(0, diffDays(today, s.targetDate));
  const projectedAtTarget = Math.round(balance + (weeklyNet * calendarDaysLeft) / 7);
  const remaining = Math.max(0, s.goalAmount - balance);
  let reachDate = null;
  if (remaining === 0) reachDate = today;
  else if (weeklyNet > 0) {
    const days = Math.ceil((remaining / weeklyNet) * 7);
    if (days <= 365 * 30) reachDate = addDays(today, days);
  }
  return {
    weeklyNet, projectedAtTarget, reachDate,
    reachesGoal: projectedAtTarget >= s.goalAmount,
    pctAtTarget: percentOf(projectedAtTarget, s.goalAmount),
  };
}

// ---------------------------------------------------------------------------
// Graphique « Évolution des revenus » (indépendant de la projection)
// ---------------------------------------------------------------------------

export const CHART_RANGES = ['S', 'M', '6M', '1A'];

export function revenueSeries(state, today, range) {
  const buckets = [];
  if (range === 'S' || range === 'M') {
    const n = range === 'S' ? 7 : 30;
    for (let i = n - 1; i >= 0; i--) {
      const k = addDays(today, -i);
      buckets.push({ from: k, to: k, unit: 'day' });
    }
  } else if (range === '6M') {
    const ws = startOfWeek(today);
    for (let i = 25; i >= 0; i--) {
      const from = addDays(ws, -7 * i);
      buckets.push({ from, to: addDays(from, 6), unit: 'week' });
    }
  } else {
    const ms = startOfMonth(today);
    for (let i = 11; i >= 0; i--) {
      const from = addMonths(ms, -i);
      buckets.push({ from, to: endOfMonth(from), unit: 'month' });
    }
  }
  return buckets.map((b) => ({ ...b, value: grossRevenueBetween(state, b.from, b.to) }));
}
