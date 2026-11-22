// Nelyqor — onboarding : 5 étapes puis choix Gratuit / Nelyqor+.
// Aucune valeur préremplie : 18 000 € et 21 % ne sont que des placeholders.
import { h, icon } from './dom.js';
import { Logo, Button, AmountInput, DateInput, Field, Segmented, ActivityDaysPicker } from './components.js';
import { parseAmountToCents, parseRateToBp, centsToInput, rateToInput, RATE_SCALE } from '../core/money.js';
import { todayKey, addDays, isValidKey } from '../core/dates.js';
import { completeOnboarding } from '../core/store.js';
import { PRICE_LINE, FREE_FEATURES, PLUS_FEATURES, checklist } from './premium.js';
import { openAuthSheet } from './account.js';
import { ContributionDueFields } from './contrib.js';
import { restoreBackup } from './backup.js';
import { isConfigured } from '../core/cloud.js';

const TOTAL_STEPS = 5;

export function renderOnboarding(root) {
  const draft = {
    goalAmount: null, targetDate: '', hasSavings: null, initialSavings: null,
    hasContributions: null, contributionRate: null, contributionFrequency: null, contributionDueDate: null, plannedActivityDays: [],
  };
  let step = 1;

  const go = (n) => { step = n; render(); window.scrollTo(0, 0); };

  function frame({ question, body, onNext, nextLabel = 'Continuer' }) {
    const steps = h('div', { class: 'ob-steps', 'aria-label': `Étape ${step} sur ${TOTAL_STEPS}` },
      Array.from({ length: TOTAL_STEPS }, (_, i) => h('i', { class: i < step ? 'on' : '' })));
    return h('main', { class: 'screen no-nav' },
      h('div', { class: 'ob' },
        h('div', { class: 'ob-top' },
          step > 1
            ? h('button', { type: 'button', class: 'back-btn', onClick: () => go(step - 1) }, icon('back', 20), 'Retour')
            : Logo({ size: 30 }),
          steps),
        h('form', {
          class: 'ob', novalidate: true,
          onSubmit: (e) => { e.preventDefault(); onNext(); },
        },
          h('p', { class: 'eyebrow' }, `Étape ${step} sur ${TOTAL_STEPS}`),
          h('h1', { class: 'ob-q', id: 'ob-q' }, question),
          h('div', { class: 'ob-body' }, body),
          h('div', { class: 'ob-actions' }, Button(nextLabel, null, { type: 'submit' })))));
  }

  // --- Étape 1 : objectif ---------------------------------------------------
  function step1() {
    const amount = AmountInput({ id: 'ob-goal', placeholder: '18 000', big: true, value: centsToInput(draft.goalAmount), autofocus: true, label: 'Montant de ton objectif' });
    const field = Field('Montant de ton objectif', amount.wrap, { forId: 'ob-goal', hint: 'Le montant total que tu veux atteindre.' });
    return frame({
      question: 'Quel est ton objectif ?',
      body: [field.el, h('div', { class: 'ob-login' },
        h('span', { class: 'muted' }, 'Tu as déjà utilisé Nelyqor ?'),
        h('button', { type: 'button', class: 'btn-link', onClick: () => restoreBackup() }, 'Restaurer une sauvegarde'),
        isConfigured() && h('button', { type: 'button', class: 'btn-link', onClick: () => openAuthSheet({ mode: 'signin' }) }, 'Me connecter et récupérer mes données'))],
      onNext: () => {
        const v = parseAmountToCents(amount.input.value);
        if (!v || v <= 0) return field.setError('Indique un montant supérieur à 0 €.');
        draft.goalAmount = v;
        go(2);
      },
    });
  }

  // --- Étape 2 : date cible -------------------------------------------------
  function step2() {
    const tomorrow = addDays(todayKey(), 1);
    const date = DateInput({ id: 'ob-date', value: draft.targetDate, min: tomorrow, label: 'Date cible' });
    const field = Field('Date cible', date.wrap, { forId: 'ob-date', hint: 'Elle sert à calculer tes objectifs du jour, de la semaine et ta projection.' });
    return frame({
      question: 'À quelle date veux-tu atteindre ton objectif ?',
      body: field.el,
      onNext: () => {
        const v = date.input.value;
        if (!isValidKey(v)) return field.setError('Choisis une date.');
        if (v <= todayKey()) return field.setError('Choisis une date après aujourd’hui.');
        draft.targetDate = v;
        go(3);
      },
    });
  }

  // --- Étape 3 : somme de départ --------------------------------------------
  function step3() {
    const amount = AmountInput({ id: 'ob-savings', placeholder: '0', value: centsToInput(draft.initialSavings), label: 'Somme déjà de côté' });
    const field = Field('Combien as-tu déjà de côté pour cet objectif ?', amount.wrap, { forId: 'ob-savings' });
    field.el.hidden = draft.hasSavings !== true;
    const choiceError = h('p', { class: 'field-error', role: 'alert', hidden: true });
    const choice = Segmented([{ value: true, label: 'Oui' }, { value: false, label: 'Non' }], draft.hasSavings, (v) => {
      draft.hasSavings = v;
      choiceError.hidden = true;
      field.el.hidden = !v;
      if (v) setTimeout(() => amount.input.focus(), 50);
    }, { label: 'As-tu déjà une somme de côté ?' });
    return frame({
      question: 'As-tu déjà une somme de côté pour cet objectif ?',
      body: [choice, choiceError, field.el],
      onNext: () => {
        if (draft.hasSavings === null) { choiceError.textContent = 'Choisis Oui ou Non.'; choiceError.hidden = false; return; }
        if (draft.hasSavings === false) { draft.initialSavings = 0; return go(4); }
        const v = parseAmountToCents(amount.input.value);
        if (v === null || v <= 0) return field.setError('Indique un montant supérieur à 0 €.');
        if (v >= draft.goalAmount) return field.setError('Cette somme atteint déjà ton objectif. Indique une somme plus petite ou reviens modifier ton objectif.');
        draft.initialSavings = v;
        go(4);
      },
    });
  }

  // --- Étape 4 : cotisations ------------------------------------------------
  function step4() {
    const rate = AmountInput({ id: 'ob-rate', placeholder: '21', suffix: '%', value: rateToInput(draft.contributionRate), label: 'Pourcentage de cotisations' });
    const field = Field('Quel pourcentage souhaites-tu réserver pour tes cotisations ?', rate.wrap, { forId: 'ob-rate', hint: 'Ce pourcentage sera mis de côté sur chaque revenu brut.' });
    field.el.hidden = draft.hasContributions !== true;
    const due = ContributionDueFields({ frequency: draft.contributionFrequency, dueDate: draft.contributionDueDate, idPrefix: 'ob' });
    due.el.hidden = draft.hasContributions !== true;
    const choiceError = h('p', { class: 'field-error', role: 'alert', hidden: true });
    const choice = Segmented([{ value: true, label: 'Oui' }, { value: false, label: 'Non' }], draft.hasContributions, (v) => {
      draft.hasContributions = v;
      choiceError.hidden = true;
      field.el.hidden = !v;
      due.el.hidden = !v;
      if (v) setTimeout(() => rate.input.focus(), 50);
    }, { label: 'Es-tu soumis à des cotisations sociales ?' });
    return frame({
      question: 'Es-tu soumis à des cotisations sociales sur ces revenus ?',
      body: [choice, choiceError, field.el, due.el],
      onNext: () => {
        if (draft.hasContributions === null) { choiceError.textContent = 'Choisis Oui ou Non.'; choiceError.hidden = false; return; }
        if (draft.hasContributions === false) { draft.contributionRate = 0; return go(5); }
        const v = parseRateToBp(rate.input.value);
        if (v === null) return field.setError('Indique un pourcentage (ex. 21).');
        if (v < 0 || v >= RATE_SCALE) return field.setError('Le pourcentage doit être compris entre 0 et moins de 100 %.');
        const sched = due.read();
        if (!sched) return;
        draft.contributionFrequency = sched.contributionFrequency;
        draft.contributionDueDate = sched.contributionDueDate;
        draft.contributionRate = v;
        go(5);
      },
    });
  }

  // --- Étape 5 : jours d'activité -------------------------------------------
  function step5() {
    const picker = ActivityDaysPicker(draft.plannedActivityDays, (days) => { draft.plannedActivityDays = days; });
    return frame({
      question: 'Jours d’activité prévus',
      body: [
        h('p', { class: 'muted' }, 'Combien de jours d’activité prévois-tu généralement par semaine, et lesquels ? Ils servent au calcul de tes objectifs du jour et de la semaine.'),
        picker,
      ],
      onNext: () => go(6),
    });
  }

  // --- Écran final : Gratuit / Nelyqor+ -------------------------------------
  function planScreen() {
    const finish = (plan) => completeOnboarding({
      goalAmount: draft.goalAmount,
      targetDate: draft.targetDate,
      initialSavings: draft.initialSavings || 0,
      hasContributions: draft.hasContributions === true,
      contributionRate: draft.contributionRate || 0,
      contributionFrequency: draft.contributionFrequency || 'quarterly',
      contributionDueDate: draft.contributionDueDate,
      plannedActivityDays: draft.plannedActivityDays,
      plan,
    });
    return h('main', { class: 'screen no-nav' },
      h('div', { class: 'ob' },
        h('div', { class: 'ob-top' },
          h('button', { type: 'button', class: 'back-btn', onClick: () => go(5) }, icon('back', 20), 'Retour'),
          Logo({ size: 30 })),
        h('h1', { class: 'ob-q' }, 'Ton objectif est prêt.'),
        h('p', { class: 'muted' }, 'Choisis comment tu veux utiliser Nelyqor. Tu pourras changer à tout moment depuis ton profil.'),
        h('section', { class: 'plan plus', 'aria-label': 'Nelyqor+' },
          h('div', { class: 'card-head' }, h('h2', { class: 'plan-name' }, 'Nelyqor+'), h('span', { class: 'badge teal' }, 'Recommandé')),
          h('p', { class: 'plan-price' }, PRICE_LINE),
          h('p', { class: 'muted' }, 'Tout Nelyqor Gratuit, plus :'),
          checklist(PLUS_FEATURES),
          Button('Essayer Nelyqor+ gratuitement', () => finish('trial'), { iconName: 'sparkle' })),
        h('section', { class: 'plan', 'aria-label': 'Nelyqor Gratuit' },
          h('h2', { class: 'plan-name' }, 'Nelyqor Gratuit'),
          checklist(FREE_FEATURES),
          Button('Continuer gratuitement', () => finish('free'), { variant: 'secondary' })),
        h('p', { class: 'field-hint', style: { textAlign: 'center' } },
          'Aucun moyen de paiement n’est demandé dans Nelyqor. Le paiement de l’abonnement sera géré par l’App Store ou Google Play.')));
  }

  function render() {
    const view = [null, step1, step2, step3, step4, step5, planScreen][step]();
    root.replaceChildren(view);
  }
  render();
}
