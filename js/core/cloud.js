// Nelyqor — compte utilisateur et synchronisation cloud (Supabase).
//
// Principe : l'appareil reste la source de vérité immédiate (l'app marche hors-ligne).
// Dès qu'un compte est connecté, l'état complet est fusionné avec celui du cloud
// (js/core/sync.js) puis renvoyé, à chaque modification, au retour en ligne, au retour
// dans l'app, toutes les 60 s et dès qu'un autre appareil écrit (temps réel).
// Chaque utilisateur ne peut lire et écrire QUE sa propre ligne (règles RLS, supabase/schema.sql).

import * as store from './store.js';
import { mergeStates, sameState } from './sync.js';
import { SUPABASE_URL, SUPABASE_ANON_KEY } from '../config.js';

const TABLE = 'nelyqor_states';
const LIB_SRC = 'js/vendor/supabase-2.117.3.umd.js';
const LIB_SRI = 'sha384-BWcjm9OdFth9TbhCxZPdm+gAOUMAzQy9nmTs12ioXdCcbVnJaPMn+fMUoQCLt60R';
const PUSH_DELAY_MS = 1500;
const PULL_EVERY_MS = 60 * 1000;

let client = null;
let channel = null;
let pushTimer = null;
let syncing = null;
let syncAgain = false;
const listeners = new Set();
const status = { ready: false, user: null, provider: null, syncing: false, lastSyncAt: null, error: null, recovery: false, pendingEmail: null };

export function isConfigured() {
  try {
    return new URL(SUPABASE_URL).protocol === 'https:' && typeof SUPABASE_ANON_KEY === 'string' && SUPABASE_ANON_KEY.length > 20;
  } catch {
    return false;
  }
}

export const getStatus = () => status;
export function onStatus(fn) { listeners.add(fn); return () => listeners.delete(fn); }
function emit(patch) { Object.assign(status, patch); listeners.forEach((fn) => fn(status)); }

const redirectUrl = () => location.origin + location.pathname;

function loadLibrary() {
  if (window.supabase && window.supabase.createClient) return Promise.resolve(window.supabase);
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = LIB_SRC;
    s.integrity = LIB_SRI; // le navigateur refuse le fichier s'il a été modifié
    s.crossOrigin = 'anonymous';
    s.onload = () => (window.supabase ? resolve(window.supabase) : reject(new Error('lib')));
    s.onerror = () => reject(new Error('lib'));
    document.head.append(s);
  });
}

// ---------------------------------------------------------------------------
// Messages d'erreur lisibles (sans détails techniques exploitables)
// ---------------------------------------------------------------------------
export function friendlyError(err) {
  const m = String((err && (err.message || err.error_description || err.code)) || '').toLowerCase();
  if (!navigator.onLine || m.includes('failed to fetch') || m.includes('network')) return 'Pas de connexion internet. Réessaie dans un instant.';
  if (m.includes('invalid login') || m.includes('invalid_credentials')) return 'E-mail ou mot de passe incorrect.';
  if (m.includes('email not confirmed')) return 'Confirme d’abord ton adresse e-mail grâce au lien reçu.';
  if (m.includes('already registered') || m.includes('already been registered') || m.includes('user_already_exists')) return 'Un compte existe déjà avec cette adresse. Connecte-toi.';
  if (m.includes('password') && (m.includes('short') || m.includes('weak') || m.includes('at least'))) return 'Mot de passe trop faible : au moins 10 caractères, avec lettres et chiffres.';
  if (m.includes('rate limit') || m.includes('too many') || m.includes('429')) return 'Trop de tentatives. Patiente quelques minutes.';
  if (m.includes('same password')) return 'Choisis un mot de passe différent de l’ancien.';
  if (m.includes('provider is not enabled') || m.includes('unsupported provider')) return 'Ce mode de connexion n’est pas encore activé.';
  return 'Une erreur est survenue. Réessaie.';
}

// ---------------------------------------------------------------------------
// Démarrage
// ---------------------------------------------------------------------------
export async function initCloud() {
  if (!isConfigured() || client) return;
  try {
    const lib = await loadLibrary();
    client = lib.createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
      auth: { flowType: 'pkce', persistSession: true, autoRefreshToken: true, detectSessionInUrl: true, storageKey: 'nelyqor-auth' },
    });
  } catch {
    emit({ ready: true, error: 'Synchronisation indisponible pour le moment.' });
    return;
  }

  client.auth.onAuthStateChange((event, session) => {
    // Les appels réseau sont différés : on ne doit pas attendre dans ce rappel.
    setTimeout(() => handleSession(event, session), 0);
  });
  const { data } = await client.auth.getSession();
  await handleSession('INITIAL', data.session);
  cleanUrl();

  store.onLocalChange(schedulePush);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) syncNow(); });
  window.addEventListener('online', () => syncNow());
  setInterval(() => { if (!document.hidden) syncNow(); }, PULL_EVERY_MS);
}

