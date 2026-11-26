#!/usr/bin/env bash
# Restauration : ARRÊTER le moteur d'abord (docker compose stop engine api).
# Usage : scripts/restore.sh backups/nelyqor-XXXX.db.enc [base_cible]
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="$1"
TARGET="${2:-${NELYQOR_DB:-data/engine.db}}"   # base du MOTEUR (phase 5)
PASS="${NELYQOR_BACKUP_PASS_FILE:-secrets/backup_passphrase}"
sha256sum -c "$SRC.sha256"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "$SRC" -out "$TMP" -pass "file:$PASS"
python3 -c "import sqlite3,sys; r=sqlite3.connect(sys.argv[1]).execute('PRAGMA integrity_check').fetchone()[0]; print('intégrité:', r); sys.exit(r!='ok')" "$TMP"
[ -f "$TARGET" ] && cp "$TARGET" "$TARGET.avant-restauration.$(date -u +%s)"
rm -f "$TARGET-wal" "$TARGET-shm"
cp "$TMP" "$TARGET"
echo "Restauré dans $TARGET. Au redémarrage, le moteur repasse par la réconciliation si le réel était actif."
