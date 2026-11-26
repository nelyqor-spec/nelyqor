# Génération prospective v3 — règles pré-enregistrées (phase 5)

Écrites et commitées le 26/11/2026 vers 06:20 UTC, AVANT toute exécution du moteur v4 sur données réelles et
avant la contre-expertise finale du simulateur. Les candidats v1 et v2 restent figés et évalués tels quels
(traçabilité) ; leurs défauts connus sont publiés (rapports phases 4–5) : leurs résultats ne sont pas des
validations financières.

## Principes
1. Moteur : `backend/research/xsection_v4.py` + `strategies_v4.py`, gelés seulement APRÈS validation (tests
   déterministes + contre-expertise indépendante sans défaut bloquant ouvert).
2. Pas de sélection d'heure : chaque candidat est évalué sur **toutes** ses phases de rééquilibrage, chaque phase
   étant un compte simulé indépendant de 100 € ; le score du candidat est la **moyenne** des comptes.
3. Pas de sélection par le meilleur résultat historique : les candidats sont fixés **a priori** pour tester des
   hypothèses précises, références comprises (l'historique a montré qu'aucune configuration n'est robuste).
4. Intervalle 4 h pour tous (le plus étudié ; choisi avant les résultats v4), univers EUR, données publiques.

## Candidats (a priori)
| id | hypothèse testée |
|---|---|
| `v3_rotation_largeur` | le filtre BTC ∧ largeur du marché apporte-t-il un avantage net ? (rotation 3 marchés, poids égal, réduction > 45/100, marché) |
| `v3_rotation_largeur_volatilite` | même signal, dimensionnement à volatilité cible 4 %/j |
| `v3_rotation_largeur_limite` | même signal, exécution par ordres limités postOnly (validité 1 bougie, puis abandon) |
| `v3_rotation_sans_filtre` | témoin : rotation sans filtre de régime |
| `v3_ref_btc_filtre` | référence : BTC filtré par sa tendance |
| `v3_ref_btc_exposition_constante` | référence : BTC à exposition constante 25 % (ordre de grandeur de l'exposition investie du filtre de largeur) |
| `v3_ref_equipondere_5` | référence : 5 marchés les plus liquides, équipondérés, mensuel |

## Prédictions = décisions complètes
À chaque évaluation, pour la dernière bougie 4 h CLÔTURÉE et pour chaque compte (candidat × phase) : ordres que le
moteur émettrait à l'ouverture suivante (marché, sens, montant en euros, quantité, type, prix limite, durée de
validité, conditions d'annulation) et état du compte (trésorerie, positions). Journal chaîné sha256. À chaque
évaluation suivante, le moteur rejoue le passé : il doit **reproduire exactement** les décisions déjà enregistrées
(sinon : évaluation INVALIDE).

## Critères statistiques (figés, évalués à 6 puis 12 mois)
Un candidat n'est « retenu pour étude » que si, sur la période prospective : rendement net moyen des comptes > 0,
> achat passif BTC et > exposition constante, borne basse IC95 (bootstrap par blocs de 30 jours) du rendement
journalier moyen > 0, ≥ 100 transactions cumulées, drawdown maximal médian > −35 %. Aucun critère ne sera assoupli.
Ces critères ne constituent PAS une autorisation de trading réel (conditions de lancement : rapport phase 5).

## Départ prospectif
Minuit UTC suivant le gel (au moins 1 h après).
