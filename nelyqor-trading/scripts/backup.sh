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
# phase 7 : autorité INTERNE de Caddy (mode NELYQOR_TLS=internal) : la perdre oblige à refaire confiance sur l'iPhone
CADDY_PKI="${NELYQOR_CADDY_PKI_HOST_DIR:-$VOL/nelyqor_caddy_data/_data/caddy/pki}"
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
  python3 scripts/backup_mac.py sign "$PASS" "$DEST/$2-$TS.db.enc"  # authentification (HMAC-SHA256)
  echo "OK $DEST/$2-$TS.db.enc"
}
snap "$DB" engine
tar -C "$STATE_DIR" -cf - . | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -out "$DEST/etat-moteur-$TS.tar.enc" -pass "file:$PASS"
sha256sum "$DEST/etat-moteur-$TS.tar.enc" > "$DEST/etat-moteur-$TS.tar.enc.sha256"
python3 scripts/backup_mac.py sign "$PASS" "$DEST/etat-moteur-$TS.tar.enc"
echo "OK $DEST/etat-moteur-$TS.tar.enc"
if [ -f "$API_DB" ]; then snap "$API_DB" api; else echo "ATTENTION : audit de l'API absent ($API_DB) : non sauvegardé"; fi
if [ -d "$CADDY_PKI/authorities/local" ]; then
  tar -C "$CADDY_PKI" -cf - . | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -out "$DEST/caddy-pki-$TS.tar.enc" -pass "file:$PASS"
  sha256sum "$DEST/caddy-pki-$TS.tar.enc" > "$DEST/caddy-pki-$TS.tar.enc.sha256"
  python3 scripts/backup_mac.py sign "$PASS" "$DEST/caddy-pki-$TS.tar.enc"
  echo "OK $DEST/caddy-pki-$TS.tar.enc"
fi
# rétention (clôture phase 7, remarque 3) : par JEU d'horodatage, et non par type de fichier. On garde les
# NELYQOR_BACKUP_KEEP (30) jeux COMPLETS les plus récents (moteur + magasin d'état : la paire exigée par restore.sh),
# plus tout jeu incomplet plus récent qu'eux ; tous les fichiers des horodatages plus anciens sont supprimés ensemble.
python3 scripts/backup_retention.py "$DEST" "${NELYQOR_BACKUP_KEEP:-30}"
