# Nelyqor — audit de sécurité (version 1.2.0)

Audit du code de l'application (HTML / CSS / JavaScript), de la configuration Netlify et de
l'intégration Supabase. Les corrections listées « ✅ corrigé » sont incluses dans cette version.
Les points « ⚠️ à faire » demandent une action de ta part (réglages Supabase / stores) ou du
développement côté serveur avant la publication.

## 1. Synthèse

| Gravité | Constat | État |
|---|---|---|
| Élevée | L'abonnement Nelyqor+ est décidé côté appareil : n'importe qui peut s'activer Nelyqor+ en modifiant le stockage local. | ⚠️ à faire avant la vente (§ 6) |
| Élevée | Aucune politique de sécurité du contenu (CSP) : une faille d'injection aurait pu exécuter du code et, avec les comptes, voler la session. | ✅ corrigé |
| Moyenne | Export CSV : un libellé commençant par `=`, `+`, `-` ou `@` devenait une formule dans Excel / Sheets (injection de formules). | ✅ corrigé |
| Moyenne | Le service worker mettait en cache toute réponse, y compris les adresses avec paramètres (ex. `?code=` renvoyé après une connexion). | ✅ corrigé |
| Moyenne | Données relues du stockage sans validation : des données corrompues ou modifiées pouvaient faire planter l'app. Avec la synchro, ces données viennent aussi du réseau. | ✅ corrigé |
| Moyenne | Pas d'en-têtes HTTP de sécurité : l'app pouvait être affichée dans le cadre d'un autre site (clickjacking), pas de HSTS. | ✅ corrigé (`_headers`) |
| Moyenne | Les fichiers de développement (tests, README) étaient publiés sur le site. | ✅ corrigé (`netlify.toml`) |
| Faible | Le mini-moteur d'affichage (`h()`) acceptait une option `html` (injection HTML possible si elle avait été utilisée un jour). | ✅ supprimée |
| Faible | Les textes du graphique étaient insérés sans échappement (valeurs internes, mais par précaution). | ✅ échappés |
| Info | Photo de profil : les photos de téléphone contiennent souvent la position GPS (EXIF). | ✅ photo ré-encodée, métadonnées supprimées |

## 2. Ce qui a été vérifié et corrigé dans le code

**Injection de code (XSS).** Tout texte saisi par l'utilisateur (libellés, noms de dossiers,
pseudo, e-mail) est affiché uniquement par des nœuds texte (`textContent`), jamais en HTML.
Les seuls `innerHTML` restants portent sur des icônes SVG statiques écrites dans le code et
sur le graphique, dont les textes sont désormais échappés. Le test `tests/sync_e2e.mjs` injecte
des libellés piégés (`<script>`, `<img onerror>`) depuis le « cloud » et vérifie qu'aucun n'est
interprété.

**Politique de sécurité du contenu (CSP)**, dans `index.html` et en en-tête HTTP (`_headers`) :
- scripts : uniquement les fichiers de l'app (`script-src 'self'`) : aucun script externe,
  aucun `eval`, aucun script en ligne ;
- connexions réseau : uniquement l'app et Supabase (`*.supabase.co`) ;
- aucun cadre (`frame-ancestors 'none'`), aucun plugin, `base-uri 'none'`.

**Bibliothèque Supabase** : version exacte (2.117.3) embarquée dans `js/vendor/`, pas chargée
depuis un CDN, et vérifiée par une empreinte d'intégrité SRI (`sha384`) : le navigateur refuse
le fichier s'il a été modifié. Elle n'est chargée que si la synchronisation est configurée.

**Validation des données** (`sanitize()` dans `js/core/store.js`) : appliquée au démarrage, à
chaque fusion avec le cloud et à chaque écriture. Montants entiers positifs et bornés, dates
réelles, identifiants au format strict, textes sans caractères de contrôle et tronqués (40
caractères), couleur au format `#RRGGBB`, photo limitée à une image JPEG/PNG/WebP de 400 Ko,
statut d'abonnement parmi les valeurs connues. Une donnée invalide est ignorée, l'app ne plante pas.

