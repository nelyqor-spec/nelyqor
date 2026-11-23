// Nelyqor — composants réutilisables.
import { h, icon } from './dom.js';
import { formatCents, formatPercent } from '../core/money.js';
import { formatShort } from '../core/dates.js';
import { getState } from '../core/store.js';

// ---------------------------------------------------------------------------
// Logo — composant UNIQUE. Pour changer de logo : remplacer assets/logo-symbol.png.
// ---------------------------------------------------------------------------
export const LOGO_SRC = 'assets/logo-symbol.png';

export function Logo({ variant = 'full', size = 34 } = {}) {
  const mark = h('span', { class: 'logo-mark', style: { width: size + 'px', height: size + 'px' } },
    h('img', { src: LOGO_SRC, alt: variant === 'icon' ? 'Nelyqor' : '', width: size, height: size }));
  if (variant === 'icon') return h('span', { class: 'logo' }, mark);
  return h('span', { class: 'logo', role: 'img', 'aria-label': 'Nelyqor' },
    mark,
    h('span', { class: 'logo-word', style: { fontSize: Math.round(size * 0.62) + 'px' }, 'aria-hidden': 'true' }, 'Nely', h('span', { class: 'q' }, 'q'), 'or'),
  );
}

/** Photo de profil (ou initiale, ou silhouette). */
export function Avatar({ size = 40, cls = '' } = {}) {
  const st = getState();
  const photo = st.profile && st.profile.avatar;
  const name = ((st.settings && st.settings.displayName) || '').trim();
  const el = h('span', { class: `avatar ${cls}`, style: { width: size + 'px', height: size + 'px', fontSize: Math.round(size * 0.42) + 'px' }, 'aria-hidden': 'true' });
  if (photo) el.append(h('img', { src: photo, alt: '', width: size, height: size }));
  else if (name) el.append(name.charAt(0).toUpperCase());
  else el.append(icon('user', Math.round(size * 0.5)));
  return el;
}

/** Barre du haut : logo à gauche, photo de profil à droite (ouvre le Profil). */
export function TopBar({ logoSize = 30 } = {}) {
  return h('div', { class: 'top-bar' }, Logo({ size: logoSize }),
    h('button', {
      type: 'button', class: 'avatar-btn', 'aria-label': 'Ouvrir mon profil',
      onClick: () => document.dispatchEvent(new CustomEvent('nelyqor:open-profile')),
    }, Avatar({ size: 38 })));
}

export function PageHeader({ title, subtitle }) {
  return h('header', { class: 'page-head' },
    h('div', { style: { marginBottom: '10px' } }, TopBar()),
    h('h1', { class: 'page-title' }, title),
    subtitle && h('p', { class: 'page-sub' }, subtitle),
  );
}

// ---------------------------------------------------------------------------
export function Button(label, onClick, { variant = 'primary', iconName, small, disabled, type = 'button', id } = {}) {
  return h('button', { type, id, class: `btn btn-${variant}${small ? ' btn-sm' : ''}`, onClick, disabled },
    iconName && icon(iconName, 20), label);
}

export function Card(children, { cls = '', label } = {}) {
  return h('section', { class: `card ${cls}`, 'aria-label': label }, children);
}

export function ProgressBar(pct, { label, blue } = {}) {
  const width = Math.max(0, Math.min(100, pct || 0));
  return h('div', {
    class: 'progress' + (blue ? ' blue' : ''), role: 'progressbar',
    'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': Math.round(width), 'aria-label': label,
  }, h('span', { style: { width: width + '%' } }));
}

export function Row(label, value, { valueClass = '' } = {}) {
  return h('div', { class: 'row' }, h('span', { class: 'row-label' }, label), h('span', { class: `row-value ${valueClass}` }, value));
}

export function Stat(label, value, note, { valueClass = '' } = {}) {
  return h('div', { class: 'stat' },
    h('span', { class: 'stat-label' }, label),
    h('span', { class: `stat-value ${valueClass}` }, value),
    note && h('span', { class: 'stat-note' }, note));
}

export function Badge(text, tone = '') {
  return h('span', { class: `badge ${tone}` }, h('span', { class: 'dot' }), text);
}

