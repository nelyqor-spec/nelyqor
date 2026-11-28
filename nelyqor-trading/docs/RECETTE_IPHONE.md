# Recette sur un VRAI iPhone (avec le propriétaire)

But : vérifier à la main, sur l'iPhone du propriétaire et dans Safari, ce que les tests automatiques ne peuvent
pas prouver (Safari réel, encoche, écran d'accueil, gestes). Durée : environ 30 minutes.
Tout se fait en **SIMULATION** : aucune clé Bitvavo, aucun ordre réel. Ne jamais activer le mode réel pendant la recette.

Pour chaque étape : cocher ✅ si le résultat attendu est observé, sinon noter ❌ et ce qui a été vu (une capture
d'écran aide : bouton latéral + volume haut).

Préparation (par la personne qui installe) : le serveur tourne en simulation, `scripts/deploy_check.sh` est vert,
l'adresse HTTPS du robot est connue, le mot de passe et la phrase de signature sont prêts (jamais les mêmes).

## 1. Installer l'application
1. Ouvrir **Safari** (pas Chrome) et aller à l'adresse du robot. → Un écran « Nelyqor » demande le mot de passe.
2. Toucher le bouton **Partager** (carré avec une flèche), puis **« Sur l'écran d'accueil »**, puis **Ajouter**.
   → Une icône « Nelyqor » apparaît sur l'écran d'accueil (pas une capture floue de la page).
3. Ouvrir l'application depuis cette icône. → Elle s'ouvre **en plein écran**, sans barre d'adresse Safari.
4. Regarder le haut de l'écran. → L'heure et la batterie restent lisibles ; aucun texte n'est caché sous l'encoche
   ou la « Dynamic Island ».
5. Regarder le bas. → La barre d'onglets n'est pas cachée par la barre d'accueil (le trait horizontal de l'iPhone).

## 2. Connexion sécurisée
6. Taper un **faux** mot de passe. → « identifiants invalides ». Rien d'autre ne s'affiche.
7. Taper le bon mot de passe. → L'accueil s'affiche.
8. Fermer complètement l'application (glisser vers le haut), la rouvrir. → La session est conservée ou le mot de
   passe est redemandé ; jamais de données d'un autre compte.

## 3. Simulation ou réel : jamais de doute
9. En haut, un **bandeau BLEU** dit « SIMULATION — argent FICTIF : aucun ordre réel n'est envoyé ». → Il reste visible
   en faisant défiler et sur les 5 onglets.
10. Toucher le **?** du bandeau. → Une explication simple s'ouvre (bleu = simulation, ROUGE = argent réel).
11. Sur l'accueil, la carte du capital dit **« Capital FICTIF (simulation) »** et « Ces euros n'existent pas ».
12. Le propriétaire explique avec ses mots ce qu'il voit. → Il dit spontanément que c'est de l'argent fictif.
    (En mode réel, le bandeau et un cadre autour de tout l'écran seraient **ROUGES** avec « ARGENT RÉEL » : à ne
    PAS tester aujourd'hui ; c'est vérifié automatiquement par `scripts/e2e_pwa.cjs`.)

## 4. Lire l'accueil
13. **Alertes importantes** : soit « Aucune alerte importante », soit des encadrés (rouge = grave, orange = attention).
14. **Bénéfice / perte**, **dont réalisé**, **dont latent**, **Frais cumulés** : toucher chaque **?**.
    → Le propriétaire peut redire la différence entre « réalisé » (définitif) et « latent » (sur le papier).
15. **Positions ouvertes** : pour chaque position, il est écrit soit « ✔ Stop actif chez l'échange » (vert), soit
    « ⚠ NON PROTÉGÉE » (rouge). → Le propriétaire sait que « stop visé » n'est PAS une protection.
16. **Niveau de risque** : une note sur 100 et une jauge avec un trait à 45. Toucher le **?**.
    → Le propriétaire peut redire la règle : « au-dessus de 45/100, le robot met moins d'argent ».
17. **État du robot** : « Robot ● vivant », « Données de marché ● fraîches », « Guardian » avec l'état.

## 5. Position non protégée (si le cas se présente, sinon montrer la capture du test)
18. Montrer la capture `10_alerte_non_protegee.png` (dossier des captures de `deploy_check.sh`).
    → Le propriétaire voit qu'une position non protégée est une **ALERTE rouge en haut**, différente de la ligne
    verte « Stop actif ». Lui demander ce qu'il ferait : l'arrêt d'urgence ou la liquidation, puis prévenir.

## 6. Arrêt d'urgence (un geste + une confirmation)
19. Depuis n'importe quel onglet, toucher le bouton rouge **STOP** en bas à droite avec le pouce.
    → Une fenêtre explique ce qui va se passer et propose « Oui, arrêter maintenant » ou « Annuler ».
20. Toucher **Annuler**. → Rien ne change (l'état reste le même dans « État du robot »).
21. Recommencer et toucher **Oui, arrêter maintenant**. → « ✔ Fait » en moins d'une minute ; l'état devient
    « PAUSE ENTRÉES » et une alerte orange « Nouvelles entrées en pause » apparaît.
22. Remettre en marche : onglet **Réglages** → « Reprendre (après contrôles) ». → « ✔ Fait », retour en SIMULATION.

## 7. Autres onglets
23. **Capital** : « Historique des transactions » (achats/ventes avec frais), « Ordres », « Protections chez
    l'échange ». Faire défiler un tableau large avec le doigt : seul le tableau défile, pas la page.
24. **IA** : la carte « Laboratoire expérimental » liste v1, v2, v3 (figées, suivies, non déployées), E1
    (expérimentale) et « Short : expérimental, non déployable ». Toucher le **?** du Short.
25. **Marchés** : la liste s'affiche ; aucun texte ne déborde à droite.

## 8. Apparence et confort
26. Réglages de l'iPhone → Luminosité → passer en **Clair** puis **Sombre**. → L'application suit, tout reste lisible.
27. Dans l'application, Réglages → Apparence : **Clair**, **Sombre**, puis **Auto**. → Le changement est immédiat.
28. Tourner l'iPhone, agrandir le texte (Réglages → Affichage → Taille du texte) : rien d'essentiel n'est coupé.

## 9. Hors connexion et confidentialité
29. Activer le **mode Avion**, rouvrir l'application. → L'écran s'affiche mais les chiffres ne se chargent pas
    (message d'erreur) : **aucun ancien chiffre n'est présenté comme actuel**. Désactiver le mode Avion.
30. Toucher « Se déconnecter » (Réglages). → Retour à l'écran du mot de passe.

## 10. Le robot n'a pas besoin du téléphone (phase 8)
31. Noter l'heure, puis **éteindre complètement l'iPhone** (bouton latéral + volume, « Éteindre ») pendant au moins
    **15 minutes**. Le rallumer, rouvrir l'application. → « Robot ● vivant » ; « Dernier signe de vie » il y a moins
    d'une minute ; dans **Capital**, la courbe du capital fictif n'a **pas de trou** pendant les 15 minutes : le robot a
    continué sur le serveur.
32. Fermer l'application (glisser vers le haut) et verrouiller l'iPhone. Sur le serveur, la personne qui installe
    lance `sudo bash scripts/alerte.sh "Test" "Alerte de test"`. → La notification **ntfy** arrive sur l'écran
    verrouillé, sans montant ni marché.
33. Rouvrir l'application, onglet **IA**. → « Laboratoire expérimental » à jour ; les générations v1, v2, v3 et E1
    sont toujours marquées figées ; aucune n'est « déployée ».

## Bilan
- Nombre d'étapes ✅ / ❌ : ____ / 33 ; modèle d'iPhone et version d'iOS : ____________
- Le propriétaire sait : reconnaître SIMULATION/RÉEL ☐, lire réalisé/latent/frais ☐, repérer une position non
  protégée ☐, utiliser STOP ☐, expliquer la règle 45/100 ☐.
- Toute étape ❌ est corrigée puis la recette est refaite avant d'envisager quoi que ce soit en argent réel.
