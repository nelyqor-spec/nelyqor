// Page 5 — Profil et ses six rubriques (toutes fonctionnelles).
import { h, icon } from '../dom.js';
import {
  Logo, TopBar, Avatar, Card, Button, Badge, Field, AmountInput, DateInput, TextInput, Segmented, Switch, ActivityDaysPicker, Notice, Row, toast, confirmDialog, fmt,
} from '../components.js';
import { parseAmountToCents, parseRateToBp, centsToInput, rateToInput, formatRate, RATE_SCALE } from '../../core/money.js';
import { addDays, isValidKey, formatShort, WEEKDAYS_SHORT } from '../../core/dates.js';
import * as store from '../../core/store.js';
import { SubscriptionPanel, statusLabel, trialEndLabel } from '../premium.js';
import { fileToAvatar, pickImage } from '../image.js';
import { ACCENT_PRESETS, applyAccent } from '../theme.js';
import { AccountSection, accountSubtitle } from '../account.js';
import * as cloud from '../../core/cloud.js';

const SECTIONS = [
  { id: 'compte', title: 'Compte et synchronisation', icon: 'cloud' },
  { id: 'informations', title: 'Informations', icon: 'user' },
  { id: 'cotisations', title: 'Cotisations', icon: 'percent' },
  { id: 'jours', title: 'Jours d’activité', icon: 'calendar' },
  { id: 'abonnement', title: 'Abonnement Nelyqor+', icon: 'sparkle' },
  { id: 'preferences', title: 'Préférences', icon: 'sliders' },
  { id: 'apropos', title: 'À propos', icon: 'about' },
];

export function ProfilPage(ctx) {
  const { section } = ctx;
  if (section) {
    const def = SECTIONS.find((x) => x.id === section);
    const back = h('button', { type: 'button', class: 'back-btn', onClick: () => ctx.actions.openProfile(null) }, icon('back', 20), 'Profil');
    const builders = { compte: AccountSection, informations: Informations, cotisations: Cotisations, jours: Jours, abonnement: Abonnement, preferences: Preferences, apropos: APropos };
    return [back, h('h1', { class: 'page-title' }, def.title), ...[].concat(builders[section](ctx))];
  }
  return Overview(ctx);
}

function subtitleFor(id, state) {
  const s = state.settings;
  switch (id) {
    case 'compte': return accountSubtitle();
    case 'informations': return s.displayName ? `${s.displayName} · objectif ${fmt.eur(s.goalAmount)}` : `Pseudo, objectif ${fmt.eur(s.goalAmount)}, date cible`;
    case 'cotisations': return s.hasContributions ? `${formatRate(s.contributionRate)} réservés · versement ${s.contributionFrequency === 'monthly' ? 'mensuel' : 'trimestriel'}` : 'Non soumis aux cotisations';
    case 'jours': return s.plannedActivityDays.length ? `${s.plannedActivityDays.length} · ${s.plannedActivityDays.map((d) => WEEKDAYS_SHORT[d]).join(', ')}` : 'Aucun jour prévu';
    case 'abonnement': return statusLabel(state.subscription) + (state.subscription.status === 'trial' ? ` · jusqu’au ${trialEndLabel(state.subscription)}` : '');
    case 'preferences': return 'Couleur de l’app, fond dynamique, phrase du jour';
    case 'apropos': return `Version ${store.APP_VERSION}`;
    default: return '';
  }
}

