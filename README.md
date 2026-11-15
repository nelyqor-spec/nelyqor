# Nelyqor — V1.1

Application mobile de suivi d’objectif financier : revenus (avec cotisations), virements, retraits, objectif du jour et de la semaine, projection progressive, offre Nelyqor+.

**Nouveautés 1.1** : compte et synchronisation entre appareils (Supabase : Apple, Google, e-mail), photo de profil, couleur principale au choix, échéances de cotisations mensuelles ou trimestrielles, revenus exceptionnels, dossiers de dépenses et dépenses récurrentes planifiées (Nelyqor+), « En bref » réservé à Nelyqor+, affichage plein écran sans zoom et adapté aux tablettes et ordinateurs, audit de sécurité (voir [SECURITY.md](SECURITY.md)).

Code **sans dépendance** (HTML + CSS + JavaScript modules). Pas d’étape de build : ce dossier *est* l’application. On l’emballe ensuite avec **Capacitor** pour le Play Store et l’App Store.

## Lancer en local

```bash
cd nelyqor
python3 -m http.server 8765
# puis ouvrir http://localhost:8765
```

Pour tester sur ton téléphone sur le même Wi-Fi : `http://<ip-de-ton-ordi>:8765`.
Pour revenir au premier lancement : Profil → Informations → « Effacer toutes mes données ».

## Activer les comptes et la synchronisation (Supabase)

Sans configuration, l'app marche normalement, les données restant sur l'appareil, et la rubrique Compte affiche « Bientôt disponible ».

