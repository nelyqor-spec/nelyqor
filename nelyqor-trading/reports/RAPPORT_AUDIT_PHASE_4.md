# NELYQOR TRADING — Rapport d'audit phase 4 : recherche d'un avantage financier robuste

Date : 26/11/2026. Branche `claude/new-session-cv44ga`, PR n° 1 vers `main` (non fusionnée). **Mode réel : désactivé** —
aucun dépôt, aucun retrait, aucun ordre réel, aucune clé utilisée. Toutes les données de marché viennent de l'API
PUBLIQUE Bitvavo via GitHub Actions ; chaque chiffre ci-dessous provient d'un calcul réellement exécuté et lu dans les
journaux des jobs (runs `nelyqor-research-p4` n° 1–3, `nelyqor-research-p4b` n° 1, `nelyqor-prospective` n° 7).

Légende : **[HISTORIQUE]** données ≤ 25/11/2026 déjà consultées (recherche, pas une validation) · **[PROSPECTIF]**
données ≥ 26/11/2026 00:00 UTC · **[SYNTHÉTIQUE]** données générées pour tester le code · **[HYPOTHÈSE]** valeur
supposée, non mesurée.

## 0. Conclusion

1. **Aucun avantage financier robuste n'est démontré.** Aucune stratégie n'atteint les critères de promotion (inchangés).
2. Le **+82 % de la rotation 4 h (phase 3) est un artefact** [HISTORIQUE] : il dépend de l'heure de rééquilibrage
   (une phase sur six positive, de −62 % à +97 %) et d'un filtre de liquidité caché créé par l'agrégation stricte
   des bougies ; corrigé, la même stratégie fait **−49 % en médiane des phases** (4 h).
3. Une **contre-expertise quantitative indépendante** a trouvé un défaut qui touchait aussi les phases 3–4 : la dernière
   bougie agrégée incomplète déclenchait une fausse « radiation » à −50 % des positions ouvertes. Impact mesuré : nul sur
   la plupart des chiffres de recherche (aucune position ouverte à la fin), mais réel pour les configurations sans filtre
   (2 à 3 positions décotées) et pour les évaluations rétrospectives des candidats figés (diagnostic séparé).
4. Le **seul signal cohérent** est le **filtre de largeur de marché** (BTC haussier ET plus de 50 % des cryptos au-dessus
   de leur moyenne) : médiane des phases positive dans 17 combinaisons intervalle × traitement sur 18 (exception : 1 j en agrégation
   tolérante, −78 %), **mais** la seconde moitié de la période est le plus souvent proche de zéro ou négative et le **DSR vaut 0,09–0,14** (seuil 0,95) après ≈ 1 300–1 500
   essais cumulés. Il agit surtout en **retirant du marché** une rotation qui perd 70–85 % sans filtre. Ce n'est pas une
   preuve d'avantage ; il est suivi prospectivement (candidat v2 figé).
5. **Ordres limités** : sur 6 marchés liquides et 14 jours de **transactions publiques réelles** (1,64 million),
   l'exécution limitée coûte **10 à 29 pb** contre **≈ 26 pb** (spread réel) à **35 pb** [HYPOTHÈSE sans carnet] au marché ;
   le modèle à bougies et le rejeu sur transactions **concordent dans 94–100 % des cas** (TTL 4 h). Mais la
   décomposition montre que l'essentiel du gain simulé de la rotation vient du **spread estimé des petits marchés**
   (Corwin-Schultz), précisément là où aucune transaction n'a pu être rejouée, et que les ordres manqués étaient les
   meilleurs (**sélection adverse** confirmée).
6. **IA** : aucun modèle n'apporte d'avantage incrémental face à une règle simple ; tous désactivés.
7. **Sécurité** : deux contre-expertises adversariales indépendantes ont trouvé **4 failles critiques/élevées** et 11 autres
   défauts ; **tous corrigés et testés** sauf des risques résiduels documentés (§ 9) qui exigent de séparer la base de
   l'API de celle du moteur.
8. **Coûts** : avec 100 €, tout hébergement payant rend le résultat négatif ; seule une solution à ≈ 0–2 €/mois
   (ordinateur déjà allumé, Raspberry Pi) + GitHub Actions (0 €) est compatible.

**Recommandation : maintenir le mode réel désactivé.** Laisser tourner l'évaluation prospective (v1 + v2) au moins
6 à 12 mois ; fusionner la PR n° 1 pour activer la planification quotidienne.

## 1. Données utilisées

| Jeu | Contenu | Nature |
|---|---|---|
| Bougies 1 h | 435 marchés EUR + USDC collectés (cache incrémental), ~3 ans, chaque marché depuis son listing ; agrégation causale 2 h → 1 j | [HISTORIQUE] |
| Transactions publiques | 11/11 → 25/11/2026 : BTC 764 137, ETH 219 027, SOL 253 533, XRP 225 634, ADA 145 114, DOGE 35 177 (1,64 M) | [HISTORIQUE] |
| Carnets (profondeur 25) | 180 instantanés par marché, toutes les 10 s pendant 3 × 15 min (25/11 soir) | [HISTORIQUE] |
| Données prospectives | bougies ≥ 26/11/2026 00:00 UTC | [PROSPECTIF] |

Univers admissible moyen : 22 à 29 marchés selon l'intervalle (max 116) ; part de bougies manquantes en agrégation stricte :
42 % (1 h) → 55 % (4 h) → 73 % (1 j) — cause principale des différences entre intervalles (§ 3).

## 2. Priorité 1 — Ordres limités et qualité d'exécution

### 2.1 Décomposition de l'écart marché → limite (rotation, variante de base) [HISTORIQUE]

