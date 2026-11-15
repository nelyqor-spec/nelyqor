// Nelyqor — formulaires d'ajout, détail d'une opération, historique.
import { h } from './dom.js';
import {
  openSheet, Button, AmountInput, DateInput, TextInput, Field, Row, Notice, OperationRow, EmptyState, confirmDialog, toast, fmt, Switch,
} from './components.js';
import { CategoryPicker } from './expenses.js';
import { parseAmountToCents, contributionFor, formatRate } from '../core/money.js';
import { todayKey, isValidKey, addDays, formatLong } from '../core/dates.js';
import { computeSnapshot } from '../core/calc.js';
import * as store from '../core/store.js';

const LABEL_SUGGESTIONS = ['Livraison', 'Freelance', 'Bonus', 'Vente', 'Autre'];

const CONFIG = {
  revenue: {
    title: 'Ajouter un revenu', amountLabel: 'Montant brut', labelLabel: 'Libellé / source (facultatif)',
    labelPlaceholder: 'Ex. Livraison', submit: 'Enregistrer le revenu', toast: 'Revenu enregistré',
  },
  transfer: {
    title: 'Ajouter un virement', amountLabel: 'Montant', labelLabel: 'Note / libellé (facultatif)',
    labelPlaceholder: 'Ex. Épargne du mois', submit: 'Enregistrer le virement', toast: 'Virement ajouté',
  },
  withdrawal: {
    title: 'Ajouter un retrait', amountLabel: 'Montant', labelLabel: 'Motif / libellé (facultatif)',
    labelPlaceholder: 'Ex. Dépense imprévue', submit: 'Enregistrer le retrait', toast: 'Retrait enregistré',
  },
};

