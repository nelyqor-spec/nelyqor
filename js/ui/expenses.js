// Nelyqor+ — dossiers de dépenses et dépenses prévues (récurrentes ou ponctuelles).
import { h, icon } from './dom.js';
import {
  openSheet, Button, AmountInput, DateInput, TextInput, Field, Row, Notice, OperationRow, EmptyState, Segmented,
  confirmDialog, toast, fmt,
} from './components.js';
import { parseAmountToCents, centsToInput } from '../core/money.js';
import { todayKey, isValidKey, formatShort, startOfMonth } from '../core/dates.js';
import { pendingOccurrences } from '../core/calc.js';
import * as store from '../core/store.js';

export const FREQUENCY_LABELS = {
  0: 'Une seule fois', 1: 'Tous les mois', 2: 'Tous les 2 mois', 3: 'Tous les 3 mois',
  4: 'Tous les 4 mois', 6: 'Tous les 6 mois', 12: 'Tous les ans',
};

const categoryName = (catId) => {
  const c = store.getState().categories.find((x) => x.id === catId);
  return c ? c.name : null;
};

// ---------------------------------------------------------------------------
// Sélecteur de dossier (pastilles) avec création à la volée
// ---------------------------------------------------------------------------
export function CategoryPicker(initial, onChange) {
  let current = initial || null;
  const root = h('div', { class: 'chips', role: 'group', 'aria-label': 'Dossier' });
  const render = () => {
    const cats = store.getState().categories;
    const chip = (value, label) => h('button', {
      type: 'button', class: 'chip', 'aria-pressed': String(current === value),
      onClick: () => { current = value; render(); onChange(value); },
    }, label);
    root.replaceChildren(
      chip(null, 'Aucun'),
      ...cats.map((c) => chip(c.id, c.name)),
      h('button', {
        type: 'button', class: 'chip add',
        onClick: () => openCategoryForm({ onSaved: (cat) => { current = cat.id; render(); onChange(cat.id); } }),
      }, icon('plus', 16), 'Nouveau dossier'),
    );
  };
  render();
  return root;
}

// ---------------------------------------------------------------------------
// Création / renommage d'un dossier
// ---------------------------------------------------------------------------
export function openCategoryForm({ category = null, onSaved } = {}) {
  openSheet({
    title: category ? 'Renommer le dossier' : 'Nouveau dossier',
    build: (close) => {
      const name = TextInput({ id: 'cat-name', value: category ? category.name : '', placeholder: 'Ex. Essence, Matériel, Assurance…', maxlength: store.CATEGORY_NAME_MAX, label: 'Nom du dossier' });
      const field = Field('Nom du dossier', name.wrap, { forId: 'cat-name', hint: 'Choisis le nom que tu veux. Tu peux créer autant de dossiers que nécessaire.' });
      setTimeout(() => name.input.focus({ preventScroll: true }), 250);
      const submit = () => {
        const v = name.input.value.trim();
        if (!v) return field.setError('Donne un nom à ton dossier.');
        if (store.getState().categories.some((c) => c.name.toLowerCase() === v.toLowerCase() && (!category || c.id !== category.id))) {
          return field.setError('Un dossier porte déjà ce nom.');
        }
        let saved;
        if (category) { store.renameCategory(category.id, v); saved = { ...category, name: v }; } else saved = store.addCategory(v);
        close();
        toast(category ? 'Dossier renommé' : 'Dossier créé');
        if (onSaved && saved) onSaved(saved);
      };
      return h('form', { novalidate: true, class: 'form-stack', onSubmit: (e) => { e.preventDefault(); submit(); } },
        field.el, Button(category ? 'Enregistrer' : 'Créer le dossier', null, { type: 'submit' }));
    },
  });
}