function Overview({ state, actions }) {
  const s = state.settings;
  const sub = state.subscription;
  const name = (s.displayName || '').trim();
  const avatar = h('button', { type: 'button', class: 'avatar-edit', 'aria-label': 'Changer ma photo de profil', onClick: choosePhoto },
    Avatar({ size: 64 }), h('span', { class: 'avatar-cam' }, icon('camera', 15)));
  const tone = sub.status === 'free' ? '' : 'teal';
  return [
    TopBar(),
    Card([
      h('div', { class: 'profile-head' }, avatar,
        h('div', { style: { minWidth: 0, display: 'flex', flexDirection: 'column', gap: '6px', alignItems: 'flex-start' } },
          h('h1', { class: 'profile-name' }, name || 'Mon profil'),
          Badge(statusLabel(sub), tone))),
    ]),
    h('nav', { class: 'card menu', 'aria-label': 'Rubriques du profil', style: { padding: '4px 0' } },
      SECTIONS.map((sec) => h('button', { type: 'button', class: 'menu-item', onClick: () => actions.openProfile(sec.id) },
        h('span', { class: 'menu-ico' }, icon(sec.icon, 20)),
        h('span', { class: 'menu-txt' }, h('strong', null, sec.title), h('span', null, subtitleFor(sec.id, state))),
        h('span', { class: 'chev' }, icon('chevron', 18))))),
  ];
}

// ---------------------------------------------------------------------------
function Informations({ state, snap }) {
  const s = state.settings;
  const nameIn = TextInput({ id: 'pf-name', value: s.displayName || '', placeholder: 'Facultatif', label: 'Prénom ou pseudo' });
  const nameF = Field('Prénom ou pseudo affiché', nameIn.wrap, { forId: 'pf-name', hint: 'Facultatif. Affiché uniquement dans ton profil.' });
  const goalIn = AmountInput({ id: 'pf-goal', value: centsToInput(s.goalAmount), label: 'Objectif' });
  const goalF = Field('Objectif', goalIn.wrap, { forId: 'pf-goal' });
  const tomorrow = addDays(snap.today, 1);
  const dateIn = DateInput({ id: 'pf-date', value: s.targetDate, min: tomorrow, label: 'Date cible' });
  const dateF = Field('Date cible', dateIn.wrap, { forId: 'pf-date' });
  const initIn = AmountInput({ id: 'pf-init', value: centsToInput(s.initialSavings), placeholder: '0', label: 'Somme de départ' });
  const initF = Field('Somme déjà de côté au départ', initIn.wrap, { forId: 'pf-init' });

  const save = () => {
    const goal = parseAmountToCents(goalIn.input.value);
    const init = initIn.input.value.trim() === '' ? 0 : parseAmountToCents(initIn.input.value);
    const date = dateIn.input.value;
    let ok = true;
    [goalF, dateF, initF].forEach((f) => f.setError(''));
    if (!goal || goal <= 0) { goalF.setError('Indique un montant supérieur à 0 €.'); ok = false; }
    if (!isValidKey(date)) { dateF.setError('Choisis une date valide.'); ok = false; }
    else if (date !== s.targetDate && date <= snap.today) { dateF.setError('Choisis une date après aujourd’hui.'); ok = false; }
    if (init === null || init < 0) { initF.setError('Indique un montant valide.'); ok = false; }
    if (!ok) return;
    store.updateSettings({ displayName: nameIn.input.value.trim(), goalAmount: goal, targetDate: date, initialSavings: init });
    toast('Modifications enregistrées');
  };

  const reset = async () => {
    const yes = await confirmDialog({
      title: 'Effacer toutes les données ?',
      message: 'Ton objectif, tes revenus, virements, retraits et réglages seront supprimés de cet appareil. Cette action est définitive.',
      confirmLabel: 'Tout effacer', danger: true,
    });
    if (!yes) return;
    // Déconnexion d'abord : sinon la synchronisation ferait revenir les données du compte.
    if (cloud.getStatus().user) await cloud.signOut({ silent: true });
    store.resetAll();
  };

  return [
    Card([
      h('h2', { class: 'card-title' }, 'Photo de profil'),
      h('div', { class: 'profile-head' }, Avatar({ size: 64 }),
        h('div', { class: 'btn-stack', style: { flex: 1 } },
          Button(state.profile.avatar ? 'Changer la photo' : 'Ajouter une photo', choosePhoto, { variant: 'secondary', iconName: 'camera', small: true }),
          state.profile.avatar && Button('Retirer la photo', () => { store.setAvatar(null); toast('Photo retirée'); }, { variant: 'danger', small: true }))),
      h('p', { class: 'field-hint' }, 'Ta photo est réduite et nettoyée de ses métadonnées (lieu, appareil) avant d’être enregistrée.'),
    ]),
    Card([
      h('form', { novalidate: true, style: { display: 'flex', flexDirection: 'column', gap: '16px' }, onSubmit: (e) => { e.preventDefault(); save(); } },
        nameF.el, goalF.el, dateF.el, initF.el,
        Button('Enregistrer', null, { type: 'submit' })),
    ]),
    Card([h('div', { class: 'rows' }, Row('Membre depuis', formatShort(s.setupDate)))]),
    Card([
      h('h2', { class: 'card-title' }, 'Données'),
      h('p', { class: 'muted' }, cloud.getStatus().user
        ? 'Tes données sont enregistrées sur cet appareil et synchronisées sur ton compte. Effacer ici te déconnecte et vide cet appareil ; ton compte reste intact.'
        : 'Tes données sont enregistrées uniquement sur cet appareil.'),
      Button('Effacer toutes mes données', reset, { variant: 'danger', iconName: 'trash' }),
    ]),
  ];
}

