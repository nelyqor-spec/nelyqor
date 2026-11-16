// Nelyqor+ — offre, analyses avancées, simulateur « Et si… », export CSV.
import { h, icon } from './dom.js';
import { openSheet, Button, Card, Row, Stat, Notice, AmountInput, Field, toast, fmt, Badge } from './components.js';
import { computePremium, simulate, microStatus } from '../core/calc.js';
import { parseAmountToCents, formatRate } from '../core/money.js';
import { formatShort, formatNumeric, todayKey } from '../core/dates.js';
import * as store from '../core/store.js';

export const PRICE_LINE = '7 jours gratuits, puis 4,99\u00a0€/mois. Annulation à tout moment.';

export const FREE_FEATURES = [
  'Objectif, date cible et somme de départ',
  'Revenus, virements et retraits',
  'Calcul des cotisations',
  'Objectif du jour et de la semaine',
  'Progression globale',
  'Historique récent',
  'Échéances de cotisations à garder de côté',
  'Photo de profil, couleur de l’application',
  'Compte et synchronisation entre appareils',
  'Statistiques simples',
];

export const PLUS_FEATURES = [
  'En bref : jours restants et projection à la date cible',
  'Dossiers de dépenses (essence, matériel…) et dépenses récurrentes planifiées',
  'Micro-entreprise : alertes plafond de chiffre d’affaires et seuil de TVA',
  'Historique complet',
  'Statistiques avancées',
  'Comparaison semaine / semaine et mois / mois',
  'Analyse avance / retard',
  'Simulateur « Et si… » et scénarios',
  'Projections à 6, 12 et 18 mois et jusqu’à la date cible',
  'Export CSV de tes données',
];

export const checklist = (items) => h('ul', { class: 'checklist' }, items.map((t) => h('li', null, icon('check', 18), h('span', null, t))));

export function statusLabel(sub = store.getState().subscription) {
  if (sub.status === 'premium') return 'Nelyqor+';
  if (sub.status === 'trial') return 'Essai Nelyqor+';
  return 'Gratuit';
}

