// Nelyqor — question « Quand tombent tes cotisations ? » (onboarding + Profil).
import { h } from './dom.js';
import { Segmented, DateInput, Field } from './components.js';
import { todayKey, addDays, addMonths, isValidKey } from '../core/dates.js';

/**
 * Fréquence (chaque mois / tous les 3 mois) + date du prochain prélèvement.
 * Chaque régime est différent : on demande à la personne plutôt que de deviner.
 */
export function ContributionDueFields({ frequency = null, dueDate = null, idPrefix = 'cd' } = {}) {
  let freq = frequency;
  const today = todayKey();
  const freqError = h('p', { class: 'field-error', role: 'alert', hidden: true });
  const date = DateInput({ id: `${idPrefix}-due`, value: dueDate && dueDate >= today ? dueDate : '', min: today, max: addMonths(today, 4), label: 'Date du prochain prélèvement' });
  const dateF = Field('Quand tombe ton prochain prélèvement de cotisations ?', date.wrap, {
    forId: `${idPrefix}-due`,
    hint: 'Regarde ton dernier relevé ou ton espace URSSAF / caisse. Nelyqor en déduit tous les suivants et te dit combien garder de côté.',
  });
  const el = h('div', { class: 'form-stack' },
    h('div', { class: 'field' },
      h('span', { class: 'field-label' }, 'Tes cotisations sont prélevées…'),
      Segmented([{ value: 'monthly', label: 'Chaque mois' }, { value: 'quarterly', label: 'Tous les 3 mois' }], freq, (v) => { freq = v; freqError.hidden = true; }, { label: 'Fréquence des prélèvements de cotisations' }),
      freqError),
    dateF.el);
  return {
    el,
    /** @returns {{ contributionFrequency, contributionDueDate } | null} null si invalide (erreurs affichées). */
    read() {
      let ok = true;
      dateF.setError('');
      if (!freq) { freqError.textContent = 'Choisis une fréquence.'; freqError.hidden = false; ok = false; }
      const v = date.input.value;
      if (!isValidKey(v)) { dateF.setError('Indique la date de ton prochain prélèvement.'); ok = false; }
      else if (v < today) { dateF.setError('Choisis une date à venir.'); ok = false; }
      else if (v > addDays(addMonths(today, 4), 0)) { dateF.setError('Choisis une date dans les 4 prochains mois.'); ok = false; }
      return ok ? { contributionFrequency: freq, contributionDueDate: v } : null;
    },
  };
}
