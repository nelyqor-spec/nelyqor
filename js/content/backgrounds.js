// Nelyqor — fonds abstraits de la page Objectif.
// 8 variantes cohérentes (graphite, teal, touche de bleu). Pas de photo.
// La variante est choisie pour la journée : elle ne change pas pendant que l'utilisateur regarde.
// Les cartes restent opaques à ~92 % par-dessus : le contraste des textes est préservé.

import { dayNumber } from '../core/dates.js';

const T = 'var(--accent-rgb)'; // couleur principale choisie par l'utilisateur
const B = '120, 166, 255'; // bleu #78A6FF

export const BACKGROUNDS = [
  `radial-gradient(120% 60% at 85% -10%, rgba(${T},0.22) 0%, transparent 60%),
   radial-gradient(90% 50% at -10% 40%, rgba(${B},0.10) 0%, transparent 60%)`,
  `radial-gradient(80% 45% at 10% 0%, rgba(${T},0.20) 0%, transparent 65%),
   radial-gradient(70% 40% at 100% 55%, rgba(${T},0.08) 0%, transparent 70%)`,
  `linear-gradient(160deg, rgba(${T},0.14) 0%, transparent 35%),
   radial-gradient(60% 40% at 90% 30%, rgba(${B},0.12) 0%, transparent 70%)`,
  `radial-gradient(140% 70% at 50% -25%, rgba(${T},0.24) 0%, transparent 55%),
   radial-gradient(50% 30% at 15% 70%, rgba(${B},0.07) 0%, transparent 70%)`,
  `radial-gradient(140% 38% at 50% -5%, rgba(${T},0.17) 0%, transparent 62%),
   radial-gradient(120% 28% at 25% 38%, rgba(${B},0.08) 0%, transparent 70%),
   radial-gradient(100% 24% at 85% 62%, rgba(${T},0.06) 0%, transparent 70%)`,
  `radial-gradient(70% 35% at 0% 10%, rgba(${B},0.14) 0%, transparent 70%),
   radial-gradient(90% 50% at 100% 0%, rgba(${T},0.18) 0%, transparent 60%)`,
  `repeating-radial-gradient(circle at 110% -10%, rgba(${T},0) 0px, rgba(${T},0.045) 26px, rgba(${T},0) 52px),
   radial-gradient(100% 55% at 90% -5%, rgba(${T},0.18) 0%, transparent 60%)`,
  `linear-gradient(200deg, rgba(${B},0.10) 0%, transparent 30%),
   radial-gradient(110% 55% at 20% -15%, rgba(${T},0.20) 0%, transparent 60%)`,
];

/** Fond fixe (fond dynamique désactivé). */
export const STATIC_BACKGROUND = `radial-gradient(120% 50% at 50% -20%, rgba(${T},0.10) 0%, transparent 60%)`;

export function getDailyBackground(dateKey) {
  return BACKGROUNDS[((dayNumber(dateKey) * 3) % BACKGROUNDS.length + BACKGROUNDS.length) % BACKGROUNDS.length];
}
