// Nelyqor — écrans du compte : connexion Apple / Google / e-mail, synchronisation, suppression.
import { h, icon } from './dom.js';
import { openSheet, Card, Button, Badge, Field, Notice, Row, toast, confirmDialog } from './components.js';
import * as cloud from '../core/cloud.js';
import * as store from '../core/store.js';

const PROVIDER_LABEL = { apple: 'Apple', google: 'Google', email: 'E-mail' };
const EMAIL_RE = /^[^\s@]{1,64}@[^\s@]{1,255}\.[^\s@]{2,}$/;
export const PASSWORD_MIN = 10;

const APPLE_SVG = '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M16.37 12.6c-.02-2.2 1.8-3.26 1.88-3.31-1.03-1.5-2.62-1.7-3.19-1.73-1.36-.14-2.65.8-3.34.8-.69 0-1.75-.78-2.88-.76-1.48.02-2.85.86-3.61 2.19-1.54 2.67-.39 6.62 1.11 8.79.73 1.06 1.6 2.25 2.75 2.21 1.1-.04 1.52-.71 2.85-.71 1.33 0 1.71.71 2.88.69 1.19-.02 1.94-1.08 2.66-2.15.84-1.23 1.19-2.42 1.21-2.48-.03-.01-2.32-.89-2.34-3.54zM14.17 6.13c.61-.74 1.02-1.76.91-2.78-.88.04-1.94.59-2.57 1.32-.56.65-1.06 1.69-.92 2.69.98.08 1.97-.5 2.58-1.23z"/></svg>';
const GOOGLE_SVG = '<svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>';

function svgSpan(markup) {
  const s = document.createElement('span');
  s.style.display = 'inline-grid';
  s.innerHTML = markup; // contenu statique, défini ci-dessus
  return s;
}

export function accountSubtitle() {
  if (!cloud.isConfigured()) return 'Bientôt disponible';
  const st = cloud.getStatus();
  if (!st.user) return 'Crée un compte pour retrouver tes données partout';
  return `${st.user.email || 'Connecté'} · ${PROVIDER_LABEL[st.provider] || 'Compte'}`;
}

function passwordProblem(pw) {
  if (pw.length < PASSWORD_MIN) return `Au moins ${PASSWORD_MIN} caractères.`;
  if (!/[A-Za-z]/.test(pw) || !/\d/.test(pw)) return 'Mélange des lettres et des chiffres.';
  if (pw.length > 72) return '72 caractères maximum.';
  return null;
}

function passwordInput(id, autocomplete) {
  const input = h('input', { id, type: 'password', autocomplete, maxlength: 72, required: true, spellcheck: 'false', autocapitalize: 'off' });
  const toggle = h('button', { type: 'button', class: 'input-action', 'aria-label': 'Afficher le mot de passe' }, 'Voir');
  toggle.addEventListener('click', () => {
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    toggle.textContent = show ? 'Masquer' : 'Voir';
    toggle.setAttribute('aria-label', show ? 'Masquer le mot de passe' : 'Afficher le mot de passe');
  });
  return { input, wrap: h('div', { class: 'input-wrap' }, input, toggle) };
}

async function busy(btn, fn) {
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Un instant…';
  try { return await fn(); } finally { btn.disabled = false; btn.textContent = label; }
}

