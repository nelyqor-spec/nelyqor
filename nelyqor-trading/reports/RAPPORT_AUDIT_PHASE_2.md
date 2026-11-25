# Nelyqor Trading — Rapport d'audit, phase 2 (25/11/2026)

**Synthèse.** L'audit a relevé **45 points** (dont **9 critiques**) que les 94 tests de la phase 1 ne détectaient
pas : état réel falsifiable, laboratoire pouvant écrire dans la base de production, positions réelles
invendables après désactivation, ventes dupliquées, aucun stop hébergé, ordre déclaré « non placé » trop tôt,
double comptage des frais, solde réel d'ouverture compté deux fois, module de données absent du dépôt. Tous sont corrigés ; la colonne « Preuve » indique le test
associé (« revue » = vérifié par relecture seulement, sans test dédié). Le projet compte désormais **155 tests automatisés, tous réussis localement ET sur
GitHub Actions**. Pour la première fois, le laboratoire a tourné sur **de vraies données Bitvavo** (API publique,
via GitHub Actions) : **aucune stratégie n'est validée ; ne pas passer en réel.** Aucun ordre réel n'a été envoyé.

Légende de statut : **Développé** (code écrit) · **Testé** (tests automatisés sur simulateur/fixtures) ·
**Simulé** (fonctionne contre un faux Bitvavo) · **Vérifié réel** (exécuté contre le vrai Bitvavo).

---

## 1. Problèmes identifiés et corrections

