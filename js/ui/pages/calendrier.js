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

/** Moyenne de tous les revenus saisis, jour par jour : meilleurs et moins bons jours (base de la projection). */
function WeekdayCard(snap) {
  if (!isPremium() || snap.pace.status === 'none') return null;
  const vals = snap.pace.weekdayObservedGross;
  const nets = snap.pace.weekdayObservedNet;
  const counts = snap.pace.weekdaySamples;
  const known = vals.map((v, w) => ({ v, w })).filter((x) => x.v !== null);
  const sorted = [...known].sort((a, b) => b.v - a.v);
  const best = sorted.length >= 2 ? sorted[0].w : -1;
  const worst = sorted.length >= 2 ? sorted[sorted.length - 1].w : -1;
  const max = Math.max(1, ...known.map((x) => x.v));
  const totalDays = counts.reduce((a, b) => a + b, 0);
  const rows = WEEKDAYS.map((name, w) => {
    const v = vals[w];
    const mark = w === best ? h('span', { class: 'wk-tag best' }, 'Meilleur') : w === worst ? h('span', { class: 'wk-tag worst' }, 'Moins bon') : null;
    return h('div', { class: 'wk-row' + (w === best ? ' is-best' : '') },
      h('span', { class: 'wk-day' }, name, h('small', null, counts[w] ? `${counts[w]} jour${counts[w] > 1 ? 's' : ''}` : 'aucun revenu')),
      h('span', { class: 'wk-bar' }, h('i', { style: { width: v === null ? '0%' : `${Math.max(4, (v / max) * 100)}%` } })),
      h('span', { class: 'wk-val num' }, v === null ? '—' : fmt.eur2(v), v !== null && h('small', null, `net ${fmt.eur2(nets[w])}`), mark));
  });
  return Card([
    h('div', { class: 'card-head' }, h('h2', { class: 'card-title' }, 'Ton rythme selon le jour'), Badge('Nelyqor+', 'blue')),
    h('p', { class: 'muted', style: { fontSize: '14px' } }, `Moyenne de tous tes revenus saisis, jour par jour (${totalDays} jour${totalDays > 1 ? 's' : ''} avec revenu). La projection s’en sert : un jour qui rapporte plus compte plus.`),
    best >= 0 && h('div', { class: 'wk-podium' },
      h('div', null, h('span', null, 'Meilleur jour'), h('strong', { class: 'teal' }, WEEKDAYS[best]), h('em', null, fmt.eur2(vals[best]))),
      h('div', null, h('span', null, 'Moins bon jour'), h('strong', null, WEEKDAYS[worst]), h('em', null, fmt.eur2(vals[worst])))),
    h('div', { class: 'wk' }, rows),
    h('p', { class: 'field-hint' }, `Moyenne générale : ${fmt.eur2(snap.pace.avgGrossPerRevenueDay)} brut par jour avec revenu (hors revenus exceptionnels). Un jour jamais travaillé compte pour cette moyenne dans la projection.`),
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