export function trialEndLabel(sub = store.getState().subscription) {
  return sub.trialEndsAt ? formatShort(todayKeyFromIso(sub.trialEndsAt)) : '';
}
function todayKeyFromIso(iso) {
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** Actions d'abonnement disponibles selon l'état (prototype : simulation locale). */
function subscriptionActions(close) {
  const sub = store.getState().subscription;
  const done = (msg) => { if (close) close(); toast(msg); };
  const actions = [];
  if (sub.status === 'free' && !sub.trialUsed) {
    actions.push(Button('Essayer Nelyqor+ gratuitement', () => { store.startTrial(); done('Essai Nelyqor+ activé'); }, { iconName: 'sparkle' }));
  }
  if (sub.status !== 'premium') {
    actions.push(Button(sub.status === 'free' && !sub.trialUsed ? 'S’abonner directement (simulation)' : 'Activer Nelyqor+ (simulation)',
      () => { store.activatePremium(); done('Nelyqor+ activé'); }, { variant: sub.status === 'free' && !sub.trialUsed ? 'secondary' : 'primary' }));
  }
  return actions;
}

export function openPaywall() {
  openSheet({
    title: 'Nelyqor+',
    build: (close) => {
      const sub = store.getState().subscription;
      return [
        h('div', { class: 'plan plus' },
          h('p', { class: 'plan-price' }, sub.trialUsed ? '4,99\u00a0€/mois. Annulation à tout moment.' : PRICE_LINE),
          h('p', { class: 'muted' }, 'Tout Nelyqor Gratuit, plus :'),
          checklist(PLUS_FEATURES)),
        sub.trialUsed && sub.status === 'free' && Notice('info', 'Ton essai gratuit est terminé.', 'Toutes tes données ont été conservées.'),
        h('div', { class: 'btn-stack' }, ...subscriptionActions(close), Button('Plus tard', close, { variant: 'secondary' })),
        h('p', { class: 'field-hint', style: { textAlign: 'center' } },
          'Prototype : l’abonnement est simulé sur cet appareil. Le paiement réel sera géré par l’App Store et Google Play.'),
      ];
    },
  });
}

/** Bloc d'abonnement de la page Profil. */
export function SubscriptionPanel() {
  const sub = store.getState().subscription;
  const parts = [];
  if (sub.status === 'free') {
    parts.push(Card([
      h('div', { class: 'card-head' }, h('h2', { class: 'card-title' }, 'Ton offre'), Badge('Gratuit')),
      h('p', { class: 'muted' }, sub.trialUsed ? 'Ton essai est terminé. Toutes tes données sont conservées.' : PRICE_LINE),
      checklist(PLUS_FEATURES),
      Button('Découvrir Nelyqor+', openPaywall, { iconName: 'sparkle' }),
    ]));
  } else {
    parts.push(Card([
      h('div', { class: 'card-head' }, h('h2', { class: 'card-title' }, sub.status === 'trial' ? 'Essai Nelyqor+' : 'Nelyqor+ actif'),
        Badge(sub.status === 'trial' ? 'Essai' : 'Actif', 'teal')),
      sub.status === 'trial' && h('p', { class: 'muted' }, `Ton essai gratuit se termine le ${trialEndLabel(sub)}. Sans abonnement, tu reviendras automatiquement sur Nelyqor Gratuit, sans perdre aucune donnée.`),
      sub.status === 'premium' && h('p', { class: 'muted' }, 'Toutes les fonctions Nelyqor+ sont débloquées.'),
      checklist(PLUS_FEATURES),
      h('div', { class: 'btn-stack' },
        ...subscriptionActions(null),
        Button('Exporter mes données (CSV)', () => exportCSV(), { variant: 'secondary', iconName: 'download' })),
    ]));
    parts.push(Card([
      h('h2', { class: 'card-title' }, 'Mode démonstration'),
      h('p', { class: 'muted' }, 'Pour tester l’application, tu peux revenir sur l’offre Gratuite. Tes données sont conservées.'),
      Button('Revenir à Nelyqor Gratuit (simulation)', () => { store.backToFree(); toast('Offre Gratuite activée'); }, { variant: 'secondary' }),
    ]));
  }
  return parts;
}

// ---------------------------------------------------------------------------
// Analyses Nelyqor+ sur la page Objectif
// ---------------------------------------------------------------------------
export function PremiumTeaser(text) {
  return Card([
    h('div', { class: 'card-head' }, h('h2', { class: 'card-title' }, 'Aller plus loin avec Nelyqor+'), Badge('Nelyqor+', 'blue')),
    h('p', { class: 'muted' }, text),
    Button('Découvrir Nelyqor+', openPaywall, { variant: 'blue', iconName: 'sparkle' }),
  ], { cls: 'premium' });
}

export function PremiumObjectifSection(state, snap) {
  if (!store.isPremium()) {
    return PremiumTeaser('En bref, analyse avance / retard, projections à 6, 12 et 18 mois et jusqu’à ta date cible, simulateur « Et si… ».');
  }
  const p = computePremium(state, snap.today, snap);
  const gapAbs = Math.abs(p.gap);
  const gapText = p.gap >= 0 ? `En avance de ${fmt.eur(gapAbs)}` : `En retard de ${fmt.eur(gapAbs)}`;
  const statusNote = { none: 'Basé sur le rythme nécessaire.', refining: 'Projection en cours d’affinage', provisional: 'Projection provisoire', personal: 'Projection basée sur ton rythme' }[snap.pace.status];

  return Card([
    h('div', { class: 'card-head' }, h('h2', { class: 'card-title' }, 'Analyse Nelyqor+'), Badge('Nelyqor+', 'blue')),
    h('div', { class: 'rows' },
      Row('Avance / retard', snap.hasActivityPlan ? gapText : '—', { valueClass: p.gap >= 0 ? 'teal' : 'danger-text' }),
      Row('Montant prévu à ce jour', fmt.eur(p.expected)),
      Row('Objectif atteint vers le', p.estimatedReachDate ? formatShort(p.estimatedReachDate) : 'Pas encore estimable')),
    h('p', { class: 'eyebrow' }, 'Projections'),
    h('div', { class: 'milestones' }, p.milestones.map((m) => Stat(
      m.key === 'target' ? `Date cible · ${formatNumeric(m.date)}` : `${m.label} · ${formatNumeric(m.date)}`,
      fmt.eur(m.amount),
      m.pct === null ? null : `${fmt.pct(m.pct, 0)} de l’objectif`,
    ))),
    h('p', { class: 'field-hint' }, statusNote),
    Button('Simulateur « Et si… »', () => openSimulator(snap), { variant: 'secondary', iconName: 'sliders' }),
  ], { cls: 'premium' });
}

/** Statistiques avancées (page Revenus). */
export function PremiumRevenueStats(state, snap) {
  if (!store.isPremium()) return PremiumTeaser('Statistiques avancées, comparaison semaine / mois et historique complet de tes revenus.');
  const p = computePremium(state, snap.today, snap);
  const s = p.stats;
  return Card([
    h('div', { class: 'card-head' }, h('h2', { class: 'card-title' }, 'Statistiques avancées'), Badge('Nelyqor+', 'blue')),
    h('div', { class: 'grid-2' },
      Stat('Total brut', fmt.eur(s.totalGross)),
      Stat('Total net', fmt.eur(s.totalNet)),
      Stat('Cotisations réservées', fmt.eur(s.totalContrib)),
      Stat('Moyenne par jour avec revenu', s.avgPerRevenueDay === null ? '—' : fmt.eur(s.avgPerRevenueDay)),
      Stat('Meilleur jour', s.bestDay ? fmt.eur(s.bestDay.amount) : '—', s.bestDay ? formatShort(s.bestDay.date) : null),
      Stat('Jours avec revenus', String(s.daysWithRevenue))),
  ], { cls: 'premium' });
}

export function comparisonNote(cmp, periodLabel) {
  if (cmp.deltaPct === null) return cmp.current > 0 ? `Pas de revenu ${periodLabel} à la même période` : null;
  const sign = cmp.deltaPct > 0 ? '+' : cmp.deltaPct < 0 ? '−' : '';
  return `${sign}${fmt.pct(Math.abs(cmp.deltaPct), 0)} vs ${periodLabel}`;
}

// ---------------------------------------------------------------------------
// Simulateur « Et si… »
// ---------------------------------------------------------------------------
export function openSimulator(snap) {
  openSheet({
    title: 'Et si…',
    build: () => {
      const state = store.getState();
      const s = state.settings;
      let days = s.plannedActivityDays.length || 1;
      const amount = AmountInput({ id: 'sim-amount', placeholder: snap.dailyGrossTarget ? String(Math.ceil(snap.dailyGrossTarget / 100)) : '100', big: true, label: 'Revenu brut par jour d’activité' });
      const field = Field('Si je gagnais, par jour d’activité (brut)', amount.wrap, { forId: 'sim-amount', hint: s.hasContributions ? `Cotisations de ${formatRate(s.contributionRate)} déduites automatiquement.` : null });
      const dayVal = h('strong', { class: 'num' });
      const minus = h('button', { type: 'button', 'aria-label': 'Un jour de moins' }, '−');
      const plus = h('button', { type: 'button', 'aria-label': 'Un jour de plus' }, '+');
      const daySpan = h('span');
      const stepper = h('div', { class: 'stepper' }, minus, h('div', { class: 'value', 'aria-live': 'polite' }, dayVal, daySpan), plus);
      const result = h('div', { class: 'sim-result', 'aria-live': 'polite' });

      const update = () => {
        dayVal.textContent = String(days);
        daySpan.textContent = days > 1 ? 'jours par semaine' : 'jour par semaine';
        minus.disabled = days <= 1; plus.disabled = days >= 7;
        const cents = parseAmountToCents(amount.input.value);
        if (!cents) {
          result.replaceChildren(h('p', { class: 'muted' }, 'Indique un montant pour voir le scénario.'));
          return;
        }
        const r = simulate(state, todayKey(), { dailyGross: cents, daysPerWeek: days });
        result.replaceChildren(h('div', { class: 'preview rows' },
          Row('Ajouté par semaine (net)', fmt.eur2(r.weeklyNet)),
          Row(`À ta date cible (${formatNumeric(s.targetDate)})`, fmt.eur(r.projectedAtTarget), { valueClass: r.reachesGoal ? 'teal' : '' }),
          Row('Part de l’objectif', fmt.pct(r.pctAtTarget, 0)),
          Row('Objectif atteint le', r.reachDate ? formatShort(r.reachDate) : 'Hors de portée')),
          r.reachesGoal
            ? Notice('success', 'Avec ce rythme, tu atteins ton objectif à temps.')
            : Notice('warn', 'Avec ce rythme, l’objectif ne serait pas atteint à la date cible.'));
      };
      minus.addEventListener('click', () => { days = Math.max(1, days - 1); update(); });
      plus.addEventListener('click', () => { days = Math.min(7, days + 1); update(); });
      amount.input.addEventListener('input', update);
      update();
      return [h('p', { class: 'muted' }, 'Teste un scénario sans rien modifier dans tes données.'), field.el, h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Jours d’activité par semaine'), stepper), result];
    },
  });
}

// ---------------------------------------------------------------------------
// Export CSV (Nelyqor+)
// ---------------------------------------------------------------------------
export function buildCSV(state) {
  const euros = (c) => (c / 100).toFixed(2).replace('.', ',');
  // Protection contre l'injection de formules (Excel / LibreOffice / Sheets) : un texte saisi par
  // l'utilisateur qui commence par = + - @ tabulation ou retour chariot est neutralisé par une apostrophe.
  const text = (v) => (/^[=+\-@\t\r]/.test(String(v ?? '')) ? "'" + v : String(v ?? ''));
  const esc = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const catName = (id) => (state.categories || []).find((c) => c.id === id)?.name || '';
  const lines = [['Type', 'Date', 'Libellé', 'Dossier', 'Brut (€)', 'Cotisations (€)', 'Net / Montant (€)'].map(esc).join(';')];
  const rows = [
    ...state.revenues.map((r) => [r.exceptional ? 'Revenu exceptionnel' : 'Revenu', r.date, text(r.label), '', euros(r.grossAmount), euros(r.contributionAmount), euros(r.netAmount)]),
    ...state.transfers.map((t) => ['Virement', t.date, text(t.label), '', '', '', euros(t.amount)]),
    ...state.withdrawals.map((w) => ['Retrait', w.date, text(w.label), text(catName(w.categoryId)), '', '', '-' + euros(w.amount)]),
  ].sort((a, b) => (a[1] < b[1] ? 1 : -1));
  rows.forEach((r) => lines.push(r.map(esc).join(';')));
  return '﻿' + lines.join('\r\n');
}

export async function exportCSV() {
  const csv = buildCSV(store.getState());
  // Dans un cadre intégré (aperçu web), les téléchargements sont bloqués : on copie le CSV.
  let embedded = false;
  try { embedded = window.self !== window.top; } catch { embedded = true; }
  if (embedded && navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(csv.replace(/^﻿/, ''));
      toast('CSV copié dans le presse-papiers');
      return;
    } catch { /* on tente le téléchargement */ }
  }
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = h('a', { href: url, download: `nelyqor-export-${todayKey()}.csv` });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  toast('Export CSV prêt');
}

// ---------------------------------------------------------------------------
// Micro-entreprise : plafond de CA et franchise de TVA (page Revenus)
// ---------------------------------------------------------------------------
export function MicroCard(state, snap, { onSetup } = {}) {
  if (!store.isPremium()) return null;
  const m = microStatus(state, snap.today, snap);
  if (!m) {
    if (state.settings.microActivity === null && onSetup) {
      return Card([
        h('div', { class: 'card-head' }, h('h2', { class: 'card-title' }, 'Micro-entrepreneur ?'), Badge('Nelyqor+', 'blue')),
        h('p', { class: 'muted' }, 'Suis ton chiffre d’affaires de l’année face au plafond micro et au seuil de TVA, avec une alerte avant de les dépasser.'),
        Button('Activer le suivi', onSetup, { variant: 'secondary' }),
      ], { cls: 'premium', label: 'Micro-entreprise' });
    }
    return null;
  }
  const bar = (value, max, label) => h('div', { class: 'micro-bar' },
    h('div', { class: 'micro-row' }, h('span', null, label), h('strong', { class: 'num' }, `${fmt.eur(value)} / ${fmt.eur(max)}`)),
    h('div', { class: 'progress' + (value >= max ? ' over' : value >= max * 0.8 ? ' near' : '') },
      h('span', { style: { width: Math.min(100, (value / max) * 100) + '%' } })));
  const alerts = [];
  if (m.tvaLevel === 'over') alerts.push(Notice('danger', 'Seuil de TVA dépassé', `Au-delà de ${fmt.eur(m.tvaMajore)}, tu dois facturer la TVA dès le 1er jour du mois de dépassement. Renseigne-toi auprès de ton service des impôts.`));
  else if (m.revenue > m.tva) alerts.push(Notice('warn', 'Seuil de TVA franchi', `Tu as dépassé ${fmt.eur(m.tva)} : tu deviendras redevable de la TVA au 1er janvier (ou dès ${fmt.eur(m.tvaMajore)}).`));
  else if (m.tvaLevel === 'near') alerts.push(Notice('warn', 'Tu approches du seuil de TVA', `Plus que ${fmt.eur(m.tva - m.revenue)} avant ${fmt.eur(m.tva)}.`));
  else if (m.tvaProjectedOver) alerts.push(Notice('info', 'À ce rythme, tu dépasserais le seuil de TVA cette année', `Projection fin d’année : ${fmt.eur(m.projectedYear)}.`));
  if (m.plafondLevel === 'over') alerts.push(Notice('danger', 'Plafond micro-entreprise dépassé', 'Un dépassement deux années de suite fait sortir du régime micro. Fais le point avec un expert-comptable.'));
  else if (m.plafondLevel === 'near' || m.plafondProjectedOver) alerts.push(Notice('warn', 'Attention au plafond micro-entreprise', `Projection fin d’année : ${fmt.eur(m.projectedYear)} pour un plafond de ${fmt.eur(m.plafond)}.`));
  return Card([
    h('div', { class: 'card-head' }, h('h2', { class: 'card-title' }, `Chiffre d’affaires ${snap.today.slice(0, 4)}`), Badge('Nelyqor+', 'blue')),
    h('p', { class: 'muted', style: { fontSize: '13.5px' } }, m.label),
    bar(m.revenue, m.tva, 'Franchise de TVA'),
    bar(m.revenue, m.plafond, 'Plafond micro-entreprise'),
    h('div', { class: 'rows' }, Row('Projection fin d’année (à ton rythme)', fmt.eur(m.projectedYear))),
    ...alerts,
    h('p', { class: 'field-hint' }, `Seuils ${m.year} (encaissements bruts de l’année civile). Ils sont réduits au prorata la 1re année d’activité. À confirmer sur urssaf.fr / impots.gouv.fr.`),
  ], { cls: 'premium', label: 'Micro-entreprise' });
}
