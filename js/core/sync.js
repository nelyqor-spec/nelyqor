// Nelyqor — fusion de deux états (appareil ↔ cloud). Fonction pure, sans réseau.
// Règles :
//  - Opérations, dossiers et dépenses prévues : union par identifiant ; en cas de conflit,
//    la version modifiée le plus récemment l'emporte (updatedAt).
//  - Suppressions : un élément supprimé d'un côté est supprimé partout (journal `deleted`).
//  - Réglages, profil, préférences, abonnement, jours de repos : la rubrique modifiée
//    le plus récemment l'emporte (updatedAt par rubrique).
import { sanitize, byDateDesc } from './store.js';

const later = (a, b) => (a || '') >= (b || '');

function mergeList(a, b, deleted) {
  const map = new Map();
  for (const x of [...a, ...b]) {
    if (deleted[x.id]) continue;
    const prev = map.get(x.id);
    if (!prev || !later(prev.updatedAt, x.updatedAt)) map.set(x.id, x);
  }
  return [...map.values()];
}

export function mergeStates(localRaw, remoteRaw) {
  const local = sanitize(localRaw);
  const remote = sanitize(remoteRaw);
  if (!remote.onboardingCompleted) return local;
  if (!local.onboardingCompleted) return remote;

  const deleted = { ...remote.deleted };
  for (const [k, v] of Object.entries(local.deleted)) if (!deleted[k] || v > deleted[k]) deleted[k] = v;

  const lu = local.updatedAt;
  const ru = remote.updatedAt;
  const pick = (key, field = key) => (later(lu[key], ru[key]) ? local[field] : remote[field]);

  // Un essai consommé l'est sur tous les appareils.
  const subscription = { ...pick('subscription') };
  subscription.trialUsed = local.subscription.trialUsed || remote.subscription.trialUsed;

  const updatedAt = {};
  for (const k of new Set([...Object.keys(lu), ...Object.keys(ru)])) updatedAt[k] = later(lu[k], ru[k]) ? lu[k] : ru[k];

  return sanitize({
    version: local.version,
    onboardingCompleted: true,
    settings: pick('settings'),
    profile: pick('profile'),
    preferences: pick('preferences'),
    subscription,
    currentWeekState: pick('restDays', 'currentWeekState'),
    revenues: mergeList(local.revenues, remote.revenues, deleted).sort(byDateDesc),
    transfers: mergeList(local.transfers, remote.transfers, deleted).sort(byDateDesc),
    withdrawals: mergeList(local.withdrawals, remote.withdrawals, deleted).sort(byDateDesc),
    categories: mergeList(local.categories, remote.categories, deleted),
    plannedExpenses: mergeList(local.plannedExpenses, remote.plannedExpenses, deleted),
    deleted,
    updatedAt,
  });
}

/** Comparaison stable (pour éviter les écritures inutiles). */
export function sameState(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}
