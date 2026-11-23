// Nelyqor — store central et persistance locale.
// Toute écriture passe par ce module. Les pages ne lisent l'état que via getState() / subscribe().
// La synchronisation cloud (js/core/cloud.js) se branche dessus via onLocalChange() / replaceState().

import { buildRevenueAmounts } from './calc.js';
import { todayKey, isValidKey, addMonths } from './dates.js';

const STORAGE_KEY = 'nelyqor:v1'; // clé conservée pour ne perdre aucune donnée existante
export const SCHEMA_VERSION = 2;
export const TRIAL_DAYS = 7;
export const APP_VERSION = '1.2.1';
export const DEFAULT_ACCENT = '#2ED3B7';
export const LABEL_MAX = 40;
export const CATEGORY_NAME_MAX = 30;
export const AVATAR_MAX_CHARS = 400000; // ~300 Ko une fois décodé
export const PLAN_FREQUENCIES = [0, 1, 2, 3, 4, 6, 12]; // en mois (0 = une seule fois)
const DAY_MS = 86400000;
const MAX_AMOUNT = 100000000000; // 1 milliard d'euros, en centimes
const MAX_ITEMS = 20000;

/**
 * Structure de l'état (version 2) :
 * {
 *   version, onboardingCompleted,
 *   settings: { goalAmount, targetDate, initialSavings, hasContributions, contributionRate,
 *               contributionFrequency: 'monthly'|'quarterly', contributionDueDate: 'YYYY-MM-DD' | null (une date de prélèvement),
 *               microActivity: null|'services'|'vente'|'mixte', plannedActivityDays, displayName, setupDate, createdAt },
 *   profile: { avatar: 'data:image/jpeg;base64,…' | null },
 *   revenues:    [{ id, date, grossAmount, contributionAmount, netAmount, contributionRate, label, exceptional, createdAt, updatedAt }],
 *   transfers:   [{ id, date, amount, label, createdAt, updatedAt }],
 *   withdrawals: [{ id, date, amount, label, categoryId, plannedId, createdAt, updatedAt }],
 *   categories:  [{ id, name, createdAt, updatedAt }],                       // dossiers de dépenses (Nelyqor+)
 *   plannedExpenses: [{ id, label, amount, categoryId, startDate, everyMonths, done, createdAt, updatedAt }],
 *   deleted: { [id]: isoDate },   // suppressions, pour que la synchro ne fasse pas « revenir » un élément
 *   subscription: { status: 'free'|'trial'|'premium', trialStartedAt, trialEndsAt, trialUsed },
 *   preferences:  { dynamicBackgroundEnabled, dailyQuoteEnabled, accentColor },
 *   calendar: { 'YYYY-MM-DD': 'work' | 'off' }, // exceptions au planning : travail exceptionnel / repos exceptionnel
 *   updatedAt: { settings, profile, preferences, subscription, calendar } // horodatage par rubrique (fusion cloud)
 * }
 * Montants en centimes, taux en points de base (21 % = 2100), jours 0 = lundi … 6 = dimanche.
 */
