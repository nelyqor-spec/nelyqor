# Déploiement

## 1. VPS (action du propriétaire — paiement)
Candidat : **Hetzner CX23** (2 vCPU, 4 Go RAM, 40 Go SSD), ≈ 5,99 € HT/mois IPv4 incluse selon les
sources consultées le 25/11/2026, soit **≈ 7,19 € TTC** (TVA 20 %). Disponibilité à la commande incertaine :
vérifier dans la console Hetzner. Ne pas souscrire les sauvegardes Hetzner (+20 %) : les sauvegardes
chiffrées `scripts/backup.sh` suffisent, copiées hors serveur. Région européenne (Falkenstein/Nuremberg/Helsinki).
Plafond de 10 € TTC : respecté avec cette option. Mesures (scripts/bench.py) : moteur ≈ 90 Mo,
LAB ≈ 170 Mo, cycle de 100 marchés ≈ 1 s → 4 Go de RAM est largement suffisant.

## 2. Système
```bash
apt update && apt install -y docker.io docker-compose-v2 git ufw
ufw default deny incoming && ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp && ufw enable
git clone <dépôt> && cd nelyqor/nelyqor-trading
```
Un enregistrement DNS A `NELYQOR_DOMAIN` → IPv4 du VPS (domaine du propriétaire, ou sous-domaine gratuit type DuckDNS).

## 3. Configuration
```bash
cp .env.example .env && nano .env                  # NELYQOR_DOMAIN, laisser REAL_TRADING_ALLOWED=false
mkdir -p secrets && chmod 700 secrets
python3 scripts/set_password.py secrets/owner_password_hash
: > secrets/bitvavo_api_key && : > secrets/bitvavo_api_secret   # vides au départ
openssl rand -base64 32 > secrets/backup_passphrase && chmod 600 secrets/*
docker compose up -d --build
```
Le hash scrypt et le mot de passe ne transitent jamais par le chat.

## 4. Données et laboratoire
```bash
docker compose run --rm engine python scripts/download_candles.py --markets BTC-EUR,ETH-EUR,SOL-EUR --days 1100
docker compose --profile lab run --rm lab
```
Le rapport apparaît dans PWA › IA et `reports/lab_report_bitvavo.json`.

## 5. Paper trading
Le moteur tourne en simulation sur les prix réels Bitvavo (sans clé) dès le démarrage.
Laisser tourner pour accumuler des décisions réelles hors échantillon.

## 6. Mode réel (uniquement sur décision explicite du propriétaire)
1. Créer la clé API (docs/SECURITE.md), la déposer dans `secrets/`.
2. Recette sur le serveur, depuis le clone (l'image de production n'embarque pas les tests) :
   `python3 -m venv .venv && .venv/bin/pip install -r requirements.lock`, puis
   `docker compose stop engine && PYTHON=.venv/bin/python NELYQOR_DB=/var/lib/docker/volumes/nelyqor_data/_data/nelyqor.db scripts/acceptance.sh --record && docker compose start engine`.
3. `.env` : `NELYQOR_REAL_TRADING_ALLOWED=true`, puis `docker compose up -d`.
4. PWA › Réglages › « Demander l'activation du réel » : cocher les déclarations, mot de passe, phrase exacte.
5. Le moteur vérifie les pré-requis et réconcilie ; en cas de refus, les raisons sont affichées.

## 7. Notifications
Telegram (optionnel) : créer un bot (@BotFather), mettre `TELEGRAM_CHAT_ID` dans `.env`, le jeton dans
`secrets/telegram_bot_token`, ajouter ce secret au service `engine` dans compose.yaml, redémarrer.
Seuls les événements critiques sont envoyés (dédupliqués 6 h, 20/jour max). Sans Telegram, les alertes
restent consultables via `/api/incidents`. Web Push iOS : non implémenté en V1.
