// Pages 2, 3, 4 — Revenus, Virements, Retraits.
import { h, icon } from '../dom.js';
import { PageHeader, Card, Stat, Button, Badge, Segmented, LineChart, OperationRow, EmptyState, fmt } from '../components.js';
import { revenueSeries, computePremium, upcomingPlanned, CHART_RANGES } from '../../core/calc.js';
import { WEEKDAYS_SHORT, weekday, formatDM, formatMonth } from '../../core/dates.js';
import { isPremium } from '../../core/store.js';
import { PremiumRevenueStats, PremiumTeaser, MicroCard, comparisonNote } from '../premium.js';
import { openCategorySheet, openCategoryForm, openPlanForm, PlanList, DueBanner } from '../expenses.js';

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
    MicroCard(state, snap, { onSetup: () => actions.openProfile('cotisations') }),
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
  const premium = isPremium();
  const parts = [
    PageHeader({ title: 'Retraits', subtitle: 'Enregistre tes dépenses, classe-les par dossier et anticipe les prochaines.' }),
  ];

  // Échéances arrivées (Nelyqor+) : à valider en un geste.
  if (premium) parts.push(DueBanner(upcomingPlanned(state, snap.today, 0).filter((o) => o.due)));

  parts.push(
    Button('Ajouter un retrait', () => actions.addOperation('withdrawal'), { iconName: 'plus' }),
    Card([
      h('p', { class: 'eyebrow' }, 'Total des retraits'),
      h('p', { class: 'amount-xl' }, n ? '−' + fmt.eur2(snap.withdrawalsTotal) : fmt.eur2(0)),
      h('p', { class: 'muted' }, n ? `${n} retrait${n > 1 ? 's' : ''} déduit${n > 1 ? 's' : ''} du montant atteint` : 'Chaque retrait est déduit du montant atteint.'),
    ], { label: 'Total des retraits' }),
  );

  if (premium) {
    // --- Dossiers ---------------------------------------------------------------
    const monthStart = snap.today.slice(0, 8) + '01';
    const folders = state.categories.map((c) => {
      const items = state.withdrawals.filter((w) => w.categoryId === c.id);
      return {
        c,
        month: items.filter((w) => w.date >= monthStart).reduce((a, w) => a + w.amount, 0),
        count: items.length,
        plans: state.plannedExpenses.filter((p) => p.categoryId === c.id).length,
      };
    });
    parts.push(Card([
      h('div', { class: 'card-head' }, h('h2', { class: 'card-title' }, 'Mes dossiers'), Badge('Nelyqor+', 'blue')),
      folders.length
        ? h('div', { class: 'folders' }, folders.map((f) => h('button', {
          type: 'button', class: 'folder', onClick: () => openCategorySheet(f.c.id, { onOpenDetail: actions.openDetail }),
          'aria-label': `Dossier ${f.c.name}, ${fmt.eur2(f.month)} ce mois`,
        },
        h('span', { class: 'folder-ico' }, icon('folder', 20)),
        h('span', { class: 'folder-name' }, f.c.name),
        h('span', { class: 'folder-amount' }, fmt.eur2(f.month)),
        h('span', { class: 'folder-meta' }, `ce mois · ${f.count} dépense${f.count > 1 ? 's' : ''}${f.plans ? ` · ${f.plans} prévue${f.plans > 1 ? 's' : ''}` : ''}`))))
        : h('p', { class: 'muted' }, 'Range tes dépenses dans des dossiers à ton nom : essence, matériel, assurance… autant que tu veux.'),
      Button('Nouveau dossier', () => openCategoryForm(), { variant: 'secondary', iconName: 'plus' }),
    ], { cls: 'premium', label: 'Mes dossiers' }));

    // --- Dépenses prévues ---------------------------------------------------------
    const plans = state.plannedExpenses;
    const next12 = upcomingPlanned(state, snap.today, 365).reduce((a, o) => a + o.amount, 0);
    parts.push(Card([
      h('div', { class: 'card-head' }, h('h2', { class: 'card-title' }, 'Dépenses prévues'), Badge('Nelyqor+', 'blue')),
      plans.length
        ? [h('p', { class: 'muted' }, 'Sur les 12 prochains mois : ', h('strong', { class: 'num', style: { color: 'var(--text)' } }, fmt.eur2(next12)), '. Déjà prises en compte dans ta projection.'), PlanList(plans)]
        : h('p', { class: 'muted' }, 'Planifie tes dépenses récurrentes (ex. 2 000 € tous les 3 mois) pour les anticiper : elles seront déduites de ta projection.'),
      Button('Planifier une dépense', () => openPlanForm(), { variant: 'secondary', iconName: 'calendar' }),
    ], { cls: 'premium', label: 'Dépenses prévues' }));
  } else {
    parts.push(PremiumTeaser('Classe tes dépenses dans des dossiers (essence, matériel…) et planifie tes dépenses récurrentes : tous les mois, 2, 3 ou 4 mois… Elles sont anticipées dans ta projection.'));
  }

  parts.push(Card([
    listHeader('Derniers retraits', 'withdrawal', n > 0, actions),
    recentList('withdrawal', state.withdrawals, actions, 'Aucun retrait pour le moment.', 'Ajouter un retrait'),
  ]));
  return parts;
}