export function emptyState() {
  return {
    version: SCHEMA_VERSION,
    onboardingCompleted: false,
    settings: null,
    profile: { avatar: null },
    revenues: [],
    transfers: [],
    withdrawals: [],
    categories: [],
    plannedExpenses: [],
    deleted: {},
    subscription: { status: 'free', trialStartedAt: null, trialEndsAt: null, trialUsed: false },
    preferences: { dynamicBackgroundEnabled: true, dailyQuoteEnabled: true, accentColor: DEFAULT_ACCENT },
    calendar: {},
    updatedAt: {},
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

// ---------------------------------------------------------------------------
// Validation stricte : tout ce qui vient du stockage local ou du cloud est
// considéré comme non fiable et normalisé (types, bornes, longueurs).
// ---------------------------------------------------------------------------
const isObj = (x) => x !== null && typeof x === 'object' && !Array.isArray(x);
const cents = (v, min = 0) => (Number.isSafeInteger(v) && v >= min && v <= MAX_AMOUNT ? v : null);
const str = (v, max) => (typeof v === 'string' ? v.replace(/[\u0000-\u001F\u007F]/g, '').trim().slice(0, max) : '');
const id = (v) => (typeof v === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(v) ? v : null);
const iso = (v) => (typeof v === 'string' && v.length <= 40 && !Number.isNaN(Date.parse(v)) ? v : null);
const bool = (v) => v === true;
export const isHexColor = (v) => typeof v === 'string' && /^#[0-9A-Fa-f]{6}$/.test(v);
export const isAvatar = (v) => typeof v === 'string' && v.length <= AVATAR_MAX_CHARS && /^data:image\/(jpeg|png|webp);base64,[A-Za-z0-9+/=]+$/.test(v);

function cleanList(arr, fn) {
  if (!Array.isArray(arr)) return [];
  const seen = new Set();
  const out = [];
  for (const raw of arr.slice(0, MAX_ITEMS)) {
    if (!isObj(raw)) continue;
    const x = fn(raw);
    if (!x || seen.has(x.id)) continue;
    seen.add(x.id);
    out.push(x);
  }
  return out;
}

const base = (r) => {
  const i = id(r.id);
  if (!i) return null;
  const createdAt = iso(r.createdAt) || new Date(0).toISOString();
  return { id: i, createdAt, updatedAt: iso(r.updatedAt) || createdAt };
};

function cleanRevenue(r) {
  const b = base(r);
  const gross = cents(r.grossAmount);
  const contrib = cents(r.contributionAmount);
  if (!b || !isValidKey(r.date) || gross === null || contrib === null || contrib > gross) return null;
  const rate = Number.isSafeInteger(r.contributionRate) && r.contributionRate >= 0 && r.contributionRate < 10000 ? r.contributionRate : 0;
  return { ...b, date: r.date, grossAmount: gross, contributionAmount: contrib, netAmount: gross - contrib, contributionRate: rate, label: str(r.label, LABEL_MAX), exceptional: bool(r.exceptional), history: bool(r.history) };
}

function cleanAmountOp(r, withCategory) {
  const b = base(r);
  const amount = cents(r.amount);
  if (!b || !isValidKey(r.date) || amount === null) return null;
  const out = { ...b, date: r.date, amount, label: str(r.label, LABEL_MAX) };
  if (withCategory) {
    out.categoryId = id(r.categoryId);
    out.plannedId = id(r.plannedId);
  }
  return out;
}

function cleanCategory(r) {
  const b = base(r);
  const name = str(r.name, CATEGORY_NAME_MAX);
  return b && name ? { ...b, name } : null;
}

function cleanPlan(r) {
  const b = base(r);
  const amount = cents(r.amount);
  if (!b || !amount || !isValidKey(r.startDate) || !PLAN_FREQUENCIES.includes(r.everyMonths)) return null;
  const done = Number.isSafeInteger(r.done) && r.done >= 0 && r.done < 100000 ? r.done : 0;
  return { ...b, label: str(r.label, LABEL_MAX) || 'Dépense prévue', amount, categoryId: id(r.categoryId), startDate: r.startDate, everyMonths: r.everyMonths, done };
}

function cleanSettings(s) {
  if (!isObj(s)) return null;
  const goal = cents(s.goalAmount);
  if (!goal || !isValidKey(s.targetDate) || !isValidKey(s.setupDate)) return null;
  const days = Array.isArray(s.plannedActivityDays)
    ? [...new Set(s.plannedActivityDays.filter((d) => Number.isInteger(d) && d >= 0 && d <= 6))].sort((a, b) => a - b)
    : [];
  const has = bool(s.hasContributions);
  const rate = has && Number.isSafeInteger(s.contributionRate) && s.contributionRate >= 0 && s.contributionRate < 10000 ? s.contributionRate : 0;
  return {
    goalAmount: goal,
    targetDate: s.targetDate,
    initialSavings: cents(s.initialSavings) ?? 0,
    hasContributions: has,
    contributionRate: rate,
    contributionFrequency: s.contributionFrequency === 'monthly' ? 'monthly' : 'quarterly',
    contributionDueDate: has && isValidKey(s.contributionDueDate) ? s.contributionDueDate : null,
    microActivity: ['services', 'vente', 'mixte'].includes(s.microActivity) ? s.microActivity : null,
    plannedActivityDays: days,
    displayName: str(s.displayName, 30),
    setupDate: s.setupDate,
    createdAt: iso(s.createdAt) || new Date().toISOString(),
  };
}

export function sanitize(data) {
  const b = emptyState();
  if (!isObj(data)) return b;
  const settings = cleanSettings(data.settings);
  const sub = isObj(data.subscription) ? data.subscription : {};
  const prefs = isObj(data.preferences) ? data.preferences : {};
  // Calendrier : exceptions au planning. Migration : anciens « jours de repos » (v1.0) → 'off'.
  const calendar = {};
  const legacy = isObj(data.currentWeekState) && isObj(data.currentWeekState.restDays) ? data.currentWeekState.restDays : {};
  for (const [k, v] of Object.entries(legacy).slice(0, 5000)) if (v === true && isValidKey(k)) calendar[k] = 'off';
  if (isObj(data.calendar)) for (const [k, v] of Object.entries(data.calendar).slice(0, 5000)) if ((v === 'work' || v === 'off') && isValidKey(k)) calendar[k] = v;
  const deleted = {};
  if (isObj(data.deleted)) for (const [k, v] of Object.entries(data.deleted).slice(0, 50000)) if (id(k) && iso(v)) deleted[k] = v;
  const updatedAt = {};
  if (isObj(data.updatedAt)) for (const k of ['settings', 'profile', 'preferences', 'subscription', 'calendar']) if (iso(data.updatedAt[k])) updatedAt[k] = data.updatedAt[k];
  if (!updatedAt.calendar && isObj(data.updatedAt) && iso(data.updatedAt.restDays)) updatedAt.calendar = data.updatedAt.restDays;
  const notDeleted = (x) => !deleted[x.id];

  return {
    version: SCHEMA_VERSION,
    onboardingCompleted: Boolean(data.onboardingCompleted && settings),
    settings,
    profile: { avatar: isObj(data.profile) && isAvatar(data.profile.avatar) ? data.profile.avatar : null },
    revenues: cleanList(data.revenues, cleanRevenue).filter(notDeleted).sort(byDateDesc),
    transfers: cleanList(data.transfers, (r) => cleanAmountOp(r, false)).filter(notDeleted).sort(byDateDesc),
    withdrawals: cleanList(data.withdrawals, (r) => cleanAmountOp(r, true)).filter(notDeleted).sort(byDateDesc),
    categories: cleanList(data.categories, cleanCategory).filter(notDeleted).sort((a, c) => a.name.localeCompare(c.name, 'fr')),
    plannedExpenses: cleanList(data.plannedExpenses, cleanPlan).filter(notDeleted),
    deleted,
    subscription: {
      status: ['free', 'trial', 'premium'].includes(sub.status) ? sub.status : 'free',
      trialStartedAt: iso(sub.trialStartedAt),
      trialEndsAt: iso(sub.trialEndsAt),
      trialUsed: bool(sub.trialUsed),
    },
    preferences: {
      dynamicBackgroundEnabled: prefs.dynamicBackgroundEnabled !== false,
      dailyQuoteEnabled: prefs.dailyQuoteEnabled !== false,
      accentColor: isHexColor(prefs.accentColor) ? prefs.accentColor.toUpperCase() : DEFAULT_ACCENT,
    },
    calendar,
    updatedAt,
  };
}

export const byDateDesc = (a, b) => (a.date === b.date ? (a.createdAt < b.createdAt ? 1 : -1) : a.date < b.date ? 1 : -1);

// ---------------------------------------------------------------------------
let state = sanitize(readPersisted());
const listeners = new Set();
const localChangeListeners = new Set();

export const getState = () => state;

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Appelé à chaque modification faite PAR L'UTILISATEUR (pas lors d'une fusion cloud). */
export function onLocalChange(fn) {
  localChangeListeners.add(fn);
  return () => localChangeListeners.delete(fn);
}

const nowIso = () => new Date().toISOString();

function commit(next, { local = true, touched = [] } = {}) {
  if (touched.length) {
    const t = nowIso();
    next = { ...next, updatedAt: { ...next.updatedAt, ...Object.fromEntries(touched.map((k) => [k, t])) } };
  }
  state = next;
  writePersisted(state);
  listeners.forEach((fn) => fn(state));
  if (local) localChangeListeners.forEach((fn) => fn(state));
}

/** Remplace l'état (fusion cloud). Les données sont revalidées. */
export function replaceState(next) {
  commit(sanitize(next), { local: false });
}

export const uid = () =>
  globalThis.crypto && crypto.randomUUID
    ? crypto.randomUUID()
    : 'id-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);

// ---------------------------------------------------------------------------
// Onboarding / réglages / profil
// ---------------------------------------------------------------------------

export function completeOnboarding({ goalAmount, targetDate, initialSavings, hasContributions, contributionRate, contributionFrequency, contributionDueDate, plannedActivityDays, plan }) {
  const now = new Date();
  const settings = cleanSettings({
    goalAmount,
    targetDate,
    initialSavings,
    hasContributions,
    contributionRate: hasContributions ? contributionRate : 0,
    contributionFrequency,
    contributionDueDate,
    plannedActivityDays,
    displayName: state.settings ? state.settings.displayName : '',
    setupDate: todayKey(),
    createdAt: now.toISOString(),
  });
  const b = emptyState();
  const subscription = plan === 'trial'
    ? { status: 'trial', trialStartedAt: now.toISOString(), trialEndsAt: new Date(now.getTime() + TRIAL_DAYS * DAY_MS).toISOString(), trialUsed: true }
    : b.subscription;
  commit({ ...b, onboardingCompleted: true, settings, subscription, preferences: state.preferences, profile: state.profile },
    { touched: ['settings', 'subscription', 'preferences', 'profile', 'calendar'] });
}

export function updateSettings(patch) {
  if (!state.settings) return;
  const settings = cleanSettings({ ...state.settings, ...patch });
  if (!settings) return;
  commit({ ...state, settings }, { touched: ['settings'] });
}

export function updatePreferences(patch) {
  const preferences = sanitize({ preferences: { ...state.preferences, ...patch } }).preferences;
  commit({ ...state, preferences }, { touched: ['preferences'] });
}

export function setAvatar(dataUrl) {
  if (dataUrl !== null && !isAvatar(dataUrl)) return false;
  commit({ ...state, profile: { ...state.profile, avatar: dataUrl } }, { touched: ['profile'] });
  return true;
}

// ---------------------------------------------------------------------------
// Abonnement — simulation locale persistante.
// En production : remplacer par StoreKit (Apple) / Google Play Billing (voir SECURITY.md).
// ---------------------------------------------------------------------------

/** Repasse en Gratuit si l'essai est terminé. Les données ne sont JAMAIS supprimées. */
export function refreshSubscription(now = new Date()) {
  const sub = state.subscription;
  if (sub.status === 'trial' && sub.trialEndsAt && now.getTime() >= new Date(sub.trialEndsAt).getTime()) {
    commit({ ...state, subscription: { ...sub, status: 'free' } }, { touched: ['subscription'] });
  }
}

export const isPremium = () => state.subscription.status === 'trial' || state.subscription.status === 'premium';

export function startTrial() {
  if (state.subscription.trialUsed) return false;
  const now = new Date();
  commit({
    ...state,
    subscription: { status: 'trial', trialStartedAt: now.toISOString(), trialEndsAt: new Date(now.getTime() + TRIAL_DAYS * DAY_MS).toISOString(), trialUsed: true },
  }, { touched: ['subscription'] });
  return true;
}

export function activatePremium() {
  commit({ ...state, subscription: { ...state.subscription, status: 'premium' } }, { touched: ['subscription'] });
}

export function backToFree() {
  commit({ ...state, subscription: { ...state.subscription, status: 'free' } }, { touched: ['subscription'] });
}

// ---------------------------------------------------------------------------
// Opérations
// ---------------------------------------------------------------------------
const stamp = () => { const t = nowIso(); return { createdAt: t, updatedAt: t }; };

export function addRevenue({ grossAmount, date, label, exceptional = false }) {
  const rate = state.settings.hasContributions ? state.settings.contributionRate : 0;
  const item = cleanRevenue({
    id: uid(),
    date, // date à laquelle le revenu a été RÉALISÉ (≠ createdAt)
    ...buildRevenueAmounts(grossAmount, rate), // cotisations figées au moment de l'enregistrement
    contributionRate: rate,
    label,
    exceptional,
    ...stamp(),
  });
  if (!item) return null;
  commit({ ...state, revenues: [...state.revenues, item].sort(byDateDesc) });
  return item;
}

export function addTransfer({ amount, date, label }) {
  const item = cleanAmountOp({ id: uid(), date, amount, label, ...stamp() }, false);
  if (!item) return null;
  commit({ ...state, transfers: [...state.transfers, item].sort(byDateDesc) });
  return item;
}

export function addWithdrawal({ amount, date, label, categoryId = null, plannedId = null }) {
  const item = cleanAmountOp({ id: uid(), date, amount, label, categoryId, plannedId, ...stamp() }, true);
  if (!item) return null;
  commit({ ...state, withdrawals: [...state.withdrawals, item].sort(byDateDesc) });
  return item;
}

/** Modifie le dossier d'un retrait existant. */
export function setWithdrawalCategory(withdrawalId, categoryId) {
  commit({
    ...state,
    withdrawals: state.withdrawals.map((w) => (w.id === withdrawalId ? { ...w, categoryId: id(categoryId), updatedAt: nowIso() } : w)),
  });
}

const COLLECTIONS = { revenue: 'revenues', transfer: 'transfers', withdrawal: 'withdrawals' };

export function deleteOperation(type, itemId) {
  const key = COLLECTIONS[type];
  commit({ ...state, [key]: state[key].filter((x) => x.id !== itemId), deleted: { ...state.deleted, [itemId]: nowIso() } });
}

/**
 * Import d'historique : revenus d'avant Nelyqor (ex. un ancien tableur). Ils comptent dans les
 * moyennes, statistiques, graphiques et le chiffre d'affaires, mais PAS dans l'objectif (le montant
 * « déjà de côté » couvre déjà cette période) ni dans les cotisations à garder de côté.
 * Les doublons (même date, même montant brut) et les identifiants déjà présents sont ignorés.
 * @returns {number} nombre de revenus ajoutés
 */
export function importHistoryRevenues(list) {
  const clean = sanitize({ revenues: list }).revenues;
  const ids = new Set(state.revenues.map((r) => r.id));
  const keys = new Set(state.revenues.map((r) => `${r.date}|${r.grossAmount}`));
  const t = nowIso();
  const added = [];
  for (const r of clean) {
    const k = `${r.date}|${r.grossAmount}`;
    if (ids.has(r.id) || keys.has(k)) continue;
    keys.add(k);
    added.push({ ...r, history: true, updatedAt: t });
  }
  if (added.length) commit({ ...state, revenues: [...state.revenues, ...added].sort(byDateDesc) });
  return added.length;
}

// ---------------------------------------------------------------------------
// Dossiers de dépenses (Nelyqor+)
// ---------------------------------------------------------------------------
export function addCategory(name) {
  const item = cleanCategory({ id: uid(), name, ...stamp() });
  if (!item) return null;
  commit({ ...state, categories: [...state.categories, item].sort((a, b) => a.name.localeCompare(b.name, 'fr')) });
  return item;
}

export function renameCategory(categoryId, name) {
  const clean = str(name, CATEGORY_NAME_MAX);
  if (!clean) return false;
  commit({
    ...state,
    categories: state.categories.map((c) => (c.id === categoryId ? { ...c, name: clean, updatedAt: nowIso() } : c)).sort((a, b) => a.name.localeCompare(b.name, 'fr')),
  });
  return true;
}

/** Supprime le dossier : les retraits et dépenses prévues sont conservés, sans dossier. */
export function deleteCategory(categoryId) {
  const t = nowIso();
  commit({
    ...state,
    categories: state.categories.filter((c) => c.id !== categoryId),
    withdrawals: state.withdrawals.map((w) => (w.categoryId === categoryId ? { ...w, categoryId: null, updatedAt: t } : w)),
    plannedExpenses: state.plannedExpenses.map((p) => (p.categoryId === categoryId ? { ...p, categoryId: null, updatedAt: t } : p)),
    deleted: { ...state.deleted, [categoryId]: t },
  });
}

// ---------------------------------------------------------------------------
// Dépenses prévues / récurrentes (Nelyqor+)
// Occurrence n = startDate + n × everyMonths (sans dérive de fin de mois).
// ---------------------------------------------------------------------------
export const planOccurrence = (p, n) => addMonths(p.startDate, n * p.everyMonths);
export const planNextDate = (p) => planOccurrence(p, p.done);

export function addPlannedExpense({ label, amount, categoryId = null, startDate, everyMonths }) {
  const item = cleanPlan({ id: uid(), label, amount, categoryId, startDate, everyMonths, done: 0, ...stamp() });
  if (!item) return null;
  commit({ ...state, plannedExpenses: [...state.plannedExpenses, item] });
  return item;
}

export function updatePlannedExpense(planId, patch) {
  let ok = false;
  const list = state.plannedExpenses.map((p) => {
    if (p.id !== planId) return p;
    const next = cleanPlan({ ...p, ...patch, updatedAt: nowIso() });
    if (!next) return p;
    ok = true;
    return next;
  });
  if (ok) commit({ ...state, plannedExpenses: list });
  return ok;
}

export function deletePlannedExpense(planId) {
  commit({ ...state, plannedExpenses: state.plannedExpenses.filter((p) => p.id !== planId), deleted: { ...state.deleted, [planId]: nowIso() } });
}

/** La dépense prévue a eu lieu : on l'enregistre comme retrait et on passe à l'échéance suivante. */
export function confirmPlannedExpense(planId, { amount, date } = {}) {
  const p = state.plannedExpenses.find((x) => x.id === planId);
  if (!p) return null;
  const t = nowIso();
  const w = cleanAmountOp({ id: uid(), date: date || minDate(planNextDate(p), todayKey()), amount: amount ?? p.amount, label: p.label, categoryId: p.categoryId, plannedId: p.id, createdAt: t, updatedAt: t }, true);
  if (!w) return null;
  commit({
    ...state,
    withdrawals: [...state.withdrawals, w].sort(byDateDesc),
    plannedExpenses: state.plannedExpenses.map((x) => (x.id === planId ? { ...x, done: x.done + 1, updatedAt: t } : x)),
  });
  return w;
}

/** L'échéance n'a pas eu lieu : on passe à la suivante sans retrait. */
export function skipPlannedExpense(planId) {
  commit({
    ...state,
    plannedExpenses: state.plannedExpenses.map((x) => (x.id === planId ? { ...x, done: x.done + 1, updatedAt: nowIso() } : x)),
  });
}

const minDate = (a, b) => (a < b ? a : b);

// ---------------------------------------------------------------------------
// Calendrier : un jour précis travaillé ou non, sans toucher au planning habituel.
// ---------------------------------------------------------------------------

/** Ce jour est-il travaillé selon le planning habituel (sans exception) ? */
export function isPlannedWeekday(dateKey) {
  const wd = (new Date(dateKey + 'T12:00:00Z').getUTCDay() + 6) % 7;
  return Boolean(state.settings && state.settings.plannedActivityDays.includes(wd));
}

/** Indique si `dateKey` est travaillé. Si c'est déjà le planning habituel, l'exception est retirée. */
export function setDayWorked(dateKey, worked) {
  if (!isValidKey(dateKey)) return;
  const calendar = { ...state.calendar };
  if (worked === isPlannedWeekday(dateKey)) delete calendar[dateKey];
  else calendar[dateKey] = worked ? 'work' : 'off';
  commit({ ...state, calendar }, { touched: ['calendar'] });
}

/** Ancienne API (« Je ne travaille pas aujourd'hui ») conservée pour compatibilité. */
export function setRestDay(dateKey, isRest) {
  setDayWorked(dateKey, !isRest);
}

// ---------------------------------------------------------------------------
// Réinitialisation complète de CET appareil (retour au premier lancement)
// ---------------------------------------------------------------------------

export function resetAll() {
  try { localStorage.removeItem(STORAGE_KEY); } catch { /* ignore */ }
  memoryFallback = null;
  commit(emptyState(), { local: false });
}
