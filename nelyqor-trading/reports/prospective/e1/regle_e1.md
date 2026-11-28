# Génération expérimentale E1 (phase 6) : règles pré-enregistrées, directionnel et événementiel

Ces règles ont été écrites et commitées le 26/11/2026, **avant** toute observation prospective et **sans aucune recherche
sur l'historique**. Les paramètres sont fixés a priori et ne sont pas optimisés. La génération est **distincte**
de v1, v2 et v3, qui restent figées et inchangées.

**Statut** : EXPÉRIMENTAL et NON DÉPLOYABLE. Le Short n'est pas accessible par l'API Bitvavo
(`docs/SHORT_BITVAVO.md`, `API_AVAILABLE = False`). Toute la génération est en simulation : aucun ordre, aucun emprunt,
aucun argent réel. Un résultat favorable ne constituerait ni une validation de rentabilité ni une autorisation.

## Données et calendrier
- **Marchés** : BTC-EUR et ETH-EUR (bougies 1 h publiques Bitvavo, agrégées en 4 h par l'agrégation causale v4).
- **Calendrier** : décision à la clôture d'une bougie 4 h, exécution à l'ouverture suivante (moteur
  `backend/research/directional.py`, après correction des défauts de sa contre-expertise et avant le gel).
- **Départ prospectif** : à minuit UTC après le gel (au moins 1 h après). Aucune décision antérieure au départ n'est
  comptée.

## Règle directionnelle (identique pour tous les candidats)
- `trend_rule(fast=10, slow=40, band_atr=0.5)`, soit environ 1,7 jour et 6,7 jours en 4 h.
  - LONG si la moyenne rapide dépasse la moyenne lente de plus de 0,5 ATR et que la clôture est au-dessus de la
    moyenne lente.
  - SHORT dans le cas symétrique.
  - Sinon CASH, en particulier quand l'historique est insuffisant ou qu'une bougie manque.
- **Configuration** `DirConfig` par défaut :
  - frais de 0,25 % par côté, glissement de 5 pb, minimum d'ordre de 5 €, aucun levier (`max_gross = 1`,
    collatéral ≥ 100 %) ;
  - emprunt à 0,0023 %/h, liquidation à 110 % de santé [HYPOTHÈSE] avec 2 % de frais et 0,5 % de glissement ;
  - stop à 2 ATR, TP1 à 2 ATR sur 50 %, TP2 à 4 ATR, stop suiveur à 3 ATR, seuil de rentabilité après TP1 ;
  - volatilité cible de 2 %/j, plafond de 50 % par position, réduction au-delà d'un risque de 45/100 ;
  - anti-martingale.
- **Disponibilité d'emprunt** : BTC et ETH sont supposés empruntables en permanence [HYPOTHÈSE ; tous deux figurent parmi
  les actifs cités par Bitvavo].

## Filtre événementiel (abstention seulement)
À la décision de la bougie t et pour le marché m, un événement est **bloquant** si un groupe d'observations du journal
événementiel (`backend/events`), évalué **uniquement** avec les observations reçues avant la clôture de t (`assess`),
remplit toutes ces conditions :
1. Il est dans une catégorie de la liste {piratage, faillite, radiation, reglementation, stablecoin}.
2. Il porte sur l'actif de m (BTC ou ETH), ou bien il ne cite aucun actif et relève de la catégorie faillite ou
   stablecoin (risque systémique).
3. Il est corroboré par **au moins 2 éditeurs distincts**, n'est ni recyclé ni marqué rumeur, et sa première réception
   date de **moins de 24 h**.

Effet : le signal de m est **forcé à CASH**, ce qui interdit une nouvelle entrée et ferme la position existante à
l'ouverture suivante. Le filtre **n'ouvre jamais** de position. Aucune position n'est prise sur la seule base d'un titre.

Données événementielles manquantes (cache perdu, collecte en panne) : le filtre est inactif et la période est
**comptée et publiée**. Chaque prédiction enregistre les identifiants des groupes bloquants utilisés, ce qui permet le
rejeu exact.

## Candidats (a priori)
| id | règle | Short | filtre événementiel |
|---|---|---|---|
| `e1_ls` | tendance | oui | non |
| `e1_lc` | tendance (SHORT remplacé par CASH) | non | non |
| `e1_ls_evt` | tendance | oui | oui |
| `e1_lc_evt` | tendance (SHORT remplacé par CASH) | non | oui |
| `e1_cash` | CASH permanent | — | — |

Référence hors moteur : achat passif de BTC-EUR depuis le départ, frais compris.

## Hypothèses testées et critères (figés, évalués à 6 puis 12 mois)
Différence journalière d'équité, avec IC95 par bootstrap par blocs de 30 jours :
- **H1, apport du Short** : `e1_ls` − `e1_lc`.
- **H2, apport des événements** : `e1_ls_evt` − `e1_ls`, et `e1_lc_evt` − `e1_lc`.
- **H3, apport directionnel** : `e1_lc` − `e1_cash`, et `e1_lc` − achat passif de BTC.

Une hypothèse n'est **soutenue** que si la borne basse de l'IC95 de la différence moyenne journalière est > 0, avec :
- au moins 30 transactions pour H1 et H3 ;
- au moins 10 décisions modifiées par le filtre pour H2 (sinon « non concluant ») ;
- un drawdown maximal supérieur à −35 % pour le candidat testé.

Aucun critère ne sera assoupli. Une hypothèse soutenue ne vaut pas autorisation de trading réel.

## Prédictions et rejeu
Pour chaque candidat, chaque évaluation enregistre, pour la dernière bougie 4 h clôturée, la décision complète :
- signaux par marché ;
- entrées en attente (sens, montant) ;
- positions (sens, quantité, stop, TP1, TP2, stop suiveur) et état du compte ;
- groupes événementiels bloquants.

Le journal est chaîné par sha256. À chaque évaluation, le passé est rejoué et doit reproduire exactement chaque décision
enregistrée ; sinon l'évaluation est **INVALIDE**. Les créneaux manqués sont comptés et jamais reconstitués.

## Amendement 1 (26/11/2026, avant le départ et avant toute observation prospective)
Une contre-expertise a montré que le rejeu des candidats `*_evt` dépendait du cache événementiel, qui peut être évincé :
une perte de cache aurait rendu E1 INVALIDE de façon définitive. Chaque prédiction enregistre désormais aussi les
groupes bloquants **non vides** de toutes les bougies écoulées depuis la prédiction précédente (`blocages_historique`).
Le rejeu utilise ces enregistrements en priorité, et non le cache.

Le premier gel (16:04 UTC) est retiré et conservé sous `candidats_figes_e1_retire_avant_depart.json`. Aucune règle de
décision, aucun paramètre, aucun candidat ni aucun critère n'est modifié. Seuls la traçabilité et l'empreinte du script
changent.

Limite connue : sans planification (PR non fusionnée), il n'y a qu'environ une évaluation par jour. Une seule bougie 4 h
sur 6 est donc enregistrée comme prédiction ; les autres sont rejouées. Le nombre de créneaux prédits est publié.
