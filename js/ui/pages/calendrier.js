// Page 6 — Calendrier : modifier ses jours (travail en plus / repos) et voir tout se recalculer,
// projection comprise (Nelyqor+).
import { h } from '../dom.js';
import { PageHeader, Card, Stat, Button, Badge, Row, fmt } from '../components.js';
import { WEEKDAYS, WEEKDAYS_SHORT, formatNumeric, formatShort } from '../../core/dates.js';
import { computePremium, PROJECTION_STATUS_LABEL } from '../../core/calc.js';
import { percentOf } from '../../core/money.js';
import { isPremium } from '../../core/store.js';
import { CalendarView } from '../calendar.js';
import { PremiumTeaser } from '../premium.js';

// Dernière projection affichée : sert à montrer de combien elle bouge après un changement de jour.
let lastProjection = null;

function ProjectionCard(state, snap) {
  if (!isPremium()) {
    lastProjection = null;
    return PremiumTeaser('Vois ta projection bouger en direct : chaque jour ajouté ou retiré dans le calendrier met à jour le montant prévu à ta date cible.');
  }
  const head = h('div', { class: 'card-head' }, h('h2', { class: 'card-title' }, 'Projection'), Badge('Nelyqor+', 'blue'));
  let body;
  if (snap.targetReached) {
    body = h('p', { class: 'muted' }, 'La date cible est atteinte : définis une nouvelle date pour relancer la projection.');
    lastProjection = null;
  } else if (!snap.hasActivityPlan && snap.remainingActivityDays === 0) {
    body = h('p', { class: 'muted' }, 'Aucun jour d’activité prévu : ajoute des jours pour voir ta projection.');
    lastProjection = null;
  } else if (snap.pace.status === 'none') {
    body = h('p', { class: 'muted' }, PROJECTION_STATUS_LABEL.none);
    lastProjection = null;
  } else {
    const p = computePremium(state, snap.today, snap);
    const proj = snap.simpleProjection;
    const pct = percentOf(proj, snap.goal);
    const delta = lastProjection === null ? 0 : proj - lastProjection;
    lastProjection = proj;
    body = [
      h('p', { class: 'muted' }, `Au rythme actuel, à ta date cible (${formatNumeric(snap.target)}) :`),
      h('div', { style: { display: 'flex', alignItems: 'baseline', gap: '10px', flexWrap: 'wrap' } },
        h('span', { class: 'amount-lg', style: { color: 'var(--teal)' } }, fmt.eur(proj)),
        delta !== 0 && Badge(`${delta > 0 ? '▲ +' : '▼ −'}${fmt.eur(Math.abs(delta))} avec ce changement`, delta > 0 ? 'teal' : 'warn')),
      h('div', { class: 'rows' },
        Row('Part de ton objectif', fmt.pct(pct, 0), { valueClass: pct >= 100 ? 'teal' : '' }),
        Row('Objectif atteint vers le', p.estimatedReachDate ? formatShort(p.estimatedReachDate) : 'Pas encore estimable'),
        snap.plannedUntilTarget > 0 && Row('Dépenses prévues déduites', fmt.eur(snap.plannedUntilTarget))),
      h('p', { class: 'field-hint' }, PROJECTION_STATUS_LABEL[snap.pace.status]),
    ];
  }
  return Card([head, body], { cls: 'premium', label: 'Projection' });
}

/** Ce que l'utilisateur gagne en moyenne selon le jour de la semaine (base de la projection). */
function WeekdayCard(snap) {
  if (!isPremium() || snap.pace.status === 'none') return null;
  const avg = snap.pace.observedNetPerActivityDay || 0;
  const vals = snap.pace.weekdayObservedNet;
  const max = Math.max(1, ...vals.filter((v) => v !== null));
  const rows = WEEKDAYS.map((name, w) => {
    const v = vals[w];
    const diff = v === null ? 0 : v - avg;
    const tag = v === null ? null : Math.abs(diff) >= 500
      ? h('small', { style: { color: diff > 0 ? 'var(--teal)' : 'var(--warn)', fontWeight: '700', marginLeft: '6px' } }, `${diff > 0 ? '+' : '−'}${fmt.eur(Math.abs(diff))}`)
      : null;
    return h('div', { class: 'wk-row' },
      h('span', { class: 'wk-day' }, name),
      h('span', { class: 'wk-bar' }, h('i', { style: { width: v === null ? '0%' : `${Math.max(4, (v / max) * 100)}%` } })),
      h('span', { class: 'wk-val num' }, v === null ? '—' : fmt.eur(v), tag));
  });
  return Card([
    h('div', { class: 'card-head' }, h('h2', { class: 'card-title' }, 'Ton rythme selon le jour'), Badge('Nelyqor+', 'blue')),
    h('p', { class: 'muted', style: { fontSize: '14px' } }, `Moyenne nette de tes ${8} derniers jours de chaque type. La projection s’en sert : un jour qui rapporte plus compte plus.`),
    h('div', { class: 'wk' }, rows),
    h('p', { class: 'field-hint' }, `Moyenne générale : ${fmt.eur(avg)} par jour d’activité. Un jour sans historique (ex. un samedi exceptionnel) compte pour cette moyenne.`),
  ], { cls: 'premium', label: 'Ton rythme selon le jour' });
}

export function CalendrierPage({ state, snap, actions }) {
  const s = state.settings;
  const planned = s.plannedActivityDays.length
    ? `${s.plannedActivityDays.length} jour${s.plannedActivityDays.length > 1 ? 's' : ''} : ${s.plannedActivityDays.map((d) => WEEKDAYS_SHORT[d]).join(', ')}`
    : 'Aucun jour prévu';

  return [
    PageHeader({ title: 'Calendrier', subtitle: 'Un jour en plus ou un jour de repos ? Appuie dessus : tout se recalcule.' }),
    Card([
      h('h2', { class: 'card-title' }, 'Cette semaine'),
      h('div', { class: 'grid-2' },
        Stat('Jours d’activité', String(snap.weekActivityDays)),
        Stat('Objectif de la semaine · brut', fmt.eur2(snap.weeklyTarget)),
        Stat('Objectif par jour · brut', snap.dailyGrossTarget ? fmt.eur2(snap.dailyGrossTarget) : '—', null, { valueClass: 'teal' }),
        Stat(`Jours restants jusqu’au ${formatNumeric(s.targetDate)}`, String(snap.remainingActivityDays))),
    ], { cls: 'accent', label: 'Cette semaine' }),
    ProjectionCard(state, snap),
    Card([CalendarView()], { label: 'Calendrier du mois' }),
    WeekdayCard(snap),
    Card([
      h('h2', { class: 'card-title' }, 'Mon planning habituel'),
      h('p', { class: 'muted' }, planned),
      Button('Modifier mes jours habituels', () => actions.openProfile('jours'), { variant: 'secondary', iconName: 'edit' }),
    ], { label: 'Mon planning habituel' }),
  ];
}