export function openOperationForm(type, { date } = {}) {
  const cfg = CONFIG[type];
  const today = todayKey();
  const state = store.getState();
  const rate = state.settings.hasContributions ? state.settings.contributionRate : 0;
  const balance = computeSnapshot(state, today).balance;

  openSheet({
    title: cfg.title,
    build: (close) => {
      const amount = AmountInput({ id: 'op-amount', placeholder: '0,00', big: true, autofocus: true, label: cfg.amountLabel });
      const amountField = Field(cfg.amountLabel, amount.wrap, { forId: 'op-amount' });
      const dateIn = DateInput({ id: 'op-date', value: date || today, max: today, label: 'Date' });
      const dateField = Field(type === 'revenue' ? 'Date de réalisation' : 'Date', dateIn.wrap, { forId: 'op-date' });
      const labelIn = TextInput({ id: 'op-label', placeholder: cfg.labelPlaceholder, label: cfg.labelLabel });
      const labelField = Field(cfg.labelLabel, labelIn.wrap, { forId: 'op-label' });

      let suggestions = null;
      let exceptional = false;
      let exceptionalRow = null;
      let categoryId = null;
      let categoryField = null;
      if (type === 'withdrawal' && store.isPremium()) {
        categoryField = h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Dossier (facultatif)'),
          CategoryPicker(null, (v) => { categoryId = v; }));
      }
      if (type === 'revenue') {
        exceptionalRow = h('div', { class: 'switch-row', style: { borderTop: 0, padding: 0 } },
          h('div', { class: 'txt' }, h('strong', null, 'Revenu exceptionnel'),
            h('span', { class: 'muted', style: { fontSize: '13px' } }, 'Prime, vente ponctuelle… Compté une seule fois : il ne gonfle pas ta projection.')),
          Switch(false, (v) => { exceptional = v; }, 'Revenu exceptionnel'));
        suggestions = h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: '8px' } },
          LABEL_SUGGESTIONS.map((s) => h('button', {
            type: 'button', class: 'badge', style: { cursor: 'pointer', border: '0', minHeight: '34px', padding: '6px 12px' },
            onClick: () => { labelIn.input.value = s; },
          }, s)));
        labelField.el.append(suggestions);
      }

      // Aperçu du calcul (revenu) / avertissement (retrait)
      const live = h('div');
      const update = () => {
        const cents = parseAmountToCents(amount.input.value) || 0;
        if (type === 'revenue') {
          const contrib = contributionFor(cents, rate);
          live.replaceChildren(h('div', { class: 'preview rows', 'aria-live': 'polite' },
            Row('Brut', fmt.eur2(cents)),
            rate > 0 && Row(`Cotisations réservées (${formatRate(rate)})`, '−' + fmt.eur2(contrib)),
            Row('Net ajouté à l’objectif', fmt.eur2(cents - contrib), { valueClass: 'teal' })));
        } else if (type === 'transfer') {
          live.replaceChildren(h('div', { class: 'preview rows' },
            Row('Ajouté à l’objectif', fmt.eur2(cents), { valueClass: 'teal' }),
            Row('Cotisations', 'Aucune')),
            h('p', { class: 'field-hint' }, 'Un virement est compté une seule fois : il n’est jamais répété dans ta projection.'));
        } else {
          live.replaceChildren(cents > balance
            ? Notice('warn', 'Ce retrait dépasse le montant disponible.',
              `Montant actuellement atteint : ${fmt.eur2(balance)}. Après ce retrait, ton solde objectif serait de ${fmt.eur2(balance - cents)}.`)
            : h('div', { class: 'preview rows' },
              Row('Montant disponible', fmt.eur2(balance)),
              Row('Après ce retrait', fmt.eur2(balance - cents))));
        }
      };
      amount.input.addEventListener('input', () => { amountField.setError(''); update(); });
      update();

      const submit = async () => {
        const cents = parseAmountToCents(amount.input.value);
        const d = dateIn.input.value;
        let ok = true;
        if (!cents || cents <= 0) { amountField.setError('Indique un montant supérieur à 0 €.'); ok = false; }
        if (!isValidKey(d)) { dateField.setError('Choisis une date valide.'); ok = false; }
        else if (d > today) { dateField.setError('La date ne peut pas être dans le futur.'); ok = false; }
        else dateField.setError('');
        if (!ok) return;
        if (type === 'withdrawal' && cents > balance) {
          const confirmed = await confirmDialog({
            title: 'Confirmer ce retrait ?',
            message: `Ce retrait de ${fmt.eur2(cents)} dépasse le montant actuellement disponible (${fmt.eur2(balance)}). Ton solde objectif deviendrait négatif.`,
            confirmLabel: 'Confirmer le retrait',
            danger: true,
          });
          if (!confirmed) return;
        }
        const payload = { date: d, label: labelIn.input.value };
        if (type === 'revenue') store.addRevenue({ ...payload, grossAmount: cents, exceptional });
        else if (type === 'transfer') store.addTransfer({ ...payload, amount: cents });
        else store.addWithdrawal({ ...payload, amount: cents, categoryId });
        close();
        toast(cfg.toast);
      };

      const form = h('form', {
        style: { display: 'flex', flexDirection: 'column', gap: '16px' }, novalidate: true,
        onSubmit: (e) => { e.preventDefault(); submit(); },
      }, amountField.el, live, dateField.el, labelField.el, exceptionalRow, categoryField, Button(cfg.submit, null, { type: 'submit' }));
      return form;
    },
  });
}

// ---------------------------------------------------------------------------
const TYPE_NAMES = {
  revenue: { one: 'Revenu', deleted: 'Revenu supprimé', history: 'Historique des revenus', empty: 'Aucun revenu enregistré pour le moment.' },
  transfer: { one: 'Virement', deleted: 'Virement supprimé', history: 'Historique des virements', empty: 'Aucun virement pour le moment.' },
  withdrawal: { one: 'Retrait', deleted: 'Retrait supprimé', history: 'Historique des retraits', empty: 'Aucun retrait pour le moment.' },
};

