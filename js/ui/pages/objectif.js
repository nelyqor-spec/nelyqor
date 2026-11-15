// Page 1 — Objectif (tableau de bord). Aucun calcul ici : tout vient du snapshot.
import { h } from '../dom.js';
import { TopBar, Card, ProgressBar, Row, Stat, Badge, Button, Notice, OperationRow, EmptyState, fmt } from '../components.js';
import { formatDayMonth, formatNumeric, formatShort, WEEKDAYS_SHORT } from '../../core/dates.js';
import { PROJECTION_STATUS_LABEL } from '../../core/calc.js';
import { getDailyQuote } from '../../content/quotes.js';
import { setRestDay, isPremium } from '../../core/store.js';
import { PremiumObjectifSection } from '../premium.js';

export function ObjectifPage(ctx) {
  const { state, snap, actions } = ctx;
  const s = state.settings;
  const prefs = state.preferences;

  // --- Header ----------------------------------------------------------------
  const header = h('header', { class: 'page-head', style: { gap: '10px' } },
    TopBar({ logoSize: 32 }),
    h('p', { class: 'today-line' }, formatDayMonth(snap.today)),
    prefs.dailyQuoteEnabled && h('p', { class: 'quote' }, getDailyQuote(snap.today, snap.todayIsActivity)));

  // --- Situations particulières ---------------------------------------------
  const notices = [];
  if (snap.goalReached) {
    notices.push(Notice('success', 'Objectif atteint, bravo !', `Tu as atteint ${fmt.eur2(snap.balance)} sur ${fmt.eur(snap.goal)}.`));
  } else if (snap.targetReached) {
    notices.push(h('div', { class: 'card' },
      Notice('info', 'Date cible atteinte', `Ta date cible (${formatNumeric(s.targetDate)}) est arrivée. Tu peux définir une nouvelle date ou un nouvel objectif.`),
      Button('Modifier mon objectif ou ma date', () => actions.openProfile('informations'), { variant: 'secondary', iconName: 'edit' })));
  }
  if (!snap.hasActivityPlan) {
    notices.push(h('div', { class: 'card' },
      Notice('warn', 'Aucun jour d’activité prévu', 'Aucune cible journalière n’est calculée tant qu’aucun jour n’est sélectionné.'),
      Button('Choisir mes jours d’activité', () => actions.openProfile('jours'), { variant: 'secondary' })));
  }

  // --- Carte objectif global -------------------------------------------------
  const globalCard = Card([
    h('div', { class: 'card-head' },
      h('div', { style: { minWidth: 0 } },
        h('p', { class: 'eyebrow' }, 'Objectif global'),
        h('p', { class: 'amount-xl', style: { marginTop: '6px' } }, fmt.eur(snap.balance)),
        h('p', { class: 'muted num' }, `sur ${fmt.eur(snap.goal)}`)),
      h('span', { class: 'pct-big' }, fmt.pct(snap.globalPct))),
    ProgressBar(snap.globalPct, { label: 'Progression globale' }),
    h('div', { class: 'grid-2' },
      Stat('Montant restant', fmt.eur(snap.remaining)),
      Stat('Date cible', formatNumeric(s.targetDate))),
  ], { cls: 'accent', label: 'Objectif global' });

  // --- Carte Aujourd'hui -----------------------------------------------------
  const todayStatus = snap.todayIsActivity
    ? Badge('Journée d’activité prévue', 'teal')
    : snap.todayMarkedRest ? Badge('Journée de repos (exceptionnelle)', 'warn') : Badge('Journée de repos');
  const todayRows = h('div', { class: 'rows' });
  if (snap.todayIsActivity && !snap.goalReached && snap.dailyGrossTarget > 0) {
    todayRows.append(Row('Objectif brut du jour', fmt.eur2(snap.dailyGrossTarget), { valueClass: 'teal' }));
    if (snap.rate > 0) todayRows.append(Row('Soit en net, après cotisations', fmt.eur2(snap.dailyNetTarget)));
  }
  if (snap.todayRealized > 0) todayRows.append(Row('Réalisé aujourd’hui · brut', fmt.eur2(snap.todayRealized)));
  const todayButtons = h('div', { class: 'btn-stack' },
    Button('Saisir mes revenus', () => actions.addOperation('revenue'), { iconName: 'plus' }));
  if (snap.todayPlanned && !snap.targetReached) {
    todayButtons.append(snap.todayMarkedRest
      ? Button('Finalement, je travaille aujourd’hui', () => setRestDay(snap.today, false), { variant: 'secondary' })
      : Button('Je ne travaille pas aujourd’hui', () => setRestDay(snap.today, true), { variant: 'secondary' }));
  }
  const todayCard = Card([
    h('div', { class: 'card-head' },
      h('div', null, h('p', { class: 'eyebrow' }, 'Aujourd’hui'), h('h2', { class: 'card-title', style: { marginTop: '4px' } }, formatDayMonth(snap.today))),
      todayStatus),
    todayRows.childNodes.length ? todayRows : null,
    todayButtons,
  ], { label: 'Aujourd’hui' });

  // --- Carte Objectif de la semaine ------------------------------------------
  const weekParts = [
    h('div', null,
      h('h2', { class: 'card-title' }, 'Objectif de la semaine'),
      h('p', { class: 'card-sub' }, 'Selon tes jours d’activité prévus cette semaine.')),
  ];
  if (snap.weekActivityDays === 0) {
    weekParts.push(
      h('p', { class: 'muted' }, snap.goalReached
        ? 'Ton objectif global est atteint.'
        : snap.targetReached ? 'La date cible est atteinte.'
          : snap.hasActivityPlan ? 'Plus aucun jour d’activité prévu d’ici dimanche. Ton nouvel objectif de la semaine sera calculé lundi.'
            : 'Aucun jour d’activité prévu.'),
      h('div', { class: 'rows' }, Row('Déjà réalisé · brut', fmt.eur2(snap.weeklyRealized))));
  } else {
    weekParts.push(
      h('div', { class: 'grid-2' },
        Stat('Objectif semaine · brut', fmt.eur2(snap.weeklyTarget)),
        Stat('Déjà réalisé · brut', fmt.eur2(snap.weeklyRealized), null, { valueClass: 'teal' }),
        Stat('Reste à faire · brut', fmt.eur2(snap.weeklyRemaining)),
        Stat('Progression', fmt.pct(snap.weeklyProgress))),
      h('div', { style: { display: 'flex', flexDirection: 'column', gap: '8px' } },
        h('div', { style: { display: 'flex', justifyContent: 'space-between', gap: '10px' } },
          h('span', { class: 'muted', style: { fontSize: '13px' } }, 'Progression de la semaine'),
          h('span', { class: 'num', style: { fontWeight: '700', color: 'var(--teal)' } }, fmt.pct(snap.weeklyProgress))),
        ProgressBar(snap.weeklyBarPct, { label: 'Progression de la semaine' })));
    if (snap.weeklyExceeded) {
      weekParts.push(Notice('success', 'Félicitations, objectif de la semaine dépassé !', `${'+' + fmt.eur(snap.weeklySurplus)} au-dessus de ton objectif`));
    } else if (snap.weeklyReached) {
      weekParts.push(Notice('success', 'Objectif de la semaine atteint.', null));
    }
    let perDay;
    if (snap.weeklyRemaining === 0) perDay = 'Objectif atteint';
    else if (snap.remainingWeekActivityDays === 0) perDay = 'Plus de jour d’activité prévu';
    else perDay = fmt.eur2(snap.remainingPerActivityDay);
    const adv = snap.weeklyAdvance;
    const advText = adv > 0 ? `En avance de ${fmt.eur2(adv)}` : adv < 0 ? `En retard de ${fmt.eur2(-adv)}` : 'Dans le rythme';
    weekParts.push(h('div', { class: 'rows' },
      Row('Jours d’activité réalisés / prévus', `${snap.weekDaysElapsed} / ${snap.weekActivityDays}`),
      Row('Jours d’activité restants', String(snap.remainingWeekActivityDays)),
      Row('À viser par jour restant', perDay, { valueClass: snap.weeklyRemaining > 0 && snap.remainingWeekActivityDays > 0 ? 'teal' : '' }),
      !snap.weeklyReached && Row('Rythme de la semaine', advText, { valueClass: adv >= 0 ? 'teal' : 'danger-text' })));
  }
  const weekCard = Card(weekParts, { label: 'Objectif de la semaine' });

  // --- Cotisations à mettre de côté (échéances mensuelles / trimestrielles) --
  let contribCard = null;
  const c = snap.contributions;
  if (c && c.toSetAside > 0) {
    const period = c.frequency === 'monthly' ? 'mois' : 'trimestre';
    contribCard = Card([
      h('div', { class: 'card-head' },
        h('div', null, h('p', { class: 'eyebrow' }, 'Cotisations'), h('h2', { class: 'card-title', style: { marginTop: '4px' } }, 'À garder de côté')),
        Badge(c.frequency === 'monthly' ? 'Mensuelles' : 'Trimestrielles')),
      h('p', { class: 'amount-lg' }, fmt.eur2(c.toSetAside)),
      h('div', { class: 'rows' },
        Row(`Prochaine échéance${c.next.closed ? ` (${period} terminé)` : ''}`, formatShort(c.next.due)),
        Row(c.next.closed ? `Montant du ${period} précédent` : `Cumul du ${period} en cours`, fmt.eur2(c.next.amount)),
        c.next.closed && c.current.amount > 0 && Row(`Déjà cumulé ce ${period}`, fmt.eur2(c.current.amount))),
      h('p', { class: 'field-hint' }, 'Cet argent n’est pas compté dans ton objectif : il est déjà déduit de tes revenus nets et de ta projection.'),
    ], { label: 'Cotisations à garder de côté' });
  }

  // --- En bref (Nelyqor+) -----------------------------------------------------
  const plannedNames = s.plannedActivityDays.map((d) => WEEKDAYS_SHORT[d]).join(', ');
  let projection;
  if (snap.targetReached) projection = h('p', { class: 'muted' }, 'La date cible est atteinte : la projection reprendra avec une nouvelle date.');
  else if (!snap.hasActivityPlan) projection = h('p', { class: 'muted' }, 'Aucun jour d’activité prévu : projection indisponible.');
  else if (snap.pace.status === 'none') projection = h('p', { class: 'muted' }, PROJECTION_STATUS_LABEL.none);
  else {
    projection = h('div', { style: { display: 'flex', flexDirection: 'column', gap: '6px' } },
      h('p', null, 'Au rythme actuel, tu atteindrais environ ', h('strong', { class: 'teal num' }, fmt.eur(snap.simpleProjection)), ' à ta date cible.'),
      snap.plannedUntilTarget > 0 && h('span', { class: 'field-hint' }, `Dépenses prévues déduites : ${fmt.eur(snap.plannedUntilTarget)}.`),
      h('span', { class: 'field-hint' }, PROJECTION_STATUS_LABEL[snap.pace.status]));
  }
  const briefCard = isPremium() && Card([
    h('div', { class: 'card-head' }, h('h2', { class: 'card-title' }, 'En bref'), Badge('Nelyqor+', 'blue')),
    h('div', { class: 'rows' },
      Row(`Jours d’activité restants avant le ${formatNumeric(s.targetDate)}`, String(snap.remainingActivityDays)),
      Row('Jours d’activité prévus', s.plannedActivityDays.length ? `${s.plannedActivityDays.length} · ${plannedNames}` : 'Aucun')),
    h('div', { style: { borderTop: '1px solid var(--border)', paddingTop: '12px', display: 'flex', flexDirection: 'column', gap: '6px' } },
      h('p', { class: 'eyebrow' }, 'Projection'), projection),
  ], { cls: 'premium', label: 'En bref' });

  // --- Mouvements récents ----------------------------------------------------
  const recent = [
    ...state.revenues.map((x) => ({ type: 'revenue', item: x })),
    ...state.transfers.map((x) => ({ type: 'transfer', item: x })),
    ...state.withdrawals.map((x) => ({ type: 'withdrawal', item: x })),
  ].sort((a, b) => (a.item.date === b.item.date ? (a.item.createdAt < b.item.createdAt ? 1 : -1) : a.item.date < b.item.date ? 1 : -1)).slice(0, 5);
  const recentCard = Card([
    h('h2', { class: 'card-title' }, 'Mouvements récents'),
    recent.length
      ? h('div', { class: 'op-list' }, recent.map((r) => OperationRow(r.type, r.item, actions.openDetail)))
      : EmptyState('Aucun mouvement pour le moment.', 'Saisir mes revenus', () => actions.addOperation('revenue'), 'calendar'),
  ]);

  return [header, ...notices, globalCard, todayCard, weekCard, contribCard, briefCard, PremiumObjectifSection(state, snap), recentCard];
}
