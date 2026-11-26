#!/usr/bin/env bash
# Sauvegarde chiffrée (AES-256 + PBKDF2) de la production : base du MOTEUR engine.db (copie cohérente via l'API
# backup de SQLite), magasin d'état du moteur (/state, OBLIGATOIRE : sans lui, INCIDENT après restauration) et
# audit de l'API api.db. commandes.db / affichage.db sont dérivées : non sauvegardées.
# Cibles par défaut : les VOLUMES Docker du projet « nelyqor » (phase 6, contre-expertise D7) ; une cible absente
# est REFUSÉE (jamais une sauvegarde silencieuse d'un fichier vide ou d'un chemin de développement).
# La phrase de passe est lue depuis secrets/backup_passphrase (jamais dans le dépôt).
# Usage : scripts/backup.sh [dossier_destination]   — à planifier (cron/systemd timer) quotidiennement, en root.
set -euo pipefail
cd "$(dirname "$0")/.."
VOL="${NELYQOR_VOLUMES_DIR:-/var/lib/docker/volumes}"
DEST="${1:-backups}"
DB="${NELYQOR_DB:-$VOL/nelyqor_engine_data/_data/engine.db}"
STATE_DIR="${NELYQOR_ENGINE_STATE_HOST_DIR:-$VOL/nelyqor_engine_state/_data}"
API_DB="${NELYQOR_API_DB:-$VOL/nelyqor_api_data/_data/api.db}"
PASS="${NELYQOR_BACKUP_PASS_FILE:-secrets/backup_passphrase}"
[ -f "$DB" ] || { echo "REFUS : base du moteur absente : $DB (définir NELYQOR_DB)" >&2; exit 1; }
[ -f "$STATE_DIR/etat_moteur.json" ] || { echo "REFUS : magasin d'état absent : $STATE_DIR/etat_moteur.json (définir NELYQOR_ENGINE_STATE_HOST_DIR)" >&2; exit 1; }
[ -f "$PASS" ] || { echo "REFUS : phrase de passe absente : $PASS" >&2; exit 1; }
mkdir -p "$DEST"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
snap() {  # snap SOURCE NOM : copie SQLite cohérente, contrôle d'intégrité, chiffrement
  python3 - "$1" "$TMP" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True); dst = sqlite3.connect(sys.argv[2]); src.backup(dst); dst.close()
r = sqlite3.connect(sys.argv[2]).execute("PRAGMA integrity_check").fetchone()[0]
print("intégrité:", r); sys.exit(r != "ok")
PY
  openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -in "$TMP" -out "$DEST/$2-$TS.db.enc" -pass "file:$PASS"
  sha256sum "$DEST/$2-$TS.db.enc" > "$DEST/$2-$TS.db.enc.sha256"
  echo "OK $DEST/$2-$TS.db.enc"
}
snap "$DB" engine
tar -C "$STATE_DIR" -cf - . | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -out "$DEST/etat-moteur-$TS.tar.enc" -pass "file:$PASS"
sha256sum "$DEST/etat-moteur-$TS.tar.enc" > "$DEST/etat-moteur-$TS.tar.enc.sha256"
echo "OK $DEST/etat-moteur-$TS.tar.enc"
if [ -f "$API_DB" ]; then snap "$API_DB" api; else echo "ATTENTION : audit de l'API absent ($API_DB) : non sauvegardé"; fi
# rétention : NELYQOR_BACKUP_KEEP (30 par défaut) dernières sauvegardes de chaque type
for p in engine-*.db.enc api-*.db.enc etat-moteur-*.tar.enc; do
  # shellcheck disable=SC2012
  ls -1t "$DEST"/$p 2>/dev/null | tail -n +$(( ${NELYQOR_BACKUP_KEEP:-30} + 1 )) | while read -r f; do rm -f "$f" "$f.sha256"; done
done
