#!/usr/bin/env bash
# Restauration d'une sauvegarde de scripts/backup.sh : base du moteur, magasin d'état (même horodatage,
# OBLIGATOIRE) et audit de l'API s'il existe. ARRÊTER le moteur et l'API d'abord (docker compose stop engine api) :
# refus si le moteur de production tourne. Cibles par défaut : les VOLUMES Docker du projet « nelyqor » ; une cible
# absente est refusée. Les fichiers restaurés appartiennent à l'uid 10001 (utilisateur des conteneurs).
# Usage : scripts/restore.sh backups/engine-AAAAMMJJTHHMMSSZ.db.enc
set -euo pipefail
cd "$(dirname "$0")/.."
SRC="${1:?usage : scripts/restore.sh backups/engine-<horodatage>.db.enc}"
VOL="${NELYQOR_VOLUMES_DIR:-/var/lib/docker/volumes}"
TARGET="${NELYQOR_DB:-$VOL/nelyqor_engine_data/_data/engine.db}"
STATE_DIR="${NELYQOR_ENGINE_STATE_HOST_DIR:-$VOL/nelyqor_engine_state/_data}"
API_DB="${NELYQOR_API_DB:-$VOL/nelyqor_api_data/_data/api.db}"
PASS="${NELYQOR_BACKUP_PASS_FILE:-secrets/backup_passphrase}"
OWNER="${NELYQOR_RESTORE_OWNER:-10001:10001}"
CADDY_PKI="${NELYQOR_CADDY_PKI_HOST_DIR:-$VOL/nelyqor_caddy_data/_data/caddy/pki}"   # « - » : ne pas changer le propriétaire (tests uniquement)
case "$(basename "$SRC")" in engine-*.db.enc) ;; *) echo "REFUS : attendu engine-<horodatage>.db.enc" >&2; exit 1 ;; esac
TS="$(basename "$SRC" .db.enc)"; TS="${TS#engine-}"
STATE_SRC="$(dirname "$SRC")/etat-moteur-$TS.tar.enc"
API_SRC="$(dirname "$SRC")/api-$TS.db.enc"
[ -d "$(dirname "$TARGET")" ] || { echo "REFUS : dossier cible absent : $(dirname "$TARGET")" >&2; exit 1; }
[ -d "$STATE_DIR" ] || { echo "REFUS : magasin d'état cible absent : $STATE_DIR" >&2; exit 1; }
[ -f "$STATE_SRC" ] || { echo "REFUS : sauvegarde du magasin d'état absente : $STATE_SRC" >&2; exit 1; }
if command -v docker > /dev/null && [ -n "$(docker ps -q --filter label=com.docker.compose.project=nelyqor \
    --filter label=com.docker.compose.service=engine 2>/dev/null)" ]; then
  echo "REFUS : le moteur de production tourne (docker compose stop engine api)" >&2; exit 1
fi
own() { [ "$OWNER" = - ] || chown "$OWNER" "$@"; }
TMP="$(mktemp)"; TMPD="$(mktemp -d)"
trap 'rm -rf "$TMP" "$TMPD"' EXIT
STAMP="$(date -u +%s)"
restore_db() {  # restore_db FICHIER_CHIFFRÉ CIBLE
  sha256sum -c "$1.sha256"
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "$1" -out "$TMP" -pass "file:$PASS"
  python3 -c "import sqlite3,sys; r=sqlite3.connect(sys.argv[1]).execute('PRAGMA integrity_check').fetchone()[0]; print('intégrité:', r); sys.exit(r!='ok')" "$TMP"
  if [ -f "$2" ]; then cp -p "$2" "$2.avant-restauration.$STAMP"; fi
  rm -f "$2-wal" "$2-shm"
  cp "$TMP" "$2"; chmod 600 "$2"; own "$2"
  echo "Restauré : $2"
}
sha256sum -c "$STATE_SRC.sha256"
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "$STATE_SRC" -pass "file:$PASS" | tar -C "$TMPD" -xf -
[ -f "$TMPD/etat_moteur.json" ] || { echo "REFUS : archive du magasin d'état sans etat_moteur.json" >&2; exit 1; }
restore_db "$SRC" "$TARGET"
tar -C "$STATE_DIR" -cf "$STATE_DIR/../etat-avant-restauration.$STAMP.tar" . 2>/dev/null || true
find "$STATE_DIR" -mindepth 1 -delete
cp -a "$TMPD/." "$STATE_DIR/"; own -R "$STATE_DIR"
echo "Restauré : magasin d'état $STATE_DIR"
if [ -f "$API_SRC" ]; then
  [ -d "$(dirname "$API_DB")" ] && restore_db "$API_SRC" "$API_DB" || echo "ATTENTION : dossier de api.db absent : non restauré"
fi
PKI_SRC="$(dirname "$SRC")/caddy-pki-$TS.tar.enc"
if [ -f "$PKI_SRC" ]; then
  # autorité interne de Caddy (propriétaire root dans le conteneur Caddy) : l'iPhone garde sa confiance
  if [ -d "$(dirname "$(dirname "$CADDY_PKI")")" ]; then
    sha256sum -c "$PKI_SRC.sha256"
    mkdir -p "$CADDY_PKI" && chmod 700 "$CADDY_PKI"
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "$PKI_SRC" -pass "file:$PASS" | tar -C "$CADDY_PKI" -xf -
    echo "Restauré : autorité interne de Caddy $CADDY_PKI"
  else
    echo "ATTENTION : volume de Caddy absent ($CADDY_PKI) : autorité interne non restaurée"
  fi
fi
echo "Au redémarrage, le moteur repasse par la réconciliation si le réel était actif (ordres ouverts rapprochés)."
