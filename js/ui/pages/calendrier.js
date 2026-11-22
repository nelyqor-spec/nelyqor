// Page 6 — Calendrier : modifier ses jours (travail en plus / repos) et voir tout se recalculer.
import { h } from '../dom.js';
import { PageHeader, Card, Stat, Button, fmt } from '../components.js';
import { WEEKDAYS_SHORT, formatNumeric } from '../../core/dates.js';
import { CalendarView } from '../calendar.js';

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
    Card([CalendarView()], { label: 'Calendrier du mois' }),
    Card([
      h('h2', { class: 'card-title' }, 'Mon planning habituel'),
      h('p', { class: 'muted' }, planned),
      Button('Modifier mes jours habituels', () => actions.openProfile('jours'), { variant: 'secondary', iconName: 'edit' }),
    ], { label: 'Mon planning habituel' }),
  ];
}
