// Pages 2, 3, 4 — Revenus, Virements, Retraits.
import { h } from '../dom.js';
import { PageHeader, Card, Stat, Button, Segmented, LineChart, OperationRow, EmptyState, fmt } from '../components.js';
import { revenueSeries, computePremium, CHART_RANGES } from '../../core/calc.js';
import { WEEKDAYS_SHORT, weekday, formatDM, formatMonth } from '../../core/dates.js';
import { isPremium } from '../../core/store.js';
import { PremiumRevenueStats, comparisonNote } from '../premium.js';

const RECENT_COUNT = 10;
let chartRange = 'M'; // conservé pendant la session

function recentList(type, items, actions, emptyText, emptyButton) {
  if (!items.length) return EmptyState(emptyText, emptyButton, () => actions.addOperation(type), type === 'revenue' ? 'revenue' : type);
  return h('div', { class: 'op-list' }, items.slice(0, RECENT_COUNT).map((it) => OperationRow(type, it, actions.openDetail)));
}

function listHeader(title, type, hasItems, actions) {
  return h('div', { class: 'section-title' },
    h('h2', { class: 'card-title' }, title),
    hasItems && h('button', { type: 'button', class: 'btn-link', onClick: () => actions.openHistory(type) }, 'Tout voir'));
}

// ---------------------------------------------------------------------------
export function RevenusPage({ state, snap, actions }) {
  const premium = isPremium();
  const cmp = premium ? computePremium(state, snap.today, snap).comparison : null;

  const summary = h('div', { class: 'grid-2' },
    Card([
      h('p', { class: 'stat-label' }, 'Cette semaine'),
      h('p', { class: 'amount-md' }, fmt.eur2(snap.weekRevenueGross)),
      h('p', { class: 'stat-note' }, `brut · ${fmt.eur2(snap.weekRevenueNet)} net`),
      cmp && comparisonNote(cmp.week, 'semaine préc.') && h('p', { class: 'stat-note teal' }, comparisonNote(cmp.week, 'semaine préc.')),
    ], { cls: '', label: 'Cette semaine' }),
    Card([
      h('p', { class: 'stat-label' }, 'Ce mois'),
      h('p', { class: 'amount-md' }, fmt.eur2(snap.monthRevenueGross)),
      h('p', { class: 'stat-note' }, `brut · ${fmt.eur2(snap.monthRevenueNet)} net`),
      cmp && comparisonNote(cmp.month, 'mois préc.') && h('p', { class: 'stat-note teal' }, comparisonNote(cmp.month, 'mois préc.')),
    ], { label: 'Ce mois' }));

  // Graphique
  const chartBody = h('div');
  const renderChart = () => {
    const series = revenueSeries(state, snap.today, chartRange);
    const total = series.reduce((a, b) => a + b.value, 0);
    if (total === 0) {
      chartBody.replaceChildren(EmptyState('Aucun revenu sur cette période.', null, null, 'chart'));
      return;
    }
    const label = (p) => p.unit === 'month' ? formatMonth(p.from)
      : chartRange === 'S' ? WEEKDAYS_SHORT[weekday(p.from)] : formatDM(p.from);
    chartBody.replaceChildren(
      h('p', { class: 'muted', style: { fontSize: '13px' } }, `Total sur la période : `, h('strong', { class: 'num', style: { color: 'var(--text)' } }, fmt.eur2(total)), ' brut'),
      LineChart(series, { formatLabel: label, ariaLabel: `Évolution des revenus bruts, total ${fmt.eur2(total)}` }));
  };
  const rangeLabels = { S: 'S', M: 'M', '6M': '6M', '1A': '1A' };
  const chartCard = Card([
    h('div', { class: 'card-head' }, h('h2', { class: 'card-title' }, 'Évolution des revenus')),
    Segmented(CHART_RANGES.map((r) => ({ value: r, label: rangeLabels[r] })), chartRange, (v) => { chartRange = v; renderChart(); }, { small: true, label: 'Période : S = 7 jours, M = 30 jours, 6M = 6 mois, 1A = 1 an' }),
    chartBody,
  ]);
  renderChart();

  const listCard = Card([
    listHeader('Derniers revenus', 'revenue', state.revenues.length > 0, actions),
    recentList('revenue', state.revenues, actions, 'Aucun revenu enregistré pour le moment.', 'Ajouter mon premier revenu'),
  ]);

  return [
    PageHeader({ title: 'Revenus', subtitle: 'Enregistre tes revenus et suis leur évolution.' }),
    Button('Ajouter un revenu', () => actions.addOperation('revenue'), { iconName: 'plus' }),
    summary,
    chartCard,
    PremiumRevenueStats(state, snap),
    listCard,
  ];
}

// ---------------------------------------------------------------------------
export function VirementsPage({ state, snap, actions }) {
  const n = state.transfers.length;
  return [
    PageHeader({ title: 'Virements', subtitle: 'Enregistre les sommes ajoutées directement à ton objectif.' }),
    Button('Ajouter un virement', () => actions.addOperation('transfer'), { iconName: 'plus' }),
    Card([
      h('p', { class: 'eyebrow' }, 'Total des virements'),
      h('p', { class: 'amount-xl' }, fmt.eur2(snap.transfersTotal)),
      h('p', { class: 'muted' }, n ? `${n} virement${n > 1 ? 's' : ''} · 100 % ajouté à ton objectif, sans cotisations` : '100 % de chaque virement est ajouté à ton objectif, sans cotisations.'),
    ], { cls: 'accent', label: 'Total des virements' }),
    Card([
      listHeader('Derniers virements', 'transfer', n > 0, actions),
      recentList('transfer', state.transfers, actions, 'Aucun virement pour le moment.', 'Ajouter un virement'),
    ]),
  ];
}

// ---------------------------------------------------------------------------
export function RetraitsPage({ state, snap, actions }) {
  const n = state.withdrawals.length;
  return [
    PageHeader({ title: 'Retraits', subtitle: 'Enregistre les retraits effectués sur ton objectif.' }),
    Button('Ajouter un retrait', () => actions.addOperation('withdrawal'), { iconName: 'plus' }),
    Card([
      h('p', { class: 'eyebrow' }, 'Total des retraits'),
      h('p', { class: 'amount-xl' }, n ? '−' + fmt.eur2(snap.withdrawalsTotal) : fmt.eur2(0)),
      h('p', { class: 'muted' }, n ? `${n} retrait${n > 1 ? 's' : ''} déduit${n > 1 ? 's' : ''} du montant atteint` : 'Chaque retrait est déduit du montant atteint.'),
    ], { label: 'Total des retraits' }),
    Card([
      listHeader('Derniers retraits', 'withdrawal', n > 0, actions),
      recentList('withdrawal', state.withdrawals, actions, 'Aucun retrait pour le moment.', 'Ajouter un retrait'),
    ]),
  ];
}