1. Crée un projet sur [supabase.com](https://supabase.com), **en Europe**.
2. *SQL Editor* : colle et exécute `supabase/schema.sql` (table, règles de sécurité RLS, temps réel, suppression de compte).
3. *Project Settings → API* : copie *Project URL* et la clé *anon / publishable* dans `js/config.js`. Jamais la clé `service_role`.
4. *Authentication → URL Configuration* : *Site URL* = `https://majestic-kringle-01ea49.netlify.app/index.html`, et la même adresse dans *Redirect URLs*.
5. *Authentication → Providers* :
   - **Email** : activé, « Confirm email » activé, mot de passe ≥ 10 caractères ;
   - **Google** : crée un identifiant OAuth sur Google Cloud Console (type « Application Web »), avec comme URI de redirection celle affichée par Supabase (`https://<projet>.supabase.co/auth/v1/callback`) ;
   - **Apple** : nécessite un compte Apple Developer (Services ID + clé « Sign in with Apple »).
6. Redéploie sur Netlify. Teste : Profil → Compte et synchronisation → Créer mon compte.

Les réglages de sécurité conseillés côté Supabase (CAPTCHA, mots de passe piratés, MFA…) sont listés dans [SECURITY.md](SECURITY.md).

## Tests

```bash
node --test tests/calc.test.mjs tests/v11.test.mjs   # 32 tests du moteur de calcul, validation, fusion, CSV
python3 -m http.server 8765 &                         # serveur local
node tests/sync_e2e.mjs                               # 19 vérifications : synchro entre 2 appareils (faux serveur)
pip install playwright
CHROME_PATH=/chemin/vers/chrome python3 tests/e2e.py   # 123 vérifications : parcours complet + responsive 320/375/390/430
```

## Structure

```
index.html                 point d’entrée
css/app.css                design system (couleurs, cartes, boutons, nav, safe areas)
js/app.js                  navigation 5 onglets, rendu, fond du jour
js/core/money.js           montants en CENTIMES, taux en points de base (21 % = 2100), formats FR
js/core/dates.js           dates locales "YYYY-MM-DD", semaine lundi → dimanche
js/core/calc.js            ⭐ SOURCE UNIQUE DES CALCULS (solde, objectif jour/semaine, projection…)
js/core/store.js           état central, persistance locale, validation stricte (sanitize)
js/core/sync.js            fusion appareil ↔ cloud (union, suppressions, rubrique la plus récente)
js/core/cloud.js           compte Supabase (Apple, Google, e-mail) + synchronisation automatique
js/config.js               URL et clé publique Supabase (vides = mode local uniquement)
js/vendor/                 bibliothèque Supabase 2.117.3 (embarquée, vérifiée par SRI)
js/content/quotes.js       421 phrases quotidiennes (266 activité, 155 repos)
js/content/backgrounds.js  8 fonds abstraits
js/ui/components.js        composants réutilisables (Logo, cartes, champs, feuilles, graphique…)
js/ui/forms.js             ajout revenu / virement / retrait, détail, historique
js/ui/premium.js           Nelyqor+ : offre, analyses, simulateur, export CSV
js/ui/expenses.js          Nelyqor+ : dossiers de dépenses, dépenses prévues / récurrentes
js/ui/account.js           écrans de connexion et de gestion du compte
js/ui/theme.js             couleur principale personnalisable
js/ui/image.js             photo de profil (recadrage, réduction, suppression des métadonnées)
js/ui/onboarding.js        5 étapes + choix Gratuit / Nelyqor+
js/ui/pages/*.js           Objectif, Revenus / Virements / Retraits, Profil
assets/logo-symbol.png     symbole du logo (remplacer ce fichier = logo changé partout)
assets/icons/              icônes d’application (1024, 512, 192, 180)
supabase/schema.sql        base de données + règles de sécurité (à exécuter dans Supabase)
_headers                   en-têtes de sécurité HTTP (Netlify)
```

## Règles de calcul (résumé)

- **Solde objectif** = fonds initiaux + revenus **nets** + virements − retraits.
- **Revenu** : cotisations = brut × taux (figées à l’enregistrement), net = brut − cotisations.
- **Objectif du jour (net)** = restant au début de la semaine ÷ jours d’activité restants jusqu’à la date cible ; **brut** = net ÷ (1 − taux).
- **Objectif de la semaine** = objectif brut du jour × jours d’activité de la semaine. Il ne baisse pas quand on ajoute un revenu. Il est recalculé si le planning, une journée de repos, l’objectif, la date ou le taux changent.
- **% semaine** = réalisé brut ÷ objectif semaine, **non plafonné** (la barre l’est à 100 %).
- **Projection** : solde actuel + rythme × jours d’activité à venir − dépenses prévues d’ici là. Rythme utilisé = rythme nécessaire × (1 − confiance) + rythme observé × confiance, avec confiance = min(1, jours observés ÷ 20). Le rythme observé ne prend que les revenus nets **réguliers**, à leur date de réalisation, sur les 30 derniers jours d’activité observés.
- **Virements et revenus exceptionnels** : comptés **une seule fois** dans le solde, jamais extrapolés sur l’avenir (un gros virement n’est pas répété dans la projection).
- **Dépenses prévues** (Nelyqor+) : échéance n = première date + n × fréquence (1 fois, 1, 2, 3, 4, 6 ou 12 mois). Elles sont déduites de la projection et ajoutées au « reste à gagner » de l’objectif du jour ; une fois l’échéance arrivée, « Payée » crée le retrait, « Pas cette fois » la passe.
- **Cotisations** : versement mensuel ou trimestriel. Montant à garder de côté = cotisations du mois / trimestre en cours (+ celles de la période précédente tant que son échéance, fin du mois suivant, n’est pas passée).

## Passer sur le Play Store et l’App Store (Capacitor)

```bash
npm init -y
npm install @capacitor/core @capacitor/cli @capacitor/android @capacitor/ios
npx cap init Nelyqor com.nelyqor.app --web-dir .
npx cap add android
npx cap add ios          # nécessite un Mac avec Xcode
npx cap sync
npx cap open android     # puis Build > Generate Signed Bundle (.aab)
```

Conseil : mets les fichiers de l’app dans un sous-dossier `www/` (et `--web-dir www`) pour ne pas embarquer `node_modules` et les tests.

À prévoir avant la publication :

1. **Abonnements réels** : remplacer `startTrial` / `activatePremium` / `refreshSubscription` dans `js/core/store.js` par StoreKit (Apple) et Google Play Billing. Le plus simple : **RevenueCat** (`@revenuecat/purchases-capacitor`), qui gère l’essai de 7 jours, les deux stores et la restauration des achats. Retirer alors les boutons « (simulation) » de `js/ui/premium.js`.
2. **Bouton « Restaurer mes achats »** : Apple l’exige pour les abonnements, à ajouter avec la vraie facturation.
3. **Politique de confidentialité** : obligatoire sur les deux stores (URL publique), même sans compte. Elle peut être courte, puisque les données restent sur l’appareil. Ajouter le lien dans À propos une fois la page en ligne.
4. **Stockage** : dans l’app native, iOS peut vider le `localStorage` d’une WebView en cas de manque de place. Avant la sortie, passer `readPersisted` / `writePersisted` sur `@capacitor/preferences` (2 fonctions à modifier dans `store.js`). Avec un compte, les données sont de toute façon récupérées depuis le cloud.
7. **Connexion Apple / Google en natif** : utiliser les SDK natifs puis `signInWithIdToken` (voir SECURITY.md § 5).
5. **Export CSV** : en natif, utiliser `@capacitor/filesystem` + `@capacitor/share` dans `exportCSV()`.
6. **Icône** : `assets/icons/icon-1024.png` sert de base (outil `@capacitor/assets` pour générer toutes les tailles).
