// Nelyqor — couleur principale personnalisable.
// La couleur choisie remplace l'accent turquoise partout (boutons, barres, graphiques, fonds).
import { DEFAULT_ACCENT, isHexColor } from '../core/store.js';

export const ACCENT_PRESETS = [
  { name: 'Turquoise', hex: '#2ED3B7' },
  { name: 'Vert', hex: '#4ADE80' },
  { name: 'Bleu', hex: '#60A5FA' },
  { name: 'Violet', hex: '#A78BFA' },
  { name: 'Rose', hex: '#F472B6' },
  { name: 'Rouge', hex: '#F87171' },
  { name: 'Orange', hex: '#FB923C' },
  { name: 'Or', hex: '#FACC15' },
];

const rgbOf = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));

function luminance([r, g, b]) {
  const c = [r, g, b].map((v) => { const x = v / 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}

/**
 * Sur fond sombre, une couleur trop foncée deviendrait illisible : on l'éclaircit
 * jusqu'à un contraste suffisant avec le fond de l'app (#0F1720).
 */
export function readableAccent(hex) {
  let rgb = rgbOf(isHexColor(hex) ? hex : DEFAULT_ACCENT);
  const bgL = luminance([15, 23, 32]);
  for (let i = 0; i < 20 && (luminance(rgb) + 0.05) / (bgL + 0.05) < 4.5; i++) {
    rgb = rgb.map((v) => Math.round(v + (255 - v) * 0.12));
  }
  return rgb;
}

export function applyAccent(hex) {
  const rgb = readableAccent(hex);
  const root = document.documentElement.style;
  const hexOut = '#' + rgb.map((v) => v.toString(16).padStart(2, '0')).join('');
  root.setProperty('--teal', hexOut);
  root.setProperty('--accent-rgb', rgb.join(', '));
  // Texte posé sur la couleur : sombre teinté si la couleur est claire, blanc sinon.
  const ink = luminance(rgb) > 0.28 ? `rgb(${rgb.map((v) => Math.round(v * 0.13)).join(', ')})` : '#FFFFFF';
  root.setProperty('--teal-ink', ink);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', '#0F1720');
}
