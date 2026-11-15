# Nelyqor — V1

Application mobile de suivi d’objectif financier : revenus (avec cotisations), virements, retraits, objectif du jour et de la semaine, projection progressive, offre Nelyqor+.

Code **sans dépendance** (HTML + CSS + JavaScript modules). Pas d’étape de build : ce dossier *est* l’application. On l’emballe ensuite avec **Capacitor** pour le Play Store et l’App Store.

## Lancer en local

```bash
cd nelyqor
python3 -m http.server 8765
# puis ouvrir http://localhost:8765
```

Pour tester sur ton téléphone sur le même Wi-Fi : `http://<ip-de-ton-ordi>:8765`.
Pour revenir au premier lancement : Profil → Informations → « Effacer toutes mes données ».

## Tests

```bash
node --test tests/calc.test.mjs        # 21 tests du moteur de calcul
python3 -m http.server 8765 &          # serveur local
pip install playwright && playwright install chromium
python3 tests/e2e.py                   # 120 vérifications : parcours complet + responsive 320/375/390/430
```

> Dans `tests/e2e.py`, le chemin `executable_path="/usr/bin/google-chrome"` est à adapter (ou à supprimer pour utiliser le Chromium de Playwright).

## Structure

```
index.html                 point d’entrée
css/app.css                design system (couleurs, cartes, boutons, nav, safe areas)
js/app.js                  navigation 5 onglets, rendu, fond du jour
js/core/money.js           montants en CENTIMES, taux en points de base (21 % = 2100), formats FR
js/core/dates.js           dates locales "YYYY-MM-DD", semaine lundi → dimanche
js/core/calc.js            ⭐ SOURCE UNIQUE DES CALCULS (solde, objectif jour/semaine, projection…)
js/core/store.js           état central + persistance locale (à remplacer par un backend plus tard)
js/content/quotes.js       421 phrases quotidiennes (266 activité, 155 repos)
js/content/backgrounds.js  8 fonds abstraits
js/ui/components.js        composants réutilisables (Logo, cartes, champs, feuilles, graphique…)
js/ui/forms.js             ajout revenu / virement / retrait, détail, historique
js/ui/premium.js           Nelyqor+ : offre, analyses, simulateur, export CSV
js/ui/onboarding.js        5 étapes + choix Gratuit / Nelyqor+
js/ui/pages/*.js           Objectif, Revenus / Virements / Retraits, Profil
assets/logo-symbol.png     symbole du logo (remplacer ce fichier = logo changé partout)
assets/icons/              icônes d’application (1024, 512, 192, 180)
```

## Règles de calcul (résumé)

- **Solde objectif** = fonds initiaux + revenus **nets** + virements − retraits.
- **Revenu** : cotisations = brut × taux (figées à l’enregistrement), net = brut − cotisations.
- **Objectif du jour (net)** = restant au début de la semaine ÷ jours d’activité restants jusqu’à la date cible ; **brut** = net ÷ (1 − taux).
- **Objectif de la semaine** = objectif brut du jour × jours d’activité de la semaine. Il ne baisse pas quand on ajoute un revenu. Il est recalculé si le planning, une journée de repos, l’objectif, la date ou le taux changent.
- **% semaine** = réalisé brut ÷ objectif semaine, **non plafonné** (la barre l’est à 100 %).
- **Projection** : rythme utilisé = rythme nécessaire × (1 − confiance) + rythme observé × confiance, avec confiance = min(1, jours observés ÷ 20). Le rythme observé ne prend que les revenus nets, à leur date de réalisation, sur les 30 derniers jours d’activité observés.

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
4. **Stockage** : dans l’app native, iOS peut vider le `localStorage` d’une WebView en cas de manque de place. Avant la sortie, passer `readPersisted` / `writePersisted` sur `@capacitor/preferences` (2 fonctions à modifier dans `store.js`).
5. **Export CSV** : en natif, utiliser `@capacitor/filesystem` + `@capacitor/share` dans `exportCSV()`.
6. **Icône** : `assets/icons/icon-1024.png` sert de base (outil `@capacitor/assets` pour générer toutes les tailles).
