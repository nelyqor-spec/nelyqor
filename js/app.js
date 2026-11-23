// Nelyqor — point d'entrée : navigation 6 onglets, rendu, fonds.
import { h, icon } from './ui/dom.js';
import * as store from './core/store.js';
import { computeSnapshot } from './core/calc.js';
import { todayKey } from './core/dates.js';
import { getDailyBackground, STATIC_BACKGROUND } from './content/backgrounds.js';
import { renderOnboarding } from './ui/onboarding.js';
import { openOperationForm, openOperationDetail, openHistory } from './ui/forms.js';
import { openPaywall } from './ui/premium.js';
import { ObjectifPage } from './ui/pages/objectif.js';
import { RevenusPage, VirementsPage, RetraitsPage } from './ui/pages/operations.js';
import { ProfilPage } from './ui/pages/profil.js';
import { CalendrierPage } from './ui/pages/calendrier.js';
import { applyAccent } from './ui/theme.js';
import { openNewPasswordSheet } from './ui/account.js';
import { openCalendar } from './ui/calendar.js';
import * as cloud from './core/cloud.js';
import { applyLinkedBackup } from './ui/backup.js';
import { toast } from './ui/components.js';

const TABS = [
  { id: 'objectif', label: 'Objectif', icon: 'target', page: ObjectifPage },
  { id: 'revenus', label: 'Revenus', icon: 'revenue', page: RevenusPage },
  { id: 'virements', label: 'Virements', icon: 'transfer', page: VirementsPage },
  { id: 'retraits', label: 'Retraits', icon: 'withdrawal', page: RetraitsPage },
  { id: 'profil', label: 'Profil', icon: 'user', page: ProfilPage },
  { id: 'calendrier', label: 'Calendrier', icon: 'calendar', page: CalendrierPage },
];

const ui = { tab: 'objectif', profileSection: null, renderedDay: todayKey(), wasOnboarded: null };

const bgLayer = h('div', { class: 'bg-layer', 'aria-hidden': 'true' });
const root = h('div', { id: 'root' });
document.getElementById('app').replaceChildren(bgLayer, root);

const actions = {
  addOperation: (type, opts) => openOperationForm(type, opts),
  openDetail: (type, item) => openOperationDetail(type, item),
  openHistory: (type) => openHistory(type, { onDiscoverPlus: openPaywall }),
  openPaywall,
  openProfile: (section) => {
    ui.tab = 'profil';
    ui.profileSection = section;
    render({ scrollTop: true });
  },
  goTab: (id) => {
    ui.tab = id;
    if (id !== 'profil') ui.profileSection = null;
    render({ scrollTop: true });
  },
};

function Nav() {
  return h('nav', { class: 'nav', 'aria-label': 'Navigation principale' },
    h('div', { class: 'nav-inner' }, TABS.map((t) => {
      const active = ui.tab === t.id;
      return h('button', {
        type: 'button', class: 'nav-item' + (active ? ' active' : ''), 'aria-current': active ? 'page' : null,
        onClick: () => {
          if (t.id === 'profil' && ui.tab === 'profil') ui.profileSection = null; // re-tap = retour au menu Profil
          actions.goTab(t.id);
        },
      }, h('span', { class: 'nav-ico' }, icon(t.icon, 22)), h('span', { class: 'nav-label' }, t.label));
    })));
}

function applyBackground(state, today) {
  if (!state.onboardingCompleted || ui.tab !== 'objectif') {
    bgLayer.style.backgroundImage = 'none';
    return;
  }
  bgLayer.style.backgroundImage = state.preferences.dynamicBackgroundEnabled ? getDailyBackground(today) : STATIC_BACKGROUND;
}

function render({ scrollTop = false } = {}) {
  store.refreshSubscription();
  const state = store.getState();
  applyAccent(state.preferences.accentColor);
  const today = todayKey();
  ui.renderedDay = today;
  const y = window.scrollY;

  if (!state.onboardingCompleted) {
    // Premier lancement (ou données effacées) : onboarding, puis onglet Objectif.
    ui.tab = 'objectif';
    ui.profileSection = null;
    applyBackground(state, today);
    if (ui.wasOnboarded !== false) {
      ui.wasOnboarded = false;
      renderOnboarding(root);
    }
    return;
  }
  ui.wasOnboarded = true;

  const snap = computeSnapshot(state, today);
  const tab = TABS.find((t) => t.id === ui.tab) || TABS[0];
  const content = tab.page({ state, snap, today, actions, section: ui.profileSection });
  const main = h('main', { class: 'screen', id: 'main' }, content);
  root.replaceChildren(main, Nav());
  applyBackground(state, today);
  document.title = `${tab.label} · Nelyqor`;
  if (scrollTop) window.scrollTo(0, 0);
  else window.scrollTo(0, y);
}

store.subscribe(() => render());

// Photo de profil (en haut à droite) → Profil.
document.addEventListener('nelyqor:open-profile', () => actions.openProfile(null));

// Compte : on rafraîchit l'affichage quand l'état de connexion change.
let recoveryShown = false;
cloud.onStatus((st) => {
  if (st.recovery && !recoveryShown) { recoveryShown = true; openNewPasswordSheet(); }
  if (!st.recovery) recoveryShown = false;
  if (store.getState().onboardingCompleted && ui.tab === 'profil') render();
});

// Nouveau jour (application restée ouverte) : nouvelle phrase, nouveau fond, nouvelle semaine.
function checkDayChange() {
  if (todayKey() !== ui.renderedDay && store.getState().onboardingCompleted) render();
  else store.refreshSubscription();
}
document.addEventListener('visibilitychange', () => { if (!document.hidden) checkDayChange(); });
setInterval(checkDayChange, 60 * 1000);

render();
cloud.initCloud();
applyLinkedBackup().then((added) => { if (added) toast('Tes données sont chargées'); }).catch(() => toast('Lien de données illisible.'));

// Saisie rapide : raccourcis de l'icône (appui long) → ?action=revenu | retrait | calendrier
(function quickAction() {
  const url = new URL(location.href);
  const action = url.searchParams.get('action');
  if (!action) return;
  url.searchParams.delete('action');
  history.replaceState(null, '', url.pathname + url.search + url.hash);
  if (!store.getState().onboardingCompleted) return;
  if (action === 'revenu') actions.addOperation('revenue');
  else if (action === 'retrait') { actions.goTab('retraits'); actions.addOperation('withdrawal'); }
  else if (action === 'calendrier') openCalendar();
})();

// Hors-ligne (PWA). Ignoré si non disponible (ex. aperçu).
if ('serviceWorker' in navigator && location.protocol === 'https:') {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}
