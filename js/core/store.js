// Nelyqor — store central et persistance locale.
// Toute écriture passe par ce module. Pour passer plus tard à un backend / cloud,
// il suffit de remplacer readPersisted() / writePersisted() : les pages ne lisent
// l'état que via getState() / subscribe().

import { buildRevenueAmounts } from './calc.js';
import { todayKey, isValidKey } from './dates.js';

const STORAGE_KEY = 'nelyqor:v1';
const SCHEMA_VERSION = 1;
export const TRIAL_DAYS = 7;
export const APP_VERSION = '1.0.0';
const DAY_MS = 86400000;

/**
 * Structure de l'état :
 * {
 *   version, onboardingCompleted,
 *   settings: { goalAmount, targetDate, initialSavings, hasContributions, contributionRate,
 *               plannedActivityDays, displayName, setupDate, createdAt },
 *   revenues:    [{ id, date, grossAmount, contributionAmount, netAmount, contributionRate, label, createdAt }],
 *   transfers:   [{ id, date, amount, label, createdAt }],
 *   withdrawals: [{ id, date, amount, label, createdAt }],
 *   subscription: { status: 'free'|'trial'|'premium', trialStartedAt, trialEndsAt, trialUsed },
 *   preferences:  { dynamicBackgroundEnabled, dailyQuoteEnabled },
 *   currentWeekState: { restDays: { 'YYYY-MM-DD': true } } // « Je ne travaille pas aujourd'hui »
 * }
 * Montants en centimes, taux en points de base (21 % = 2100), jours 0 = lundi … 6 = dimanche.
 */
function emptyState() {
  return {
    version: SCHEMA_VERSION,
    onboardingCompleted: false,
    settings: null,
    revenues: [],
    transfers: [],
    withdrawals: [],
    subscription: { status: 'free', trialStartedAt: null, trialEndsAt: null, trialUsed: false },
    preferences: { dynamicBackgroundEnabled: true, dailyQuoteEnabled: true },
    currentWeekState: { restDays: {} },
  };
}

let memoryFallback = null;

function readPersisted() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : memoryFallback;
  } catch {
    return memoryFallback;
  }
}

function writePersisted(data) {
  memoryFallback = data;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
    return true;
  } catch {
    return false;
  }
}

function sanitize(data) {
  const base = emptyState();
  if (!data || typeof data !== 'object') return base;
  const ops = (arr) => (Array.isArray(arr) ? arr.filter((x) => x && x.id && isValidKey(x.date)) : []);
  return {
    ...base,
    onboardingCompleted: Boolean(data.onboardingCompleted && data.settings),
    settings: data.settings || null,
    revenues: ops(data.revenues),
    transfers: ops(data.transfers),
    withdrawals: ops(data.withdrawals),
    subscription: { ...base.subscription, ...(data.subscription || {}) },
    preferences: { ...base.preferences, ...(data.preferences || {}) },
    currentWeekState: { restDays: { ...((data.currentWeekState && data.currentWeekState.restDays) || {}) } },
  };
}

let state = sanitize(readPersisted());
const listeners = new Set();

export const getState = () => state;

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function commit(next) {
  state = next;
  writePersisted(state);
  listeners.forEach((fn) => fn(state));
}

const uid = () =>
  globalThis.crypto && crypto.randomUUID
    ? crypto.randomUUID()
    : 'id-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);

const byDateDesc = (a, b) => (a.date === b.date ? (a.createdAt < b.createdAt ? 1 : -1) : a.date < b.date ? 1 : -1);

// ---------------------------------------------------------------------------
// Onboarding / réglages
// ---------------------------------------------------------------------------

export function completeOnboarding({ goalAmount, targetDate, initialSavings, hasContributions, contributionRate, plannedActivityDays, plan }) {
  const now = new Date();
  const settings = {
    goalAmount,
    targetDate,
    initialSavings,
    hasContributions,
    contributionRate: hasContributions ? contributionRate : 0,
    plannedActivityDays: [...plannedActivityDays].sort((a, b) => a - b),
    displayName: '',
    setupDate: todayKey(),
    createdAt: now.toISOString(),
  };
  const base = emptyState();
  const subscription = plan === 'trial'
    ? { status: 'trial', trialStartedAt: now.toISOString(), trialEndsAt: new Date(now.getTime() + TRIAL_DAYS * DAY_MS).toISOString(), trialUsed: true }
    : base.subscription;
  commit({ ...base, onboardingCompleted: true, settings, subscription });
}

