// Nelyqor — dates.
// On manipule des "clés de jour" au format "YYYY-MM-DD", dans le fuseau horaire de l'utilisateur.
// L'arithmétique se fait en UTC pour éviter les surprises liées au changement d'heure.

const DAY = 86400000;

export const WEEKDAYS = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche'];
export const WEEKDAYS_SHORT = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim'];

const pad = (n) => String(n).padStart(2, '0');

/** Date locale (fuseau de l'utilisateur) -> "YYYY-MM-DD" */
export function toKey(date = new Date()) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function todayKey() {
  return toKey(new Date());
}

export function isValidKey(k) {
  if (typeof k !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(k)) return false;
  const [y, m, d] = k.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  return dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d;
}

function toUTC(k) {
  const [y, m, d] = k.split('-').map(Number);
  return Date.UTC(y, m - 1, d);
}

function fromUTC(ms) {
  const d = new Date(ms);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
}

export function addDays(k, n) {
  return fromUTC(toUTC(k) + n * DAY);
}

/** Nombre de jours de a vers b (b - a). */
export function diffDays(a, b) {
  return Math.round((toUTC(b) - toUTC(a)) / DAY);
}

/** Numéro de jour absolu (utile pour les rotations quotidiennes). */
export function dayNumber(k) {
  return Math.floor(toUTC(k) / DAY);
}

/** 0 = lundi … 6 = dimanche */
export function weekday(k) {
  return (new Date(toUTC(k)).getUTCDay() + 6) % 7;
}

export function startOfWeek(k) {
  return addDays(k, -weekday(k));
}

export function endOfWeek(k) {
  return addDays(startOfWeek(k), 6);
}

export function startOfMonth(k) {
  return k.slice(0, 8) + '01';
}

export function endOfMonth(k) {
  const [y, m] = k.split('-').map(Number);
  return fromUTC(Date.UTC(y, m, 0));
}

/** Ajoute n mois en restant sur un jour valide (31 janv. + 1 mois -> 28/29 févr.). */
export function addMonths(k, n) {
  const [y, m, d] = k.split('-').map(Number);
  const target = new Date(Date.UTC(y, m - 1 + n, 1));
  const last = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
  target.setUTCDate(Math.min(d, last));
  return fromUTC(target.getTime());
}

export const minKey = (a, b) => (a < b ? a : b);
export const maxKey = (a, b) => (a > b ? a : b);

const fmtLong = new Intl.DateTimeFormat('fr-FR', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });
const fmtDayMonth = new Intl.DateTimeFormat('fr-FR', { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC' });
const fmtShort = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
const fmtNumeric = new Intl.DateTimeFormat('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC' });
const fmtMonth = new Intl.DateTimeFormat('fr-FR', { month: 'short', timeZone: 'UTC' });
const fmtDM = new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short', timeZone: 'UTC' });

const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

export const formatLong = (k) => cap(fmtLong.format(toUTC(k)));
export const formatDayMonth = (k) => cap(fmtDayMonth.format(toUTC(k)));
export const formatShort = (k) => fmtShort.format(toUTC(k));
export const formatNumeric = (k) => fmtNumeric.format(toUTC(k));
export const formatMonth = (k) => cap(fmtMonth.format(toUTC(k)).replace('.', ''));
export const formatDM = (k) => fmtDM.format(toUTC(k)).replace('.', '');