export function Notice(tone, title, text, iconName = tone === 'success' ? 'check' : tone === 'info' ? 'info' : 'warn') {
  return h('div', { class: `notice ${tone}`, role: tone === 'danger' ? 'alert' : 'status' },
    icon(iconName, 20), h('div', null, title && h('strong', null, title), text && h('span', null, text)));
}

export function EmptyState(text, buttonLabel, onClick, iconName = 'chart') {
  return h('div', { class: 'empty' },
    h('div', { class: 'empty-icon' }, icon(iconName, 24)),
    h('p', null, text),
    buttonLabel && Button(buttonLabel, onClick, { variant: 'primary', small: true, iconName: 'plus' }));
}

// ---------------------------------------------------------------------------
// Ligne d'opération (revenu / virement / retrait)
// ---------------------------------------------------------------------------
const OP_META = {
  revenue: { icon: 'revenue', fallback: 'Revenu' },
  transfer: { icon: 'transfer', fallback: 'Virement' },
  withdrawal: { icon: 'withdrawal', fallback: 'Retrait' },
};

export function OperationRow(type, item, onOpen) {
  const meta = OP_META[type];
  let amount;
  if (type === 'revenue') {
    amount = h('span', { class: 'op-amount pos' }, '+' + formatCents(item.grossAmount, { forceDecimals: true }),
      item.contributionAmount > 0 && h('small', null, `net ${formatCents(item.netAmount, { forceDecimals: true })}`));
  } else if (type === 'transfer') {
    amount = h('span', { class: 'op-amount pos' }, '+' + formatCents(item.amount, { forceDecimals: true }));
  } else {
    amount = h('span', { class: 'op-amount neg' }, '−' + formatCents(item.amount, { forceDecimals: true }));
  }
  return h('button', { class: 'op', type: 'button', onClick: () => onOpen(type, item), 'aria-label': `${item.label || meta.fallback}, ${formatShort(item.date)}. Voir le détail` },
    h('span', { class: `op-icon ${type}` }, icon(meta.icon, 20)),
    h('span', { class: 'op-main' },
      h('span', { class: 'op-label' }, item.label || meta.fallback),
      h('span', { class: 'op-date' }, formatShort(item.date) + (item.history ? ' · historique' : ''))),
    amount);
}

// ---------------------------------------------------------------------------
// Champs de saisie
// ---------------------------------------------------------------------------
export function AmountInput({ id, value = '', placeholder = '', suffix = '€', big, onInput, autofocus, label }) {
  const input = h('input', {
    id, type: 'text', inputmode: 'decimal', autocomplete: 'off', enterkeyhint: 'done',
    placeholder, value, 'aria-label': label,
  });
  if (onInput) input.addEventListener('input', () => onInput(input.value));
  if (autofocus) setTimeout(() => input.focus({ preventScroll: true }), 250);
  const wrap = h('div', { class: 'input-wrap' + (big ? ' big' : '') }, input, suffix && h('span', { class: 'suffix' }, suffix));
  return { wrap, input };
}

export function DateInput({ id, value, min, max, onInput, label }) {
  const input = h('input', { id, type: 'date', value, min, max, 'aria-label': label, required: true });
  if (onInput) input.addEventListener('input', () => onInput(input.value));
  return { wrap: h('div', { class: 'input-wrap' }, input), input };
}

export function TextInput({ id, value = '', placeholder = '', maxlength = 40, label }) {
  const input = h('input', { id, type: 'text', value, placeholder, maxlength, autocomplete: 'off', 'aria-label': label });
  return { wrap: h('div', { class: 'input-wrap' }, input), input };
}

export function Field(labelText, control, { hint, forId } = {}) {
  const error = h('p', { class: 'field-error', role: 'alert', hidden: true });
  const el = h('div', { class: 'field' }, h('label', { for: forId }, labelText), control, hint && h('p', { class: 'field-hint' }, hint), error);
  return {
    el,
    setError(msg) {
      error.textContent = msg || '';
      error.hidden = !msg;
      const wrap = control.classList && control.classList.contains('input-wrap') ? control : control.querySelector && control.querySelector('.input-wrap');
      if (wrap) wrap.classList.toggle('invalid', Boolean(msg));
    },
  };
}