export function updateSettings(patch) {
  if (!state.settings) return;
  const settings = { ...state.settings, ...patch };
  if (patch.plannedActivityDays) settings.plannedActivityDays = [...patch.plannedActivityDays].sort((a, b) => a - b);
  if (patch.hasContributions === false) settings.contributionRate = 0;
  commit({ ...state, settings });
}

export function updatePreferences(patch) {
  commit({ ...state, preferences: { ...state.preferences, ...patch } });
}

// ---------------------------------------------------------------------------
// Abonnement — simulation locale persistante.
// En production : remplacer par StoreKit (Apple) / Google Play Billing.
// Nelyqor ne demande jamais de carte bancaire.
// ---------------------------------------------------------------------------

/** Repasse en Gratuit si l'essai est terminé. Les données ne sont JAMAIS supprimées. */
export function refreshSubscription(now = new Date()) {
  const sub = state.subscription;
  if (sub.status === 'trial' && sub.trialEndsAt && now.getTime() >= new Date(sub.trialEndsAt).getTime()) {
    commit({ ...state, subscription: { ...sub, status: 'free' } });
  }
}

export const isPremium = () => state.subscription.status === 'trial' || state.subscription.status === 'premium';

export function startTrial() {
  if (state.subscription.trialUsed) return false;
  const now = new Date();
  commit({
    ...state,
    subscription: { status: 'trial', trialStartedAt: now.toISOString(), trialEndsAt: new Date(now.getTime() + TRIAL_DAYS * DAY_MS).toISOString(), trialUsed: true },
  });
  return true;
}

export function activatePremium() {
  commit({ ...state, subscription: { ...state.subscription, status: 'premium' } });
}

export function backToFree() {
  commit({ ...state, subscription: { ...state.subscription, status: 'free' } });
}

// ---------------------------------------------------------------------------
// Opérations
// ---------------------------------------------------------------------------

export function addRevenue({ grossAmount, date, label }) {
  const rate = state.settings.hasContributions ? state.settings.contributionRate : 0;
  const item = {
    id: uid(),
    date, // date à laquelle le revenu a été RÉALISÉ (≠ createdAt)
    ...buildRevenueAmounts(grossAmount, rate), // cotisations figées au moment de l'enregistrement
    contributionRate: rate,
    label: (label || '').trim(),
    createdAt: new Date().toISOString(),
  };
  commit({ ...state, revenues: [...state.revenues, item].sort(byDateDesc) });
  return item;
}

export function addTransfer({ amount, date, label }) {
  const item = { id: uid(), date, amount, label: (label || '').trim(), createdAt: new Date().toISOString() };
  commit({ ...state, transfers: [...state.transfers, item].sort(byDateDesc) });
  return item;
}

export function addWithdrawal({ amount, date, label }) {
  const item = { id: uid(), date, amount, label: (label || '').trim(), createdAt: new Date().toISOString() };
  commit({ ...state, withdrawals: [...state.withdrawals, item].sort(byDateDesc) });
  return item;
}

const COLLECTIONS = { revenue: 'revenues', transfer: 'transfers', withdrawal: 'withdrawals' };

export function deleteOperation(type, id) {
  const key = COLLECTIONS[type];
  commit({ ...state, [key]: state[key].filter((x) => x.id !== id) });
}

// ---------------------------------------------------------------------------
// Semaine en cours : « Je ne travaille pas aujourd'hui » (réversible)
// ---------------------------------------------------------------------------

export function setRestDay(dateKey, isRest) {
  const restDays = { ...state.currentWeekState.restDays };
  if (isRest) restDays[dateKey] = true;
  else delete restDays[dateKey];
  commit({ ...state, currentWeekState: { restDays } });
}

// ---------------------------------------------------------------------------
// Réinitialisation complète (retour au premier lancement)
// ---------------------------------------------------------------------------

export function resetAll() {
  try { localStorage.removeItem(STORAGE_KEY); } catch { /* ignore */ }
  memoryFallback = null;
  commit(emptyState());
}