Chaîne d'attribution (points de rendement net sur ~3 ans, 100 €), après correction d'un défaut de prix NaN dans les
modèles « clôture » (défaut n° 10, § 8) :

| Composante | 1 h | 4 h | 1 j |
|---|---|---|---|
| Marché (taker 0,25 %, demi-spread estimé + 5 pb) | −14,8 % | −3,9 % | −41,4 % |
| + commissions maker (0,15 %) | +11,9 | +10,4 | +6,7 |
| + suppression spread et glissement | +22,4 | +40,0 | +48,4 |
| + prix de clôture au lieu de l'ouverture suivante | +0,3 | +17,8 | −12,6 |
| + décalage limite de 5 pb | +8,1 | +8,7 | +5,4 |
| + non-exécutions et sélection (limite réaliste) | **−29,2** | **−18,0** | **−4,7** |
| = Limite postOnly réaliste | −1,4 % | +55,0 % | +1,7 % |
| Hybride (limite puis marché) | +7,2 % | +32,2 % | +1,2 % |

- **Transactions évitées / sélection adverse** : taux de remplissage des entrées 91–97 % ; rendement ultérieur des entrées
  **manquées** 82–215 pb contre −66 à +42 pb pour les entrées remplies : les ordres non exécutés sont ceux que le
  marché a quittés vers le haut. Coût de cette sélection : −5 à −29 points.
- **Le gain vient surtout du spread modélisé** (+22 à +48 points) : le spread des petits marchés est estimé par
  Corwin-Schultz à partir des hauts/bas ; il n'a pas pu être vérifié sur transactions réelles (§ 2.3).
- Sensibilité (4 h, limite) : exigence de traversée 0 / 2 / 10 / 25 pb → +56 / +55 / +38 / +16 % ; TTL × 2 / × 4 → +40 /
  +37 % ; décalage 0 / 15 pb → +53 / +40 %. En 1 h et 1 j, une traversée de 10 pb suffit à rendre la limite négative.

### 2.2 Simulateur d'exécution sur transactions publiques et carnets (`backend/research/lob_sim.py`)

File d'attente (taille affichée au niveau × multiplicateur), profondeur, exécutions partielles, latence aller/retour,
annulations (crédit de file), rejet postOnly, durée de validité fixe ou adaptative (flux vendeur passé), sélection
adverse (prix 1/5/30 min après remplissage), coût d'opportunité du reliquat, repli au marché en parcourant le carnet.
Trois scénarios : pessimiste (latence 500 ms, file × 1,5, aucune annulation créditée, remplissage seulement par
traversée), intermédiaire (200 ms, × 1, 50 %), optimiste (50 ms, × 0,7, 100 %). **Une bougie qui touche le prix ne
remplit jamais.** 8 tests unitaires.

Grille systématique [HISTORIQUE] : 83 placements par marché et par configuration (toutes les 4 h, achat ET vente,
30 €), 6 marchés, 3 décalages, 3 scénarios, TTL 4 h fixe ou adaptatif :

| Marché | Achat 0 pb pessimiste : remplissage / coût effectif | Vente 0 pb pessimiste | Sélection adverse achat 5 min / 30 min | Accord bougies ↔ transactions (achat 0 pb) |
|---|---|---|---|---|
| BTC-EUR | 95 % / 11,6 pb | 93 % / 17,4 pb | −6,9 / −10,0 pb | 80/83 |
| ETH-EUR | 99 % / 19,0 pb | 89 % / 23,2 pb | −9,4 / −14,1 pb | 82/83 |
| SOL-EUR | 98 % / 16,0 pb | 94 % / 15,9 pb | −8,5 / −7,7 pb | 81/83 |
| XRP-EUR | 100 % / 15,0 pb | 98 % / 17,2 pb | −4,9 / −7,4 pb | 83/83 |
| ADA-EUR | 100 % / 15,0 pb | 96 % / 23,1 pb | −9,6 / −10,0 pb | 83/83 |
| DOGE-EUR | 95 % / 29,0 pb | 90 % / 27,7 pb | −16,4 / −19,3 pb | 79/83 |

- Coût au marché de comparaison : **35 pb [HYPOTHÈSE]** (0,25 % + demi-spread de 5 pb + impact 5 pb) faute de carnet
  historique ; avec le spread réel observé sur BTC (≈ 1 pb), il serait ≈ 26 pb. **Économie mesurable : ≈ 10–15 pb par
  côté sur les grands marchés**, moins sur DOGE.
- Les trois scénarios donnent presque les mêmes résultats avec un TTL de 4 h : sur ces marchés, le prix traverse
  presque toujours le niveau dans les 4 h. Le TTL **adaptatif** réduit le remplissage (47–84 %) et transforme le coût
  en coût d'opportunité : il n'est pas meilleur.
- Sélection adverse : après un achat rempli, le prix continue de baisser (−5 à −19 pb à 30 min) ; pour les ventes elle
  est faible.
- **Limite majeure** : la rotation choisit surtout des petits marchés ; **aucun de ses ordres** ne tombait dans les 14 jours
  couverts (filtre de régime baissier en novembre 2026 → aucune entrée) : le modèle limite **n'est donc pas validé sur les
  marchés qu'il sert**. La collecte continue (cache `bitvavo-micro-v1`).

### 2.3 Verdict exécution
L'avantage des ordres limités est réel mais **modeste et localisé** (≈ 10–15 pb par côté sur les grands marchés) ; le
« +59 points » de la rotation repose surtout sur un spread estimé des petits marchés, non vérifié. Le candidat prospectif
v1 `xs_eur_4h_base_limite` face à `xs_eur_4h_base_marche` tranchera.