/** Choix Oui / Non (ou autre) — boutons à état pressé. */
export function Segmented(options, value, onChange, { small, label } = {}) {
  const root = h('div', { class: 'segmented' + (small ? ' small' : ''), role: 'group', 'aria-label': label });
  root.style.setProperty('--n', String(options.length));
  const render = (v) => {
    root.replaceChildren(...options.map((o) => h('button', {
      type: 'button', 'aria-pressed': String(o.value === v),
      onClick: () => { render(o.value); onChange(o.value); },
    }, o.label)));
  };
  render(value);
  return root;
}

export function Switch(checked, onChange, label) {
  const btn = h('button', { type: 'button', class: 'switch', role: 'switch', 'aria-checked': String(checked), 'aria-label': label });
  btn.addEventListener('click', () => {
    const next = btn.getAttribute('aria-checked') !== 'true';
    btn.setAttribute('aria-checked', String(next));
    onChange(next);
  });
  return btn;
}

/** Sélecteur des jours d'activité : nombre (0 à 7) + jours choisis, toujours synchronisés. */
export function ActivityDaysPicker(initial, onChange) {
  const SHORT = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim'];
  const LONG = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche'];
  let days = [...initial].sort((a, b) => a - b);
  const root = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '14px' } });
  const emit = () => onChange([...days]);

  const render = () => {
    const minus = h('button', { type: 'button', 'aria-label': 'Un jour de moins', disabled: days.length === 0 }, '−');
    const plus = h('button', { type: 'button', 'aria-label': 'Un jour de plus', disabled: days.length === 7 }, '+');
    minus.addEventListener('click', () => { days = days.slice(0, -1); render(); emit(); });
    plus.addEventListener('click', () => {
      const next = [0, 1, 2, 3, 4, 5, 6].find((d) => !days.includes(d));
      if (next !== undefined) days = [...days, next].sort((a, b) => a - b);
      render(); emit();
    });
    const count = days.length;
    const stepper = h('div', { class: 'stepper' }, minus,
      h('div', { class: 'value', 'aria-live': 'polite' }, h('strong', { class: 'num' }, String(count)),
        h('span', null, count > 1 ? 'jours par semaine' : 'jour par semaine')),
      plus);
    const chips = h('div', { class: 'days', role: 'group', 'aria-label': 'Jours d’activité' },
      SHORT.map((s, i) => h('button', {
        type: 'button', class: 'day-chip', 'aria-pressed': String(days.includes(i)), 'aria-label': LONG[i],
        onClick: () => {
          days = days.includes(i) ? days.filter((d) => d !== i) : [...days, i].sort((a, b) => a - b);
          render(); emit();
        },
      }, s)));
    root.replaceChildren(...[stepper, chips,
      count === 0 ? h('p', { class: 'field-hint' }, 'Aucun jour d’activité prévu : aucune cible journalière ne sera calculée.') : null].filter(Boolean));
  };
  render();
  return root;
}

// ---------------------------------------------------------------------------
// Feuille (bottom sheet), confirmation, toast
// ---------------------------------------------------------------------------
let sheetCount = 0;

export function openSheet({ title, build, onClose }) {
  const id = 'sheet-title-' + ++sheetCount;
  const previouslyFocused = document.activeElement;
  const backdrop = h('div', { class: 'sheet-backdrop' });
  const sheet = h('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': id, tabindex: '-1' });
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    if (onClose) onClose();
    backdrop.remove();
    sheet.remove();
    document.removeEventListener('keydown', onKey);
    if (!document.querySelector('.sheet')) document.body.style.overflow = '';
    if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus({ preventScroll: true });
  };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  backdrop.addEventListener('click', close);
  document.addEventListener('keydown', onKey);
  sheet.append(
    h('div', { class: 'sheet-grip', 'aria-hidden': 'true' }),
    h('div', { class: 'sheet-head' },
      h('h2', { class: 'sheet-title', id }, title),
      h('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Fermer', onClick: close }, icon('close', 20))),
  );
  const content = build(close);
  // Les éléments conditionnels (`cond && h(...)`) valent false/null : on ne les affiche pas.
  sheet.append(...[].concat(content).flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false && c !== ''));
  document.body.append(backdrop, sheet);
  document.body.style.overflow = 'hidden';
  setTimeout(() => { if (!sheet.contains(document.activeElement)) sheet.focus?.(); }, 0);
  return close;
}