// ---------------------------------------------------------------------------
function Cotisations({ state }) {
  const s = state.settings;
  let has = s.hasContributions;
  let frequency = s.contributionFrequency || 'quarterly';
  const freqField = h('div', { class: 'field' },
    h('span', { class: 'field-label' }, 'À quelle fréquence les verses-tu ?'),
    Segmented([{ value: 'monthly', label: 'Chaque mois' }, { value: 'quarterly', label: 'Tous les 3 mois' }], frequency, (v) => { frequency = v; }, { label: 'Fréquence de versement' }),
    h('p', { class: 'field-hint' }, 'Nelyqor t’indique combien garder de côté jusqu’à la prochaine échéance.'));
  freqField.hidden = !has;
  const rateIn = AmountInput({ id: 'pf-rate', value: s.hasContributions ? rateToInput(s.contributionRate) : '', placeholder: '21', suffix: '%', label: 'Taux de cotisations' });
  const rateF = Field('Quel pourcentage souhaites-tu réserver ?', rateIn.wrap, { forId: 'pf-rate' });
  rateF.el.hidden = !has;
  const choice = Segmented([{ value: true, label: 'Oui' }, { value: false, label: 'Non' }], has, (v) => { has = v; rateF.el.hidden = !v; freqField.hidden = !v; }, { label: 'Soumis aux cotisations' });
  const save = () => {
    rateF.setError('');
    if (!has) {
      store.updateSettings({ hasContributions: false, contributionRate: 0 });
      return toast('Modifications enregistrées');
    }
    const v = parseRateToBp(rateIn.input.value);
    if (v === null || v < 0 || v >= RATE_SCALE) return rateF.setError('Indique un pourcentage entre 0 et moins de 100 %.');
    store.updateSettings({ hasContributions: true, contributionRate: v, contributionFrequency: frequency });
    toast('Modifications enregistrées');
  };
  return Card([
    h('form', { novalidate: true, style: { display: 'flex', flexDirection: 'column', gap: '16px' }, onSubmit: (e) => { e.preventDefault(); save(); } },
      h('p', { class: 'field-label' }, 'Es-tu soumis à des cotisations sociales sur ces revenus ?'),
      choice, rateF.el, freqField,
      Notice('info', null, 'Le nouveau taux s’applique aux prochains revenus. Les revenus déjà enregistrés gardent les cotisations calculées au moment de leur saisie.'),
      Button('Enregistrer', null, { type: 'submit' })),
  ]);
}