## 3. Priorité 2 — Rotation : pourquoi 4 h ?

### 3.1 Effet de l'heure de rééquilibrage [HISTORIQUE]
Rendement net « détention min 3 j » selon la phase (bougie de décision) :

| Intervalle | Phases | Min | Médiane | Max | Phases positives |
|---|---|---|---|---|---|
| 1 h | 8 | −52 % | −29 % | +9 % | 2/8 |
| 2 h | 12 | −48 % | −24 % | +75 % | 4/12 |
| 4 h | 6 | −62 % | −26 % | **+97 %** | 1/6 |
| 8 h | 3 | −51 % | −47 % | +86 % | 1/3 |
| 12 h | 2 | −38 % | −27 % | −16 % | 0/2 |

Tous les résultats positifs partagent une **décision à la clôture de 16:00 UTC** (4 h phase 0, 8 h phase 0, 2 h phases 1–3) ;
effet d'heure trouvé après coup, non confirmé en 1 h (17:00 UTC : −37 %) : **non exploité**, et la contre-expertise montre
qu'un tel écart entre phases est compatible avec du bruit pour un portefeuille de 3 lignes (170–200 points d'écart
reproduits sur données synthétiques).

### 3.2 Agrégation et filtre de liquidité caché
En agrégation stricte, une seule heure sans transaction rend la bougie agrégée absente ; la volatilité devient NaN
pendant 14 jours, le marché est exclu et **la position est vendue**. Corrigé (rendements depuis le dernier prix connu,
fenêtres partielles, `backend/research/xsection_v3.py`) ou en agrégation tolérante, la rotation 4 h « détention 3 j »
passe à **−49 % / −63 % en médiane des phases** ; même sens à tous les intervalles.

### 3.3 Hypothèses H1–H7 (pré-enregistrées) [HISTORIQUE, phase 0]
Meilleures : H6 (sortie en régime défavorable) 4 h +109 %, H5 (rotation seulement si gain > coût) 4 h +99 %, H7 (réserve
dynamique) 4 h +70 % — toutes à la phase 0 et avec détention 3 j, donc soumises au même artefact. H1–H4 (force relative
multi-horizon, ajustement à la volatilité, confirmation multi-horizon, momentum résiduel BTC) : aucune meilleure que la
base de façon cohérente. DSR de la meilleure : 0,23 (4 h) / 0,31 (8 h). Contrôle toutes phases (§ 3.4) : H5 −47 %
médiane (4 h, corrigé).

### 3.4 Contrôle complet toutes phases, trois traitements (`scripts/research_phase4b.py`) [HISTORIQUE]
Médiane des phases (en gras : traitement corrigé) :

| Configuration | 1 h | 2 h | 4 h | 8 h | 12 h | 1 j |
|---|---|---|---|---|---|---|
| base | −12 / **−20** | −40 / **−33** | −40 / **−58** | −49 / **−20** | −42 / **−55** | −41 / **−68** |
| détention 3 j | −34 / **−35** | −14 / **−20** | −26 / **−49** | −47 / **−42** | −27 / **−70** | −18 / **−67** |
| Kelly | +24 / **0** | +6 / **−26** | 0 / **−50** | 0 / **−16** | 0 / **−38** | +24 / **−23** |
| égal + filtre 30 j | −1 / **−12** | −13 / **−31** | −10 / **−53** | −13 / **−11** | −11 / **−44** | +13 / **−52** |
| largeur (BTC ∧ > 50 % haussiers) | +82 / **+59** | +48 / **+63** | +38 / **+42** | +36 / **+38** | +33 / **+20** | +70 / **+21** |
| largeur + Kelly | +114 / **+74** | +83 / **+80** | +51 / **+35** | +74 / **+24** | +62 / **+24** | +107 / **+38** |
| sans filtre | −36 / **−78** | −70 / **−75** | −78 / **−84** | −82 / **−81** | −83 / **−83** | −85 / **−84** |

(valeurs « stricte_v2 / **stricte corrigée** » en % ; l'agrégation tolérante corrigée donne le même sens, sauf largeur en
1 j : −78 %). Pour « largeur » : seconde moitié médiane de −57 % à +36 %, deux moitiés positives dans 0–67 % des phases,
**DSR 0,09 à 0,14** pour la meilleure configuration corrigée (1 296 à 1 488 essais cumulés).

## 4. Priorité 3 — Filtre de régime [HISTORIQUE]

| 4 h, base, phase 0 | Net | Moitié 1 | Moitié 2 | DD max | Temps actif |
|---|---|---|---|---|---|
| aucun filtre | −72 % | −25 % | −63 % | −86 % | 100 % |
| BTC seul | −4 % | +4 % | −8 % | −69 % | 51 % |
| exposition constante égale (51 %) | −37 % | +1 % | −38 % | −63 % | 100 % |
| tendance du marché | +88 % | +92 % | −2 % | −38 % | 29 % |
| proportion haussière | +62 % | +45 % | +11 % | −49 % | 27 % |
| dispersion haute | +88 % | +106 % | −9 % | −53 % | 47 % |
| volatilité basse | −73 % | −25 % | −63 % | −86 % | 84 % |
| corrélation basse | −35 % | +8 % | −40 % | −73 % | 70 % |
| liquidité haute | −16 % | +71 % | −51 % | −65 % | 42 % |
| BTC ∧ proportion haussière | +89 % | +58 % | +20 % | −49 % | 25 % |
| vote majoritaire | +46 % | +70 % | −14 % | −53 % | 42 % |

- Le filtre BTC fait mieux qu'une exposition constante de même niveau (−4 % contre −37 %) et que 93 % de 30 régimes
  aléatoires de même persistance (blocs de 30 jours) en 4 h (83 % en 1 j, 60 % en 1 h) : effet de **minutage réel mais
  faible**, qui ne rend pas la stratégie rentable.
