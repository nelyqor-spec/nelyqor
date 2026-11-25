# Nelyqor Trading

Système personnel de trading algorithmique **spot** sur Bitvavo, pilotable depuis un iPhone (PWA).
**Mode par défaut : SIMULATION.** Aucun ordre réel n'est envoyé sans activation explicite, authentifiée et
distincte du propriétaire. **Aucune stratégie n'est validée statistiquement à ce jour. Le capital peut être
intégralement perdu.**

> Rapports : [`reports/RAPPORT_AUDIT_PHASE_2.md`](reports/RAPPORT_AUDIT_PHASE_2.md) (audit, corrections, résultats
> sur données Bitvavo RÉELLES) et [`reports/RAPPORT_FINAL.md`](reports/RAPPORT_FINAL.md) (phase 1).

## Sommaire
- [Architecture](docs/ARCHITECTURE.md) · [Sécurité](docs/SECURITE.md) · [Déploiement VPS + Bitvavo](docs/DEPLOIEMENT.md)
- [Runbooks (incidents, sauvegarde, mise à jour, révocation)](docs/RUNBOOKS.md) · [Fiscalité](docs/FISCALITE.md)

## Installation locale (développement)

```bash
cd nelyqor-trading
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.lock          # versions figées
scripts/acceptance.sh                      # ruff + mypy + pytest (sortie dans reports/acceptance_output.txt)
python scripts/run_lab.py --source synthetic   # laboratoire sur données SYNTHÉTIQUES (démonstration)
```

API + PWA en local (sans moteur) :
```bash
mkdir -p secrets && python scripts/set_password.py secrets/owner_password_hash
NELYQOR_ROLE=api NELYQOR_SECRETS_DIR=secrets NELYQOR_COOKIE_SECURE=false uvicorn backend.api.app:app --port 8088
# http://127.0.0.1:8088  — ou http://127.0.0.1:8088/?demo=1 (données fictives étiquetées)
```

## Production (résumé — détail dans docs/DEPLOIEMENT.md)

```bash
cp .env.example .env            # renseigner NELYQOR_DOMAIN ; laisser NELYQOR_REAL_TRADING_ALLOWED=false
mkdir -p secrets && chmod 700 secrets
python3 scripts/set_password.py secrets/owner_password_hash
: > secrets/bitvavo_api_key; : > secrets/bitvavo_api_secret   # vides = simulation pure (paper trading)
docker compose up -d --build     # engine + api + caddy (HTTPS)
openssl rand -hex 32 > secrets/engine_state_key && chmod 600 secrets/*
docker compose --profile lab run --rm collector  # bougies Bitvavo publiques (incrémental, reprenable)
docker compose --profile lab run --rm lab        # walk-forward sur données Bitvavo réelles, isolé
```

## Exploitation courante
| Action | Où |
|---|---|
| Voir capital, P&L, TWR, résultat économique | PWA › Accueil |
| Pause / reprise / **arrêt d'urgence** | PWA › Réglages (ou Accueil) |
| Liquidation manuelle (estimation + mot de passe + « LIQUIDER ») | PWA › Capital |
| Rapprocher un apport/retrait manuel | PWA › Capital |
| Exports CSV et aide 2086 | PWA › Capital |
| Activer le réel | `NELYQOR_REAL_TRADING_ALLOWED=true` dans `.env` **et** PWA › Réglages (mot de passe + phrase) |

## Structure
```
backend/core          Decimal, horloge UTC, config + bornes immuables (hash), logs JSON masqués
backend/storage       SQLite WAL, migrations, transactions sérialisées
backend/market_data   REST/WS Bitvavo (signature, budget de poids, backoff, nonce carnet), univers
backend/features      indicateurs causaux (aucune fuite temporelle — testé)
backend/strategies    momentum, breakout, mean reversion, trend, swing, rebond, event (inactif), hybride
backend/models        régimes par règles, logistique / LightGBM + calibration + Brier, Isolation Forest
backend/portfolio     score de risque, plafond, CVaR, bootstrap par blocs, objectif J, Kelly borné, QUANT
backend/guardian      machine d'états + veto
backend/execution     intentions persistées, idempotence, réconciliation, simulateur d'échange, verrou
backend/accounting    grand livre EUR, TWR, P&L, flux externes, réconciliation, aide fiscale 2086
backend/research      backtest conservateur, walk-forward purgé, holdout, DSR, promotion, registre
backend/api           FastAPI (auth scrypt, cookies sécurisés, CSRF, anti-bruteforce, audit)
backend/runner.py     service moteur (seul détenteur des clés)
frontend/             PWA iPhone 5 onglets (sans build, CSP stricte)
tests/                155 tests : ordres, protections, sécurité, comptabilité, données, résilience, finance,
                      quant, WebSocket, sources externes, API
scripts/              lab, téléchargement bougies, mot de passe, backup/restore, bench, recette, e2e PWA
```
