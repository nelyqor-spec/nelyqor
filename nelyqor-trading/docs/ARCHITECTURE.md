# Architecture

```text
 iPhone / PWA ──HTTPS (Caddy)──> api (FastAPI, rôle "api", AUCUNE clé Bitvavo)
                                   │  écrit control_commands / lit l'état
                                   ▼
                           SQLite WAL (/data/nelyqor.db)
                                   ▲
 Bitvavo REST/WS <── engine (rôle "engine", SEUL détenteur des clés)
                      QUANT (signaux, risque, dimensionnement) → GUARDIAN (veto) → EXECUTION
 lab (rôle "lab", à la demande, nice 19, CPU/RAM limités, sans secret) → experiments / lab_report
```

## Flux d'une décision
1. **Scanner** (≈5 s) : `ticker/book` + `ticker/24h` (2 requêtes pour tout l'univers, pas une par marché).
   Tous les marchés sont scannés ; `check_eligibility` filtre statut, devise EUR, fraîcheur, spread,
   liquidité, carnet croisé, anomalie.
2. **Bougies 1 h** clôturées uniquement (la bougie en cours est exclue) pour les marchés éligibles.
3. **QUANT** (`portfolio/decision.py`) : pour chaque stratégie, signal → estimation prudente de l'avantage net
   (rendement passé après signal − seuil de rentabilité frais+spread − k·erreur standard) → score de risque →
   `size_buy` qui applique simultanément : disponible après réserve, plafond risque, max par position,
   exposition totale, max par ordre, 10 % de la profondeur, perte de stress, minimum Bitvavo
   (refus, jamais d'arrondi vers le haut). « Attendre » est l'option par défaut.
4. **GUARDIAN** (`guardian/guardian.py`) : veto (mode, état, marge, données périmées, spread, anomalie,
   fonds, limites, anti-martingale, type d'ordre supporté).
5. **EXECUTION** (`execution/engine.py`) : intention persistée → réservation → `clientOrderId` UUID +
   `operatorId` → envoi → fills dédupliqués → suivi. Timeout = recherche par `clientOrderId` puis trades,
   **jamais** de resoumission ; ambiguïté = pause des entrées.
6. **Comptabilité** reconstruite depuis le journal des fills (source de vérité, survit au redémarrage).

## États GUARDIAN
`SIMULATION` (défaut) · `PAUSE_NOUVELLES_ENTREES` · `RECONCILIATION` · `REEL_AUTORISE` · `INCIDENT`.
- Le passage en `REEL_AUTORISE` n'est possible que par `activate_real()` (drapeau serveur + mot de passe
  re-saisi + phrase exacte + pré-requis : clés, recette enregistrée, réconciliation OK, aucun ordre inconnu,
  aucun flux non identifié, déclarations retrait désactivé / IP fixe / 2FA / acceptation du risque).
- Redémarrage alors que le réel était actif → `RECONCILIATION` obligatoire avant toute entrée.
- Pause ≠ liquidation. Pas d'arrêt définitif sur perte cumulée (choix explicite du propriétaire).

## Laboratoire
Chaque famille a son portefeuille virtuel de 100 € avec mêmes données/frais. Walk-forward à fenêtre
croissante, purge entre entraînement et test, holdout final (20 %) évalué une seule fois, robustesse
(frais ×2, spread +30 bps), rendement par régime, DSR, critères de promotion **figés dans le code de
production** (`research/metrics.py`). Tous les essais sont journalisés (`experiments`).
Registre : candidat → simulation → validé (preuve exigée) → actif (interdit au LAB) → retiré ; hash des
paramètres vérifié ; rollback vers la dernière version validée, sinon pause.

## Choix techniques et écarts
- PWA en JavaScript sans étape de build (pas React/TS) : moins de dépendances, CSP stricte, même rendu iPhone.
- Bougies 1 h comme horizon principal V1 (le cahier liste 1 min → 1 semaine) : volume de requêtes et
  stockage compatibles avec le budget ; autres horizons ajoutables via `download_candles.py --interval`.
- WebSocket carnet implémenté et testé (nonce/resync) mais le moteur V1 utilise le scanner REST agrégé ;
  le WS est à brancher pour les marchés détenus si la latence l'exige.
- Paires EUR uniquement en comptabilité ; USDC non activé tant que son intérêt net n'est pas démontré.
- Protections natives Bitvavo (phase 2) : stop-loss hébergé posé sur toute position, relevé en place
  (`PUT /order`), jamais abaissé ; « protégé » uniquement après relecture chez l'échange
  (`backend/execution/protection.py`). Pas d'OCO supposé : prises de bénéfices gérées par le moteur.
- Deux exécuteurs : simulation (toujours) et réel (si clés) ; les positions réelles restent gérées même
  après désactivation du réel.