**Export CSV** : neutralisation des formules (apostrophe devant `= + - @ tab CR`) et guillemets
doublés.

**Service worker** : seules les réponses réussies, du même domaine et sans paramètres sont mises
en cache ; les retours de connexion (`?code=…`) ne sont jamais stockés.

**Photo de profil** : recadrée et redessinée dans un canvas (256 px, JPEG). Le fichier d'origine
n'est jamais conservé : un fichier piégé ne peut pas passer, et les métadonnées (lieu, appareil,
date) disparaissent. Taille d'entrée limitée à 15 Mo.

**Comptes** (`js/core/cloud.js`, `js/ui/account.js`) :
- connexion par Apple, Google ou e-mail + mot de passe, avec le flux PKCE (le plus sûr pour
  une app sans serveur) ;
- mot de passe : 10 caractères minimum, lettres et chiffres, masqué par défaut ;
- « mot de passe oublié » : la réponse est la même que l'adresse existe ou non (on ne révèle
  pas qui a un compte) ;
- messages d'erreur traduits et sans détail technique ;
- l'adresse est nettoyée après le retour de connexion ;
- suppression du compte depuis l'app (exigée par l'App Store) : efface le compte et toutes
  ses données ;
- « Se déconnecter et vider cet appareil » pour les appareils partagés ;
- « Effacer toutes mes données » déconnecte d'abord le compte (sinon la synchro ferait revenir
  les données).

## 3. Base de données Supabase (`supabase/schema.sql`)

- **Row Level Security activée et forcée** : chaque utilisateur ne peut lire, créer, modifier
  ou supprimer **que sa propre ligne** (`auth.uid() = user_id`). Le rôle `anon` (visiteur non
  connecté) n'a aucun droit sur la table.
- La clé « anon / publishable » mise dans `js/config.js` est publique par conception : seule,
  elle ne donne accès à rien. **Ne jamais mettre la clé `service_role` / `secret` dans l'app.**