- Appliqué au BTC seul : acheter-conserver +74 % (4 h), filtré −16 %, exposition constante +43 % : **le filtre détruit de
  la valeur sur le BTC lui-même** ; son intérêt est de couper une rotation perdante.
- Biais corrigés en phase 4b : la proportion haussière comptait une bougie absente comme baissière ; l'indicateur de
  corrélation choisissait ses marchés sur l'échantillon complet (fuite d'information, faible).

## 5. Priorité 4 — IA en filtre secondaire [HISTORIQUE, 4 h, walk-forward 4 plis purgés]

| Usage | Modèle | Règle simple | Verdict |
|---|---|---|---|
| Mauvaises opportunités (rendement net 3 j < 0) | LightGBM : AUC 0,53, Brier skill −0,07 ; logistique : AUC 0,49 | — | rejetés |
| Effet économique de l'écartement (périodes de test) | LightGBM −25,9 %, logistique −35,4 % | aucun filtre −25,2 % ; vol −46,4 % ; tendance −27,0 % | aucun gain : rejetés |
| Pertes extrêmes (< −10 % en 1 j) | LightGBM AUC 0,77 ; décile supérieur 12,4 % (base 3,4 %) | volatilité AUC **0,82** ; décile 13,8 % | règle simple meilleure : modèle rejeté |
| Probabilité de remplissage d'un ordre limité | logistique Brier skill +0,002 | terciles de volatilité +0,004 | aucun pouvoir (n = 106) : rejeté |
| Classification de régime | logistique (période de test) **+1,9 %** | règle BTC −25,2 % | écart positif mais un seul découpage, non pré-enregistré comme candidat : à reproduire, non retenu |
| Incertitude (quantile) | phase 3 : IC positif mais sans valeur économique | — | rejeté |

Les modèles ne déclenchent jamais de transaction ; aucun n'est activé dans le moteur.

## 6. Priorité 5 — Dimensionnement [HISTORIQUE, phase 0 puis toutes phases]

| 4 h base, phase 0 | Net | DD max | CVaR95 par bougie | Exposition |
|---|---|---|---|---|
| égal | −3,9 % | −69 % | 1,97 % | 32 % |
| volatilité (cible 4 %/j) | +3,5 % | −60 % | 1,65 % | 31 % |
| Kelly fractionné (¼) | +114,7 % | −51 % | 1,70 % | 31 % |
| CVaR (2 %) | +10,3 % | −62 % | 1,79 % | 31 % |
| robuste (corrélation) | −6,7 % | −54 % | 1,36 % | 30 % |

- **Kelly n'est pas un dimensionnement** : la moyenne passée doit être positive pour investir, donc c'est un filtre de
  momentum absolu sur 30 jours (vérifié). Isolé (« égal + filtre 30 j »), puis contrôlé sur toutes les phases et corrigé :
  Kelly −50 % (4 h), −23 % (1 j) ; filtre seul −53 % / −52 %. **Aucune méthode ne crée d'avantage** ; les méthodes
  volatilité et CVaR réduisent le drawdown de 7 à 15 points.
- Réduction quadratique au-delà d'un risque de 45/100 **conservée dans toutes les méthodes** (testée). Minimum Bitvavo
  5 € respecté ; sous ce minimum le capital reste en euros ; exposition nulle → 100 % en euros (testé).

## 7. Priorité 6 — Validation prospective

| Lot | Gel | Départ | Candidats | Empreinte |
|---|---|---|---|---|
| v1 (phase 3) | 25/11/2026 20:07:04 UTC, commit `9003293` | 26/11/2026 00:00 UTC | 5 (inchangés) | 8 fichiers, intacte |
| v2 (phase 4) | 25/11/2026 21:45:18 UTC, commit `89698a1` | 26/11/2026 00:00 UTC | 4 (règle pré-enregistrée avant lecture des résultats, application mécanique) | 7 fichiers, intacte |

Candidats v2 : `v2_hyp_4h_H5_rotation_si_gain_net_det3j`, `v2_regime_4h_btc_et_proportion_haussiere`,
`v2_dimensionnement_4h_kelly`, `v2_temoin_4h_sans_filtre`. Règle : `reports/prospective/v2/regle_de_selection.md`
(commit `af4b065`, antérieur à la lecture), entrées de la sélection archivées.

Trois natures de résultats séparées (v2) : **prédictions** enregistrées avant observation dans un journal chaîné sha256
(une prédiction manquée est comptée, jamais reconstituée), **notations** à horizon, **rétrospectif** sur données nouvelles.
Rattrapage : créneau de 14:43 UTC qui ne s'exécute que si l'évaluation du jour a échoué. Quotas : dépôt public (minutes
gratuites), ≈ 3 min par évaluation, cache ≈ 0,3 Go sur 10 Go.

Défauts connus des évaluations figées (non corrigés pour ne pas modifier le gel, publiés) : (a) le rétrospectif peut
compter à −50 % une position ouverte à la dernière bougie agrégée incomplète → **diagnostic hors gel** `prospective_corrige.py`
publié chaque jour à côté ; (b) les prédictions v2 ignorent dimensionnement, seuil de rotation et détention minimale :
pour Kelly et H5 elles sont identiques à la base (la notation ne peut ni valider ni rejeter ces deux candidats ; seul
leur rétrospectif les distingue).

### 7.1 Résultats prospectifs disponibles [PROSPECTIF] — évaluation du 26/11/2026 05:47 UTC (run n° 7, déclenchement manuel)