// ---------------------------------------------------------------------------
// Feuille de connexion / inscription
// ---------------------------------------------------------------------------
export function openAuthSheet({ mode = 'signin' } = {}) {
  if (!cloud.isConfigured()) {
    toast('La synchronisation n’est pas encore activée.');
    return;
  }
  openSheet({
    title: mode === 'signup' ? 'Créer mon compte' : 'Me connecter',
    build: (close) => {
      const msg = h('div', { 'aria-live': 'polite' });
      const showError = (text) => msg.replaceChildren(Notice('danger', null, text));

      const provider = (p) => {
        const b = h('button', { type: 'button', class: `btn btn-provider ${p}` }, svgSpan(p === 'apple' ? APPLE_SVG : GOOGLE_SVG), `Continuer avec ${PROVIDER_LABEL[p]}`);
        b.addEventListener('click', () => busy(b, async () => {
          try { await cloud.signInWithProvider(p); } catch (e) { showError(e.message); }
        }));
        return b;
      };

      const email = h('input', { id: 'auth-email', type: 'email', autocomplete: 'email', inputmode: 'email', maxlength: 254, required: true, spellcheck: 'false', autocapitalize: 'off', placeholder: 'toi@exemple.fr' });
      const emailF = Field('Adresse e-mail', h('div', { class: 'input-wrap' }, email), { forId: 'auth-email' });
      const pw = passwordInput('auth-pw', mode === 'signup' ? 'new-password' : 'current-password');
      const pwF = Field('Mot de passe', pw.wrap, { forId: 'auth-pw', hint: mode === 'signup' ? `Au moins ${PASSWORD_MIN} caractères, avec lettres et chiffres.` : null });
      const submitBtn = Button(mode === 'signup' ? 'Créer mon compte' : 'Me connecter', null, { type: 'submit' });

      const submit = () => busy(submitBtn, async () => {
        msg.replaceChildren();
        emailF.setError(''); pwF.setError('');
        const e = email.value.trim().toLowerCase();
        const p = pw.input.value;
        let ok = true;
        if (!EMAIL_RE.test(e)) { emailF.setError('Indique une adresse e-mail valide.'); ok = false; }
        const pb = mode === 'signup' ? passwordProblem(p) : (p ? null : 'Indique ton mot de passe.');
        if (pb) { pwF.setError(pb); ok = false; }
        if (!ok) return;
        try {
          if (mode === 'signup') {
            const r = await cloud.signUpWithEmail(e, p);
            if (r === 'check-email') {
              msg.replaceChildren(Notice('success', 'Vérifie ta boîte mail', `Un lien de confirmation a été envoyé à ${e}. Ouvre-le pour activer ton compte.`));
              return;
            }
          } else {
            await cloud.signInWithEmail(e, p);
          }
          close();
          toast('Connecté · synchronisation en cours');
        } catch (err) {
          showError(err.message);
        }
      });

      const forgot = h('button', { type: 'button', class: 'btn-link', style: { alignSelf: 'flex-start' } }, 'Mot de passe oublié ?');
      forgot.addEventListener('click', async () => {
        const e = email.value.trim().toLowerCase();
        if (!EMAIL_RE.test(e)) { emailF.setError('Indique d’abord ton adresse e-mail.'); email.focus(); return; }
        try {
          await cloud.resetPassword(e);
          msg.replaceChildren(Notice('success', 'E-mail envoyé', 'Si un compte existe avec cette adresse, tu vas recevoir un lien pour choisir un nouveau mot de passe.'));
        } catch (err) { showError(err.message); }
      });

      const other = h('button', { type: 'button', class: 'btn-link' }, mode === 'signup' ? 'J’ai déjà un compte' : 'Créer un compte');
      other.addEventListener('click', () => { close(); openAuthSheet({ mode: mode === 'signup' ? 'signin' : 'signup' }); });

      return [
        h('p', { class: 'muted' }, 'Ton compte garde tes données en sécurité et les synchronise sur tous tes appareils.'),
        h('div', { class: 'btn-stack' }, provider('apple'), provider('google')),
        h('div', { class: 'or' }, h('span', null, 'ou avec ton e-mail')),
        h('form', { novalidate: true, class: 'form-stack', onSubmit: (ev) => { ev.preventDefault(); submit(); } },
          emailF.el, pwF.el, mode === 'signin' && forgot, msg, submitBtn),
        h('div', { style: { textAlign: 'center' } }, other),
      ];
    },
  });
}

/** Nouveau mot de passe après un lien « mot de passe oublié ». */
export function openNewPasswordSheet() {
  openSheet({
    title: 'Nouveau mot de passe',
    onClose: () => cloud.dismissRecovery(),
    build: (close) => {
      const pw = passwordInput('np-pw', 'new-password');
      const f = Field('Choisis un nouveau mot de passe', pw.wrap, { forId: 'np-pw', hint: `Au moins ${PASSWORD_MIN} caractères, avec lettres et chiffres.` });
      const btn = Button('Enregistrer', null, { type: 'submit' });
      return h('form', {
        novalidate: true, class: 'form-stack',
        onSubmit: (ev) => {
          ev.preventDefault();
          busy(btn, async () => {
            const pb = passwordProblem(pw.input.value);
            if (pb) return f.setError(pb);
            try { await cloud.updatePassword(pw.input.value); close(); toast('Mot de passe modifié'); } catch (e) { f.setError(e.message); }
          });
        },
      }, f.el, btn);
    },
  });
}