/** Retire de l'adresse les paramètres de connexion (code, erreurs) après traitement. */
function cleanUrl() {
  const u = new URL(location.href);
  let changed = false;
  for (const k of ['code', 'error', 'error_code', 'error_description', 'type']) if (u.searchParams.has(k)) { u.searchParams.delete(k); changed = true; }
  if (u.hash && /access_token|error/.test(u.hash)) { u.hash = ''; changed = true; }
  if (changed) history.replaceState(null, '', u.pathname + u.search + u.hash);
}

async function handleSession(event, session) {
  const user = session ? session.user : null;
  const prevId = status.user && status.user.id;
  emit({
    ready: true,
    user,
    provider: user ? ((user.app_metadata && user.app_metadata.provider) || 'email') : null,
    recovery: event === 'PASSWORD_RECOVERY' ? true : status.recovery && Boolean(user),
  });
  if (user && user.id !== prevId) {
    subscribeRealtime(user.id);
    await syncNow();
  }
  if (!user) unsubscribeRealtime();
}

function subscribeRealtime(userId) {
  unsubscribeRealtime();
  try {
    channel = client.channel('nelyqor-' + userId)
      .on('postgres_changes', { event: '*', schema: 'public', table: TABLE, filter: `user_id=eq.${userId}` }, () => syncNow())
      .subscribe();
  } catch { channel = null; }
}

function unsubscribeRealtime() {
  if (channel && client) client.removeChannel(channel);
  channel = null;
}

// ---------------------------------------------------------------------------
// Synchronisation : lire → fusionner → écrire si nécessaire
// ---------------------------------------------------------------------------
function schedulePush() {
  if (!status.user) return;
  clearTimeout(pushTimer);
  pushTimer = setTimeout(syncNow, PUSH_DELAY_MS);
}

export function syncNow() {
  if (!client || !status.user) return Promise.resolve(false);
  if (syncing) { syncAgain = true; return syncing; }
  syncing = runSync().finally(() => {
    syncing = null;
    if (syncAgain) { syncAgain = false; syncNow(); }
  });
  return syncing;
}

async function runSync() {
  if (!navigator.onLine) return false;
  const userId = status.user.id;
  emit({ syncing: true });
  try {
    const { data: row, error } = await client.from(TABLE).select('data').eq('user_id', userId).maybeSingle();
    if (error) throw error;
    const local = store.sanitize(store.getState());
    const merged = row ? mergeStates(local, row.data) : local;
    if (!sameState(merged, local)) store.replaceState(merged);
    const remote = row ? store.sanitize(row.data) : null;
    if (merged.onboardingCompleted && (!remote || !sameState(merged, remote))) {
      const { error: upErr } = await client.from(TABLE).upsert({ user_id: userId, data: merged }, { onConflict: 'user_id' });
      if (upErr) throw upErr;
    }
    emit({ syncing: false, lastSyncAt: new Date().toISOString(), error: null });
    return true;
  } catch (e) {
    emit({ syncing: false, error: friendlyError(e) });
    return false;
  }
}

// ---------------------------------------------------------------------------
// Connexion / inscription
// ---------------------------------------------------------------------------
function requireClient() {
  if (!client) throw new Error('Synchronisation indisponible pour le moment.');
  return client;
}

export async function signInWithProvider(provider) {
  if (!['apple', 'google'].includes(provider)) throw new Error('provider');
  const { error } = await requireClient().auth.signInWithOAuth({ provider, options: { redirectTo: redirectUrl() } });
  if (error) throw new Error(friendlyError(error));
}

export async function signInWithEmail(email, password) {
  const { error } = await requireClient().auth.signInWithPassword({ email, password });
  if (error) throw new Error(friendlyError(error));
}

/** @returns {Promise<'signed-in'|'check-email'>} */
export async function signUpWithEmail(email, password) {
  const { data, error } = await requireClient().auth.signUp({ email, password, options: { emailRedirectTo: redirectUrl() } });
  if (error) throw new Error(friendlyError(error));
  if (data.session) return 'signed-in';
  emit({ pendingEmail: email });
  return 'check-email';
}

export async function resetPassword(email) {
  const { error } = await requireClient().auth.resetPasswordForEmail(email, { redirectTo: redirectUrl() });
  // Même réponse que l'adresse existe ou non : on ne révèle pas quels e-mails ont un compte.
  if (error && /rate|too many|429/i.test(error.message || '')) throw new Error(friendlyError(error));
}

export async function updatePassword(password) {
  const { error } = await requireClient().auth.updateUser({ password });
  if (error) throw new Error(friendlyError(error));
  emit({ recovery: false });
}

export async function signOut({ silent = false } = {}) {
  clearTimeout(pushTimer);
  if (!silent && status.user) await syncNow(); // dernière sauvegarde avant de partir
  try { await requireClient().auth.signOut({ scope: 'local' }); } catch { /* session déjà absente */ }
  unsubscribeRealtime();
  emit({ user: null, provider: null, lastSyncAt: null, recovery: false });
}

/** Suppression définitive du compte et des données cloud (exigé par l'App Store). */
export async function deleteAccount() {
  const { error } = await requireClient().rpc('delete_my_account');
  if (error) throw new Error(friendlyError(error));
  await signOut({ silent: true });
}

export function dismissRecovery() { emit({ recovery: false }); }
