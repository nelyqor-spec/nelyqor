// Nelyqor — calendrier mensuel des jours d'activité.
// Les jours du planning habituel s'affichent automatiquement. Un appui sur un jour crée une
// exception pour CE jour uniquement : travail exceptionnel (ex. un dimanche) ou repos exceptionnel.
// Objectif du jour, de la semaine et projection sont recalculés aussitôt.
import { h, icon } from './dom.js';
import { openSheet, Button, toast } from './components.js';
import { todayKey, addDays, addMonths, startOfMonth, endOfMonth, weekday, isValidKey } from '../core/dates.js';
import { dayOverridesOf, isActivityDay, countActivityDays } from '../core/calc.js';
import * as store from '../core/store.js';

const MONTH = new Intl.DateTimeFormat('fr-FR', { month: 'long', year: 'numeric', timeZone: 'UTC' });
const DAY_LONG = new Intl.DateTimeFormat('fr-FR', { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC' });
const monthLabel = (k) => { const s = MONTH.format(Date.parse(k + 'T12:00:00Z')); return s.charAt(0).toUpperCase() + s.slice(1); };
const dayLabel = (k) => DAY_LONG.format(Date.parse(k + 'T12:00:00Z'));

/** Calendrier autonome (utilisé dans une feuille ou dans le Profil). */
export function CalendarView({ initialMonth } = {}) {
  let month = startOfMonth(initialMonth && isValidKey(initialMonth) ? initialMonth : todayKey());
  const root = h('div', { class: 'cal' });

  const render = () => {
    const st = store.getState();
    const s = st.settings;
    const ov = dayOverridesOf(st);
    const today = todayKey();
    const first = month;
    const last = endOfMonth(month);
    const revenueDays = new Set(st.revenues.filter((r) => r.date >= first && r.date <= last).map((r) => r.date));

    const prev = h('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Mois précédent' }, icon('back', 20));
    const next = h('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Mois suivant' }, icon('chevron', 20));
    prev.addEventListener('click', () => { month = addMonths(month, -1); render(); });
    next.addEventListener('click', () => { month = addMonths(month, 1); render(); });

    const cells = [];
    for (let i = 0; i < weekday(first); i++) cells.push(h('span', { class: 'cal-pad', 'aria-hidden': 'true' }));
    for (let k = first; k <= last; k = addDays(k, 1)) {
      const planned = s.plannedActivityDays.includes(weekday(k));
      const o = ov[k];
      const worked = isActivityDay(s, ov, k);
      const cls = ['cal-day', worked ? 'work' : 'free'];
      if (o === 'work' && !planned) cls.push('extra');
      if ((o === 'off' || o === true) && planned) cls.push('rest');
      if (k === today) cls.push('today');
      if (k < today) cls.push('past');
      if (k > s.targetDate) cls.push('after');
      const state = cls.includes('extra') ? 'travail exceptionnel' : cls.includes('rest') ? 'repos exceptionnel' : worked ? 'jour travaillé' : 'jour libre';
      const btn = h('button', {
        type: 'button', class: cls.join(' '), 'aria-pressed': String(worked),
        'aria-label': `${dayLabel(k)} : ${state}${revenueDays.has(k) ? ', revenu enregistré' : ''}. Appuie pour changer.`,
      }, h('span', { class: 'cal-num' }, String(Number(k.slice(8)))), revenueDays.has(k) && h('span', { class: 'cal-dot', 'aria-hidden': 'true' }));
      btn.addEventListener('click', () => {
        store.setDayWorked(k, !worked);
        const nowWorked = !worked;
        toast(nowWorked === planned
          ? `${dayLabel(k)} : retour au planning habituel`
          : nowWorked ? `${dayLabel(k)} : travail exceptionnel ajouté` : `${dayLabel(k)} : repos exceptionnel`);
        render();
      });
      cells.push(btn);
    }

    const worked = countActivityDays(s, ov, first, last);
    let extra = 0;
    let rest = 0;
    for (const [k, o] of Object.entries(ov)) {
      if (k < first || k > last) continue;
      const planned = s.plannedActivityDays.includes(weekday(k));
      if (o === 'work' && !planned) extra++;
      else if ((o === 'off' || o === true) && planned) rest++;
    }

    root.replaceChildren(
      h('div', { class: 'cal-head' }, prev, h('h3', { class: 'cal-title', 'aria-live': 'polite' }, monthLabel(month)), next),
      h('div', { class: 'cal-grid cal-wd', 'aria-hidden': 'true' }, ['L', 'M', 'M', 'J', 'V', 'S', 'D'].map((d) => h('span', null, d))),
      h('div', { class: 'cal-grid', role: 'group', 'aria-label': `Jours de ${monthLabel(month)}` }, cells),
      h('p', { class: 'cal-sum' },
        h('strong', null, `${worked} jour${worked > 1 ? 's' : ''} d’activité`),
        extra ? ` · +${extra} exceptionnel${extra > 1 ? 's' : ''}` : '',
        rest ? ` · −${rest} repos` : ''),
      h('div', { class: 'cal-legend' },
        h('span', null, h('i', { class: 'lg work' }), 'Travaillé'),
        h('span', null, h('i', { class: 'lg extra' }), 'Exceptionnel'),
        h('span', null, h('i', { class: 'lg rest' }), 'Repos'),
        h('span', null, h('i', { class: 'lg dot' }), 'Revenu')),
    );
  };
  render();
  return root;
}

export function openCalendar() {
  openSheet({
    title: 'Mon calendrier',
    build: (close) => [
      h('p', { class: 'muted', style: { fontSize: '14px' } },
        'Tes jours habituels sont déjà cochés. Appuie sur un jour pour l’ajouter exceptionnellement (ex. un dimanche) ou le passer en repos : ça ne change que ce jour-là, et ta projection est recalculée.'),
      CalendarView(),
      Button('Terminé', close, { variant: 'secondary' }),
    ],
  });
}
