#!/usr/bin/env bash
# Sauvegarde PLANIFIÉE (timer systemd nelyqor-backup.timer, phase 7) : scripts/backup.sh (chiffrée, rétention
# NELYQOR_BACKUP_KEEP), vérification de la sauvegarde produite (déchiffrement + intégrité SQLite) et ALERTE
# (scripts/alerte.sh) en cas d'échec. Refuse de lancer une sauvegarde si le disque de destination dépasse le seuil
# critique (elle accélérerait la saturation). En root (lit les volumes Docker).
# Usage : scripts/sauvegarde_auto.sh [dossier]   (défaut : NELYQOR_BACKUP_DIR ou /var/backups/nelyqor)
set -uo pipefail
cd "$(dirname "$0")/.."
DEST=${1:-${NELYQOR_BACKUP_DIR:-/var/backups/nelyqor}}
CRIT=${NELYQOR_DISK_CRIT_PCT:-90}
PASS="${NELYQOR_BACKUP_PASS_FILE:-secrets/backup_passphrase}"
fail() {
  echo "ÉCHEC de la sauvegarde : $1" >&2
  bash scripts/alerte.sh "Sauvegarde ECHOUEE" "$1" || true
  exit 1
}
mkdir -p "$DEST" 2>/dev/null && chmod 700 "$DEST" 2>/dev/null || fail "dossier de sauvegarde inutilisable : $DEST"
use=$(df -P "$DEST" | awk 'NR==2 {gsub("%", "", $5); print $5}')
[ -n "$use" ] && [ "$use" -lt "$CRIT" ] || fail "disque de $DEST rempli à ${use:-?} % (seuil $CRIT %) : sauvegarde non lancée"
out=$(bash scripts/backup.sh "$DEST" 2>&1) || fail "$(printf '%s\n' "$out" | tail -1)"
printf '%s\n' "$out"
last=$(printf '%s\n' "$out" | sed -n 's/^OK \(.*engine-.*\.db\.enc\)$/\1/p' | tail -1)
[ -n "$last" ] && [ -f "$last" ] || fail "sauvegarde du moteur introuvable après backup.sh"
TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "$last" -out "$TMP" -pass "file:$PASS" 2>/dev/null \
  || fail "la sauvegarde $last ne se déchiffre pas"
r=$(python3 -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('PRAGMA integrity_check').fetchone()[0])" "$TMP" 2>&1)
[ "$r" = ok ] || fail "sauvegarde $last : intégrité « $r »"
echo "VÉRIFIÉE $last (déchiffrement + intégrité ok)"