// ---------------------------------------------------------------------------
function Jours({ state }) {
  let days = [...state.settings.plannedActivityDays];
  const picker = ActivityDaysPicker(days, (d) => { days = d; });
  return Card([
    h('p', { class: 'muted' }, 'Choisis tes jours d’activité habituels. Tes objectifs journaliers, ton objectif de la semaine, tes jours restants et ta projection seront recalculés.'),
    picker,
    Button('Enregistrer', () => { store.updateSettings({ plannedActivityDays: days }); toast('Modifications enregistrées'); }),
  ]);
}

// ---------------------------------------------------------------------------
function Abonnement() {
  return SubscriptionPanel();
}

// ---------------------------------------------------------------------------
function Preferences({ state }) {
  const p = state.preferences;
  const row = (title, desc, checked, key) => h('div', { class: 'switch-row' },
    h('div', { class: 'txt' }, h('strong', null, title), h('span', { class: 'muted', style: { fontSize: '13px' } }, desc)),
    Switch(checked, (v) => { store.updatePreferences({ [key]: v }); toast('Modifications enregistrées'); }, title));
  const current = (p.accentColor || '').toUpperCase();
  const swatches = h('div', { class: 'swatches', role: 'radiogroup', 'aria-label': 'Couleur principale' });
  const custom = h('input', { type: 'color', value: current || '#2ED3B7', 'aria-label': 'Couleur personnalisée' });
  const choose = (hex) => {
    applyAccent(hex);
    store.updatePreferences({ accentColor: hex.toUpperCase() });
  };
  custom.addEventListener('change', () => { choose(custom.value); toast('Couleur enregistrée'); });
  custom.addEventListener('input', () => applyAccent(custom.value)); // aperçu en direct
  const isPreset = ACCENT_PRESETS.some((c) => c.hex === current);
  swatches.append(
    ...ACCENT_PRESETS.map((c) => h('button', {
      type: 'button', class: 'swatch', role: 'radio', 'aria-checked': String(c.hex === current), 'aria-label': c.name, title: c.name,
      style: { background: c.hex }, onClick: () => { choose(c.hex); toast(`Couleur « ${c.name} » enregistrée`); },
    })),
    h('label', { class: 'swatch custom', 'aria-checked': String(!isPreset), title: 'Couleur personnalisée' }, icon('palette', 18), custom));
  return [Card([
    h('h2', { class: 'card-title' }, 'Couleur principale'),
    h('p', { class: 'muted', style: { fontSize: '13.5px' } }, 'Choisis la couleur de ton Nelyqor : boutons, graphiques, barres de progression…'),
    swatches,
  ]), Card([
    row('Fond dynamique', 'Un fond abstrait différent chaque jour sur la page Objectif. Désactivé : fond fixe.', p.dynamicBackgroundEnabled, 'dynamicBackgroundEnabled'),
    row('Phrase quotidienne', 'Une phrase différente chaque jour, adaptée aux jours d’activité et de repos.', p.dailyQuoteEnabled, 'dailyQuoteEnabled'),
  ])];
}

// ---------------------------------------------------------------------------
function APropos() {
  return Card([
    h('div', { style: { display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: '14px' } },
      Logo({ size: 48 }),
      h('p', null, 'Nelyqor t’aide à suivre tes revenus et à progresser vers tes objectifs financiers.'),
      h('div', { class: 'rows', style: { alignSelf: 'stretch' } },
        Row('Version', store.APP_VERSION),
        Row('Données', cloud.getStatus().user ? 'Sur cet appareil + compte Nelyqor' : 'Stockées sur cet appareil'))),
  ]);
}

// ---------------------------------------------------------------------------
async function choosePhoto() {
  const file = await pickImage();
  if (!file) return;
  try {
    const dataUrl = await fileToAvatar(file);
    if (!store.setAvatar(dataUrl)) throw new Error('Image non valide.');
    toast('Photo de profil mise à jour');
  } catch (e) {
    toast(e.message || 'Impossible d’utiliser cette image.');
  }
}
