// Nelyqor — argent et pourcentages.
// Tous les montants sont stockés et calculés en CENTIMES (entiers).
// Les taux de cotisation sont stockés en POINTS DE BASE : 21 % = 2100, 21,5 % = 2150.
// On ne formate qu'au moment de l'affichage.

export const RATE_SCALE = 10000; // 100 % = 10000

/** "1 234,56" | "1234.5" | "12 €" -> centimes (entier) ; null si invalide. */
export function parseAmountToCents(input) {
  if (input === null || input === undefined) return null;
  let s = String(input).trim().replace(/[\s  €]/g, '');
  if (!s) return null;
  s = s.replace(',', '.');
  if (!/^\d+(\.\d{0,2})?$/.test(s)) return null;
  const [intPart, decPart = ''] = s.split('.');
  const cents = parseInt(intPart, 10) * 100 + parseInt((decPart + '00').slice(0, 2), 10);
  return Number.isSafeInteger(cents) ? cents : null;
}

/** "21" | "21,5" -> 2150 points de base ; null si invalide. */
export function parseRateToBp(input) {
  if (input === null || input === undefined) return null;
  let s = String(input).trim().replace(/[\s  %]/g, '').replace(',', '.');
  if (!s) return null;
  if (!/^\d+(\.\d{0,2})?$/.test(s)) return null;
  const [i, d = ''] = s.split('.');
  return parseInt(i, 10) * 100 + parseInt((d + '00').slice(0, 2), 10);
}

/** Cotisations réservées sur un revenu brut (arrondi au centime). */
export function contributionFor(grossCents, rateBp) {
  if (!rateBp) return 0;
  return Math.round((grossCents * rateBp) / RATE_SCALE);
}

/** Net = brut - cotisations. */
export function netFor(grossCents, rateBp) {
  return grossCents - contributionFor(grossCents, rateBp);
}

/** Brut nécessaire pour obtenir un net donné (arrondi au centime supérieur). */
export function grossNeededForNet(netCents, rateBp) {
  if (netCents <= 0) return 0;
  if (!rateBp) return netCents;
  return Math.ceil((netCents * RATE_SCALE) / (RATE_SCALE - rateBp));
}

/** Pourcentage réel (non plafonné). null si la base vaut 0. */
export function percentOf(part, total) {
  if (!total) return null;
  return (part / total) * 100;
}

const eur2 = new Intl.NumberFormat('fr-FR', { style: 'currency', currency: 'EUR', minimumFractionDigits: 2, maximumFractionDigits: 2 });
const eur0 = new Intl.NumberFormat('fr-FR', { style: 'currency', currency: 'EUR', minimumFractionDigits: 0, maximumFractionDigits: 0 });

/** 310050 -> "3 100,50 €" ; 1800000 -> "18 000 €" (décimales seulement si utiles). */
export function formatCents(cents, { sign = false, forceDecimals = false } = {}) {
  const abs = Math.abs(cents);
  const useDecimals = forceDecimals || abs % 100 !== 0;
  const str = (useDecimals ? eur2 : eur0).format(abs / 100);
  if (cents < 0) return '−' + str;
  if (sign && cents > 0) return '+' + str;
  return str;
}

/** 63.71 -> "63,7 %" ; 145 -> "145 %". */
export function formatPercent(value, digits = 1) {
  if (value === null || value === undefined || !isFinite(value)) return '—';
  const f = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: digits, minimumFractionDigits: 0 });
  return f.format(value) + ' %';
}

/** 2150 -> "21,5 %" */
export function formatRate(bp) {
  return formatPercent(bp / 100, 2);
}

/** Valeur à mettre dans un champ texte : 310050 -> "3100,50" */
export function centsToInput(cents) {
  if (cents === null || cents === undefined) return '';
  const i = Math.floor(cents / 100);
  const d = cents % 100;
  return d ? `${i},${String(d).padStart(2, '0')}` : String(i);
}

export function rateToInput(bp) {
  if (bp === null || bp === undefined) return '';
  const i = Math.floor(bp / 100);
  const d = bp % 100;
  if (!d) return String(i);
  return `${i},${String(d).padStart(2, '0').replace(/0$/, '')}`;
}