export function confirmDialog({ title, message, confirmLabel = 'Confirmer', cancelLabel = 'Annuler', danger = false }) {
  return new Promise((resolve) => {
    let answer = false; // fermeture par le fond / Échap / croix = annulation
    openSheet({
      title,
      onClose: () => resolve(answer),
      build: (close) => [
        h('p', { class: 'muted', style: { lineHeight: '1.5' } }, message),
        h('div', { class: 'btn-stack' },
          Button(confirmLabel, () => { answer = true; close(); }, { variant: danger ? 'danger' : 'primary' }),
          Button(cancelLabel, () => close(), { variant: 'secondary' })),
      ],
    });
  });
}

let toastTimer = null;
export function toast(message) {
  document.querySelectorAll('.toast').forEach((t) => t.remove());
  const el = h('div', { class: 'toast', role: 'status', 'aria-live': 'polite' }, icon('check', 18), message);
  document.body.append(el);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), 2400);
}

// ---------------------------------------------------------------------------
// Graphique en ligne (SVG) — vraies données uniquement.
// ---------------------------------------------------------------------------
export function LineChart(points, { formatLabel, ariaLabel }) {
  const W = 340, H = 170, padL = 6, padR = 6, padT = 22, padB = 24;
  const max = Math.max(...points.map((p) => p.value));
  const n = points.length;
  const x = (i) => padL + (n === 1 ? (W - padL - padR) / 2 : (i * (W - padL - padR)) / (n - 1));
  const y = (v) => padT + (H - padT - padB) * (1 - (max ? v / max : 0));
  const line = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
  const area = `${line} L${x(n - 1).toFixed(1)},${H - padB} L${x(0).toFixed(1)},${H - padB} Z`;
  const idxs = n <= 7 ? points.map((_, i) => i) : [0, Math.round((n - 1) / 3), Math.round((2 * (n - 1)) / 3), n - 1];
  const maxIdx = points.findIndex((p) => p.value === max);
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', ariaLabel);
  const gid = 'g' + Math.random().toString(36).slice(2, 8);
  const esc = (v) => String(v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const labels = idxs.map((i) => {
    const anchor = i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle';
    return `<text class="chart-axis" x="${x(i).toFixed(1)}" y="${H - 6}" text-anchor="${anchor}">${esc(formatLabel(points[i]))}</text>`;
  }).join('');
  const maxLabelX = Math.min(W - padR, Math.max(padL, x(maxIdx)));
  const maxAnchor = maxIdx === 0 ? 'start' : maxIdx === n - 1 ? 'end' : 'middle';
  svg.innerHTML = `
    <defs><linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" style="stop-color:var(--teal)" stop-opacity="0.32"/><stop offset="100%" style="stop-color:var(--teal)" stop-opacity="0"/>
    </linearGradient></defs>
    <line x1="${padL}" y1="${H - padB}" x2="${W - padR}" y2="${H - padB}" stroke="rgba(255,255,255,0.08)"/>
    <line x1="${padL}" y1="${padT}" x2="${W - padR}" y2="${padT}" stroke="rgba(255,255,255,0.05)" stroke-dasharray="3 4"/>
    <path d="${area}" fill="url(#${gid})"/>
    <path d="${line}" fill="none" style="stroke:var(--teal)" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${x(maxIdx).toFixed(1)}" cy="${y(max).toFixed(1)}" r="4" style="fill:var(--teal)" stroke="#0F1720" stroke-width="2"/>
    <text class="chart-axis" x="${maxLabelX.toFixed(1)}" y="${(y(max) - 9).toFixed(1)}" text-anchor="${maxAnchor}" style="fill:#F5F7FA;font-weight:600">${esc(formatCents(max))}</text>
    ${labels}`;
  return h('div', { class: 'chart-wrap' }, svg);
}

export const fmt = {
  eur: (c) => formatCents(c),
  eur2: (c) => formatCents(c, { forceDecimals: true }),
  pct: (v, d) => formatPercent(v, d),
};
