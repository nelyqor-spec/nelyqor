# NELYQOR TRADING — Rapport d'audit phase 3 : recherche de rentabilité et optimisation quantitative

Date : 25/11/2026. Branche `claude/new-session-cv44ga`. **Mode réel : désactivé** (aucun ordre réel, aucune clé
utilisée, aucun argent engagé). Tous les chiffres ci-dessous viennent de calculs réellement exécutés ; les
résultats de recherche proviennent du run GitHub Actions `nelyqor-research` n° 45625708694 (données publiques
Bitvavo, sans clé), lus dans les journaux des jobs (marqueurs `PHASE3_JSON_DEBUT/FIN`).

## 0. Conclusion en une page

- **Aucune stratégie n'a démontré d'avantage financier robuste après tous les coûts.** Aucune n'est promue.
  Les critères de promotion n'ont pas été assouplis.
- Le meilleur résultat transversal (`EUR_4h / detention_min_3j`, +82,4 % sur ~3 ans, Sharpe 0,69) est
  **non significatif** une fois corrigé du nombre d'essais (DSR 0,19 pour 106 essais ; il faudrait ≥ 0,95),
  avec un drawdown maximal de −55 %, un IC95 du rendement de [−62 % ; +974 %], et il **disparaît** aux
  intervalles voisins (1 h −34 %, 2 h −10 %, 12 h −27 %, 1 j −34 %). Le walk-forward « choisir la meilleure
  variante sur le passé puis l'appliquer » donne +8,6 % (4 h) et +0,4 % (8 h), négatif ailleurs.
- Après coût d'hébergement, même ce meilleur cas est **négatif** dès 3 €/mois (−26 % du capital sur 3 ans) ;
  à 7,19 €/mois l'hébergement seul (≈ 259 €) dépasse le capital de 100 €. Avec 100 € de capital, **seul un
  hébergement à 0 €** (ordinateur déjà allumé / offre gratuite) est compatible avec un résultat non négatif,
  et uniquement si l'avantage devient réel — ce qui n'est pas démontré.
- L'exécution par ordres limite (postOnly) est le seul levier mesuré qui améliore nettement tous les
  intervalles testés (ex. 4 h : marché −17,3 % → limite +24,1 %, 10,91 € de frais économisés), mais il repose
  sur un modèle de remplissage prudent qui **doit être vérifié en paper trading réel** avant toute confiance.
- EUR bat USDC sur les 8 actifs comparés, à toutes les tailles (20/100/1000 €), avec les hypothèses de frais
  retenues ; USDC n'a que 7 marchés avec historique suffisant.
- Les modèles ML (logistique, LightGBM, XGBoost, ridge bayésien, régression quantile) **n'apportent aucun gain
  net** exploitable : tous rejetés. Les bases simples restent la référence.
- **Résultats indépendants disponibles : aucun.** Les candidats ont été figés le 25/11/2026 à 20:07:04 UTC
  (commit `9003293`), avant le cutoff du 26/11/2026 00:00 UTC ; la validation prospective commence demain et
  ne pourra conclure qu'après plusieurs mois.

**Recommandation : ne pas activer le mode réel.** Laisser tourner la simulation et l'évaluation prospective.

## 1. Nouveaux défauts trouvés en phase 3 et corrections

