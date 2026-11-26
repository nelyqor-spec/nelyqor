# NELYQOR TRADING — Rapport d'audit phase 5 : fiabilité mathématique et validation scientifique

Date : 26/11/2026. Branche `claude/new-session-cv44ga`, PR n° 1 vers `main` (ouverte, **non fusionnée**).
**Mode réel : désactivé.** Aucun ordre, aucun dépôt, aucun retrait, aucune clé utilisée. Données de marché : API
PUBLIQUE Bitvavo via GitHub Actions ; chaque chiffre provient d'un calcul exécuté et lu dans les journaux des jobs :
`nelyqor-research-p5` runs n° 1–3 (45767095268, 45802244061, 45802933782), `nelyqor-prospective` n° 10–11,
`nelyqor-microstructure` n° 1.

Légende : **[HISTORIQUE]** données ≤ 25/11/2026, déjà consultées (recherche, jamais une validation) · **[PROSPECTIF]**
données postérieures au départ figé · **[SYNTHÉTIQUE]** données générées pour tester le code · **[HYPOTHÈSE]** valeur
supposée, non mesurée.

## 0. Conclusion

1. **Aucun avantage financier crédible n'est détecté.** Je le déclare explicitement. Je **recommande de suspendre les
   recherches historiques répétitives** : chaque nouvelle exploration du même historique augmente le nombre d'essais
   (≈ 1 550 cumulés) sans pouvoir produire de preuve. La seule voie restante est la **validation prospective** des
   candidats figés (v1, v2, v3), sur au moins 6 puis 12 mois.
