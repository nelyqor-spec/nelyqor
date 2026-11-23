// Nelyqor — sauvegarde et restauration de TOUTES les données dans un fichier (gratuit).
// Le fichier .json reste chez l'utilisateur (Fichiers, iCloud Drive, Google Drive, e-mail…).
// À la restauration, il est validé puis FUSIONNÉ avec les données présentes : rien n'est écrasé.
import { h } from './dom.js';
import { Card, Button, Notice, toast, confirmDialog } from './components.js';
import { todayKey } from '../core/dates.js';
import * as store from '../core/store.js';
import { mergeStates } from '../core/sync.js';

const MAX_BYTES = 10 * 1024 * 1024;
const LAST_BACKUP_KEY = 'nelyqor:last-backup';

export function buildBackup(state = store.getState()) {
  return JSON.stringify({ app: 'nelyqor', format: 1, appVersion: store.APP_VERSION, exportedAt: new Date().toISOString(), data: store.sanitize(state) }, null, 1);
}

/** Lit un fichier de sauvegarde. Lève une Error lisible si le fichier n'est pas valable. */
export function parseBackup(text) {
  let obj;
  try { obj = JSON.parse(text); } catch { throw new Error('Ce fichier n’est pas une sauvegarde Nelyqor.'); }
  if (!obj || obj.app !== 'nelyqor' || typeof obj.data !== 'object') throw new Error('Ce fichier n’est pas une sauvegarde Nelyqor.');
  const data = store.sanitize(obj.data);
  if (!data.onboardingCompleted) throw new Error('Cette sauvegarde ne contient pas d’objectif.');
  return { data, exportedAt: obj.exportedAt };
}