// ---------------------------------------------------------------------------
// Détail d'un dossier : dépenses, dépenses prévues, renommer, supprimer
// ---------------------------------------------------------------------------
export function openCategorySheet(categoryId, { onOpenDetail } = {}) {
  const cat = store.getState().categories.find((c) => c.id === categoryId);
  if (!cat) return;
  openSheet({
    title: cat.name,
    build: (close) => {
      const st = store.getState();
      const items = st.withdrawals.filter((w) => w.categoryId === categoryId);
      const plans = st.plannedExpenses.filter((p) => p.categoryId === categoryId);
      const monthStart = startOfMonth(todayKey());
      const monthTotal = items.filter((w) => w.date >= monthStart).reduce((a, w) => a + w.amount, 0);
      const total = items.reduce((a, w) => a + w.amount, 0);
      return [
        h('div', { class: 'grid-2' },
          h('div', { class: 'stat' }, h('span', { class: 'stat-label' }, 'Ce mois'), h('span', { class: 'stat-value' }, fmt.eur2(monthTotal))),
          h('div', { class: 'stat' }, h('span', { class: 'stat-label' }, 'Total'), h('span', { class: 'stat-value' }, fmt.eur2(total)),
            h('span', { class: 'stat-note' }, `${items.length} dépense${items.length > 1 ? 's' : ''}`))),
        plans.length > 0 && h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Dépenses prévues'), PlanList(plans)),
        Button('Planifier une dépense dans ce dossier', () => { close(); openPlanForm({ categoryId }); }, { variant: 'secondary', iconName: 'calendar' }),
        items.length
          ? h('div', { class: 'op-list' }, items.map((w) => OperationRow('withdrawal', w, (t, i) => { close(); if (onOpenDetail) onOpenDetail(t, i); })))
          : EmptyState('Aucune dépense dans ce dossier pour le moment.', null, null, 'withdrawal'),
        h('div', { class: 'btn-row' },
          Button('Renommer', () => { close(); openCategoryForm({ category: cat }); }, { variant: 'secondary', iconName: 'edit', small: true }),
          Button('Supprimer', async () => {
            const ok = await confirmDialog({
              title: `Supprimer le dossier « ${cat.name} » ?`,
              message: 'Les dépenses et dépenses prévues de ce dossier sont conservées, simplement sans dossier.',
              confirmLabel: 'Supprimer le dossier', danger: true,
            });
            if (!ok) return;
            store.deleteCategory(categoryId);
            close();
            toast('Dossier supprimé');
          }, { variant: 'danger', iconName: 'trash', small: true })),
      ];
    },
  });
}

// ---------------------------------------------------------------------------
// Dépenses prévues
// ---------------------------------------------------------------------------
export function openPlanForm({ plan = null, categoryId = null } = {}) {
  const today = todayKey();
  openSheet({
    title: plan ? 'Modifier la dépense prévue' : 'Planifier une dépense',
    build: (close) => {
      let every = plan ? plan.everyMonths : 1;
      let cat = plan ? plan.categoryId : categoryId;
      const label = TextInput({ id: 'pl-label', value: plan ? plan.label : '', placeholder: 'Ex. Plein d’essence, Assurance pro…', label: 'Libellé' });
      const labelF = Field('Libellé', label.wrap, { forId: 'pl-label' });
      const amount = AmountInput({ id: 'pl-amount', value: plan ? centsToInput(plan.amount) : '', placeholder: '0,00', big: true, label: 'Montant' });
      const amountF = Field('Montant de chaque échéance', amount.wrap, { forId: 'pl-amount' });
      const date = DateInput({ id: 'pl-date', value: plan ? store.planNextDate(plan) : today, label: 'Date' });
      const dateF = Field(plan ? 'Prochaine échéance' : 'Première échéance', date.wrap, { forId: 'pl-date' });
      const freq = Segmented(
        store.PLAN_FREQUENCIES.map((m) => ({ value: m, label: m === 0 ? '1 fois' : m === 12 ? '1 an' : `${m} mois` })),
        every, (v) => { every = v; }, { small: true, label: 'Fréquence' });
      freq.classList.add('wrap-4');
      const submit = () => {
        const cents = parseAmountToCents(amount.input.value);
        const d = date.input.value;
        let ok = true;
        [labelF, amountF, dateF].forEach((f) => f.setError(''));
        if (!label.input.value.trim()) { labelF.setError('Donne un nom à cette dépense.'); ok = false; }
        if (!cents || cents <= 0) { amountF.setError('Indique un montant supérieur à 0 €.'); ok = false; }
        if (!isValidKey(d)) { dateF.setError('Choisis une date valide.'); ok = false; }
        if (!ok) return;
        const data = { label: label.input.value, amount: cents, categoryId: cat, everyMonths: every };
        // En modification, la date saisie devient la nouvelle prochaine échéance.
        if (plan) store.updatePlannedExpense(plan.id, { ...data, startDate: d, done: 0 });
        else store.addPlannedExpense({ ...data, startDate: d });
        close();
        toast(plan ? 'Dépense prévue modifiée' : 'Dépense planifiée');
      };
      return h('form', { novalidate: true, class: 'form-stack', onSubmit: (e) => { e.preventDefault(); submit(); } },
        labelF.el, amountF.el,
        h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Fréquence'), freq),
        dateF.el,
        h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Dossier (facultatif)'), CategoryPicker(cat, (v) => { cat = v; })),
        Notice('info', null, 'Les dépenses prévues sont déduites de ta projection et prises en compte dans ton objectif du jour, pour que tu puisses les anticiper.'),
        Button(plan ? 'Enregistrer' : 'Planifier la dépense', null, { type: 'submit' }),
        plan && Button('Supprimer cette dépense prévue', async () => {
          const yes = await confirmDialog({ title: 'Supprimer cette dépense prévue ?', message: 'Les retraits déjà enregistrés sont conservés.', confirmLabel: 'Supprimer', danger: true });
          if (!yes) return;
          store.deletePlannedExpense(plan.id);
          close();
          toast('Dépense prévue supprimée');
        }, { variant: 'danger', iconName: 'trash' }));
    },
  });
}