export function openOperationDetail(type, item, { onDeleted } = {}) {
  const names = TYPE_NAMES[type];
  openSheet({
    title: item.label || names.one,
    build: (close) => {
      const rows = h('div', { class: 'preview rows' },
        Row('Type', names.one),
        Row('Date', formatLong(item.date)));
      if (type === 'revenue') {
        rows.append(
          item.exceptional && Row('Nature', 'Revenu exceptionnel (hors projection)'),
          Row('Brut', fmt.eur2(item.grossAmount)),
          item.contributionAmount > 0 && Row(`Cotisations réservées${item.contributionRate ? ` (${formatRate(item.contributionRate)})` : ''}`, '−' + fmt.eur2(item.contributionAmount)),
          Row('Net ajouté à l’objectif', fmt.eur2(item.netAmount), { valueClass: 'teal' }));
      } else if (type === 'transfer') {
        rows.append(Row('Ajouté à l’objectif', fmt.eur2(item.amount), { valueClass: 'teal' }));
      } else {
        rows.append(Row('Retiré de l’objectif', '−' + fmt.eur2(item.amount), { valueClass: 'danger-text' }));
      }
      let catBlock = null;
      if (type === 'withdrawal' && store.isPremium()) {
        catBlock = h('div', { class: 'field' }, h('span', { class: 'field-label' }, 'Dossier'),
          CategoryPicker(item.categoryId, (v) => { store.setWithdrawalCategory(item.id, v); toast('Dossier mis à jour'); }));
      }
      const del = Button(`Supprimer ce ${names.one.toLowerCase()}`, async () => {
        const ok = await confirmDialog({
          title: `Supprimer ce ${names.one.toLowerCase()} ?`,
          message: 'Cette opération sera retirée de ton historique et tous les calculs seront mis à jour.',
          confirmLabel: 'Supprimer', danger: true,
        });
        if (!ok) return;
        store.deleteOperation(type, item.id);
        close();
        if (onDeleted) onDeleted();
        toast(names.deleted);
      }, { variant: 'danger', iconName: 'trash' });
      return [rows, catBlock, del];
    },
  });
}

const COLLECTION = { revenue: 'revenues', transfer: 'transfers', withdrawal: 'withdrawals' };
export const FREE_HISTORY_DAYS = 30;

/** Historique : complet avec Nelyqor+, 30 derniers jours en Gratuit. */
export function openHistory(type, { onDiscoverPlus } = {}) {
  const names = TYPE_NAMES[type];
  openSheet({
    title: names.history,
    build: (close) => {
      const premium = store.isPremium();
      const all = store.getState()[COLLECTION[type]];
      const since = addDays(todayKey(), -(FREE_HISTORY_DAYS - 1));
      const items = premium ? all : all.filter((x) => x.date >= since);
      const hidden = all.length - items.length;
      const parts = [];
      if (!premium) {
        parts.push(Notice('info', `Historique des ${FREE_HISTORY_DAYS} derniers jours`,
          hidden > 0
            ? `${hidden} opération${hidden > 1 ? 's plus anciennes sont' : ' plus ancienne est'} conservée${hidden > 1 ? 's' : ''} et visible${hidden > 1 ? 's' : ''} avec l’historique complet de Nelyqor+.`
            : 'L’historique complet est inclus dans Nelyqor+.'));
        if (onDiscoverPlus) parts.push(Button('Découvrir Nelyqor+', () => { close(); onDiscoverPlus(); }, { variant: 'secondary', iconName: 'sparkle' }));
      }
      parts.push(items.length
        ? h('div', { class: 'op-list' }, items.map((it) => OperationRow(type, it, (t, i) => openOperationDetail(t, i, { onDeleted: close }))))
        : EmptyState(premium ? names.empty : `Aucune opération sur les ${FREE_HISTORY_DAYS} derniers jours.`, null, null, 'calendar'));
      return parts;
    },
  });
}