- Taille d'une ligne limitée (5 Mo) et type JSON vérifié par la base.
- `updated_at` est posé par la base (impossible à falsifier depuis l'app).
- `delete_my_account()` : fonction `security definer` avec `search_path` vide, réservée aux
  utilisateurs connectés, qui ne peut supprimer que le compte de l'appelant.
- Le temps réel ne diffuse que les lignes autorisées par RLS.

## 4. ⚠️ Réglages à faire dans le tableau de bord Supabase

1. **Région** : crée le projet en Europe (ex. Paris `eu-west-3` ou Francfort) : données
   personnelles et financières, RGPD.
2. **Authentication → Providers → Email** : activer « Confirm email » ; longueur minimale du mot
   de passe : 10 ; exigences « lettres et chiffres » ; activer **Leaked password protection**
   (refus des mots de passe déjà piratés, offre payante).
3. **Authentication → URL Configuration** : *Site URL* = l'adresse de l'app ; *Redirect URLs* :
   uniquement l'adresse de l'app (et le schéma de l'app native, ex. `com.nelyqor.app://`). Pas de joker.
4. **Authentication → Rate limits** : laisser les limites par défaut (ou les baisser) ; activer
   **CAPTCHA** (Cloudflare Turnstile ou hCaptcha) dès l'ouverture publique, contre les inscriptions
   automatiques. Il faudra alors l'ajouter au formulaire d'inscription.
5. **Apple / Google** : renseigner les identifiants OAuth dans Providers ; côté Google, ne déclarer
   que les URL de redirection Supabase.
6. Activer la **MFA** pour ton propre compte d'administration Supabase et pour GitHub / Netlify.
7. Pas de clé `service_role` dans Netlify, GitHub ou l'app.

## 5. Application native (Capacitor)

- Stocker la session dans le trousseau (iOS Keychain / Android Keystore) plutôt que dans
  `localStorage` : `@capacitor/preferences` + un plugin de stockage sécurisé.
- Connexion Apple / Google en natif : utiliser les SDK natifs (Sign in with Apple, Google Sign-In)
  puis `supabase.auth.signInWithIdToken`, et un lien profond (`com.nelyqor.app://`) pour les e-mails.
- Ajouter le domaine Supabase exact au CSP si tu utilises un domaine personnalisé.
- Option conseillée : verrouillage de l'app par Face ID / empreinte (données financières).

## 6. ⚠️ Abonnement : à sécuriser avant de vendre

Aujourd'hui l'abonnement est **simulé sur l'appareil** (bouton « (simulation) »). Tout ce qui est
côté appareil peut être modifié par l'utilisateur. Avant la mise en vente :
- vendre via StoreKit / Google Play Billing (le plus simple : **RevenueCat**) ;
- faire valider l'achat **côté serveur** (webhook RevenueCat → fonction Supabase) qui écrit dans
  une table `entitlements` **en lecture seule** pour l'utilisateur (RLS : `select` uniquement) ;
- l'app lit alors son droit Nelyqor+ dans cette table, et non plus dans son propre état ;
- retirer les boutons « (simulation) » et ajouter « Restaurer mes achats ».

Les fonctions Nelyqor+ étant calculées sur l'appareil, un utilisateur très motivé pourra toujours
les débloquer pour lui-même ; l'important est qu'il ne puisse pas se créer un abonnement payé
reconnu par ton serveur.

## 7. Vie privée / RGPD

- La politique de confidentialité doit maintenant mentionner : compte (e-mail), données
  synchronisées chez Supabase (hébergeur, région), finalité, durée de conservation, suppression
  du compte depuis l'app, contact.
- Données minimales : aucun nom réel requis, aucune donnée bancaire, aucun traceur ni outil
  d'analyse dans l'app.
- Droit à l'effacement : bouton « Supprimer mon compte ». Droit à la portabilité : export CSV.

## 8. Tests de sécurité automatisés

- `node --test tests/v11.test.mjs` : validation des données piégées, injection CSV, fusion.
- `node tests/sync_e2e.mjs` : deux appareils + faux serveur ; données piégées venant du cloud,
  rejet des mots de passe faibles, suppression du compte.
- `python3 tests/e2e.py` : parcours complet et responsive (320 → 430 px).

## 9. Checklist « avant de lancer une application » (25 points)

Légende : ✅ fait · ⚙️ réglage à faire dans Supabase / Netlify · ⚠️ à faire avant la vente · — sans objet pour Nelyqor.

| # | Point | État | Comment c'est couvert dans Nelyqor |
|---|---|---|---|
| **Accès & données** |||
| 1 | Vraie authentification | ✅ | Supabase Auth (Apple, Google, e-mail), flux PKCE, sessions qui expirent et se renouvellent. |
| 2 | Clés d'API cachées | ✅ | Seule la clé publique « anon » est dans l'app (prévue pour ça). Aucune clé secrète dans le code ni dans l'historique Git (vérifié). |
| 3 | Base de données sécurisée | ✅ | RLS activée et forcée : chacun ne lit / écrit que sa ligne ; aucun droit pour les visiteurs. |
| 4 | HTTPS imposé | ✅ | Netlify en HTTPS + en-tête HSTS + `upgrade-insecure-requests`. |
| 5 | Mots de passe hachés | ✅ | Gérés par Supabase (bcrypt) ; l'app ne voit ni ne stocke jamais un mot de passe. |
| **Attaques** |||
| 6 | Validation côté serveur | ✅ | Base : type JSON, taille max 5 Mo, RLS. App : `sanitize()` revalide tout ce qui vient du serveur ou d'un fichier. |
| 7 | Protection contre le XSS | ✅ | Textes affichés en nœuds texte uniquement, CSP stricte sans script en ligne, test automatique avec données piégées. |
| 8 | Activités suspectes surveillées | ⚙️ | Journaux d'authentification Supabase (*Logs → Auth*) ; activer les alertes e-mail du projet. |
| 9 | Routes admin verrouillées | — / ✅ | Aucune page ni route d'administration dans l'app ; l'administration se fait dans le tableau de bord Supabase (protégé par MFA, § 4). |
| 10 | Requêtes paramétrées | ✅ | Aucune requête SQL construite à la main : l'app passe par l'API Supabase (PostgREST), paramétrée par conception. |
| **Configuration** |||
| 11 | Variables d'environnement | ✅ | Pas de secret côté app ; les seules valeurs (URL + clé publique) sont dans `js/config.js`, publiques par conception. |
| 12 | Limite de requêtes | ⚙️ | Limites de Supabase Auth (connexion, inscription, e-mails) actives par défaut ; ajouter un CAPTCHA à l'ouverture (§ 4). |
| 13 | CORS restreint | ✅ | L'app n'a pas d'API à elle. CSP `connect-src` : l'app ne peut appeler que son domaine et Supabase. Côté Supabase, la protection repose sur RLS, pas sur CORS. |
| 14 | En-têtes de sécurité | ✅ | `_headers` : CSP, HSTS, X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy. |
| 15 | Cookies de session sécurisés | — | Aucun cookie : la session est un jeton envoyé dans l'en-tête de chaque requête (protégé par la CSP). En natif : trousseau sécurisé (§ 5). |
| **Abus** |||
| 16 | Protection CSRF | ✅ | Sans cookie de session, une attaque CSRF n'a rien à exploiter ; formulaires limités au domaine (`form-action 'self'`). |
| 17 | Liens de réinitialisation qui expirent | ⚙️ | Supabase : liens à usage unique ; régler *Auth → Email → OTP expiry* à 1 h maximum (3 600 s, valeur par défaut). |
| 18 | Fichiers : type et taille limités | ✅ | Photo : types image uniquement, 15 Mo max, ré-encodée en JPEG 256 px. Sauvegarde : JSON Nelyqor uniquement, 10 Mo max, validée. |
| 19 | Prix et paiements vérifiés | ⚠️ | Abonnement encore simulé : à valider côté serveur avec RevenueCat avant la vente (§ 6). |
| **Production** |||
| 20 | Mode debug désactivé | ✅ | Aucun `console.log`, aucun mode debug ; les erreurs montrées à l'utilisateur ne contiennent aucun détail technique. |
| 21 | Dépendances à jour | ✅ | Une seule dépendance (Supabase 2.117.3, octobre 2026), figée et vérifiée par empreinte SRI. À mettre à jour tous les 2-3 mois. |
| 22 | Paquets inutilisés supprimés | ✅ | Aucun autre paquet : pas de framework, pas de `node_modules` livré. |
| **Fuites** |||
| 23 | Fichiers exposés détectés | ✅ | Tests, schéma SQL, README, SECURITY.md et fichiers marketing bloqués sur le site (`netlify.toml`). Le zip de mise en ligne ne contient que l'app. |
| 24 | Secrets dans l'historique Git | ✅ | Historique scanné (clés API, JWT, clés privées, mots de passe) : rien trouvé. |
| 25 | Clés qui ont fuité révoquées | ✅ | Aucune clé n'a fui. Si une clé secrète Supabase fuitait un jour : *Project Settings → API → Roll* immédiatement. |

**Bilan : 19 points faits, plus le 9 sans risque (pas de route d'administration) ; 3 réglages à cocher dans Supabase (8, 12, 17, voir aussi § 4) ; 1 point bloquant avant la vente (19 : paiement réel) ; 1 sans objet (15 : pas de cookie).**
