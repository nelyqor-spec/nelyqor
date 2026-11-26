#!/usr/bin/env bash
# Sauvegarde chiffrée de la base du MOTEUR engine.db (phase 5 ; copie cohérente via l'API backup de SQLite, puis chiffrement AES-256 + PBKDF2).
# La phrase de passe est lue depuis secrets/backup_passphrase (jamais dans le dépôt).
# Usage : scripts/backup.sh [dossier_destination]   — à planifier (cron/systemd timer) quotidiennement.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="${1:-backups}"
DB="${NELYQOR_DB:-data/engine.db}"   # base du MOTEUR (phase 5) ; commandes/affichage sont dérivées, non sauvegardées
PASS="${NELYQOR_BACKUP_PASS_FILE:-secrets/backup_passphrase}"
mkdir -p "$DEST"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
python3 - "$DB" "$TMP" <<'EOF'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2]); src.backup(dst); dst.close()
print("intégrité:", sqlite3.connect(sys.argv[2]).execute("PRAGMA integrity_check").fetchone()[0])
EOF
openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -in "$TMP" -out "$DEST/engine-$TS.db.enc" -pass "file:$PASS"
sha256sum "$DEST/engine-$TS.db.enc" > "$DEST/engine-$TS.db.enc.sha256"
# rétention : 30 dernières sauvegardes
ls -1t "$DEST"/engine-*.db.enc 2>/dev/null | tail -n +31 | while read -r f; do rm -f "$f" "$f.sha256"; done
echo "OK $DEST/engine-$TS.db.enc"
# Magasin d'état du moteur (phase 4) : sans lui, le robot repasse en INCIDENT après restauration.
STATE_DIR="${NELYQOR_ENGINE_STATE_HOST_DIR:-}"
if [ -n "$STATE_DIR" ] && [ -d "$STATE_DIR" ]; then
  tar -C "$STATE_DIR" -cf - . | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -out "$DEST/etat-moteur-$TS.tar.enc" -pass "file:$PASS"
  echo "OK $DEST/etat-moteur-$TS.tar.enc"
else
  echo "ATTENTION : NELYQOR_ENGINE_STATE_HOST_DIR non défini : magasin d'état du moteur NON sauvegardé"
fi
