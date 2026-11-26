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
python3 scripts/set_password.py secrets/owner_password_hash   # mot de passe PUIS phrase de signature (distincte,
                                                            # 16 car. min.) : owner_command_salt (public, servi par
                                                            # Caddy) et owner_command_key (moteur SEUL)
: > secrets/bitvavo_api_key && : > secrets/bitvavo_api_secret   # vides au départ
openssl rand -base64 32 > secrets/backup_passphrase
openssl rand -hex 32 > secrets/engine_state_key     # clé d'intégrité de l'état (lue par le moteur SEUL)
chmod 600 secrets/*
# Les conteneurs tournent sous l'uid 10001 et Compose monte les secrets TELS QUELS (uid/mode ignorés pour un
# secret de type fichier) : sans ce chown, moteur et API s'arrêtent (« secret … illisible par l'uid 10001 »).
chown 10001:10001 secrets/bitvavo_api_key secrets/bitvavo_api_secret secrets/owner_password_hash \
  secrets/engine_state_key secrets/owner_command_key secrets/owner_command_salt
docker compose up -d --build
scripts/deploy_check.sh            # facultatif, AVANT la mise en service : pile de test séparée (voir § 10)
```
Le hash scrypt et le mot de passe ne transitent jamais par le chat.

## 4. Données et laboratoire
```bash
docker compose --profile lab run --rm collector   # téléchargement incrémental (relancer = reprise)
docker compose --profile lab run --rm lab         # laboratoire isolé (aucune clé, pas d'accès à la base de prod)
```
Le rapport apparaît dans PWA › IA (`/lab/reports/lab_report_bitvavo.json`, lecture seule pour l'API).
**Alternative gratuite hors serveur** : le workflow `.github/workflows/nelyqor-trading.yml` exécute recette,
téléchargement et laboratoire sur les machines GitHub (déclenchement manuel « Run workflow » ou push sur une
branche `claude/**`) ; résultats dans le journal du job et l'artefact (7 jours). Le VPS n'a alors pas besoin
de faire les calculs lourds.

## 5. Paper trading
Le moteur tourne en simulation sur les prix réels Bitvavo (sans clé) dès le démarrage.
Laisser tourner pour accumuler des décisions réelles hors échantillon.

## 6. Mode réel (uniquement sur décision explicite du propriétaire)
1. Créer la clé API (docs/SECURITE.md), la déposer dans `secrets/`.
2. Recette sur le serveur, depuis le clone (l'image de production n'embarque pas les tests) :
   `python3 -m venv .venv && .venv/bin/pip install -r requirements.lock`, puis
   `docker compose stop engine && PYTHON=.venv/bin/python NELYQOR_DB=/var/lib/docker/volumes/nelyqor_engine_data/_data/engine.db scripts/acceptance.sh --record && docker compose start engine`.
3. `.env` : `NELYQOR_REAL_TRADING_ALLOWED=true`, puis `docker compose up -d`.
4. PWA › Réglages › « Demander l'activation du réel » : cocher les déclarations, mot de passe, phrase exacte.
5. Le moteur vérifie les pré-requis et réconcilie ; en cas de refus, les raisons sont affichées.

## 7. Notifications
Telegram (optionnel) : créer un bot (@BotFather), mettre `TELEGRAM_CHAT_ID` dans `.env`, le jeton dans
`secrets/telegram_bot_token`, ajouter ce secret au service `engine` dans compose.yaml, redémarrer.
Seuls les événements critiques sont envoyés (dédupliqués 6 h, 20/jour max). Sans Telegram, les alertes
restent consultables via `/api/incidents`. Web Push iOS : non implémenté en V1.

## 8. État du moteur et arrêt d'urgence (phase 4)
- Le moteur conserve son état autoritaire dans le volume `engine_state` (`/state`), jamais monté dans l'API ni le LAB.
  Le sauvegarder : `NELYQOR_ENGINE_STATE_HOST_DIR=/var/lib/docker/volumes/nelyqor_engine_state/_data scripts/backup.sh`.
  Sans lui, après restauration, le moteur passe en INCIDENT (levée par une reprise signée).
- Arrêt d'urgence sans l'iPhone : `scripts/emergency_stop.sh` (dépose `/state/ARRET_URGENCE`).
- Après un arrêt d'urgence ou une pause, attendre 30 s avant de signer une reprise (tolérance d'horloge).

## 9. Bases séparées API / moteur (phase 5)
| Volume | Fichier | Moteur | API |
|---|---|---|---|
| `engine_data` (`/engine`) | `engine.db` : journal, intentions, flux, décisions, journal des commandes | lecture/écriture | **aucun accès** |
| `commands` (`/commands`) | `commandes.db` : table `control_commands` seule | lecture seule (`:ro`) | écriture |
| `display` (`/display`) | `affichage.db` : copie d'affichage (≤ 10 s) | écriture (jamais relue) | lecture seule (`:ro`) |
| `api_data` (`/apidata`) | `api.db` : audit des connexions/demandes | aucun accès | écriture |
| `engine_state` (`/state`) | magasin signé, `ARRET_URGENCE` | lecture/écriture | aucun accès |

Migration depuis une installation phase 4 (volume `data`, `nelyqor.db`), moteur et API ARRÊTÉS :
```bash
docker compose stop engine api
docker compose run --rm --no-deps -v nelyqor_data:/old engine python -c \
  "import sqlite3; s=sqlite3.connect('/old/nelyqor.db'); d=sqlite3.connect('/engine/engine.db'); s.backup(d); print(d.execute('PRAGMA integrity_check').fetchone())"
docker compose up -d && docker volume rm nelyqor_data   # après vérification
```
L'ancienne base était modifiable par l'API : si le réel a été activé, vérifier la première réconciliation (aucun
écart, sinon pause) avant toute reprise. Sauvegardes : `NELYQOR_DB=/var/lib/docker/volumes/nelyqor_engine_data/_data/engine.db
scripts/backup.sh` (`commandes.db`/`affichage.db` sont dérivées et non sauvegardées).

En mode réel, la PWA affiche `capital_authentifie` (`/api/status`) : si `ok=false`, les achats réels sont suspendus
(écart journal/soldes, soldes Bitvavo illisibles ou périmés) ; ventes et protections continuent. Rapprocher un flux
réel (`ack_flow`) demande la phrase de signature.

## 10. Vérification de déploiement en conditions réelles (phase 6)
`scripts/deploy_check.sh` construit l'image, démarre la pile de `compose.yaml` sous un AUTRE nom de projet
(`nelyqor-check` : volumes distincts de la production, Caddy en HTTP sur `127.0.0.1:18080`), avec des secrets
FACTICES générés pour l'occasion, aucune clé Bitvavo et un faux Bitvavo public (`scripts/deploy_fake_bitvavo.py`,
données synthétiques). Il affiche PASS/FAIL pour : montages réels (`/commands` en lecture seule dans le moteur
— écriture refusée avec EROFS —, `/display` en lecture seule dans l'API, ni `/engine` ni `/state` dans l'API,
utilisateur non root, pas de socket Docker, `read_only`, `cap_drop`, `no-new-privileges`), isolation des bases
(l'API ne peut pas ouvrir `engine.db` ; `bases_separees` vrai par la fonction réelle `channel_writable`), santé,
redémarrages (`docker compose restart`, `kill -9`), coupures réseau (moteur, API, échange en panne ou renvoyant
des données périmées : aucun achat, aucun plantage, reprise propre), arrêt d'urgence SSH et recette PWA
(`scripts/e2e_pwa.cjs`, si Node + Playwright sont installés). Résumé et journaux : `reports/deploy_check/`.
Le même script tourne sur GitHub Actions (`.github/workflows/nelyqor-deploy-check.yml`). Ne vérifie PAS :
le certificat Let's Encrypt (domaine réel), le pare-feu du VPS, ni l'accès réel à Bitvavo.