| # | Défaut | Gravité | Correction | Test |
|---|---|---|---|---|
| 1 | API compromise pouvait déposer une commande « activer le réel » / « liquider » (seul le mot de passe, vérifié par l'API, protégeait) | Critique | Commandes sensibles **signées dans l'iPhone** (PBKDF2-SHA256 600 000 it. + HMAC-SHA256, WebCrypto), vérifiées par le moteur avec `secrets/owner_command_key` que l'API ne possède pas ; horodatage 5 min, nonce anti-rejeu | `tests/test_command_sig.py` (équivalence Node/Python), `test_activate_real_end_to_end_requires_everything`, e2e PWA 14/14 |
| 2 | Moteur transversal : ordres au marché exécutés dans la bougie du signal (biais d'anticipation sur l'équité) | Élevée | Ordres mis en file et exécutés à l'ouverture de i+1 | `test_xsection.py` |
| 3 | Z-score transversal NaN quand la dispersion est nulle → tous les scores NaN | Moyenne | Dispersion nulle → score 0 | `test_xsection.py` |
| 4 | Réallocation pouvait tourner pendant une PAUSE (rotation inutile, frais) | Moyenne | Réallocation seulement en états SIMULATION/REAL | `tests/test_actions.py`, résilience |
| 5 | Simulateur : champs officiels absents (`selfTradePrevention`, `visible`, `timeInForce`) | Faible | Ajoutés, conformes au SDK officiel | `test_bitvavo_conformity.py` |
| 6 | Flux WebSocket : identifiant d'exécution `fillId` (WS) ≠ `id` (REST) — risque de doublon vérifié | — (pas un défaut) | Constat : un événement WS `order`/`fill` ne fait que déclencher une relecture REST ; seuls les fills REST (champ `id`) sont enregistrés (`INSERT OR IGNORE`), donc aucun doublon possible | revue de code `runner.on_ws_event`, `engine._apply_order` |
| 7 | Grand livre mono-devise : un fill USDC aurait été compté en EUR | Élevée (si USDC) | Grand livre multi-devises ; `quote_eur_rate` obligatoire pour toute devise ≠ EUR | `test_accounting.py` (USDC) |
| 8 | Contrôle de type masqué dans un script (`mypy \| tail -1` masquait le code retour) | Faible | Typage corrigé ; CI appelle `python -m mypy` directement | CI recette |
| 9 | Recherche « strat » plantait sur univers vide | Faible | Garde explicite | smoke synthétique |
| 10 | Résolution d'un marché sans historique avant listing | Moyenne | Éligibilité point-in-time (âge minimal, liquidité passée) ; aucune donnée antérieure au listing | `test_xsection.py` |

Défauts connus non corrigés (signalés) :
- `acceptance_tests_ok` reste une valeur non signée en base (positionnée par le script de recette).
- Pause / arrêt d'urgence / reprise restent déposables par une API compromise (actions conservatrices, choix assumé).
- La recherche « strat » partage une base LAB dans un même run : plusieurs familles échouent au critère
  `holdout_vierge` (le holdout a déjà été consulté par un essai précédent). C'est le comportement voulu
  (prudence), mais cela rend ces lignes non promouvables par construction.

## 2. Tests

- **183 tests** passent localement (`pytest`), `ruff` et `mypy` propres ; CI recette `nelyqor-trading` verte
  sur les commits précédents (run du commit final en cours au moment de la rédaction).
- Nouveaux en phase 3 : `test_xsection.py` (7), `test_venue.py` (4), `test_actions.py` (5),
  `test_command_sig.py` (2), `test_bitvavo_conformity.py` (2), `test_prospective.py` (2), compléments
  quant (filtres, univers maximal), comptabilité USDC, résilience (activation/liquidation signées).
- Recette PWA (Playwright, `scripts/e2e_pwa.cjs`) : 14/14, dont signature navigateur acceptée par le moteur
  et refus d'activation lorsque les pré-requis réels manquent.

## 3. Données : marchés et périodes

| Jeu | Contenu |
|---|---|
| 1 h, univers | **369 / 435** marchés collectés (362 EUR, 7 USDC), **5 740 115** bougies, 278 Mo ; budget de temps atteint, 66 marchés peu liquides (dont la plupart des USDC) complétés aux runs suivants (cache incrémental) |
| Période | jusqu'à ~3 ans (1 100 jours) avant le 25/11/2026 ; chaque marché ne commence qu'à son listing (aucune donnée antérieure) |
| 15 min | 30 marchés les plus liquides, 2 ans ; trous listés |
| 4 h natif | 5 marchés (contrôle de l'agrégation causale 1 h → 4 h) |
| Intervalles dérivés | 2 h, 4 h, 8 h, 12 h, 1 j par agrégation **causale** de 1 h (`backend/data/resample.py`) |
| Instantané carnets | 435 marchés (spreads), profondeur pour 68 |

Contrôle du biais de survivance : l'univers transversal est **point-in-time** (seuls les marchés listés,
assez anciens et assez liquides à la date t sont éligibles) ; un marché qui disparaît avec position ouverte
subit une décote de 50 %. Limite : l'API ne renvoie que les marchés **encore** listés ; les marchés retirés
avant aujourd'hui sont absents (biais résiduel favorable). La recherche « strat » utilise 10 survivants avec
≥ 2 ans d'historique : biais de survivance explicite.

**Toute la période jusqu'au 25/11/2026 est de la RECHERCHE**, pas une validation indépendante.

## 4. Résultats détaillés par stratégie (tous coûts inclus : taker 0,25 %, maker 0,15 %, glissement 5 pb, spread estimé)

### 4.1 Classement transversal (rotation top 3 parmi tout l'univers, filtre de régime BTC)

Rendement net sur ~3 ans / drawdown max / nombre de transactions, EUR (362 marchés) :

| Variante | 1 h | 2 h | 4 h | 8 h | 12 h | 1 j |
|---|---|---|---|---|---|---|
| base | −11 % / −66 % / 325 | −37 % / −78 % | −17 % / −70 % / 234 | +0 % / −66 % | −27 % / −70 % | −38 % / −72 % |
| sans_filtre_regime | −56 % / −79 % | −62 % | −77 % / −87 % | −74 % | −80 % | −84 % / −93 % |
| moins_de_rotation | −19 % | −52 % | −38 % | −24 % | −16 % | −27 % |
| seuil_apres_frais | −4 % | −36 % | −13 % | +5 % | −26 % | −25 % |
| stop_suiveur_3atr | −77 % | −73 % | −60 % | −29 % | −39 % | −45 % |
| momentum_seul | −35 % | −35 % | −23 % | −24 % | −27 % | −32 % |
| tendance_seule | −35 % / −57 % | −37 % | −31 % / −52 % | −25 % | −34 % | −23 % / −49 % |
| detention_min_3j | −34 % | −10 % | **+82 % / −55 % / 226** | **+82 % / −58 % / 232** | −27 % | −34 % |

USDC (7 marchés seulement, frais taker EUR appliqués par prudence) : entre −13 % et +14 % sur la plupart des
variantes, drawdowns −8 à −21 % ; `sans_filtre_regime` −44 à −58 % partout.

Lectures :
- Le **filtre de régime** est l'élément le plus robuste : le retirer dégrade chaque intervalle (−44 à −84 %).
- Le résultat +82 % n'existe qu'à 4 h et 8 h : signe typique de sur-ajustement / chance (8 variantes × 6
  intervalles × 2 devises = 106 essais). DSR 0,19 → **non significatif**.
- Par année (4 h, detention_min_3j) : 2024 +66,1 %, 2025 −17,6 %, 2026 +32,6 % — instable.
- Walk-forward (sélection de la variante sur le passé, application au futur, chaîné) : EUR 1 h −32,4 %,
  2 h −39,3 %, **4 h +8,6 %**, 8 h +0,4 %, 12 h −61,3 %, 1 j −56,6 % ; USDC 1 h +1,1 %, 2 h −17,3 %,
  4 h −11,0 %, 8 h −7,9 %, 12 h 0,0 %, 1 j −14,4 %.
- Taille de l'univers (4 h, EUR, base) : 5 marchés −31,3 %, 20 −53,3 %, 50 −10,3 %, 100/tous −17,3 % :
  pas de gain monotone à élargir l'univers.

### 4.2 Stratégies par marché (tendance, cassure, swing) avec filtres et sorties

10 marchés EUR (ADA, AVAX, BTC, DOGE, ETH, LINK, PEPE, SOL, SUI, XRP), filtres choisis par fold
(aucun / tendance / tendance+volatilité / complet), deux sorties (stop suiveur 3 ATR ; protection du bénéfice :
stop au point mort après 1 R + suiveur 2,5 ATR). 4 folds walk-forward purgés + holdout interne.
**720 essais (480 en 4 h–1 j, 240 en 1 h–2 h) ; aucune promotion.**

| Intervalle / famille / sortie | WF hors échantillon | Holdout | PF | Trades holdout | DSR |
|---|---|---|---|---|---|
| 1 h tendance / suiveur | −36,5 % | −5,8 % | 0,79 | 172 | 0,10 |
| 1 h cassure / suiveur | −30,5 % | −24,8 % | 0,42 | 273 | 0,00 |
| 1 h swing / suiveur | −19,5 % | +1,4 % | 1,19 | 62 | 0,18 |
| 1 h tendance / protection | −42,2 % | −6,2 % | 0,76 | 181 | 0,01 |
| 1 h cassure / protection | −31,5 % | −14,7 % | 0,51 | 220 | 0,00 |
| 1 h swing / protection | −18,5 % | −0,8 % | 0,89 | 62 | 0,05 |
| 2 h tendance / suiveur | −20,6 % | −2,0 % | 0,92 | 136 | 0,10 |
| 2 h cassure / suiveur | +3,8 % | −10,0 % | 0,61 | 125 | 0,00 |
| 2 h swing / suiveur | −6,8 % | −1,5 % | 0,72 | 28 | 0,02 |
| 2 h tendance / protection | −26,4 % | −1,8 % | 0,93 | 151 | 0,04 |
| 2 h cassure / protection | +8,7 % | −9,4 % | 0,61 | 125 | 0,00 |
| 2 h swing / protection | −6,6 % | −1,9 % | 0,63 | 28 | 0,01 |
| 4 h tendance / protection | −9,0 % | **+7,6 %** | 1,93 | 39 | 0,31 |
| 4 h tendance / suiveur | −4,5 % | +5,8 % | 1,58 | — | — |
| 12 h cassure / suiveur | — | +6,4 % | 6,51 | 9 | — |
| 1 j (toutes familles) | négatif | négatif | — | — | — |

Points clés : sans coûts, plusieurs lignes 1 h/2 h sont légèrement positives (+3 à +9 %) ; **les coûts
transforment ces gains en pertes** (frais doublés : −2 à −38 %). Les rares holdouts positifs ont trop peu de
transactions (< 100 exigées) et un walk-forward négatif.

### 4.3 Hybride
Pondération des familles par Sharpe d'entraînement : prévue mais non retenue faute de famille
individuellement positive hors échantillon (combiner des signaux négatifs ne crée pas d'avantage).

## 5. Comparaison EUR / USDC

Hypothèses (à confirmer par l'endpoint `/account/fees` du compte réel, non interrogé faute de clé) :
EUR maker 0,15 % / taker 0,25 % ; USDC maker/taker 0,05 % ; conversion EUR↔USDC 0,25 % taker ;
une conversion pour 20 transactions (« capital conservé en USDC »). Instantané réel : USDC-EUR bid 0,9136 /
ask 0,9137 (spread 1,1 pb) ; volatilité journalière USDC/EUR 0,54 %, écart max à la médiane 30 j 5,1 %.

Coût aller-retour total à 100 € (frais + spread + impact, points de base) :

| Actif | EUR | USDC par transaction | USDC capital conservé | Risque de change (1 σ) |
|---|---|---|---|---|
| BTC | 50,2 | 65,2 | 16,7 | 92,8 |
| ETH | 50,1 | 65,4 | 16,9 | 92,8 |
| SOL | 50,2 | 65,0 | 16,4 | 92,8 |
| SUI | 53,5 | 130,2 | 81,7 | 92,8 |
| XRP | 100,1 | 117,9 | 69,3 | 92,8 |
| ADA | 102,4 | 123,5 | 74,9 | 92,8 |
| DOGE | 103,1 | 127,7 | 79,2 | 92,8 |
| PEPE | 105,2 | 157,7 | 109,1 | 92,8 |

**Route choisie : EUR sur 8/8 marchés, à 20 €, 100 € et 1 000 €.** Garder du capital en USDC réduit les frais
de transaction sur BTC/ETH/SOL, mais ajoute un risque de change USDC/EUR (≈ 93 pb à 1 σ sur l'horizon) supérieur
à l'économie, et les carnets USDC sont plus minces (seulement 7 marchés exploitables historiquement).
Le moteur (`backend/execution/venue.py`) choisit la route au cas par cas ; aujourd'hui il choisit EUR.

## 6. Économies de frais simulées (postOnly / marché / hybride)

Règle : un ordre limite n'est **jamais** considéré rempli parce que le prix l'a touché ; il faut une traversée
d'au moins 2 pb et au plus 5 % du volume de la bougie ; sinon il expire (TTL) et l'opportunité manquée est
mesurée ; la sélection adverse (remplissage avant baisse) est mesurée.

| Intervalle (variante base, EUR) | Marché | Limite (postOnly) | Hybride (limite puis marché) |
|---|---|---|---|
| 1 h | −11,1 % | −0,4 % ; 13,83 € économisés ; 36 entrées manquées | **+10,1 %** |
| 4 h | −17,3 % | **+24,1 %** ; 10,91 € économisés ; 17 manquées | +16,6 % |
| 1 j | −37,6 % | +5,8 % | **+12,7 %** |

Interprétation : l'écart vient autant de l'**entrée à meilleur prix** (−5 pb) que des frais maker. Ces gains
dépendent fortement du modèle de remplissage ; l'évaluation prospective compare précisément `base_limite`
et `base_marche` sur le même signal pour le vérifier. Le comparateur d'actions (`backend/portfolio/actions.py`)
choisit acheter maintenant / limite / attendre selon la **borne basse** (espérance − 2 erreurs-types).

## 7. Résultats après tous les coûts, selon l'hébergement (100 €, ~3 ans)

| Stratégie | 0 €/mois | 3 € | 5 € | 7,19 € | 10 € |
|---|---|---|---|---|---|
| XS EUR 4 h detention_min_3j (meilleur cas, non significatif) | +82,4 % | −26,0 % | −98,3 % | −177,5 % | −279,0 % |
| XS EUR 4 h base (marché) | −17,3 % | −125,8 % | −198,1 % | −277,2 % | −378,8 % |

(Une perte > 100 % signifie que l'hébergement cumulé dépasse le capital.) **Aucune augmentation de capital
n'est recommandée pour masquer ce déficit** : le problème est l'absence d'avantage démontré, pas la taille.

Infrastructure moins chère (sources consultées le 25/11/2026) :
- **0 €** : calcul lourd sur GitHub Actions (déjà en place : collecte, recherche, prospectif) ;
  Oracle Cloud Always Free (Ampere 2 OCPU / 12 Go) — risque de récupération des instances inactives
  (CPU p95 < 20 % sur 7 jours) et disponibilité aléatoire.
- **≈ 0,5 €/mois** : Raspberry Pi / ordinateur domestique (électricité) — IP dynamique, à protéger.
- **≈ 7,13–7,19 € TTC** : Hetzner CAX11/CX23 après la hausse de juin 2026 (disponibilité incertaine).
- GitHub Actions **n'est pas un serveur permanent** : il ne peut ni tenir des stops ni réagir en continu.
  Les stops natifs hébergés par Bitvavo (phase 2) réduisent le besoin de disponibilité du moteur, mais
  un moteur en mode réel exige toujours une machine allumée.

## 8. Modèles retenus et rejetés (4 h, 289 marchés, 234 883 lignes, horizon 1 jour, coût aller-retour 0,7 %)

| Modèle | IC moyen (IC95) | Top quintile net vs univers (IC95) | Brier skill | Décision |
|---|---|---|---|---|
| Logistique | −0,016 (−0,035 ; +0,005) | −0,83 % (−0,98 ; −0,66) | +0,0005 (n.s.) | rejeté |
| LightGBM | −0,010 (−0,025 ; +0,005) | −0,71 % | +0,0079 (+0,004 ; +0,012) | rejeté |
| XGBoost | −0,012 (−0,027 ; +0,003) | −0,67 % | +0,0078 | rejeté |
| Ridge bayésien | −0,033 (−0,051 ; −0,014) | −0,75 % | — | rejeté |
| Quantile LightGBM q50 | **+0,055 (+0,040 ; +0,071)** | −0,61 % (−0,75 ; −0,45) | couverture q10–q90 78 % (visée 80 %) | rejeté |
| Référence momentum relatif | −0,011 | −0,62 % | — | référence |

Le modèle quantile a un **pouvoir de classement statistiquement positif** mais trop faible pour couvrir
0,7 % de coûts : le quintile supérieur sous-performe l'univers après coûts. LightGBM/XGBoost ont un léger
pouvoir de calibration (Brier skill > 0) sans valeur économique.

Régimes : « toujours investi » −0,94 %/période (IC95 −1,35 ; −0,49) ; règles haussières −0,60 %
(IC95 −1,92 ; +0,41) ; GMM haussier −1,55 % (9 périodes, non concluant). **Retenu : filtre de régime à
règles** (le plus simple, et le seul dont l'absence est nettement pénalisante en §4.1).

Dimensionnement : Kelly fractionné, CVaR et bootstrap par blocs sont implémentés pour le **contrôle** du
risque ; aucun n'augmente la taille. La réduction de taille au-delà d'un score de risque de 45/100 est
conservée (`risk_cut = 45`, testée).

## 9. Résultats indépendants disponibles

**Aucun à ce jour.** Les 5 candidats suivants ont été figés le 25/11/2026 à 20:07:04 UTC
(`reports/prospective/candidats_figes.json`, commit `9003293`, empreintes sha256 du code) ; seules les
bougies ≥ 26/11/2026 00:00 UTC compteront :

1. `xs_eur_4h_detention3j_hybride` — meilleur Sharpe de recherche, exécution hybride ;
2. `xs_eur_4h_base_limite` — test du modèle de remplissage limite ;
3. `xs_eur_4h_base_marche` — témoin au marché du même signal ;
4. `xs_eur_8h_detention3j_marche` — contrôle d'horizon ;
5. `strat_4h_tendance_protection` — non promu, suivi seulement.

Le workflow `nelyqor-prospective` évalue une fois par jour et journalise les **intentions** (marchés visés,
signaux) avant que leur résultat soit connu. Règle GitHub : la planification quotidienne ne s'exécute que
lorsque le workflow est sur la branche par défaut ; d'ici là il faut le déclencher à la main
(« Run workflow ») ou fusionner la branche. Toute modification du code figé rend l'évaluation « INVALIDE »
(signalé, jamais masqué). Un jugement statistique demandera **au moins 6 à 12 mois** (rotation 4 h :
~70 transactions/an).

## 10. Audit de sécurité (phase 3)

| Point | Constat | Statut |
|---|---|---|
| Intégrité de l'autorisation du réel | Activation = mot de passe + déclarations + phrase exacte + **signature iPhone** vérifiée par le moteur + pré-requis (recette, clés, réconciliation) + `NELYQOR_REAL_TRADING_ALLOWED=true` | corrigé, testé |
| Clés API | lues par le moteur seul (`read_secret` refuse API/LAB) ; jamais dans le frontend, les logs ou git | testé |
| Commandes forgées | signature HMAC + nonce + 5 min ; rejeu refusé ; champs signés uniquement | testé |
| Ordres dupliqués | `clientOrderId` unique (UUID) par intention, recherche par `clientOrderId` et dans les trades avant toute nouvelle soumission, quantité vendable contrôlée en transaction | testé (phase 2 + 3) |
| Stops hébergés | stops natifs Bitvavo, confirmés par relecture ; mise à jour par `PUT /order` (SDK officiel) | testé |
| Comptabilité | multi-devises, frais en devise de cotation, `onHold` frais inclus | testé |
| Simulateur vs spécification | champs et erreurs alignés sur les SDK officiels Python/Node (documentation web bloquée localement) | partiellement vérifié |
| LAB | aucun accès aux clés ni à la base de production ; ne peut ni activer un modèle ni changer d'état | testé |

Mode réel : **désactivé** (`NELYQOR_REAL_TRADING_ALLOWED=false` par défaut ; aucune clé déposée).

## 11. Limites

- Données : un seul échange, ~3 ans, marchés retirés absents (survivance résiduelle), 66 marchés complétés
  après coup ; frais réels du compte non interrogés (hypothèses publiques).
- Modèle d'exécution : remplissage limite estimé sur bougies 1 h (pas de carnet historique) ; impact de marché
  estimé par Corwin-Schultz + plancher ; latence d'une bougie.
- Multiplicité : ~830 essais au total en phase 3 (106 transversaux, 720 par marché, 6 modèles) ; le DSR n'est calculé que dans chaque bloc.
- Capital de 100 € : minimum d'ordre 5 € et frais fixes relatifs élevés ; résultats non extrapolables à
  d'autres tailles sans recalcul.
- GitHub Actions : exécutions périodiques, pas continues ; artefacts non téléchargeables depuis cet
  environnement (lecture via journaux).

## 12. Conditions de lancement (toutes requises, aucune remplie aujourd'hui pour le réel)

1. Au moins un candidat figé **positif après tous coûts sur ≥ 6 mois prospectifs**, avec ≥ 100 transactions,
   profit factor > 1,2, borne basse IC95 > 0, et meilleur que l'achat passif BTC ajusté du risque.
2. Écart prospectif limite vs marché confirmé (sinon exécution au marché seulement).
3. Hébergement couvert par le gain attendu (en pratique : 0 € ou quasi nul avec 100 €).
4. Recette complète verte sur le serveur cible, clés API sans droit de retrait, liste blanche IP, 2FA.
5. Décision explicite du propriétaire, signée depuis l'iPhone.

## 13. Statut

| Élément | Développé | Testé | Simulé (données réelles) | Vérifié en conditions réelles |
|---|---|---|---|---|
| Moteur transversal + variantes | oui | oui | oui | non |
| Exécution limite / hybride | oui | oui | oui | **non (prospectif à venir)** |
| Comparateur EUR/USDC | oui | oui | oui (instantané) | non (frais du compte) |
| Comparateur d'actions / réallocation | oui | oui | oui | non |
| Modèles ML | oui | oui | oui | rejetés |
| Commandes signées | oui | oui | e2e | non (pas de déploiement) |
| Évaluation prospective | oui | oui | démarre le 26/11/2026 | — |
| Mode réel | existant | oui | — | **désactivé** |
