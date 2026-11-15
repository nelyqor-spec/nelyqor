// Test de bout en bout de la synchronisation : deux « appareils » (deux navigateurs isolés)
// et un faux serveur Supabase en mémoire (dans ce processus Node).
// Lancer : python3 -m http.server 8765 &  puis  node tests/sync_e2e.mjs
import { createRequire } from 'node:module';

const { chromium } = createRequire(import.meta.url)('playwright');

const URL = process.env.NELYQOR_URL || 'http://localhost:8765/index.html';
let remote = null; // la « ligne » nelyqor_states du compte
const writes = [];
let failures = 0;
const check = (name, cond) => { console.log(`${cond ? 'OK  ' : 'FAIL'} ${name}`); if (!cond) failures++; };

const FAKE_SUPABASE = () => {
  const listeners = [];
  const getSession = () => { try { return JSON.parse(localStorage.getItem('fake-session')); } catch { return null; } };
  const setSession = (s, event) => {
    if (s) localStorage.setItem('fake-session', JSON.stringify(s)); else localStorage.removeItem('fake-session');
    listeners.forEach((fn) => fn(event, s));
  };
  const user = (email) => ({ id: '11111111-1111-4111-8111-111111111111', email, app_metadata: { provider: 'email' } });
  const client = {
    auth: {
      onAuthStateChange: (fn) => { listeners.push(fn); return { data: { subscription: { unsubscribe() {} } } }; },
      getSession: async () => ({ data: { session: getSession() } }),
      signInWithPassword: async ({ email, password }) => {
        if (password !== 'motdepasse123') return { error: { message: 'Invalid login credentials' } };
        setSession({ user: user(email) }, 'SIGNED_IN'); return { error: null };
      },
      signUp: async ({ email }) => { const s = { user: user(email) }; setSession(s, 'SIGNED_IN'); return { data: { session: s }, error: null }; },
      signOut: async () => { setSession(null, 'SIGNED_OUT'); return { error: null }; },
      signInWithOAuth: async () => ({ error: null }),
      resetPasswordForEmail: async () => ({ error: null }),
      updateUser: async () => ({ error: null }),
    },
    from: () => ({
      select: () => ({ eq: () => ({ maybeSingle: async () => { const d = await window.__remoteGet(); return { data: d ? { data: d } : null, error: null }; } }) }),
      upsert: async (row) => { await window.__remoteSet(row.data); return { error: null }; },
    }),
    rpc: async () => { await window.__remoteSet(null); return { error: null }; },
    channel: () => ({ on() { return this; }, subscribe() { return this; } }),
    removeChannel: () => {},
  };
  window.supabase = { createClient: () => client };
};

async function device(browser, name) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  await ctx.exposeFunction('__remoteGet', () => remote);
  await ctx.exposeFunction('__remoteSet', (d) => { remote = d; writes.push(name); });
  await ctx.addInitScript(FAKE_SUPABASE);
  await ctx.route('**/js/config.js', (r) => r.fulfill({ contentType: 'text/javascript', body: "export const SUPABASE_URL = 'https://test-projet.supabase.co'; export const SUPABASE_ANON_KEY = 'cle-publique-de-test-0123456789';" }));
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(URL);
  return { page, errors };
}

const stateOf = (page) => page.evaluate(() => JSON.parse(localStorage.getItem('nelyqor:v1')));
const sync = (page) => page.evaluate(async () => (await import('/js/core/cloud.js')).syncNow());

const browser = await chromium.launch();

// --- Appareil A : utilise l'app, puis crée un compte ---------------------------
const A = await device(browser, 'A');
await A.page.evaluate(async () => {
  const s = await import('/js/core/store.js');
  const d = await import('/js/core/dates.js');
  const t = d.todayKey();
  s.completeOnboarding({ goalAmount: 1000000, targetDate: d.addDays(t, 200), initialSavings: 0, hasContributions: false, contributionRate: 0, contributionFrequency: 'quarterly', plannedActivityDays: [0, 1, 2, 3, 4], plan: 'trial' });
  s.addRevenue({ grossAmount: 12345, date: t, label: 'Revenu A' });
  s.addCategory('Essence');
});
await A.page.locator('.nav-item').nth(4).click();
await A.page.locator('.menu-item').first().click();
await A.page.getByRole('button', { name: 'Créer mon compte' }).first().click();
await A.page.fill('#auth-email', 'test@exemple.fr');
await A.page.fill('#auth-pw', 'court');
await A.page.locator('.sheet button[type=submit]').click();
check('mot de passe trop faible refusé', await A.page.locator('.sheet .field-error:not([hidden])').count() > 0);
await A.page.fill('#auth-pw', 'motdepasse123');
await A.page.locator('.sheet button[type=submit]').click();
await A.page.waitForTimeout(800);
check('A : connecté', (await A.page.textContent('#main')).includes('test@exemple.fr'));
check('A : données envoyées au cloud', remote && remote.revenues.length === 1 && remote.revenues[0].label === 'Revenu A');
check('A : dossier envoyé', remote && remote.categories.length === 1);