/** Liste compacte des dépenses prévues. */
export function PlanList(plans) {
  const today = todayKey();
  return h('div', { class: 'op-list' }, plans.map((p) => {
    const next = pendingOccurrences(p, '9999-12-31')[0];
    const cat = categoryName(p.categoryId);
    const meta = [FREQUENCY_LABELS[p.everyMonths], cat].filter(Boolean).join(' · ');
    return h('button', {
      type: 'button', class: 'op', onClick: () => openPlanForm({ plan: p }),
      'aria-label': `${p.label}, ${fmt.eur2(p.amount)}, ${meta}. Modifier`,
    },
    h('span', { class: 'op-icon planned' }, icon('calendar', 20)),
    h('span', { class: 'op-main' },
      h('span', { class: 'op-label' }, p.label),
      h('span', { class: 'op-date' }, next ? `${next.date < today ? 'En retard · ' : 'Prochaine : '}${formatShort(next.date)} · ${meta}` : `Terminée · ${meta}`)),
    h('span', { class: 'op-amount neg' }, '−' + fmt.eur2(p.amount)));
  }));
}

/** Échéances arrivées : on valide (→ retrait) ou on passe. */
export function DueBanner(due) {
  if (!due.length) return null;
  return h('section', { class: 'card due', 'aria-label': 'Dépenses prévues à valider' },
    h('div', { class: 'card-head' }, h('h2', { class: 'card-title' }, 'À valider'), h('span', { class: 'badge warn' }, String(due.length))),
    ...due.slice(0, 5).map((o) => h('div', { class: 'due-item' },
      h('div', { class: 'op-main' },
        h('span', { class: 'op-label' }, `${o.plan.label} · ${fmt.eur2(o.amount)}`),
        h('span', { class: 'op-date' }, `${o.overdue ? 'Prévue le' : 'Aujourd’hui'} ${o.overdue ? formatShort(o.date) : ''}`.trim())),
      h('div', { class: 'btn-row' },
        Button('Payée', () => { store.confirmPlannedExpense(o.plan.id); toast('Retrait enregistré'); }, { small: true, iconName: 'check' }),
        Button('Pas cette fois', () => { store.skipPlannedExpense(o.plan.id); toast('Échéance passée'); }, { small: true, variant: 'secondary' })))));
}