| Lot | Validité | Bougies 4 h prospectives | Transactions | Rendement | Remarque |
|---|---|---|---|---|---|
| v1 (5 candidats) | **valide** (empreintes intactes) | 2 (dont 1 incomplète) ; 8 h : 0 | 0 | 0,0 % | intention enregistrée : aucune cible (régime BTC défavorable), aucun signal d'entrée sur les 10 marchés de `strat_4h` |
| v2 (4 candidats) | **valide**, journal chaîné intact (4 lignes) | 2 | 0 | 0,0 % | 1 prédiction horodatée par candidat (0 manquée, 0 notée — horizon non atteint) |
| Diagnostic hors gel | — | 1 complète | 0 | — | aucune décote de fin (aucune position) |

**Aucun résultat prospectif exploitable à ce stade** (quelques heures). Observation de données : `CLEAR-EUR` répond
« market parameter is invalid » (marché retiré) — le biais de survivance de l'univers historique est réel.


## 8. Corrections réalisées et nouveaux défauts

### A. Défauts trouvés lors des travaux de la phase 4

| # | Défaut identifié | Gravité | Correction | Test |
|---|---|---|---|---|
| 1 | **Réactivation non autorisée du réel** : après un arrêt d'urgence en mode réel, une commande `resume` NON signée (déposable par une API compromise) rétablissait le mode réel (`resume_state = REAL`) | Critique | La reprise vers le réel exige une signature `resume_real` du propriétaire (PBKDF2 + HMAC, iPhone) ; l'arrêt d'urgence reste non signé et toujours possible ; la reprise vers la simulation reste simple | `test_emergency_stop_then_forged_resume_cannot_rearm_real`, `test_resume_to_simulation_stays_unsigned` |
| 2 | **Indicateur de recette non signé** (`acceptance_tests_ok`) : un simple booléen en base, sans lien avec le code | Élevée | Preuve signée (clé `engine_state_key`) : commit, sha256 du code `backend/**/*.py`, de `config/*`, de la sortie des tests, nombre de réussites/échecs, horodatage ; revérifiée par le moteur contre le code qu'il EXÉCUTE (refus si un fichier change, si un test échoue, si > 30 jours) | `test_proof_valid_and_bound_to_code`, `test_proof_rejects_failed_tests_and_parses_pytest`, `test_unsigned_acceptance_flag_no_longer_sufficient` |
| 3 | **Autorisation signée réutilisable après révocation** : une signature d'activation encore dans sa fenêtre de 5 min (nonce différent) restait valable après un retour en simulation | Élevée | Horodatage de révocation `auth_revoked_ms` signé dans l'état du Guardian (retour en simulation, arrêt d'urgence) ; toute signature antérieure est refusée | `test_signature_from_before_revocation_is_refused`, `test_revocation_timestamp_tamper_detected` |
| 4 | **Double exécution d'une commande** si deux boucles/instances lisaient la même commande en attente (le marquage `processing` ne vérifiait pas le nombre de lignes modifiées) | Moyenne (le verrou `flock` limitait déjà à une instance) | Prise atomique : seule la boucle dont l'`UPDATE … WHERE status='pending'` modifie 1 ligne exécute | `test_command_taken_once_by_concurrent_loops` |
| 5 | Collecte des transactions publiques : l'endpoint refuse `end − start > 24 h` ; la première version paginait sur 14 jours d'un bloc (0 transaction collectée au 1er run) | Moyenne (recherche) | Pagination par fenêtres ≤ 24 h, reprise incrémentale, compteur d'erreurs | `tests/test_collect_micro.py` |
| 6 | Agrégation stricte des bougies : une bougie 4 h / 12 h / 1 j est NaN dès qu'une heure manque ; Bitvavo n'émet pas de bougie sans transaction → les petits marchés disparaissent davantage aux grands intervalles | Moyenne (biais de recherche) | Non modifiée (fichier figé v1) ; mesurée : agrégation tolérante comparée en §C | partie `rotation` |
| 7 | DSR de la phase 3 : variance des Sharpe calculée en mélangeant des Sharpe par bougie d'intervalles différents (non comparables) | Faible (rapport) | Phase 4 : variance estimée sur les essais du même intervalle, nombre d'essais cumulé phases 3+4 | `research_phase4.dsr_of` |
| 8 | Workflow prospectif : garde de rattrapage écrite avec `[ … ] && …` en dernière commande sous `bash -e` → échec du job quand la condition est fausse (run n° 2) | Faible | `|| true` + création du dossier ; run n° 3 vert | GitHub Actions |
| 9 | Recette CI du commit phase 4 (1) : tri d'imports d'un test (ruff) | Faible | Corrigé au commit suivant ; recette verte | GitHub Actions |
| 10 | Décomposition : prix NaN à la décision pour un marché sans bougie (modèles « clôture » et « limite idéale ») : frais NaN, attribution fausse au premier run | Moyenne (recherche) | dernier prix connu ; résultats du run n° 1 écartés, run n° 3 publié | `test_decomposition_models_never_produce_nan_with_missing_candles` |

Points vérifiés sans défaut : les 5 candidats v1 et leurs empreintes sha256 sont intacts (`test_v1_freeze_untouched`) ; le
moteur v2 reproduit EXACTEMENT la v1 (équité identique au centime, mêmes transactions) en modes marché/limite/hybride
(`test_v2_reproduces_v1_exactly`) ; une commande interrompue par un crash n'est jamais rejouée
(`test_interrupted_command_not_replayed_after_crash`).


### A bis. Défauts trouvés par les deux contre-expertises de sécurité indépendantes (agents auditeurs, attaques exécutées)

Hypothèse de menace : l'API web est compromise (elle lit/écrit toute la base partagée, voit passer le mot de passe),
sans accès aux secrets du moteur ni à son volume d'état.

| # | Défaut (vérifié par un test d'attaque) | Gravité | Correction | Test de non-régression |
|---|---|---|---|---|
| C1 | Clé de signature dérivée du **mot de passe**, que l'API reçoit en clair : une API compromise pouvait recalculer la clé et forger `activate_real` / `resume_real` / `liquidation_confirm` | Critique | **Phrase de signature distincte**, jamais envoyée (`set_password.py` exige 16 car. et ≠ mot de passe) ; **PWA servie par Caddy** en lecture seule (plus par l'API) | `test_C1_signing_key_not_derivable_from_login_password`, e2e 14/14 |
| C2 | **Rejeu** d'anciens enregistrements signés de l'état du Guardian (aucun compteur) : retour silencieux en REEL_AUTORISE ; variante via RECONCILIATION + reprise automatique | Critique (si le réel a été activé une fois) | État du Guardian, nonces, méta des positions réelles, périmètre exclu, ouverture réelle dans un **magasin réservé au moteur** (`backend/core/engine_store.py` : document signé, séparation de domaine, compteur monotone + marqueur, écriture atomique) sur le volume `/state` jamais monté dans l'API ; la base n'a plus qu'une copie d'affichage | `test_C2_replaying_old_signed_db_rows_has_no_effect`, `test_store_tampering_or_replay_of_old_file_is_detected` |
| E1 | **Suppression** de `auth_revoked_ms` / des nonces → rejeu d'une activation signée après un arrêt d'urgence ; suppression de l'état = sortie silencieuse d'INCIDENT | Élevée | idem (magasin) ; fichier disparu après initialisation = falsification | `test_E1_deleting_revocation_and_nonces_in_db_has_no_effect`, `test_store_deleted_after_initialisation_is_detected` |
| E2 | Vente au marché forcée d'une position RÉELLE via la clé non signée `realloc_real`, même en PAUSE | Élevée | Demandes de réallocation **en mémoire** du moteur, ignorées hors SIMULATION/REAL | `test_E2_injected_reallocation_request_cannot_sell` |
| D1 | L'API imposait le sel et le **nombre d'itérations** PBKDF2 à l'iPhone (1 itération → attaque hors ligne de la phrase) | Élevée | 600 000 itérations **figées** dans `sig.js`, sel validé, servi par Caddy depuis un fichier en lecture seule | `test_browser_signature_matches_python` (refus d'1 itération) |
| D2 | Arrêt d'urgence SSH déposé dans la base : neutralisable par l'API | Élevée | **Canal propre au moteur** : fichier `ARRET_URGENCE` dans `/state`, lu à chaque cycle (`scripts/emergency_stop.sh`) | `test_D2_emergency_file_in_engine_state_dir` |
| D3 | Une fausse intention `UNKNOWN` en base bloquait **toutes** les soumissions réelles, stops compris | Élevée | Intentions **étiquetées HMAC** par le moteur (champs immuables) ; une ligne étrangère est signalée (pause des entrées) et ne bloque plus les protections | `test_D3_forged_unknown_intent_does_not_block_and_pauses` |
| D4 | Magasin perdu/corrompu : ré-ouverture du compte qui **excluait les positions du robot** ; INCIDENT levé par une reprise non signée ; marqueur illisible = crash | Moyenne | Historique réel + magasin vierge ⇒ INCIDENT, pas de ré-ouverture ; levée par reprise **signée** uniquement ; compteur jamais en dessous du marqueur ; marqueur illisible ⇒ INCIDENT ; `backup.sh` sauvegarde `/state` | `test_D4_lost_store_with_real_history_goes_incident_and_needs_signature` |
| D5 | Une réponse HTML de l'API sur `/api/*` pouvait exécuter un script sur l'origine de la PWA (service worker piégé) | Moyenne (hypothèse non exécutée) | Caddy : CSP `sandbox; default-src 'none'` sur `/api/*`, en-tête `Service-Worker-Allowed` retiré, export CSV par `fetch` + blob | revue de configuration |
| D7 | Estimation de liquidation relue depuis la base (mode modifiable) | Faible | Estimation **en mémoire** du moteur ; mode signé | `test_D7_liquidation_estimate_tampered_in_db_ignored` |
| D8 | Disque plein : l'arrêt d'urgence échouait sans effet | Faible | Blocage des entrées **en mémoire d'abord**, puis persistance | `test_D8_emergency_stop_effective_in_memory_even_if_write_fails` |
| M1 | `resume_real` rejouable après une simple pause | Moyenne | La pause révoque aussi | `test_M1_pause_revokes_previous_resume_signature` |
| M2 | Course sur `resume_state` lu deux fois | Moyenne | Cible de reprise figée et revérifiée | revue + test d'agent |
| F1/A5 | Signature datée du futur survivant à une révocation (+4 min, puis +29 s) | Faible | Refus au-delà de +30 s ; révocation posée à « maintenant + 30 s » (une nouvelle signature n'est acceptée que 30 s après un arrêt/pause) | `test_F1_future_dated_signature_refused`, `test_A5_signature_29s_in_future_issued_before_stop_is_revoked` |
| — | Entrée réelle possible si le drapeau serveur est retiré après activation | Faible | `check()` exige le drapeau à chaque entrée réelle | `test_real_entry_vetoed_when_server_flag_absent` |
| — | Empreinte de recette limitée à `backend/` ; `CONFIG_DIR` effectif non haché | Faible | Empreinte : backend, frontend, scripts, dépendances, config effective ; sources des tests hachées (informatif) ; séparation de domaine | `test_proof_valid_and_bound_to_code` |

Seconde passe de vérification : les 9 attaques de la première passe échouent désormais (A5 réduite puis corrigée).

### B. Défauts méthodologiques trouvés par la contre-expertise quantitative indépendante (agent auditeur)

| # | Défaut (vérifié) | Gravité | Traitement (sans modifier les fichiers figés) | Effet mesuré [HISTORIQUE] |
|---|---|---|---|---|
| Q1 | Fausse « radiation » à −50 % des positions ouvertes quand la dernière bougie agrégée est incomplète (moteurs v1 et v2) | Critique | `fix_tail` (`xsection_v3.py`) ; diagnostic hors gel quotidien | décotes de fin : 2–3 positions pour les variantes sans filtre ; 0 ailleurs (aucune position ouverte en fin de période) ; démontré sur synthétique (−49 % → +1 %) |
| Q2 | Agrégation stricte = filtre de liquidité caché (1 h manquante → 14 j d'exclusion + vente forcée) | Majeure | `build_panel_v3` (rendements depuis le dernier prix, fenêtres partielles) | rotation 4 h détention 3 j : −26 % → −49 % (médiane des phases) |
| Q3 | Kelly = filtre de momentum absolu 30 j | Majeure | variante `égal + filtre 30 j` isolée | Kelly ne survit pas à la correction (−50 % en 4 h) |
| Q4 | Résultats publiés à la phase 0 du rééquilibrage | Majeure | médiane sur toutes les phases comme estimation principale | +97 % → −26 % (non corrigé) / −49 % (corrigé) |
| Q5 | Prédictions v2 ignorant dimensionnement/seuil/détention | Majeure | documenté (gel) ; le rétrospectif les distingue | — |
| Q6 | `correlation_basse` : marchés choisis sur l'échantillon complet (fuite) | Mineure | sélection glissante (`regime_exposures_v3`) | n'affecte aucun candidat |
| Q7 | `proportion_haussiere` : bougie absente comptée baissière | Mineure | `regime_exposures_v3` | filtre « largeur » toujours positif après correction |
| Q8 | DSR calculé pour la seule partie rotation, essais non cumulés | Moyenne | DSR avec ≈ 1 200 essais antérieurs + essais de la partie | DSR 0,09–0,14 |
| Q9 | Partie IA : survivance (`dropna` sur prix futur), seuils sur-ajustés | Mineure | documenté ; modèles rejetés de toute façon | — |
| Q10 | TTL et coûts non comparables entre intervalles (TTL 4 h vs 24 h ; spread sur 30 bougies) | Mineure | documenté | — |

## 8 bis. Priorité 8 — Infrastructure gratuite (recherche documentaire du 25/11/2026 ; rien n'a été commandé)

Vérification par un agent documentaire indépendant. Les pages officielles (docs.github.com, docs.oracle.com,
docs.bitvavo.com) n'étaient pas joignables depuis l'environnement : les points ci-dessous viennent d'extraits de ces
pages vus via un moteur de recherche ([officiel/extrait]) ou de sources secondaires ([secondaire]).

| Option | Coût direct | Quotas / conditions | Disponibilité, conservation, risques |
|---|---|---|---|
| **GitHub Actions** (dépôt public) | 0 € | runners standards gratuits sans quota de minutes [officiel/extrait] ; cache 10 Go/dépôt, éviction LRU au-delà ; entrées non consultées depuis ~7 jours supprimées (rétention par défaut, changelog du 20/11/2025 ; formulation exacte non relue) ; artefacts et journaux 90 jours max en public [officiel/extrait] ; job ≤ 6 h | planification uniquement depuis la branche par défaut ; cron retardé en début d'heure, voire abandonné sous forte charge [secondaire] → créneau de rattrapage ; **workflows planifiés désactivés après 60 jours sans activité** du dépôt [officiel/extrait] ; journal prospectif en cache (consulté chaque jour, donc conservé) + artefact 90 j ; **inadapté à un moteur de trading** (pas de processus continu, pas de gestion des stops en temps réel) |
| **Oracle Cloud Always Free** | 0 € (carte de crédit exigée pour vérifier l'identité, pré-autorisation annulée ; cartes prépayées/virtuelles refusées) | Ampere A1 : 2 OCPU / 12 Go (1 500 OCPU-h, 9 000 Go-h/mois) selon plusieurs sources de 2026 [secondaire] ; un extrait de la page officielle affichait encore 4 OCPU / 24 Go : **à vérifier dans la console** ; 200 Go de stockage bloc ; 10 To/mois de trafic sortant | instance récupérable si, sur 7 jours, CPU p95 < 20 % [officiel/extrait] ET réseau < 20 % ET mémoire < 20 % pour A1 [secondaire] : le moteur (≈ 1 s de calcul par cycle, ≈ 260 Mo de RAM) remplit ces trois conditions → **risque réel d'arrêt** ; générer une charge artificielle pour l'éviter serait contraire à l'esprit des règles et n'est pas proposé ; capacité A1 souvent indisponible ; passage en paiement à l'usage = risque de facturation : **non recommandé sans décision explicite du propriétaire** |
| **Ordinateur personnel / Raspberry Pi** | électricité ≈ 0,7 €/mois (Pi 5, ~4 W) à ≈ 2 €/mois (mini-PC ~10 W) à 0,25 €/kWh ; ~0 € marginal si la machine est déjà allumée | aucun quota ; API publique Bitvavo : 1 000 points de poids/min par IP sans clé [officiel/extrait] | coupures (secteur, box, mises à jour) : en SIMULATION, seul l'échantillon est interrompu ; en réel, les stops natifs Bitvavo protègent les positions même moteur arrêté ; IP domestique dynamique incompatible avec la liste blanche IP d'une clé API réelle (sans objet en simulation) ; sauvegardes `scripts/backup.sh` hors machine |

API Bitvavo utilisée par le simulateur : `GET /{market}/trades` poids 5, `limit` ≤ 1000, **fenêtre end − start ≤ 24 h**
[officiel/extrait] — ce qui confirme la cause de l'échec du premier run de collecte et la correction ;
`GET /{market}/book` poids 1, `depth` ≤ 1000 [extrait d'une page voisine, très probable].

**Recommandation (sans abonnement)** : simulation continue sur un ordinateur personnel déjà allumé ou un Raspberry Pi
(≈ 0–2 €/mois d'électricité), calculs lourds et évaluation prospective quotidienne sur GitHub Actions (0 €). Aucun
service payant n'a été commandé.

## 9. Sécurité : état et risques résiduels

Mode réel désactivé (`NELYQOR_REAL_TRADING_ALLOWED=false`, aucune clé déposée). Clés Bitvavo lisibles par le moteur
seul ; LAB sans secret ni accès à la base de production (tests de compose). Corrections : § 8 (A et A bis).

Risques résiduels **acceptés et documentés** (exigent une base propre au moteur, hors périmètre de cette phase) :
- le journal réel (`fills`, `external_flows`) est relu depuis la base partagée : un faux dépôt « identifié » gonfle
  l'équité comptable jusqu'à la réconciliation suivante (≤ 5 min, pause en cas d'écart) ; borné par `max_order_eur`
  (500 €) et par le solde réel ;
- `ack_flow` et les pré-requis `aucun_ordre_inconnu` / `aucun_flux_non_identifie` reposent sur des tables modifiables ;
- la PWA affiche ce que l'API renvoie : une API compromise peut mentir à l'affichage (elle ne peut plus signer) ;
- une API compromise peut ne pas relayer un arrêt d'urgence venant de l'iPhone → utiliser le canal SSH (`/state`).

## 10. Coûts économiques réels (100 €, ~3 ans) [HISTORIQUE]

| Configuration (médiane des phases, traitement corrigé, 4 h) | 0 €/mois | 3 € | 5 € | 7,19 € | 10 € |
|---|---|---|---|---|---|
| Rotation de base | −58 % | −166 % | −238 % | −317 % | −419 % |
| Largeur (BTC ∧ proportion haussière) | +42 % | −66 % | −138 % | −217 % | −319 % |

(> −100 % : l'hébergement cumulé dépasse le capital ; 1 € de loyer mensuel = −36 points sur 3 ans.) Aucune augmentation
de capital n'est recommandée pour masquer ce déficit.

## 11. Tests et vérifications (résultats vérifiables)

- `pytest` : **231 tests réussis**, 0 échec (local, 26/11/2026) ; `ruff` et `mypy` sans erreur ; recette CI
  (`nelyqor-trading`) verte sur les commits de sécurité.
- Recette PWA (Playwright, iPhone 13) : **14/14**, dont l'activation signée avec la phrase de signature.
- Nouveaux fichiers de tests phase 4 : `test_lob_sim.py` (8), `test_xsection_v2.py` (8), `test_xsection_v3.py` (4),
  `test_phase4_security.py` (9), `test_phase4_engine_store.py` (15), `test_prospective_v2.py` (3), `test_collect_micro.py` (1).
- Moteur v2 = moteur v1 au centime près (`test_v2_reproduces_v1_exactly`) ; empreintes des gels v1 et v2 intactes
  (`test_v1_freeze_untouched` + contrôle à chaque évaluation).
- Contre-expertises : 4 agents indépendants (sécurité × 2, méthodologie, documentation) ; leurs tests d'attaque sont
  dans le répertoire de travail de session (non commités).

## 12. Pull request

PR n° 1 `claude/new-session-cv44ga` → `main` : https://github.com/nelyqor-spec/nelyqor/pull/1 — **ouverte, non fusionnée**
(aucune fusion automatique). Tant qu'elle n'est pas fusionnée, l'évaluation prospective ne tourne que sur déclenchement
manuel ou push (la planification GitHub ne s'exécute que depuis la branche par défaut).

## 13. Blocages restants

1. **Validation prospective** : quelques heures de données seulement ; 6 à 12 mois nécessaires.
2. **Fusion de la PR n° 1** (décision du propriétaire) pour activer l'évaluation quotidienne ; GitHub désactive la
   planification après 60 jours sans activité du dépôt.
3. **Exécution sur petits marchés** : aucun ordre de la rotation à rejouer sur transactions réelles pour l'instant ;
   pas de carnets historiques (collecte en cours).
4. **Base partagée API/moteur** : risques résiduels du § 9.
5. **Frais réels du compte** non interrogés (aucune clé) ; hypothèses 0,15 / 0,25 %.
6. **Hébergement** : toute solution payante rend le résultat négatif avec 100 € ; Oracle Always Free exposé au
   recyclage des instances inactives.
7. Pages officielles (GitHub, Oracle, Bitvavo) non joignables depuis l'environnement : certaines valeurs
   d'infrastructure restent [secondaire].

## 14. Conditions de lancement (inchangées, aucune remplie pour le réel)

Au moins un candidat figé positif après tous coûts sur ≥ 6 mois prospectifs, ≥ 100 transactions, profit factor > 1,2,
borne basse IC95 > 0, meilleur que l'achat passif BTC ajusté du risque ; écart limite/marché confirmé en prospectif ;
hébergement couvert ; recette signée valide sur le serveur ; clés sans retrait, liste blanche IP, 2FA ; décision
explicite du propriétaire signée avec la phrase de signature.