// ---------------------------------------------------------------------------
// Rubrique Profil → Compte et synchronisation
// ---------------------------------------------------------------------------
export function AccountSection() {
  if (!cloud.isConfigured()) {
    return Card([
      Notice('info', 'Bientôt disponible', 'La création de compte et la synchronisation entre appareils arrivent très vite. En attendant, tes données restent en sécurité sur cet appareil.'),
    ]);
  }
  const st = cloud.getStatus();
  if (!st.ready) return Card([h('p', { class: 'muted' }, 'Chargement…')]);

  if (!st.user) {
    return [
      Card([
        h('div', { class: 'card-head' }, h('h2', { class: 'card-title' }, 'Garde tes données partout'), icon('shield', 22)),
        h('ul', { class: 'checklist' },
          ...['Retrouve Nelyqor sur ton téléphone, ta tablette et ton ordinateur', 'Change d’appareil sans rien perdre', 'Connexion avec Apple, Google ou ton e-mail', 'Chaque compte n’accède qu’à ses propres données']
            .map((t) => h('li', null, icon('check', 18), h('span', null, t)))),
        h('div', { class: 'btn-stack' },
          Button('Créer mon compte', () => openAuthSheet({ mode: 'signup' }), { iconName: 'user' }),
          Button('J’ai déjà un compte', () => openAuthSheet({ mode: 'signin' }), { variant: 'secondary' })),
        h('p', { class: 'field-hint' }, 'Tes données actuelles seront ajoutées à ton compte : rien n’est perdu.'),
      ]),
    ];
  }

  const last = st.lastSyncAt ? new Date(st.lastSyncAt).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' }) : '—';
  const syncBtn = Button('Synchroniser maintenant', null, { variant: 'secondary', iconName: 'refresh' });
  syncBtn.addEventListener('click', () => busy(syncBtn, async () => { if (await cloud.syncNow()) toast('Synchronisé'); else toast(cloud.getStatus().error || 'Synchronisation impossible'); }));

  const logout = async () => {
    const yes = await confirmDialog({ title: 'Se déconnecter ?', message: 'Tes données restent sur cet appareil et sur ton compte.', confirmLabel: 'Se déconnecter' });
    if (!yes) return;
    await cloud.signOut();
    toast('Déconnecté');
  };
  const logoutAndWipe = async () => {
    const yes = await confirmDialog({
      title: 'Se déconnecter et vider cet appareil ?',
      message: 'Pratique sur un appareil partagé : les données sont retirées de cet appareil, mais restent sur ton compte.',
      confirmLabel: 'Déconnecter et vider', danger: true,
    });
    if (!yes) return;
    await cloud.signOut();
    store.resetAll();
  };
  const remove = async () => {
    const yes = await confirmDialog({
      title: 'Supprimer définitivement ton compte ?',
      message: 'Ton compte et toutes tes données Nelyqor seront supprimés du cloud et de cet appareil. Cette action est irréversible. Pense à exporter tes données avant (Nelyqor+).',
      confirmLabel: 'Supprimer mon compte', danger: true,
    });
    if (!yes) return;
    try {
      await cloud.deleteAccount();
      store.resetAll();
      toast('Compte supprimé');
    } catch (e) { toast(e.message); }
  };

  return [
    Card([
      h('div', { class: 'card-head' },
        h('div', { style: { minWidth: 0 } }, h('p', { class: 'eyebrow' }, 'Connecté'), h('p', { class: 'wrap', style: { fontWeight: '650', marginTop: '4px' } }, st.user.email || 'Compte Nelyqor')),
        Badge(PROVIDER_LABEL[st.provider] || 'Compte', 'teal')),
      h('div', { class: 'rows' },
        Row('Dernière synchronisation', st.syncing ? 'En cours…' : last),
        Row('État', st.error ? st.error : 'À jour sur tous tes appareils', { valueClass: st.error ? 'danger-text' : 'teal' })),
      syncBtn,
    ]),
    Card([
      h('h2', { class: 'card-title' }, 'Sécurité'),
      h('div', { class: 'btn-stack' },
        st.provider === 'email' && Button('Changer mon mot de passe', openNewPasswordSheet, { variant: 'secondary', iconName: 'shield' }),
        Button('Se déconnecter', logout, { variant: 'secondary', iconName: 'logout' }),
        Button('Se déconnecter et vider cet appareil', logoutAndWipe, { variant: 'secondary' })),
    ]),
    Card([
      h('h2', { class: 'card-title' }, 'Supprimer mon compte'),
      h('p', { class: 'muted' }, 'Supprime ton compte et toutes les données associées, sur le cloud comme ici.'),
      Button('Supprimer mon compte', remove, { variant: 'danger', iconName: 'trash' }),
    ]),
  ];
}