// --- Appareil B : nouvel appareil, se connecte dès l'accueil ------------------
const B = await device(browser, 'B');
await B.page.waitForSelector('#ob-goal');
await B.page.getByRole('button', { name: 'Me connecter et récupérer mes données' }).click();
await B.page.fill('#auth-email', 'test@exemple.fr');
await B.page.fill('#auth-pw', 'mauvais-mdp-1');
await B.page.locator('.sheet button[type=submit]').click();
await B.page.waitForTimeout(300);
check('B : mauvais mot de passe → message clair', (await B.page.textContent('.sheet')).includes('E-mail ou mot de passe incorrect'));
await B.page.fill('#auth-pw', 'motdepasse123');
await B.page.locator('.sheet button[type=submit]').click();
await B.page.waitForTimeout(1000);
let sb = await stateOf(B.page);
check('B : données récupérées (onboarding sauté)', sb.onboardingCompleted && sb.revenues.length === 1);
check('B : écran Objectif affiché', await B.page.locator('.nav').count() === 1);

// --- B supprime le revenu A et en ajoute un ; A reçoit les changements --------
await B.page.evaluate(async () => {
  const s = await import('/js/core/store.js');
  const d = await import('/js/core/dates.js');
  const st = s.getState();
  s.deleteOperation('revenue', st.revenues[0].id);
  s.addRevenue({ grossAmount: 5000, date: d.todayKey(), label: 'Revenu B' });
  s.updatePreferences({ accentColor: '#A78BFA' });
});
await B.page.waitForTimeout(2200); // envoi automatique après 1,5 s
check('B : modifications envoyées', remote.revenues.length === 1 && remote.revenues[0].label === 'Revenu B');

// Pendant ce temps, A a ajouté un retrait hors-ligne (pas encore synchronisé).
await A.page.evaluate(async () => {
  const s = await import('/js/core/store.js');
  const d = await import('/js/core/dates.js');
  s.addWithdrawal({ amount: 2000, date: d.todayKey(), label: 'Retrait A' });
});
await sync(A.page);
const sa = await stateOf(A.page);
check('A : suppression faite sur B appliquée', !sa.revenues.some((r) => r.label === 'Revenu A'));
check('A : revenu de B reçu', sa.revenues.some((r) => r.label === 'Revenu B'));
check('A : son retrait conservé (fusion, rien perdu)', sa.withdrawals.some((w) => w.label === 'Retrait A'));
check('A : couleur choisie sur B appliquée', sa.preferences.accentColor === '#A78BFA');
await sync(B.page);
sb = await stateOf(B.page);
check('B : retrait de A reçu', sb.withdrawals.some((w) => w.label === 'Retrait A'));

// --- Données piégées venant du cloud : nettoyées, l'app ne plante pas ---------
remote = { ...remote, revenues: [...remote.revenues, { id: '<img src=x onerror=alert(1)>', date: 'x', grossAmount: -5 }, { id: 'ok-1', date: '2026-01-01', grossAmount: 100, contributionAmount: 0, label: '<script>alert(1)</script>'.repeat(5), createdAt: '2026-01-01T00:00:00Z' }], profile: { avatar: 'javascript:alert(1)' } };
await sync(A.page);
const sa2 = await stateOf(A.page);
check('données invalides rejetées', !sa2.revenues.some((r) => r.id.includes('<')) && sa2.profile.avatar === null);
check('libellé tronqué à 40 caractères', sa2.revenues.find((r) => r.id === 'ok-1').label.length <= 40);
await A.page.locator('.nav-item').nth(1).click();
check('libellé affiché comme texte (pas de HTML injecté)', await A.page.locator('#main script').count() === 0);

// --- Déconnexion + suppression du compte --------------------------------------
await B.page.evaluate(async () => (await import('/js/core/cloud.js')).deleteAccount());
check('suppression du compte : données cloud effacées', remote === null);

check('aucune erreur JavaScript (A)', A.errors.length === 0);
check('aucune erreur JavaScript (B)', B.errors.length === 0);
if (A.errors.length || B.errors.length) console.log(A.errors, B.errors);
await browser.close();
console.log(failures ? `\n${failures} échec(s)` : '\nSynchronisation : tous les tests passent');
process.exit(failures ? 1 : 0);