Gravité : **C** critique (perte d'argent ou de contrôle possible) · **M** majeure (résultat faux) · **m** mineure.
Les numéros de ligne renvoient au code CORRIGÉ (commit de cette phase).

### 1.1 Sécurité et contrôle

| # | Grav. | Fichier:ligne | Problème et cause | Correction | Preuve |
|---|---|---|---|---|---|
| S1 | C | `backend/guardian/guardian.py:102`, `:133` | L'état (`REEL_AUTORISE`…) était une simple valeur en base. L'API et **le laboratoire** (même volume, accès en écriture) pouvaient passer le robot en réel sans activation. | État, état de reprise et date d'activation **signés HMAC-SHA256** avec `secrets/engine_state_key`, lisible par le moteur seul. Signature invalide → `INCIDENT`. Réel refusé sans clé. | `test_direct_db_write_of_state_is_detected`, `test_forged_resume_state_cannot_resume_to_real`, `test_real_refused_without_integrity_key`, `test_api_and_lab_roles_cannot_read_engine_state_key` |
| S2 | C | `compose.yaml` (services `lab`, `collector`) | Le LAB montait la base de production en écriture (`data:/data`). | LAB sur des volumes séparés : `market` en lecture seule, `lab` pour sa propre base. Collecteur sans secret. L'API lit le rapport en lecture seule. | `test_compose_isolates_lab_from_production_and_secrets` |
| S3 | C | `guardian.py:273` | Après « retour en simulation », **toute** vente réelle, protections comprises, était refusée : les positions réelles devenaient invendables et non protégeables. | Les ordres protecteurs réels restent autorisés quel que soit l'état ; les entrées réelles exigent `REEL_AUTORISE`. Deux exécuteurs : simulation et réel. | `test_real_protective_sell_allowed_after_deactivation` |
| S4 | M | `guardian.py:303` | Une entrée avec une equity ≤ 0 contournait silencieusement les limites par position et d'exposition. | Veto explicite. | `test_conditional_buy_and_zero_equity_entry_vetoed` |
| S5 | M | `backend/api/auth.py:49` | Verrouillage global après 20 échecs : un attaquant pouvait bloquer le propriétaire, et donc l'arrêt d'urgence. | Seuil global porté à 200 ; arrêt d'urgence possible en SSH (`scripts/emergency_stop.sh`). | `test_bruteforce_lockout` (par IP), runbook |
| S6 | M | `backend/runner.py:106`, `:46` | Commande interrompue par un crash rejouée au redémarrage (une liquidation pouvait être rejouée) ; commandes anciennes exécutées des heures plus tard. | Statut `processing` posé avant exécution ; jamais rejouée. Expiration à 10 min, sauf pause et arrêt d'urgence. | `test_command_interrupted_by_crash_is_not_replayed`, `test_stale_command_expires_but_emergency_stop_never_expires` |
| S7 | M | `runner.py:628` | Estimation de liquidation réutilisable : double liquidation possible pendant 2 min. | Estimation à usage unique. | `test_liquidation_estimate_single_use` |
| S8 | m | `backend/core/config.py:100` | Le rôle `api`/`lab` pouvait lire `telegram_*` et toute future clé moteur. | Préfixes `bitvavo`, `engine_`, `telegram` réservés au moteur. | idem S1 |
| S9 | m | `backend/api/app.py:261` | Bornes de l'année fiscale en heure locale du serveur. | Année civile en heure de Paris ; année validée. | revue |

### 1.2 Exécution, ordres et protections (intégration Bitvavo)

| # | Grav. | Fichier:ligne | Problème et cause | Correction | Preuve |
|---|---|---|---|---|---|
| E1 | C | `backend/execution/engine.py` (`submit`, `active_sell_base`) | **Ventes dupliquées possibles** : deux sorties (ou sortie + liquidation) sur la même position n'étaient pas bloquées. | Quantité vendable = position − ventes actives (stops compris), contrôlée dans la transaction qui crée l'intention. Toute vente exige `position_qty`. | `test_duplicate_sell_blocked_while_stop_active`, `test_two_exit_sells_second_vetoed`, `test_sell_without_position_qty_refused` |
| E2 | C | `backend/execution/protection.py:52` | **Aucun stop hébergé chez Bitvavo** : en cas de panne du serveur, aucune protection. | Stop-loss natif sur chaque position, « protégée » **seulement après relecture** chez l'échange (`awaitingTrigger`, bon déclencheur, quantité couverte). Relevé en place (`PUT /order`), jamais abaissé. Annulation confirmée avant toute vente volontaire. | 20 tests dans `tests/test_protection.py` |
| E3 | C | `engine.py:312` | Après un timeout, l'ordre était déclaré « non placé » au bout de 3 requêtes immédiates : il pouvait apparaître ensuite (cohérence différée) et se remplir hors suivi. | `NOT_PLACED` seulement après expiration de la fenêtre d'accès signée (10 s + 5 s), au-delà de laquelle l'échange rejette la requête. Avant : `UNKNOWN` + pause, recherche à chaque cycle. | `test_timeout_before_accept_pauses_and_never_resubmits` |
| E4 | M | `engine.py:324` | Crash entre la persistance et l'envoi : intention `SUBMITTING`/`PENDING` éternelle, qui bloquait tout. | `recover()` au démarrage et à chaque cycle. | `test_stop_not_protected_while_submission_ambiguous` |
| E5 | M | `engine.py:210` | Réservation non réduite lors d'un remplissage partiel (fonds bloqués deux fois) ; frais de réservation codés en dur. | Réservation résiduelle recalculée à chaque mise à jour ; taux taker actualisé. | `test_partial_fill_then_complete`, `test_reservation_counts_open_orders` |
| E6 | M | `engine.py:151` | Arrondis faits après le contrôle Guardian ; déclencheur de stop arrondi vers le bas (stop élargi). | Arrondi vers le bas **avant** les contrôles ; déclencheur de stop arrondi vers le haut. | `test_stop_trigger_rounded_up_never_widened`, `test_precision_rounds_down` |
| E7 | M | `engine.py:227` | Réponse d'ordre non vérifiée contre l'identifiant client. | `clientOrderId` vérifié dans chaque réponse ; incohérence → `UNKNOWN` + pause. | revue + `test_operator_and_client_id_sent` |
| E8 | M | `engine.py:408` | Ordres limités d'entrée jamais annulés (fonds immobilisés indéfiniment). | Annulation après 15 min. | revue |
| E9 | M | `runner.py:161` | Redémarrage en papier : ordres ouverts perdus mais intentions « ouvertes » → `UNKNOWN` → pause permanente. | Intentions papier ouvertes marquées annulées au redémarrage ; stops reposés. | `test_restart_with_open_position_rebuilds_from_journal` |
| E10 | m | `engine.py` (`ref_price`) | Vente au marché soumise sans contrôle précalculé évaluée à 0 € (refus). | Prix de référence explicite. | `test_exit_cancels_stop_first_then_sells` |
| E11 | m | `bitvavo_rest.py:154` | Bannissement 105 non géré ; pas de synchronisation d'horloge (signature hors fenêtre). | Budget à zéro jusqu'à la date indiquée ; `sync_time()` au démarrage ; nouvelle tentative GET après resynchronisation. Format repris du SDK officiel. | revue (non vérifié en réel) |
| E12 | m | `runner.py:214` | Frais codés en dur pour le réel. | Actualisation horaire via `GET /account` (taker/maker du palier). | revue (non vérifié en réel) |

### 1.3 Comptabilité et réconciliation

| # | Grav. | Fichier:ligne | Problème et cause | Correction | Preuve |
|---|---|---|---|---|---|
| A1 | C | `backend/accounting/ledger.py:85`, `:101` | **Frais prélevés en jetons comptés deux fois** (dans le coût ET via la quantité réduite) ; à la vente, coût des jetons de frais non sorti. | Coût = montant brut ; coût sortant = coût moyen × toutes les unités sortantes. | Test de propriété sur 300 séquences aléatoires : **échoue sur l'ancien code** (écart de 1,51 €), réussit sur le nouveau |
| A2 | M | `runner.py:245` | Rejeu des fills puis des flux (pas chronologique) : dépôt de crypto puis vente → exception. | Rejeu chronologique fusionné. | `test_unidentified_manual_flow_pauses_entries` |
| A3 | C | `runner.py:415` | Compte réel : le solde existant n'était jamais comptabilisé (écart permanent) ; au premier rapprochement, un dépôt antérieur était **compté deux fois**. | Solde d'ouverture enregistré une fois ; mouvements antérieurs marqués « inclus ». Autres actifs du compte **exclus** du périmètre du robot (jamais vendus). | idem |
| A4 | M | `runner.py:444` | Un même écart était inséré à chaque cycle (5 min). Valeur en EUR à 0 lors du rapprochement manuel, d'où un P&L gonflé. | Une ligne d'écart par actif ; valorisation au moment du rapprochement. | idem |
| A5 | M | `runner.py` (`_reconcile_real`) | Frais de retrait ignorés (écart récurrent) ; fenêtre d'historique sans recouvrement (dépôts tardifs manqués). | Retrait = montant + frais ; recouvrement de 7 jours, dédupliqué. | idem |

### 1.4 Données, recherche et biais

| # | Grav. | Fichier:ligne | Problème et cause | Correction | Preuve |
|---|---|---|---|---|---|
| D1 | C | `.gitignore:3` | `data/` excluait aussi le paquet `backend/data/` : **module absent du dépôt**. Détecté par la CI GitHub, invisible en local. | `/data/` (racine uniquement). | CI verte |
| D2 | M | `backend/data/downloader.py:62` | Fenêtres de `limit+1` bougies avec réponse la plus récente d'abord : **la plus ancienne bougie de chaque fenêtre perdue** (5 sur 3 000). | Fenêtres d'exactement `limit` bougies ; détection d'une réponse tronquée. | `test_incremental_download_and_resume_after_interruption` |
| D3 | M | `backend/strategies/base.py:76` | Breakout : l'invalidation comparait au canal **courant**, qui inclut la bougie de cassure : sortie quasi immédiate. | Niveau de cassure figé à l'entrée. | `test_breakout_exit_not_immediate` |
| D4 | M | `backend/portfolio/decision.py:61` | Espérance estimée sur des signaux **chevauchants** (incertitude sous-estimée) ; entrée au prix de clôture vu. | Signaux indépendants uniquement ; entrée à l'ouverture suivante ; borne basse à 2 erreurs-types. | `test_forward_outcomes_non_overlapping` |
| D5 | M | `decision.py:86` | Les outils J (log-croissance − λ·CVaR), Kelly et corrélation étaient codés mais **non utilisés** par la décision. Avec λ = 1, J ne pouvait jamais être positif. | Branchés : fraction = min(argmax J, Kelly fractionné, plafond) × décote de corrélation ; λ = 0,1 de départ (à calibrer sur l'entraînement). | `tests/test_quant.py` (8 tests) |
| D6 | M | `backend/models/predict.py:24`, `:114` | Cible au prix de clôture (non exécutable) ; référence de comparaison = taux de base de l'entraînement (inadapté aux changements de régime) ; « calibré » sans test statistique. | Cible à l'ouverture suivante ; référence = taux de la fenêtre de calibration ; compétence de Brier avec IC 95 % par bootstrap par blocs ; « calibré » seulement si borne basse > 0. | `test_model_finds_planted_signal_and_not_noise` |
| D7 | M | `backend/research/walkforward.py:224` | Holdout réutilisable à l'infini (surapprentissage par consultations répétées). | Registre des consultations ; un holdout déjà vu fait échouer la promotion (`holdout_vierge`). | rapport CI (le benchmark l'a déclenché à tort : D11) |
| D8 | M | `walkforward.py:77` | « Aucune fuite » codé en dur à `False` dans les critères. | Contrôle de fuite exécuté (signaux passés invariants à une perturbation du futur). | rapport CI : `fuite_temporelle: false` mesuré |
| D9 | M | `backend/research/backtest.py:104` | Marchés absents ou radiés : prix NaN → equity NaN ; aucune gestion de la radiation (biais de survivance). | Aucun prix inventé ; sortie forcée avec décote de 50 % en cas de radiation ; univers complet possible (`--all`). | `test_delisting_forces_exit_*`, `test_data_gap_no_execution_inside_gap` |
| D10 | M | `walkforward.py:198` | Paramètres du holdout choisis sur le dernier fold seulement. | Re-sélection sur tout le pré-holdout (purge incluse). | revue |
| D11 | m | `scripts/run_lab.py` | Le benchmark de tendance partageait le nom `trend_following` (faux « holdout déjà consulté »). | Nom distinct. | rapport CI suivant |
| D12 | m | `backend/portfolio/risk.py:68` | CVaR : 1000 × 0,05 = 50,000000000000004 → 51 observations. | Arrondi avant `ceil`. | `test_cvar_prudent_small_samples` |
| D13 | m | `backend/features/indicators.py:136` | Valeurs `inf` (division par un volume nul) passaient le filtre NaN jusqu'aux modèles. | `inf` → NaN. | revue |
| D14 | m | `scripts/bench.py` | Benchmark « sous charge » : le LAB concurrent échouait au lancement (option renommée), donc la mesure était invalide. | Corrigé ; vérification `lab_concurrent_pendant_mesure: true`. | `reports/bench_output.json` |
| D15 | m | `backend/core/num.py:17` | `np.float64` → `Decimal` impossible (NumPy 2) : corrigé en phase 1, test ajouté. | — | `test_decimal_conversion_from_numpy_and_float` |

### 1.5 Interface et tests trompeurs

| # | Grav. | Problème | Correction |
|---|---|---|---|
| T1 | M | Le test e2e « arrêt d'urgence transmis » réussissait même si le moteur ne traitait pas la commande. | Test strict avec un vrai moteur (faux Bitvavo) : **13/13** (`reports/e2e_pwa_output.txt`). |
| T2 | m | `test_uncalibrated_model_labelled` était tautologique (réussissait quel que soit le résultat). | Remplacé par des tests à **signal implanté** (détecte un signal réel, n'en invente pas dans le bruit). |
| T3 | m | Plusieurs tests de résilience écrivaient l'état en base directement (ce qui est désormais détecté comme une falsification). | Passage par l'API du Guardian. |
| U1 | m | La PWA n'affichait pas l'état de protection des positions. | Onglet Capital : « protégée / NON protégée » + rappel « aucun prix garanti ». |

---

## 2. Résultats des tests (exécutés)

| Suite | Résultat | Lieu |
|---|---|---|
| ruff + mypy (52 fichiers) | OK | local + GitHub Actions |
| pytest : **155 tests** (ordres 16, protections 20, Guardian/sécurité 27, comptabilité 12, données marché 9, pipeline 8, résilience 14, finance 21, quant 10, WebSocket 2, sources externes 7, API 9) | **155/155** | local + GitHub Actions (run 3) |
| Parcours iPhone (Playwright, iPhone 13), API + moteur réels sur faux Bitvavo | **13/13** | local |
| Benchmark sous charge (moteur + LAB simultanés) | voir §6 | local |

---

## 3. Résultats financiers RÉELLEMENT mesurés

**Données : RÉELLES**, API publique Bitvavo, bougies 1 h, BTC/ETH/SOL/XRP/ADA-EUR, 26 323 bougies (environ 3 ans),
téléchargées et validées par le pipeline (run GitHub Actions n° 2). Coûts simulés : taker 0,25 %, glissement de
10 pb, latence d'une bougie, minimum de 5 €. Holdout = 20 % final (environ 7,2 mois, d'avril à novembre 2026),
jamais vu auparavant.

| Stratégie | Walk-forward OOS | Holdout net | Holdout frais ×2 | PF | Trades | Holdout **économique** (hébergement 7,19 €/mois) | Promue |
|---|---|---|---|---|---|---|---|
| momentum | −37,8 % | −10,3 % | −16,5 % | 0,43 | 133 | −62,1 % | non |
| breakout | −38,2 % | −12,3 % | −19,3 % | 0,43 | 159 | −64,1 % | non |
| mean_reversion | −27,3 % | −7,4 % | −12,2 % | 0,38 | 74 | −59,2 % | non |
| trend_following | −29,9 % | −2,3 % | −7,6 % | 0,84 | 141 | −54,2 % | non |
| swing | −11,8 % | **+1,6 %** | −0,4 % | 1,50 | 36 | −50,2 % | non (36 trades < 100 ; IC 95 % inclut 0 ; négatif à frais ×2) |
| rebound | +0,1 % | 0 % (aucun trade) | 0 % | — | 0 | −51,8 % | non |
| hybride (poids issus de l'entraînement) | −23,8 % | 0 % (aucun poids positif) | 0 % | — | 0 | −51,8 % | non |
| modèle logistique (stratégie) | −72,5 % | −32,9 % | −49,7 % | 0,40 | 517 | −84,7 % | non |
| benchmark tendance EMA 50/100/200 | −29,2 % | −1,2 % | −6,5 % | 0,91 | 131 | −53,0 % | non |
| **100 € conservés en euros** | — | **0 %** | — | — | — | 0 % (sans serveur) | référence |
| **achat passif BTC** (même holdout, frais inclus) | — | **−16,6 %** | — | — | — | — | référence |

Achats passifs sur le même holdout : ETH −23,8 %, SOL −15,4 %, XRP −31,9 %, ADA −39,6 % (période baissière).

**Modèles prédictifs (BTC-EUR, données réelles) :** compétence de Brier **statistiquement positive** à 1 h
(logistique +3,8 % IC [2,0 ; 5,6] ; LightGBM +5,7 % IC [4,3 ; 7,3]) et à 6 h (+2,4 % ; +2,7 %), **nulle à 24 h**.
La stabilité par fenêtre montre une compétence non significative sur la première fenêtre. Pourtant, la stratégie
construite sur ce modèle perd 33 % : **un léger pouvoir prédictif ne couvre pas 0,5 % de coûts aller-retour**
sur des trades de quelques heures.

RÉSULTATS_4H_PLACEHOLDER

### Analyse des pertes (sans extrapolation)
1. **Coûts** : la plupart des familles font plus de 100 allers-retours en 7 mois à environ 0,6 % de coût
   chacun ; le diagnostic sans coûts (rapport CI) indique si un avantage brut existe avant frais.
2. **Régime** : le holdout est baissier sur tous les actifs (BTC −16,6 %) ; les règles long-only y sont
   défavorisées, même si plusieurs ont mieux résisté que l'achat passif.
3. **Hébergement** : 7,19 €/mois rapportés à 100 € coûtent environ 52 points sur 7,2 mois, soit plus que toute perte ou
   tout gain de trading mesuré. **Avec 100 € de capital, le résultat économique est négatif dans tous les cas
   mesurés.**
4. Un seul jeu de 5 marchés et un seul holdout : intervalles larges ; un résultat positif isolé (swing) n'est pas
   une preuve (DSR = 0,026).

---

## 4. Problèmes non résolus et limites

| Sujet | État |
|---|---|
| Intégration Bitvavo | **Développée, testée sur simulateur, jamais vérifiée contre le vrai Bitvavo** (aucune clé, `api.bitvavo.com` et `docs.bitvavo.com` bloqués ici). Formats repris des SDK officiels Bitvavo (GitHub, 2025) : `PUT /order` pour les stops non déclenchés, `GET /order` par `clientOrderId`, canal WS `account`, signature WS `GET /websocket`, réservation « onHold » incluant les frais. **À vérifier en paper trading sur le serveur** : champs exacts des réponses d'ordres conditionnels (`triggerAmount` vs `triggerPrice`), codes d'erreur 304/305 (resynchronisation d'horloge supposée), réservation des fonds d'un stop non déclenché, `GET /account` (frais). |
| Stops | Un stop ne garantit **aucun** prix : `stopLoss` → ordre au marché (glissement, gap) ; `stopLossLimit` peut ne jamais s'exécuter (testé) ; aucune exécution pendant une panne ou suspension Bitvavo. Pas d'OCO : pas de take-profit natif simultané. |
| WebSocket | Client `account` développé et testé contre un serveur local ; démarré par `runner.main()` si des clés existent ; non vérifié en réel. Le carnet WS (nonce) n'alimente pas encore le moteur (scanner REST toutes les 5 s). |
| Sources externes | 6 connecteurs gratuits développés et testés **sur fixtures uniquement** (réseau bloqué) ; aucune n'influence les décisions (barrière : test d'ablation avec IC > 0) ; licences à confirmer par le propriétaire ; calendrier macro : aucune source gratuite sans clé, dates non inventées. |
| Sécurité résiduelle | Une API compromise peut forger des commandes (le mot de passe est vérifié par l'API) ; `acceptance_tests_ok` non signé ; voir `docs/SECURITE.md`. |
| Notifications | Telegram envoyé de façon synchrone (peut bloquer la boucle jusqu'à 10 s) ; Web Push iOS absent. |
| Sessions PWA | En mémoire (déconnexion au redémarrage de l'API). |
| Données | Univers testé = 5 grands marchés (biais de survivance résiduel). L'option `--all-eur` existe mais n'a pas été exécutée. Pas d'historique des radiations antérieures. |

## 5. Faiblesses mathématiques restantes
- λ (CVaR) et γ (incertitude) sont des valeurs de départ, **non calibrées** ; leur calibration doit se faire sur
  l'entraînement uniquement (le code prévoit les paramètres, pas encore la recherche automatique).
- L'espérance d'une entrée reste estimée sur l'historique de la même règle (in-sample) ; même rétrécie et bornée à
  2 erreurs-types, elle subit un biais de sélection entre 6 familles.
- Le score de risque (pondérations 25/20/15/15/15/10) n'est pas validé ; la composante macro est neutre (0,5).
- Scénarios de stress fixes (−35 %, −10 %) ; pas de bootstrap multi-actifs dans la décision en direct.
- Corrélation : une décote simple sur la corrélation maximale, sans optimisation de portefeuille globale.
- Pas de réallocation entre positions (seulement entrées et sorties).

## 6. Serveur et coûts
Mesuré localement (4 vCPU) avec le LAB en parallèle : 100 marchés, cycle médian 1,08 s (max 1,41 s) ; RSS moteur
157 Mo, LAB 247 Mo. Instantanés d'equity limités à 1/min (au lieu de toutes les 5 s, ×12 moins de lignes) ;
grand livre mis en cache (le rejeu complet croissait avec l'historique) ; Parquet zstd environ ×5 plus compact.
**Calculs lourds hors serveur, gratuitement** : le workflow GitHub Actions télécharge, valide et lance le
laboratoire (environ 3 min par exécution) ; le VPS ne garde que le moteur et l'API. Hetzner CX23 ≈ 7,19 € TTC/mois
(estimation de la phase 1, à reconfirmer).

## 7. Recommandations
1. **Ne pas activer le réel.** Aucun critère de validation n'est satisfait sur données réelles.
2. Le résultat économique impose d'augmenter le capital ou de réduire l'hébergement (déploiement local, offre
   moins chère) : avec 100 €, l'hébergement à lui seul coûte environ 86 % du capital par an.
3. Explorer les horizons plus longs (4 h, 1 j), moins coûteux en frais, et des règles qui restent en euros en
   régime baissier ; toujours avec le même protocole (holdout vierge, frais doublés, ≥ 100 trades).
4. Déployer en **paper trading sur flux réels** pour vérifier l'intégration Bitvavo (§4) sans argent.
5. Calibrer λ/γ et le score de risque sur l'entraînement, puis mesurer leur effet hors échantillon.

## 8. Conditions nécessaires avant tout déploiement réel
Toutes doivent être vraies : (1) une stratégie promue par le laboratoire sur un holdout **vierge** réel, avec
≥ 100 trades, PF > 1,2, IC 95 % > 0, positive à frais doublés ; (2) au moins 4 semaines de paper trading sur flux
réels cohérentes avec le backtest ; (3) intégration Bitvavo vérifiée en réel (ordres conditionnels, annulation,
modification, réconciliation, WebSocket) avec un montant minimal ; (4) clé API sans droit de retrait, IP fixe,
2FA ; (5) `secrets/engine_state_key` en place ; (6) recette enregistrée ; (7) activation explicite par le
propriétaire, qui accepte le risque de perte totale.

## 9. Fichiers modifiés ou ajoutés (phase 2)
Nouveaux : `backend/execution/protection.py`, `backend/data/{validate,store,downloader}.py`,
`backend/external/sources.py`, `scripts/{emergency_stop.sh,dev_engine_fake.py,e2e_pwa.cjs}` (renforcé),
`.github/workflows/nelyqor-trading.yml`, `tests/{test_protection,test_data_pipeline,test_quant,test_websocket,
test_external}.py`, ce rapport.
Modifiés : `backend/guardian/guardian.py`, `backend/execution/{engine,simulator}.py`, `backend/runner.py`,
`backend/accounting/ledger.py`, `backend/market_data/{bitvavo_rest,bitvavo_ws,models}.py`,
`backend/portfolio/{decision,risk}.py`, `backend/models/predict.py`, `backend/strategies/base.py`,
`backend/features/indicators.py`, `backend/research/{backtest,walkforward,synthetic}.py`,
`backend/storage/db.py` (migration v2), `backend/api/{app,auth}.py`, `backend/core/{config,num}.py`,
`frontend/{app.js,app.css,sw.js}`, `compose.yaml`, `.gitignore`, `pyproject.toml`, `requirements.lock`,
`scripts/{run_lab,download_candles,bench}.py`, `docs/*.md`, `README.md`, tests existants.

## 10. Limites de l'environnement
- Réseau : Bitvavo (API et documentation), CoinGecko, mempool.space, BCE, Fed, GDELT inaccessibles depuis le
  conteneur de développement ; GitHub accessible. Données réelles obtenues via GitHub Actions (machines GitHub,
  API publique, sans clé) ; artefacts non téléchargeables ici (stockage Azure bloqué) : les résultats viennent des
  journaux du job.
- Docker Hub limité (429) ; image construite en phase 1 via un miroir.
- Aucune clé Bitvavo, aucun hébergement souscrit, aucun argent engagé.