export async function saveBackup() {
  const name = `nelyqor-sauvegarde-${todayKey()}.json`;
  const blob = new Blob([buildBackup()], { type: 'application/json' });
  // Sur téléphone : feuille de partage (Enregistrer dans Fichiers, Drive, e-mail…).
  try {
    const file = new File([blob], name, { type: 'application/json' });
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      await navigator.share({ files: [file], title: 'Sauvegarde Nelyqor' });
      markSaved();
      toast('Sauvegarde prête');
      return;
    }
  } catch (e) {
    if (e && e.name === 'AbortError') return; // partage annulé
  }
  const url = URL.createObjectURL(blob);
  const a = h('a', { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  markSaved();
  toast('Sauvegarde téléchargée');
}

function markSaved() {
  try { localStorage.setItem(LAST_BACKUP_KEY, new Date().toISOString()); } catch { /* ignore */ }
}

function lastSaved() {
  try { return localStorage.getItem(LAST_BACKUP_KEY); } catch { return null; }
}

/**
 * Fusionne des données de sauvegarde. Avec preferBackup, ses réglages l'emportent (ex. un objectif
 * créé juste pour accéder à l'app) ; les opérations des deux côtés sont toujours conservées.
 */
function applyBackupData(data, preferBackup) {
  const t = new Date().toISOString();
  const incoming = preferBackup
    ? { ...data, updatedAt: Object.fromEntries(['settings', 'profile', 'preferences', 'subscription', 'calendar'].map((k) => [k, t])) }
    : data;
  store.replaceState(mergeStates(store.getState(), incoming));
}

function hashOf(str) {
  let x = 2166136261;
  for (let i = 0; i < str.length; i++) x = Math.imul(x ^ str.charCodeAt(i), 16777619);
  return (x >>> 0).toString(36) + ':' + str.length;
}

/**
 * Lien personnel « tout compris » : …/index.html#d=<sauvegarde compressée>. Les données sont dans
 * le fragment (#), qui n'est jamais envoyé au serveur. Appliqué une seule fois par appareil ;
 * le fragment reste dans l'adresse pour que l'icône d'écran d'accueil (stockage séparé sur iPhone)
 * reçoive aussi les données à sa première ouverture.
 * @returns {Promise<boolean>} true si des données ont été ajoutées
 */
export async function applyLinkedBackup() {
  const m = /^#d=([A-Za-z0-9_-]{16,})$/.exec(location.hash);
  if (!m || typeof DecompressionStream === 'undefined') return false;
  const SEED_KEY = 'nelyqor:link-seed';
  const sig = hashOf(m[1]);
  try { if (localStorage.getItem(SEED_KEY) === sig) return false; } catch { /* ignore */ }
  const b64 = m[1].replace(/-/g, '+').replace(/_/g, '/');
  const bin = atob(b64 + '='.repeat((4 - (b64.length % 4)) % 4));
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  const text = await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate-raw'))).text();
  if (text.length > MAX_BYTES) return false;
  const { data } = parseBackup(text);
  applyBackupData(data, !store.getState().onboardingCompleted);
  try { localStorage.setItem(SEED_KEY, sig); } catch { /* ignore */ }
  return true;
}

export function restoreBackup() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'application/json,.json';
  input.style.display = 'none';
  input.addEventListener('change', async () => {
    const file = input.files && input.files[0];
    input.remove();
    if (!file) return;
    try {
      if (file.size > MAX_BYTES) throw new Error('Fichier trop lourd pour une sauvegarde Nelyqor.');
      const text = await file.text();
      // Fichier d'historique (revenus d'avant Nelyqor) : ajouté sans toucher au reste.
      let raw = null;
      try { raw = JSON.parse(text); } catch { /* traité par parseBackup */ }
      if (raw && raw.app === 'nelyqor' && raw.kind === 'history' && Array.isArray(raw.revenues)) {
        if (!store.getState().onboardingCompleted) throw new Error('Restaure d’abord ta sauvegarde (ou crée ton objectif), puis importe l’historique.');
        const n = raw.revenues.length;
        const ok = await confirmDialog({
          title: 'Ajouter ton historique ?',
          message: `${n} revenu(s) ${raw.label ? `(${raw.label}) ` : ''}vont être ajoutés. Ils comptent dans tes moyennes par jour et tes statistiques, mais pas dans ton objectif. Tes données actuelles ne changent pas.`,
          confirmLabel: 'Ajouter l’historique',
        });
        if (!ok) return;
        const added = store.importHistoryRevenues(raw.revenues);
        toast(added ? `${added} revenus d’historique ajoutés` : 'Cet historique est déjà dans l’app');
        return;
      }
      const { data, exportedAt } = parseBackup(text);
      const when = exportedAt ? new Date(exportedAt).toLocaleDateString('fr-FR', { dateStyle: 'long' }) : 'date inconnue';
      const ok = await confirmDialog({
        title: 'Restaurer cette sauvegarde ?',
        message: `Sauvegarde du ${when} : ${data.revenues.length} revenu(s), ${data.transfers.length} virement(s), ${data.withdrawals.length} retrait(s). Elle sera ajoutée à tes données actuelles, sans rien effacer.`,
        confirmLabel: 'Restaurer',
      });
      if (!ok) return;
      applyBackupData(data, true);
      toast('Sauvegarde restaurée');
    } catch (e) {
      toast(e.message || 'Restauration impossible.');
    }
  }, { once: true });
  document.body.append(input);
  input.click();
}

export function backupCard() {
  const last = lastSaved();
  return Card([
    h('h2', { class: 'card-title' }, 'Sauvegarde de mes données'),
    h('p', { class: 'muted' }, 'Enregistre une copie complète de tes données dans un fichier (Fichiers, iCloud Drive, Google Drive, e-mail…). Tu pourras la restaurer à tout moment, même sur un autre téléphone.'),
    last && h('p', { class: 'field-hint' }, `Dernière sauvegarde : ${new Date(last).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' })}`),
    h('div', { class: 'btn-stack' },
      Button('Sauvegarder mes données', saveBackup, { iconName: 'download' }),
      Button('Restaurer une sauvegarde', restoreBackup, { variant: 'secondary', iconName: 'refresh' })),
    !last && Notice('info', null, 'Conseil : fais une sauvegarde avant chaque mise à jour importante de l’application.'),
  ], { label: 'Sauvegarde de mes données' });
}