2. Le **simulateur a été réécrit** (moteur v4), vérifié par **46 scénarios déterministes calculés à la main** et par
   **deux contre-expertises indépendantes**. La première a trouvé 13 défauts (dont 4 graves), la seconde 8 (dont 2
   moyens). **Tous sont corrigés** et leurs scénarios intégrés au dépôt. Une donnée manquante n'entraîne plus jamais de
   décote : la décote ne s'applique qu'à une radiation constatée causalement, et c'est un paramètre publié
   (0,10 [HYPOTHÈSE], sensibilité 0 / 0,5 testée : **aucun effet**, car les stratégies n'ont subi aucune radiation).
3. **Fragilité démontrée** [HISTORIQUE] : la rotation 4 h avec filtre de largeur, sans détention minimale, passe de
   **+44 % à −0,6 %** (médiane des 6 phases) entre le moteur corrigé une fois et le moteur corrigé deux fois. Les
   corrections portaient sur des détails d'exécution : conserver une position lors d'une bougie absente, et réduire au-delà
   d'un risque de 45/100. Un résultat qui change de signe sur de tels détails n'est pas un avantage.
4. **Signaux élémentaires** [HISTORIQUE] : 864 tests, avec les corrections Holm et BH. Après coûts, **aucun état d'aucun
   signal n'a un rendement net positif significatif**. Les seuls effets robustes sont des **classements bruts** (les actifs volatils
   sous-performent ; l'autocorrélation positive persiste), qui ne survivent pas aux coûts d'un portefeuille de 100 €.
5. **Le filtre de largeur n'a pas d'avantage propre** : son effet s'explique par la **réduction d'exposition**. Son
   minutage n'est pas distinguable d'un minutage aléatoire de même persistance (p Holm = 1,0). Il n'est **pas** moins
   exposé les jours de baisse (30 % contre 30 % sur BTC, 31 % contre 32 % sur le marché).
6. **Sécurité** : les bases API et moteur sont séparées, et les achats réels sont dimensionnés sur le **capital
   authentifié** (soldes Bitvavo lus par le moteur) ; le plafond de 500 € n'est plus la protection. Une contre-expertise
   adversariale indépendante a trouvé **3 failles élevées** : gel du moteur, plantage mémoire et disque plein via le canal
   de commandes. Aucune ne permettait d'acheter plus que permis. **Toutes sont corrigées**, et les attaques échouent
   désormais.
7. **Génération v3 figée** le 26/11/2026 à 10:36:55 UTC (commit parent `1917023`), départ prospectif le **27/11/2026 à
   00:00 UTC**. Les prédictions sont des décisions complètes, rejouées exactement à chaque évaluation. Aucune donnée
   prospective v3 n'existe encore.

**Recommandation : maintenir le mode réel désactivé** ; fusionner la PR n° 1 (décision du propriétaire) pour que les
évaluations planifiées tournent sur la branche par défaut ; ne plus lancer de nouvelle recherche historique.

## 1. Moteur de simulation v4 (priorité 1)

Fichiers : `backend/research/xsection_v4.py` (moteur), `backend/research/strategies_v4.py` (rotation et références).
Les moteurs figés v1 et v2 sont **inchangés** (empreintes vérifiées). Leurs défauts sont publiés et leurs évaluations ne
sont **pas** des validations financières (§ 5).

| Exigence | Traitement dans v4 | Preuve (test) |
|---|---|---|
| Dernière bougie incomplète | retirée si son intervalle n'est pas écoulé à la date commune des données | `test_incomplete_aggregated_candle_dropped` |
| Agrégation temporelle | bougie agrégée présente dès qu'une heure existe ; tri et dédoublonnage ; heures invalides retirées **avant** agrégation | `test_d9_*`, audit n° 2 |
| Horodatages | début d'intervalle, ms UTC, alignés sur l'époque | tests d'agrégation |
| Bougies manquantes / absence réelle de transactions | état SANS_TRANSACTION : prix reporté, **aucune décote**, aucun achat ; une vente est reportée | `test_missing_candle_no_haircut`, `test_d5`, audit n° 2 |
| Panne de collecte | état PANNE si moins de 10 % des marchés **récemment actifs** cotent (ou aucun) : ni décision ni exécution, valorisation gelée | `test_d1_*` |
| Valorisation des positions ouvertes | au dernier prix connu ; fin d'échantillon : vente au dernier prix, frais taker, **jamais** traitée comme une radiation | `test_end_of_sample_is_not_delisting` |
| Rapprochement comptable | trésorerie + Σ quantité × prix ; invariant vérifié à chaque bougie ; trésorerie reconstituée au centime depuis le journal (3 modes) | `test_accounting_identity`, audit n° 2 |
| Disponibilité historique des marchés | NON_COTE avant la 1re bougie ; cotation tardive ; recotation | `test_relisting_back_to_active` |
| Radiation | constatée **causalement** (absence ≥ 7 j alors que ≥ 50 % des autres marchés cotent) ; décote paramétrable | `test_delisting_causal` |
| Frais, arrondis, minimums | frais sur le notionnel ; quantité arrondie vers le bas au pas ; minimum 5 € (achat **et** vente partielle) ; jamais de reliquat invendable | `test_qty_rounding`, `test_d8`, audit n° 2 |
| Biais d'anticipation | décision à la clôture t, exécution à l'ouverture t+1 ; équité identique avant une troncature (3 modes) | `test_no_lookahead_under_truncation` |
| Biais du survivant | univers point-in-time (423 marchés EUR, radiés inclus) ; décote de radiation explicite | `probe_survivor` (audit n° 1) |
| Ordres limités | remplis seulement si une bougie postérieure traverse le prix ; plafond de 5 % du volume (achats et ventes) ; reliquats conservés ; repli au marché pour une vente | `test_limit_requires_trade_through`, `test_d4` |

**Scénarios calculés à la main** : 21 dans `tests/test_xsection_v4.py` (ex. : achat de 50 € à 100 € puis vente à 110 € →
trésorerie exactement 104,7375 €) et 25 dans `tests/test_xsection_v4_audit2.py`. Tous passent à 1e-9 € près.

### 1.1 Contre-expertises indépendantes du simulateur (agents auditeurs, sans accès en écriture)

| Audit | Défauts trouvés | État |
|---|---|---|
| n° 1 (commit 8891eb8) | **D1 grave** : marchés radiés laissés dans le dénominateur de panne (pannes fictives ou radiation bloquée) · **D2 grave** : une décision vide annulait les ventes limitées en attente · D3 : ordres en attente non triés (ventes d'abord) · D4 : aucun plafond de volume sur les ventes limitées, reliquats perdus · D5 : achats au marché perdus sans le dire · **D6 grave** : aucune décision à la dernière bougie (prédiction prospective vide) · D7 : prix nul ou NaN = plantage · D8 : vente partielle hors pas de quantité · D9 : agrégation sans tri ni dédoublonnage · **S1** : l'hystérésis faisait dépasser le budget (consommait la réserve) · S2, S3 : références · S4 : position conservée jamais réduite au-delà de 45/100 | **corrigés** (commit 887c7e5) |
| n° 2 (commit 887c7e5) | **moyen** : une seule bougie absente forçait la vente d'une position détenue · **moyen** : exposition nulle → liquidation des positions risquées sans `exit_on_risk_off` · 6 mineurs (vente partielle sous 5 €, plafond de volume dépassé par la règle anti-reliquat, agrégation avant assainissement, doublons du chemin 1 h, grille vide, compteurs) | **corrigés** (commit 1917023) ; 25/25 scénarios passent |

## 2. Résultats du moteur v4 figé [HISTORIQUE] (run 45802933782, commit 1917023 = code figé v3)

423 marchés EUR, ≈ 3,0 ans, 100 € par compte, **toutes les phases de rééquilibrage** (6 en 1 h et 4 h, 3 en 8 h, 1 en 1 j),
frais taker 0,25 %, glissement 5 pb. Estimation principale : **médiane des phases** ; [min ; max] entre crochets.

| Configuration (pré-enregistrée) | 1 h | 4 h | 8 h | 1 j |
|---|---|---|---|---|
| Rotation sans filtre | −74,9 % | −73,1 % [−90 ; −22] | −85,2 % | −89,6 % |
| Rotation, filtre BTC | −33,5 % | −55,5 % [−71 ; −22] | −37,6 % | −71,9 % |
| Rotation, filtre BTC, détention 3 j | −41,7 % | −35,7 % | −25,9 % | −2,3 % |
| **Rotation, filtre largeur** | +14,7 % [−36 ; +46] | **−0,6 % [−31 ; +58]**, 2/6 positives | +18,8 % | −49,8 % |
| Rotation, largeur, détention 3 j | +39,2 % | +76,6 % [+14 ; +149] | +101 % | +31,9 % |
| Rotation, largeur, volatilité cible | +3,3 % | −4,4 % | +20,0 % | −25,8 % |
| Rotation, largeur, ordres limités | — | −0,3 % [−16 ; +38] | — | — |
| *Réf.* euros | 0 % | 0 % | 0 % | 0 % |
| *Réf.* achat passif BTC | +61,7 % (DD −53 %) | +61,7 % | +61,7 % | +66,9 % |
| *Réf.* BTC filtré par sa tendance | +34,0 % | +48,5 % | +50,5 % | +53,6 % |
| *Réf.* BTC, exposition constante = exposition réelle de « largeur » (≈ 17 %) | +12,7 % | +13,5 % (DD −11 %) | +13,4 % | +15,0 % |
| *Réf.* équipondéré 5 marchés liquides, mensuel | −46,3 % | −44,4 % | −45,5 % | −45,6 % |

- **Meilleure configuration** (Sharpe médian) : 4 h largeur détention 3 j, Sharpe 0,66, **DSR 0,11** (1 566 essais
  cumulés) ; en 8 h, DSR 0,027 ; en 1 h, DSR 0,055. Seuil : 0,95. **Aucune n'est significative.**
- La configuration « détention 3 j » a été choisie a posteriori parmi plusieurs : ses chiffres sont gonflés par la
  sélection (c'est exactement ce que mesure le DSR). Elle bat l'exposition constante, mais **jamais** l'achat passif de BTC
  en 1 h, en 1 j, ni dans la seconde moitié.
- Sensibilité à la décote de radiation (0 / 0,10 / 0,50) : **identique** (0 radiation subie). Les ordres limités ne
  changent pas le signe.
- Exposition réellement investie : 12 à 19 % pour les variantes « largeur ». La référence à exposition constante
  équivalente fait +13 à +15 % avec un drawdown de 11 %.

## 3. Avantage élémentaire des signaux (priorité 3) [HISTORIQUE]

Script `scripts/research_signals.py`, méthode `backend/research/signals.py` (agent dédié ; 11 tests).

- **Conception** :
  - bougies 1 h agrégées en jours, pour **les 24 heures de départ** poolées (aucune heure choisie) ;
  - 4 traitements des données (agrégation stricte ou tolérante × trous au dernier prix ou exclus), le principal étant
    fixé à l'avance ;
  - coûts aller-retour de 2 × (0,25 % + demi-spread de Corwin-Schultz, plancher de 5 pb) ;
  - horizons de 1, 3, 7 et 14 jours ;
  - IC95 par bootstrap par blocs de jours.
- **Signaux** : tendance BTC, largeur (binaire, terciles), dispersion, volatilité du marché, liquidité, persistance
  (marché) ; force relative 7 j, momentum absolu 30 j, volatilité, liquidité, autocorrélation et séries (actif).
- **Tests comptés : 864** (228 dans le traitement principal). 245 ont un p < 5 % brut, 114 après **Holm global**, 171
  après **BH global**. Les sous-périodes (3 360) et les phases (16 128) sont descriptives et ne servent pas de tests.
- **Résultat** :
  - les significatifs sont presque tous des **rendements nets NÉGATIFS** de l'état « haut » (−0,8 à −3,3 % par
    période) : **les coûts dominent tout** ;
  - les contrastes haut − bas robustes (24/24 phases de même signe, deux moitiés de même signe) sont : volatilité de
    l'actif **négative** (−1,2 %/j à 1 j, −4,5 % à 14 j), autocorrélation de l'actif **+0,7 %/j**, persistance du
    marché sur BTC **+0,5 %/j**. Ce sont des **écarts bruts entre deux groupes**, pas des portefeuilles réalisables ;
    leur état « haut » reste négatif après coûts ;
  - la tendance BTC sur BTC donne +0,33 %/j (p brut = 0,033) : **non significative après Holm**.
- **DSR** des 144 stratégies dérivées (1 344 essais) : toutes ≈ 0. La meilleure a un Sharpe annualisé de 1,01, avec un
  DSR ≈ 4·10⁻²¹.
- **Stabilité entre traitements** : corrélation 0,86–0,94, accord de signe 79–93 %.

### 3.1 Le filtre de largeur a-t-il un avantage propre ? Non.

| Cible | Signal | Exposition constante (même moyenne) | BTC seul (tendance) | BTC ∧ largeur | Effet minutage [IC95] | p contre minutage aléatoire (Holm) |
|---|---|---|---|---|---|---|
| BTC | −1,1 %/an | +1,3 %/an | −3,1 %/an | −1,5 %/an | −0,02 [−0,25 ; +0,21] | 0,14 (1,0) |
| Marché | −43,9 %/an | −52,7 %/an | −86 %/an | −37,7 %/an | +0,09 [−0,29 ; +0,59] | 0,11 (1,0) |

- **Décomposition** : sur le marché, l'effet total est presque entièrement un **effet d'exposition** (+1,15) ; l'effet de
  minutage est nul ou non significatif.
- **Exposition** les jours de baisse contre les jours de hausse : 0,298 / 0,303 (BTC), 0,308 / 0,318 (marché). Le filtre
  ne sort **pas** davantage pendant les baisses.
- **Largeur à état BTC fixé** : aucun effet significatif (Holm 1,0 partout).
- **Comparaison des deux études** : dans le moteur de portefeuille (§ 2), la rotation avec largeur perd moins que les
  autres rotations parce qu'elle est moins investie. Dans l'étude des signaux, la même logique, rendue comparable à une
  exposition constante, ne bat pas un minutage aléatoire.

## 4. Robustesse temporelle et essais multiples (priorité 4)

- **Toutes** les heures de rééquilibrage sont évaluées et la médiane est publiée ; aucune heure n'est choisie.
  - Dispersion entre phases (4 h, largeur) : de −31 % à +58 %.
  - Dans l'étude des signaux : 24 phases poolées, distribution par phase publiée.
- **Horizons** : 1, 3, 7, 14 jours. **Intervalles** : 1 h, 4 h, 8 h, 1 j. **Périodes** : deux moitiés et chaque année.
- **Hypothèses comptées** :
  - phase 5, moteur : 136 essais (66 en 4 h, 42 en 1 h, 28 en 8 h/1 j) ;
  - phase 5, signaux : 864 p-valeurs et 144 stratégies dérivées ;
  - phases 3 à 4b : ≈ 1 200–1 500 (ordre de grandeur documenté).
  - DSR calculé sur **1 528 à 1 566** essais cumulés ; Holm et BH sur les 864 tests.
- **Sensibilité au décalage** : c'est le premier facteur de variance. Le signe change selon la phase pour la
  plupart des configurations positives.

## 5. Candidats prospectifs v1, v2, v3 (priorité 2)

| Génération | Figée | Moteur | Défauts connus (publiés) | Statut |
|---|---|---|---|---|
| v1 (5 candidats) | 25/11/2026 20:07 UTC, `9003293` | v1 figé | décote de −50 % sur bougie incomplète, agrégation stricte (filtre de liquidité caché), fin d'échantillon | évaluée **telle quelle** pour la traçabilité ; **pas une validation financière** |
| v2 (4 candidats) | 25/11/2026 21:45 UTC, `89698a1` | v2 figé | idem + « Kelly » qui agit en fait comme un filtre de momentum | idem ; diagnostic hors gel publié à côté |
| **v3 (7 candidats)** | **26/11/2026 10:36:55 UTC**, parent `1917023` | **v4 validé** | aucun défaut ouvert connu | départ le **27/11/2026 à 00:00 UTC** |

**Règles v3** (`reports/prospective/v3/regle_v3.md`, commitée **avant** toute exécution du moteur v4 sur données
réelles), gel `reports/prospective/v3/candidats_figes_v3.json` :
- **Figé** :
  - code (empreintes sha256 de 6 fichiers), paramètres, dimensionnement, seuils de rotation, critères
    d'entrée et de sortie, exécution (marché, ou limite valable 1 bougie puis abandon) ;
  - critères statistiques à 6 et 12 mois ;
  - date de départ.
- **Évaluation sans choix d'heure** : chaque candidat est évalué sur **ses 6 phases**, chacune étant un compte de 100 €.
  Le score est la **moyenne** des comptes.
- **Candidats a priori** :
  - rotation avec largeur : au marché, à volatilité cible, en ordres limités ;
  - témoin sans filtre ;
  - références : BTC filtré, BTC à exposition constante de 25 %, équipondéré 5.
- **Prédiction = décision complète** pour la dernière bougie 4 h clôturée, pour chaque compte :
  - marché, sens, montant en €, quantité ;
  - type, prix limite, validité, conditions d'annulation ;
  - état du compte.
- **Rejeu exact** : à chaque évaluation, tout le passé est rejoué et doit reproduire chaque décision enregistrée,
  **sinon l'évaluation est INVALIDE**. Le journal est chaîné par sha256. Un test sur données synthétiques vérifie qu'une
  décision falsifiée est détectée (`tests/test_prospective_v3.py`).
- **Discrimination** : les candidats diffèrent par leurs **ordres** (montants, types et marchés), et non par un score.
  Aucune probabilité ni aucun score de confiance n'est produit.

### 5.1 Performance réellement observée [PROSPECTIF]

Données disponibles du 26/11/2026 00:00 au 26/11 ≈ 10:00 UTC (évaluation n° 11) :
- **v1** : valide, 0 transaction, régime défavorable.
- **v2** : valide, chaîne intacte (8 lignes), 0 transaction sauf le témoin sans filtre. Son rétrospectif « tel que
  figé » affiche −39,5 % sur 3 bougies : c'est le **défaut connu** de décote de fin d'échantillon (3 « radiations » fictives).
  Corrigé hors gel, le diagnostic donne −0,7 %.
- **v3** : valide, 0 prédiction (départ le 27/11), rejeu sans divergence. Exécution : 41 s pour 7 candidats × 6 comptes.
- **Aucune conclusion de performance n'est possible** sur 10 heures de données.

## 6. Dimensionnement (priorité 5)

Module `backend/portfolio/sizing_math.py` : **décision d'investir**, **sélection** et **montant** sont trois fonctions
séparées. Chaque formule est vérifiée sur un cas à solution connue (30 tests, `tests/test_sizing_math.py`) :
- **Kelly** : f = μ/σ² en rendements simples, sans filtre ;
  - le « Kelly » v2 utilisait la moyenne des log-rendements et donnait un poids nul dans 50,4 % des cas à dérive nulle ;
    **c'est un filtre de momentum, pas du Kelly** (constat publié).
- **Volatilité cible** : poids = cible/σ, plafonné.
- **CVaR historique** : k = ⌈n(1−α)⌉ pires pertes. Le minimum de 3 observations de `backend/portfolio/risk.py` rendait
  l'estimateur **moins** prudent quand n < 60 : **corrigé**.
- **Bootstrap par blocs** : le dernier bloc n'était jamais tirable : **corrigé**.
- **Optimisation robuste** : covariance de Ledoit-Wolf, puis variance minimale (vérifiée sur une matrice diagonale ;
  poids ∝ 1/σ²).
  - Dans `objective_j`, le terme d'incertitude n'a aucun effet (constat publié).
  - `decision.py` fixe √24 en dur (constat publié ; hors moteur de recherche).
- **Règle de l'utilisateur** : au-delà d'un risque de 45/100, réduction par (1 − (r−45)/55)². Elle est appliquée à
  l'entrée et désormais aussi aux **positions conservées** (réduction seulement).
  - `position_cap` réduit dès r = 0, ce qui est plus prudent ; c'est documenté.
- Aucun calcul n'utilise de probabilité inventée ni de score de confiance.

## 7. Exécution (priorité 6)

- **Collecte quotidienne de microstructure** (`.github/workflows/nelyqor-microstructure.yml`) **centrée sur les marchés
  réellement sélectionnés** par les candidats v3 (`scripts/select_micro_markets.py` : marchés des prédictions et
  positions du journal v3, plus ceux que la rotation a traités en rejouant les 90 derniers jours).
  - 1er run : 20 marchés retenus, dont BTC, UNI, PUMP, GRASS, ORCA, COTI, KMNO, BEAM, ARB, NEAR, QNT, STRK, PARTI,
    ZAMA, VET, ZEN, SOLV, FIL, NPC et SOMI.
  - Ce sont surtout de **petits marchés**, absents des 6 grands marchés de la phase 4.
  - **40 carnets par marché** ont été collectés.
- **Défaut trouvé au 1er run** : la collecte en direct des transactions publiques échouait, car Bitvavo exige
  `start` et `end` ensemble ; **0 transaction en direct a été collectée**. C'est corrigé (commit `053b442`) et relancé.
- **Modélisé dans le simulateur** :
  - file d'attente et non-exécution : traversée exigée plus plafond de volume ;
  - délai : exécution à t+1, validité, reports SANS_TRANSACTION ;
  - coût d'opportunité : achats annulés, euros conservés, comptés ;
  - repli au marché.
  - La sélection adverse a été mesurée en phase 4 sur 6 grands marchés (les ordres manqués étaient les meilleurs).
- **Interdiction d'extrapoler respectée** : les résultats d'exécution de la phase 4 (6 grands marchés, accord de
  94–100 %) **ne sont pas transposés** aux petits marchés sélectionnés. Leur exécution reste **non validée**. Il
  faudra accumuler plusieurs semaines de carnets et de transactions sur ces marchés avant de rejouer les ordres.

## 8. Sécurité (priorité 7)

### 8.1 Séparation des bases et capital authentifié (commit `b9032b0`, agent dédié ; fusion `f5cfbe8`)

| Base / volume | Moteur | API |
|---|---|---|
| `engine.db` (journal, intentions, flux, kv) | lecture et écriture | **aucun accès** |
| `commandes.db` (une seule table `control_commands`) | **lecture seule** (`:ro`), contenu non fiable | écriture |
| `affichage.db` (copie d'affichage) | écriture, **jamais relue** | lecture seule |
| `api.db` (audit API) | aucun accès | écriture |
| `/state` (magasin signé, `ARRET_URGENCE`) | lecture et écriture | aucun accès |

- **Capital authentifié** : un achat réel est dimensionné sur **min(journal, soldes Bitvavo lus par le moteur)**, relus
  à chaque cycle. Les achats sont **refusés** dans trois cas :
  - un écart journal/soldes supérieur à max(0,50 € ; 0,5 %) ;
  - des soldes illisibles ;
  - des soldes datant de plus de 3 min.
  Les ventes et les protections restent permises, et une notification de sécurité est émise.
- **Bornes relatives** : un ordre ne dépasse ni **35 % du capital authentifié** ni l'EUR libre authentifié. Un ordre de
  300 €, qui passe sous l'ancien plafond de 500 €, est **bloqué** sur un capital de 100 € (test).
- **Gonflement de l'équité** : un faux dépôt écrit par l'API n'a **aucun effet** sur la taille des achats (test).
- **Commandes signées, anti-rejeu, révocation** : non régressés. Une même commande ne s'exécute jamais deux fois, même
  remise « en attente ».
- **Intégrité comptable** : vérifiée après un vrai remplissage.
- Le réel exige un nouveau pré-requis `bases_separees`.

### 8.2 Contre-expertise adversariale indépendante (API supposée compromise, attaques exécutées)

| N° | Gravité | Attaque | Correction (commit `c8bfb21`) |
|---|---|---|---|
| 1 | **élevée** | une vue SQL récursive ou une FIFO à la place de `commandes.db` **gelait** la boucle du moteur (ventes, protections et arrêt SSH compris) | lecture du fichier en octets, bornée à 8 Mo ; fichier régulier uniquement (`lstat`, `O_NOFOLLOW`, `O_NONBLOCK`) ; base **en mémoire privée** (`deserialize`) ; schéma exact (ni vue, ni déclencheur) ; `trusted_schema=OFF` ; budget d'instructions SQLite |
| 2 | **élevée** | une charge utile de 200 Mo provoquait une `MemoryError`, qui faisait sauter la gestion des positions à chaque cycle | taille filtrée **dans le SQL** ; toute exception du canal est isolée ; `manage_positions` a toujours lieu |
| 3 | **élevée** | remplissage du disque du moteur (+36,6 Mo en 4 cycles) | 5 commandes nouvelles au plus par cycle ; seule l'**empreinte** sha256 est journalisée ; pause et arrêt valables 7 jours ; purge à 8 jours |
| 4 | moyenne | famine : 500 vieilles pauses masquaient un arrêt d'urgence récent | arrêt d'urgence lu **en priorité** ; identifiants déjà pris exclus |
| 5 | moyenne | `bases_separees` validé avec les trois bases dans un même répertoire inscriptible | exige un canal **non inscriptible** par le moteur (drapeau ST_RDONLY du montage `:ro`) et aucun lien symbolique |
| 6 | faible | un verrou exclusif de l'API bloquait chaque lecture pendant 10 s | aucun verrou partagé : la lecture se fait en octets |
| 7 | faible | lien symbolique vers `engine.db` | refusé |
| 8 | faible | un flux réel pouvait être rapproché sans signature si la clé était absente | refusé |

- **Résultat après correction** : les attaques A1–A8 de l'auditeur et le script mémoire/FIFO **échouent** (lecture en
  moins de 1 s, 0 commande lue).
- **Résistances confirmées par l'auditeur** : JSON profond, déclencheurs, `ATTACH`, signatures rejouées ou
  réutilisées, course soldes/remplissage (second achat refusé), montages `:ro` dans `docker compose config`.
- **9 tests de non-régression** : `tests/test_phase5_canal_durci.py`.

### 8.3 Risques résiduels

- **Commandes de l'iPhone** : une API compromise peut les supprimer, les retenir ou saturer le canal (au-delà de 8 Mo :
  aucune commande lue). L'arrêt d'urgence par SSH (`/state/ARRET_URGENCE`) reste indépendant (testé).
- **Affichage** : une API compromise peut mentir à l'affichage pour amener le propriétaire à signer. La signature lie
  les champs, pas ce que le propriétaire a vu.
- **Écart journal/soldes sous la tolérance** (0,50 € ou 0,5 %) : il n'est pas signalé, mais reste sans effet sur la
  taille des achats, puisque c'est le minimum qui compte.
- **Non vérifié ici** :
  - `docker compose` réel et comportement de SQLite sur de vrais montages `:ro` entre conteneurs ;
  - recette Playwright de la PWA après les modifications de la phase 5 ;
  - option `--forwarded-allow-ips "*"`, à confronter à la configuration de Caddy.
- **Migration** `nelyqor.db` → `engine.db` : manuelle, documentée dans `docs/DEPLOIEMENT.md` § 9. La première
  réconciliation est à vérifier.
- **Root sur l'hôte** : il voit tous les volumes.

## 9. GitHub et PR n° 1 (priorité 8)

- **Revue complète par un agent** :
  - aucun secret ;
  - permissions minimales : `contents: read` partout, sauf le job prospectif qui a `contents: write` pour la seule
    branche d'archive, `issues: write` pour l'alerte et `actions: read` pour l'import ponctuel ;
  - CI verte.
- **Recommandations faibles non appliquées** : épingler les actions par SHA, `unsafe-inline` dans la CSP, captures
  d'écran dans le dépôt.
- **Ajouts de la phase 5** :
  - **stockage durable** : sur la branche par défaut, les journaux prospectifs sont restaurés depuis la branche
    `prospective-journal`, puis y sont **ajoutés** (ajout seul vérifié : l'archive doit être un préfixe du nouveau
    journal). Il y a un commit par évaluation, avec la tête de chaîne dans le message ;
  - **alerte** : le job échoue et ouvre ou met à jour l'issue « Évaluation prospective en échec » dans six cas :
    évaluation invalide, candidat en erreur, chaîne rompue, journal réécrit, divergence de rejeu v3, ou journal perdu
    plus de 24 h après le départ ;
  - **rattrapage documenté** : un second créneau quotidien (14:43 UTC) est sauté si une évaluation a réussi depuis moins
    de 20 h. Les créneaux manqués sont comptés et **jamais reconstitués** ; les prédictions ne sont jamais rétroactives.
    Une entrée `seed_run_id` permet d'importer **une fois** l'artefact d'un run antérieur. Avant fusion, le journal
    n'existe que dans le cache de branche et dans les artefacts (90 j), par exemple les runs 45763513043 et 45803015429.
  - workflows `nelyqor-research-p5.yml` (recherche à la demande) et `nelyqor-microstructure.yml` (quotidien).
- **Non fusionnée** : la fusion appartient au propriétaire. Tant qu'elle n'est pas faite, **aucune évaluation
  planifiée** n'a lieu (GitHub ne planifie que sur la branche par défaut).

## 10. Infrastructure et coûts réels (priorité 9)

- **Aucun serveur commandé.**
- **GitHub Actions**, dépôt public, minutes des runners standards non facturées :
  - prospectif ≈ 3–4 min par évaluation (v3 : 41 s) ;
  - microstructure ≈ 12 min par jour ;
  - recherche p5 ≈ 11 min par intervalle (à la demande, à suspendre).
- **Cache** : ≈ 0,3 Go de marché, plus ≈ 5 Mo de microstructure qui croîtront, sur 10 Go.
- **Coût mesuré : 0 €.** Ce n'est **pas** une garantie de rentabilité. Le coût économique réel est celui du marché :
  dans le moteur v4, les frais et spreads simulés absorbent l'essentiel des rendements bruts des rotations, soit
  plusieurs centaines d'ordres sur 3 ans pour 100 €.
- Pour un éventuel mode réel futur, seul un appareil déjà allumé (≈ 0–2 €/mois d'électricité [HYPOTHÈSE]) est compatible
  avec 100 €. GitHub Actions n'est pas un serveur de trading.

## 11. Tests et vérifications

- `pytest` : **352 tests réussis**, dont :
  - 46 scénarios du moteur v4 calculés à la main ;
  - 30 de dimensionnement ;
  - 11 de signaux ;
  - 4 du prospectif v3 et de son contrôle ;
  - 21 de séparation des bases ;
  - 9 de durcissement du canal.
- `ruff` : aucune erreur. `mypy` : aucune erreur (68 fichiers).
- Les scénarios des deux auditeurs du simulateur et les attaques de l'auditeur de sécurité ont été rejoués contre le code
  final.
- Données réelles : les runs CI listés en tête. Les données synthétiques n'ont servi qu'aux tests de code (marquées
  [SYNTHÉTIQUE], jamais publiées comme résultats).

## 12. Limites statistiques

- **Historique consulté à de multiples reprises** : rien de ce qui en sort ne peut être une validation. ≈ 3 ans de
  données, soit un seul cycle haussier puis baissier de BTC (première moitié +183 %, seconde −43 %).
- **Phases peu nombreuses** : 6 au plus par intervalle ; les médianes sur 6 comptes sont très instables.
- **IC95 très larges** : celui de l'achat passif de BTC va de −67 % à +859 %. Une stratégie vraiment meilleure serait
  indiscernable en 3 ans.
- **Coûts de petits marchés** : estimés (Corwin-Schultz), non mesurés sauf sur 6 grands marchés.
- **Prospectif v3** : il faudra **≥ 100 transactions** et 6 mois avant la première évaluation ; il est très probable que
  les critères figés ne soient pas atteints.

## 13. Conditions d'une éventuelle activation réelle future (aucune n'est remplie)

1. Un candidat **v3** satisfait **tous** les critères figés à 6 **et** 12 mois [PROSPECTIF] :
   - rendement net moyen positif, supérieur à l'achat passif de BTC et à l'exposition constante ;
   - borne basse de l'IC95 du rendement journalier positive ;
   - au moins 100 transactions ;
   - drawdown médian supérieur à −35 %.
2. Les évaluations sont valides sans interruption : chaîne intacte, rejeu exact, archive en ajout seul, créneaux
   manqués expliqués.
3. Exécution validée sur **les marchés réellement sélectionnés** : ordres rejoués sur carnets et transactions
   collectés, écart avec le simulateur borné.
4. Recette de déploiement réelle :
   - `docker compose` avec les montages `:ro` vérifiés ;
   - `bases_separees` vrai ;
   - PWA 14/14 ;
   - preuve de recette signée ;
   - migration vérifiée.
5. Activation par le propriétaire seulement, avec une phrase de signature distincte. Le capital est authentifié, et la
   taille d'ordre est relative au capital (35 %), pas au plafond de 500 €.
6. Même dans ce cas : le montant reste limité à ce que l'utilisateur accepte de perdre entièrement. Pas de levier, pas
   de martingale, pas de dépôt automatique.

## 14. Fichiers principaux de la phase 5

- **Moteur et stratégies** : `backend/research/xsection_v4.py`, `backend/research/strategies_v4.py`.
- **Tests du moteur** : `tests/test_xsection_v4.py`, `tests/test_xsection_v4_audit2.py`.
- **Signaux** : `backend/research/signals.py`, `scripts/research_signals.py`, `tests/test_signals.py`.
- **Recherche** : `scripts/research_phase5.py`, `.github/workflows/nelyqor-research-p5.yml`.
- **Prospectif v3** : `scripts/prospective_v3.py`, `reports/prospective/v3/{regle_v3.md, candidats_v3.json,
  candidats_figes_v3.json}`, `tests/test_prospective_v3.py`.
- **Contrôle prospectif** : `scripts/prospective_check.py`, `.github/workflows/nelyqor-prospective.yml`.
- **Microstructure** : `scripts/select_micro_markets.py`, `scripts/collect_microstructure.py`,
  `.github/workflows/nelyqor-microstructure.yml`.
- **Dimensionnement** : `backend/portfolio/sizing_math.py`, `backend/portfolio/risk.py`.
- **Sécurité** :
  - code : `backend/storage/channels.py`, `backend/accounting/authenticated.py`, `backend/runner.py` ;
  - tests : `tests/test_phase5_separation.py`, `tests/test_phase5_canal_durci.py` ;
  - documentation : `docs/SECURITE.md`, `docs/DEPLOIEMENT.md` § 9.
