# Nelyqor Trading — Rapport de livraison V1 (25/11/2026)

**Verdict : prêt techniquement pour le paper trading. PAS prêt pour le réel au 1er décembre :**
**aucune stratégie n'a été évaluée sur des données de marché réelles** (l'accès à Bitvavo est bloqué
dans l'environnement de développement), et les protections de stop ne sont pas hébergées chez Bitvavo.

## 1. Fichiers et installation
Voir `README.md` (structure, commandes) et `docs/DEPLOIEMENT.md`. Dépendances figées : `requirements.lock`.
Image Docker construite et la pile `engine` + `api` démarrée réellement dans l'environnement de dev
(le service Caddy n'a pas été lancé : pas de nom de domaine).

## 2. Tests réellement exécutés
| Domaine | Résultat | Preuve |
|---|---|---|
| Lint (ruff), typage (mypy, 45 fichiers) | OK | `reports/acceptance_output.txt` |
| pytest : 94 tests (ordres 16, sécurité 18, comptabilité 11, données 9, résilience 11, finance 20, API 9) | **94/94 OK** | `reports/acceptance_output.txt` |
| Parcours iPhone (Playwright, profil iPhone 13) contre la pile Docker | **12/12 OK** | `reports/e2e_pwa_output.txt`, `reports/screenshots/` |
| Performance : scanner + décision + LAB simultané | 100 marchés : cycle médian ≈ 1,0 s, max 1,4 s ; RAM moteur ≈ 90 Mo, LAB ≈ 170 Mo | `reports/bench_output.json` |
| Arrêt d'urgence pendant une coupure réseau (conteneurs) | traité, état `PAUSE_NOUVELLES_ENTREES` | session de test Docker |

Défauts trouvés **par les tests** puis corrigés : CVaR (arrondi flottant prenant 51 points au lieu de 50) ;
conversion `Decimal` des flottants NumPy 2 ; couche modale invisible bloquant tous les appuis dans la PWA ;
**commandes de contrôle non traitées pendant une panne réseau** ; liquidation possible sur prix périmés.

Couverture du tableau d'acceptation :
- Données : nonce/resync carnet, données périmées/carnet croisé/marché suspendu refusés, budget de poids, backoff GET, POST jamais répété. *Non testé contre la vraie API Bitvavo.*
- Comptabilité : dépôts/retraits, frais EUR/base/tierce devise, fonds réservés, TWR, P&L, réconciliation, 2086.
- Ordres : minimums, précision, fonds insuffisants, post-only, fill partiel maker, timeout avant/après acceptation, aucune répétition, course annulation/fill, déduplication, `operatorId`/`clientOrderId`.
- Sécurité : clés absentes du frontend/logs, retraits/marge impossibles, LAB sans clé ni droit, accès non authentifié refusé, CSRF, bruteforce, ré-authentification.
- Résilience : redémarrage avec position ouverte, redémarrage depuis le réel → réconciliation, perte réseau, flux non identifié, ordre inconnu, disque plein, sauvegarde/restauration.

## 3. Rapport financier et limites
**Données utilisées : SYNTHÉTIQUES** (régimes markoviens + sauts, 8 000 bougies horaires, 3 marchés).
Ces chiffres valident la mécanique du laboratoire, **ils ne disent rien des marchés réels**.

| Stratégie | WF hors échantillon | Holdout | Holdout frais ×2 | PF | Trades holdout | Promue |
|---|---|---|---|---|---|---|
| momentum | −7,57 % | −3,74 % | −6,16 % | 0,40 | 45 | non |
| breakout | −3,88 % | −1,24 % | −2,45 % | 0,63 | 25 | non |
| mean_reversion | −0,21 % | −1,49 % | −1,91 % | 0,35 | 6 | non |
| trend_following | −0,74 % | −0,81 % | −1,83 % | 0,80 | 23 | non |
| swing | −1,42 % | −1,35 % | −1,58 % | 0,04 | 5 | non |
| rebound | −0,56 % | −2,51 % | −2,87 % | 0,06 | 7 | non |

Benchmarks même période : 100 € conservés 0 % ; achat passif BTC synthétique −51,8 %. 68 essais journalisés (DSR calculé).
Modèles prédictifs (logistique, LightGBM, horizons 1/6/24 h) : Brier **moins bon** que le taux de base
dans les 6 cas → affichés « estimation non calibrée », non utilisés pour décider.
**Conclusion : aucune stratégie promue ; tout reste en simulation.** Les seuils n'ont pas été assouplis.

Ce qu'il faut pour un vrai résultat : `download_candles.py` puis `run_lab.py --source db` sur le VPS
(≈ 3 ans d'historique 1 h), puis plusieurs semaines de paper trading.
Limites connues : biais de survivance non corrigé (pas d'historique des radiations Bitvavo avant le
démarrage du collecteur), pas de carnet historique (score de risque fixe en backtest), OHLC seul (hypothèse stop-avant-objectif).

## 4. Coût mensuel estimé
Hetzner CX23 ≈ 5,99 € HT IPv4 incluse → **≈ 7,19 € TTC** (sources de novembre 2026, à confirmer à la commande ;
disponibilité signalée incertaine). Aucun autre service payant requis (Caddy/Let's Encrypt, Telegram gratuits).
Sur 100 € de capital, 7,19 €/mois représentent ≈ 86 % du capital par an : le résultat économique sera
très probablement négatif même avec un P&L de trading positif. Le tableau de bord l'affiche séparément.

## 5. Procédure Bitvavo et déploiement
`docs/SECURITE.md` (clé lecture+trading, retrait désactivé, IP fixe, 2FA, secrets déposés directement sur
le serveur) et `docs/DEPLOIEMENT.md`. Aucun secret n'est demandé dans le chat.

## 6. Blocages et risques restants
1. **Réseau de l'environnement de dev** : api.bitvavo.com, docs.bitvavo.com, Binance, Kraken, CoinGecko inaccessibles → aucun test contre l'API réelle, aucun backtest sur données réelles. Les formats de réponse (`markets`, `ticker/book`, `candles`, `order`, `depositHistory`) sont codés d'après la documentation publique connue et **doivent être vérifiés au premier démarrage sur le VPS** (paper trading).
2. **Stops non hébergés chez Bitvavo** : si le serveur tombe, aucun stop ne s'exécute. Prochaine étape recommandée avant le réel : ordres `stopLoss` natifs avec gestion de la réservation de solde.
3. WebSocket implémenté et testé unitairement mais pas branché dans la boucle V1 (scanner REST agrégé toutes les ~5 s).
4. Sources externes (macro, actualité, sentiment, blockchain) : non branchées → module événementiel désactivé, composante macro du risque neutre (0,5).
5. Horizon unique 1 h en V1 ; USDC non activé.
6. Web Push iOS non implémenté (Telegram optionnel câblé).
7. Sessions PWA en mémoire : un redémarrage de l'API déconnecte l'utilisateur.
8. Pas d'arrêt sur perte cumulée (choix du propriétaire) : la perte totale est possible.

## 7. État de préparation au réel
| Pré-requis | État |
|---|---|
| Code d'exécution et Guardian testés | oui (sur simulateur) |
| Testé contre l'API Bitvavo réelle | **non** |
| Stratégie validée hors échantillon sur données réelles | **non** |
| Protections hébergées chez l'échange | **non** |
| Hébergement souscrit | non (action du propriétaire) |

Recommandation : déployer, laisser tourner en **paper trading sur les prix réels** et exécuter le laboratoire
sur l'historique Bitvavo. Le propriétaire peut techniquement activer le réel (drapeau serveur + action
authentifiée), mais il le ferait **sans aucune preuve de rentabilité** et avec un risque de perte totale.
Aucun ordre réel n'a été envoyé pendant ce développement.
