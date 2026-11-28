#!/usr/bin/env bash
# Vérification des INCIDENTS en conditions réelles (Docker, phase 7) — SIMULATION uniquement, secrets FACTICES.
#
# Démarre la pile de compose.yaml sous un nom de projet DISTINCT (nelyqor-incident : volumes séparés de la
# production), en HTTPS avec le certificat INTERNE de Caddy, un faux Bitvavo public (données synthétiques) et un faux
# serveur ntfy (alertes), puis provoque chaque incident et affiche PASS/FAIL réels :
#   1. HTTPS (certificat interne vérifié, TLS 1.2+ seulement, HSTS et en-têtes de sécurité, redirection HTTP) ;
#   2. accès non autorisé (API directe sans Caddy, CSRF absent/faux, rejeu de session, rejeu d'une commande signée,
#      mots de passe faux en rafale, X-Forwarded-For usurpé) ;
#   3. sauvegarde -> DESTRUCTION des volumes -> restauration -> reprise du moteur sans fill, intention ni position
#      dupliqués (empreintes comparées avant/après) ;
#   4. échec de sauvegarde -> alerte ; rétention ; surveillance du disque de l'hôte -> alerte + laboratoire arrêté ;
#   5. Bitvavo indisponible (503, 429, sans réponse), données incohérentes, coupure d'Internet du moteur ;
#   6. défaillance de processus (kill -9 moteur, API, Caddy) et redémarrage complet de la pile (et du démon Docker si
#      possible) ;
#   7. saturation RÉELLE du disque du moteur (volume tmpfs de 64 Mo) : alerte, écritures non critiques arrêtées,
#      engine.db intacte même à 100 %, reprise après libération.
# Aucune clé Bitvavo (fichiers vides), NELYQOR_REAL_TRADING_ALLOWED=false : aucun ordre réel possible.
# Usage : scripts/incident_check.sh [--no-build] [--keep]
# Variables : INCIDENT_PROJECT (nelyqor-incident), INCIDENT_HTTP_PORT (18081), INCIDENT_HTTPS_PORT (18443),
#             INCIDENT_FAKE_PORT (18089), INCIDENT_OUT (reports/incident_check), INCIDENT_IMAGE
#             (nelyqor-trading:deploy-check, JAMAIS nelyqor-trading:local), INCIDENT_DOCKERD_RESTART (commande qui
#             redémarre le démon Docker ; défaut : `sudo -n systemctl restart docker` si systemd gère Docker).
# Garde-fous : refuse le projet « nelyqor » et refuse de tourner si la pile de production est active.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT_DIR=$(pwd)
BUILD=1; KEEP=0
for a in "$@"; do
  case "$a" in
    --no-build) BUILD=0 ;;
    --keep) KEEP=1 ;;
    *) echo "option inconnue : $a" >&2; exit 2 ;;
  esac
done
PROJ=${INCIDENT_PROJECT:-nelyqor-incident}
HTTP_PORT=${INCIDENT_HTTP_PORT:-18081}
HTTPS_PORT=${INCIDENT_HTTPS_PORT:-18443}
FAKE_PORT=${INCIDENT_FAKE_PORT:-18089}
OUT=${INCIDENT_OUT:-reports/incident_check}
IMAGE=${INCIDENT_IMAGE:-nelyqor-trading:deploy-check}
DOMAIN=nelyqor.internal
case "$PROJ" in
  nelyqor|"") echo "REFUS : projet « $PROJ » = production ; choisir un autre INCIDENT_PROJECT" >&2; exit 2 ;;
esac
[ "$IMAGE" = nelyqor-trading:local ] && { echo "REFUS : $IMAGE est le tag de l'image de production" >&2; exit 2; }
if [ -n "$(docker ps -q --filter label=com.docker.compose.project=nelyqor 2>/dev/null)" ]; then
  echo "REFUS : la pile de production « nelyqor » tourne sur cet hôte ; lancer la vérification ailleurs ou en CI" >&2
  exit 2
fi
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
WORK=$(mktemp -d)
RESULTS="$OUT/resultats.tsv"
: > "$RESULTS"
PASS=0; FAIL=0; SKIP=0

export COMPOSE_PROJECT_NAME="$PROJ"
export COMPOSE_FILE="$ROOT_DIR/compose.yaml:$WORK/override.yaml"
export NELYQOR_DOMAIN="$DOMAIN" NELYQOR_TLS=internal NELYQOR_BIND_IP=127.0.0.1
export NELYQOR_REAL_TRADING_ALLOWED=false

log() { echo "[$(date -u +%T)] $*"; }
record() {
  printf '%s\t%s\t%s\n' "$1" "$2" "${3:-}" >> "$RESULTS"
  case "$1" in PASS) PASS=$((PASS + 1)) ;; FAIL) FAIL=$((FAIL + 1)) ;; *) SKIP=$((SKIP + 1)) ;; esac
  printf '%-5s %s %s\n' "$1" "$2" "${3:+— $3}"
}
check() {
  local name=$1; shift
  local o
  if o=$("$@" 2>&1); then record PASS "$name" "$(echo "$o" | tail -1 | cut -c1-200)"
  else record FAIL "$name" "$(echo "$o" | tail -1 | cut -c1-200)"; fi
}
asroot() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo -n "$@"; fi; }
dc() { docker compose "$@"; }
cid() { dc ps -q "$1" | head -1; }
iprobe() { dc exec -T "$1" python - "${@:2}" < "$ROOT_DIR/scripts/incident_probe.py"; }
dprobe() { dc exec -T "$1" python - "$2" < "$ROOT_DIR/scripts/deploy_probe.py"; }
jget() {
  python3 -c '
import json, sys
try:
    v = json.loads(sys.stdin.read().strip().splitlines()[-1])
    for k in sys.argv[1].split("."):
        v = v.get(k) if isinstance(v, dict) else None
except Exception:
    sys.exit(0)
print(v if isinstance(v, str) else json.dumps(v))' "$1"
}
counts() { iprobe engine counts 2>/dev/null | tail -1; }
state() { dprobe engine state 2>/dev/null | tail -1; }
st() { state | jget "$1"; }
fresh() { [ "$(st data_ok)" = "true" ] && python3 -c "import sys; sys.exit(0 if float('$(st heartbeat_age_s)') < 30 else 1)"; }
alive() { python3 -c "import sys; sys.exit(0 if float('$(st heartbeat_age_s)') < 30 else 1)"; }
fake_mode() { curl -fsS --max-time 5 -X POST "http://127.0.0.1:$FAKE_PORT/control/mode/$1"; }
alerts() { curl -fsS --max-time 5 "http://127.0.0.1:$FAKE_PORT/control/alerts"; }
wait_for() {
  local t=$1; shift
  local end=$(( $(date +%s) + t ))
  while [ "$(date +%s)" -lt "$end" ]; do "$@" > /dev/null 2>&1 && return 0; sleep 3; done
  "$@"
}
restarts() { docker inspect -f '{{.RestartCount}}' "$(cid "$1")"; }
running() { [ "$(docker inspect -f '{{.State.Running}}' "$(cid "$1")")" = "true" ]; }
hardkill() { local pid; pid=$(docker inspect -f '{{.State.Pid}}' "$(cid "$1")"); kill -9 "$pid" 2>/dev/null || sudo -n kill -9 "$pid"; }
CURL=(curl -sS --noproxy '*' --max-time 10 --cacert "$WORK/ca.crt" --resolve "$DOMAIN:$HTTPS_PORT:127.0.0.1")
U="https://$DOMAIN:$HTTPS_PORT"
same() {  # same JSON_AVANT JSON_APRÈS CLÉ... : valeurs identiques
  local a=$1 b=$2; shift 2
  local k
  for k in "$@"; do
    [ "$(echo "$a" | jget "$k")" = "$(echo "$b" | jget "$k")" ] || { echo "$k : $(echo "$a" | jget "$k") -> $(echo "$b" | jget "$k")"; return 1; }
  done
  echo "identiques : $*"
}
no_dupes() {  # no_dupes JSON : aucun client_order_id dupliqué, au plus un stop actif par marché, base intègre
  [ "$(echo "$1" | jget client_order_id_doublons)" = 0 ] && [ "$(echo "$1" | jget stops_actifs_max_par_marche)" -le 1 ] \
    && [ "$(echo "$1" | jget integrite)" = ok ] && echo "doublons=0, stops/marché≤1, intégrité ok" \
    || { echo "doublons=$(echo "$1" | jget client_order_id_doublons) stops=$(echo "$1" | jget stops_actifs_max_par_marche) intégrité=$(echo "$1" | jget integrite)"; return 1; }
}
same_clean() { same "$1" "$2" fills fills_empreinte achats_empreinte positions && no_dupes "$2"; }
https_ok() { [ "$("${CURL[@]}" -o /dev/null -w '%{http_code} %{ssl_verify_result}' "$U/api/health")" = "200 0" ] && echo "200, vérification 0"; }
engine_alive_https() { "${CURL[@]}" -f "$U/api/health" | grep -q '"engine_alive":true'; }
n_alerts() { alerts | python3 -c "import json,sys; print(len(json.load(sys.stdin)))"; }
alert_since() {  # alert_since N SUJET MOT : une alerte reçue après la N-ième, sur SUJET, dont le titre contient MOT
  alerts | python3 -c "import json,sys; a=json.load(sys.stdin)[int(sys.argv[1]):]; sys.exit(0 if any(x['topic']==sys.argv[2] and sys.argv[3] in x['title'] for x in a) else 1)" "$@" && echo "alerte « $3 » reçue"
}
vol() { docker volume inspect -f '{{.Mountpoint}}' "${PROJ}_$1"; }
export -f asroot dc cid iprobe dprobe jget counts state st fresh alive running restarts alerts
export ROOT_DIR FAKE_PORT

cleanup() {
  dc logs --no-color --timestamps > "$OUT/compose_logs.txt" 2>&1 || true
  for s in engine api caddy fakebitvavo; do dc logs --no-color "$s" > "$OUT/log_$s.txt" 2>&1 || true; done
  if [ "$KEEP" = 0 ]; then dc --profile lab down -v --remove-orphans > /dev/null 2>&1 || true; fi
  docker run --rm --user 0 -v "$WORK:/w" "$IMAGE" sh -c 'rm -rf /w/secrets /w/backups /w/bk_fail' > /dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

# ================================================================== 0. image, secrets FACTICES, surcharge de test
if [ "$BUILD" = 1 ]; then
  log "construction de l'image (Dockerfile du dépôt)"
  if docker build -t "$IMAGE" . > "$OUT/build.txt" 2>&1; then record PASS "construction de l'image" "$IMAGE"
  else record FAIL "construction de l'image" "voir build.txt"; tail -30 "$OUT/build.txt"; exit 1; fi
else
  docker image inspect "$IMAGE" > /dev/null 2>&1 || { echo "image $IMAGE absente" >&2; exit 1; }
  record SKIP "construction de l'image" "--no-build : image existante utilisée"
fi
mkdir -p "$WORK/secrets"
PW="faux-mdp-$(openssl rand -hex 8)"
PHRASE="fausse-phrase-de-signature-$(openssl rand -hex 8)"
docker run --rm -e NELYQOR_ROLE=lab -e PW="$PW" -e PHRASE="$PHRASE" "$IMAGE" python -c '
import os, sys
sys.path.insert(0, "/app")
from backend.api.auth import hash_password
from backend.core.command_sig import derive_key, new_salt
s = new_salt()
print(hash_password(os.environ["PW"])); print(s); print(derive_key(os.environ["PHRASE"], s).hex())
' > "$WORK/gen.txt" || { echo "génération des secrets factices impossible" >&2; exit 1; }
sed -n 1p "$WORK/gen.txt" > "$WORK/secrets/owner_password_hash"
sed -n 2p "$WORK/gen.txt" > "$WORK/secrets/owner_command_salt"
sed -n 3p "$WORK/gen.txt" > "$WORK/secrets/owner_command_key"
SALT=$(cat "$WORK/secrets/owner_command_salt")
rm -f "$WORK/gen.txt"
openssl rand -hex 32 > "$WORK/secrets/engine_state_key"
: > "$WORK/secrets/bitvavo_api_key"; : > "$WORK/secrets/bitvavo_api_secret"   # AUCUNE clé : simulation seule
echo "http://fakebitvavo:8080/ntfy/moteur" > "$WORK/secrets/ntfy_url"         # faux ntfy (réseau interne)
openssl rand -base64 32 > "$WORK/backup_passphrase"; chmod 600 "$WORK/backup_passphrase"
echo "http://127.0.0.1:$FAKE_PORT/ntfy/hote" > "$WORK/ntfy_hote"                # alertes de l'hôte -> faux ntfy
chmod 600 "$WORK/secrets"/*
docker run --rm --user 0 -v "$WORK/secrets:/s" "$IMAGE" sh -c \
  'chown 10001:10001 /s/* && chmod 644 /s/owner_command_salt' || { echo "chown des secrets factices impossible" >&2; exit 1; }

write_override() {  # write_override [tmpfs] : surcharge de TEST (faux Bitvavo/ntfy, HTTPS interne, ports locaux)
  cat > "$WORK/override.yaml" <<EOF
services:
  engine:
    image: $IMAGE
    environment:
      BITVAVO_REST_URL: http://fakebitvavo:8080/v2
      NELYQOR_DISK_WARN_FREE_MB: "${DISK_WARN:-2048}"
      NELYQOR_DISK_CRIT_FREE_MB: "${DISK_CRIT:-512}"
    depends_on: [fakebitvavo]
  api:
    image: $IMAGE
  caddy:
    ports: !override ["127.0.0.1:${HTTP_PORT}:80", "127.0.0.1:${HTTPS_PORT}:443"]
    networks:
      default: {ipv4_address: 172.30.57.10, aliases: ["$DOMAIN"]}   # nom du certificat, résolu dans la pile
    volumes:
      - $WORK/secrets/owner_command_salt:/srv/meta/owner_command_salt:ro
  lab:
    image: $IMAGE
    command: ["sleep", "3600"]   # écrivain non critique factice (arrêté par surveillance_disque.sh)
  fakebitvavo:
    image: $IMAGE
    command: ["python", "/fake/deploy_fake_bitvavo.py", "8080"]
    ports: ["127.0.0.1:${FAKE_PORT}:8080"]   # pilotage du test et faux ntfy pour les alertes de l'hôte
    volumes:
      - $ROOT_DIR/scripts/deploy_fake_bitvavo.py:/fake/deploy_fake_bitvavo.py:ro
    read_only: true
    security_opt: ["no-new-privileges:true"]
    cap_drop: ["ALL"]
    environment:
      NELYQOR_ROLE: lab
secrets:
  bitvavo_api_key: {file: $WORK/secrets/bitvavo_api_key}
  bitvavo_api_secret: {file: $WORK/secrets/bitvavo_api_secret}
  owner_password_hash: {file: $WORK/secrets/owner_password_hash}
  engine_state_key: {file: $WORK/secrets/engine_state_key}
  owner_command_key: {file: $WORK/secrets/owner_command_key}
  owner_command_salt: {file: $WORK/secrets/owner_command_salt}
  ntfy_url: {file: $WORK/secrets/ntfy_url}
EOF
  if [ "${1:-}" = tmpfs ]; then
    cat >> "$WORK/override.yaml" <<EOF
volumes:
  engine_data:
    driver_opts: {type: tmpfs, device: tmpfs, o: "size=64m,uid=10001,gid=10001,mode=0700"}
EOF
  fi
}
write_override
check "configuration compose valide (HTTPS interne)" dc config -q
check "image : sondes et faux services NON livrés" \
  docker run --rm "$IMAGE" sh -c 'test ! -e scripts/incident_probe.py && test ! -e scripts/deploy_fake_bitvavo.py && echo absents'

# ================================================================== 1. démarrage
dc --profile lab down -v --remove-orphans > /dev/null 2>&1 || true
log "démarrage de la pile ($PROJ) en SIMULATION, HTTPS interne"
if ! dc up -d --no-build engine api caddy fakebitvavo > "$OUT/up.txt" 2>&1; then
  record FAIL "démarrage de la pile" "$(tail -1 "$OUT/up.txt")"; cat "$OUT/up.txt"; exit 1
fi
record PASS "démarrage de la pile" "engine, api, caddy, fakebitvavo"
wait_for 180 fresh
check "moteur : données fraîches au démarrage" fresh

# ================================================================== 2. HTTPS (certificat interne de Caddy)
log "HTTPS : certificat interne, versions TLS, en-têtes"
wait_for 60 dc exec -T caddy test -s /data/caddy/pki/authorities/local/root.crt
dc exec -T caddy cat /data/caddy/pki/authorities/local/root.crt > "$WORK/ca.crt" 2>/dev/null
check "https : certificat vérifié par la racine interne de Caddy" wait_for 60 https_ok
no_ca() { ! curl -sS --noproxy '*' --max-time 10 --resolve "$DOMAIN:$HTTPS_PORT:127.0.0.1" -o /dev/null "$U/api/health" 2>/dev/null && echo refusé; }
check "https : certificat refusé SANS la racine interne (comme un iPhone non configuré)" no_ca
tls() { echo | timeout 15 openssl s_client -connect "127.0.0.1:$HTTPS_PORT" -servername "$DOMAIN" "$@" 2>&1; }
tls_ok() { tls "-$1" | grep -q "Protocol *: TLSv$2" && echo "TLSv$2"; }
check "tls : TLS 1.3 accepté" tls_ok tls1_3 1.3
check "tls : TLS 1.2 accepté" tls_ok tls1_2 1.2
for v in tls1_1 tls1; do
  o=$(tls "-$v" -cipher 'DEFAULT@SECLEVEL=0')
  if echo "$o" | grep -qiE 'alert protocol version|unsupported protocol|wrong version number'; then
    record PASS "tls : $v refusé par le serveur" "$(echo "$o" | grep -oiE 'alert protocol version|unsupported protocol|wrong version number' | head -1)"
  elif echo "$o" | grep -qi 'no protocols available'; then
    record SKIP "tls : $v" "client OpenSSL local incapable de proposer $v (refus non mesurable ici)"
  else
    record FAIL "tls : $v refusé par le serveur" "$(echo "$o" | grep -m1 -iE 'Protocol|error')"
  fi
done
"${CURL[@]}" -D "$WORK/h_root.txt" -o /dev/null "$U/" || true
"${CURL[@]}" -D "$WORK/h_api.txt" -o /dev/null "$U/api/health" || true
hsts() {
  local h; h=$(grep -i '^strict-transport-security:' "$WORK/h_root.txt" | tr -d '\r')
  python3 -c "import re,sys; m=re.search(r'max-age=(\d+)', sys.argv[1]); sys.exit(0 if m and int(m.group(1)) >= 31536000 else 1)" "$h" && echo "$h"
}
check "https : HSTS max-age ≥ 1 an" hsts
for h in "X-Content-Type-Options: nosniff" "X-Frame-Options: DENY" "Referrer-Policy: no-referrer"; do
  check "https : en-tête $h" grep -qi "^$h" "$WORK/h_root.txt"
done
csp_ok() {
  local c; c=$(grep -i '^content-security-policy:' "$WORK/h_root.txt")
  echo "$c" | grep -q "default-src 'self'" && echo "$c" | grep -q "frame-ancestors 'none'" \
    && ! echo "$c" | grep -qE "script-src[^;]*unsafe-inline" && echo ok
}
check "https : CSP stricte (default-src 'self', frame-ancestors 'none', aucun script inline)" csp_ok
check "https : pas d'en-tête Server" bash -c "! grep -qi '^server:' '$WORK/h_root.txt' && echo absent"
api_hdr_ok() { grep -qi '^content-security-policy: sandbox' "$WORK/h_api.txt" && grep -qi '^cache-control: no-store' "$WORK/h_api.txt" && echo ok; }
check "https : réponses /api sandboxées et non mises en cache" api_hdr_ok
redir_ok() {
  curl -sS --noproxy '*' --max-time 10 -o /dev/null -w '%{http_code} %{redirect_url}' -H "Host: $DOMAIN" \
    "http://127.0.0.1:$HTTP_PORT/" | grep -qE "^30[18] https://$DOMAIN/" && echo ok
}
check "http : redirection permanente vers https" redir_ok

# ================================================================== 3. accès non autorisé
log "accès non autorisé : API directe, CSRF, rejeu, rafale, X-Forwarded-For"
check "api : aucun port publié (accès direct impossible sans Caddy)" bash -c "[ -z \"\$(docker port $(cid api))\" ] && [ -z \"\$(docker port $(cid engine))\" ] && echo aucun"
check "api : connexion directe 127.0.0.1:8000 refusée depuis l'hôte" bash -c "! curl -s --max-time 5 http://127.0.0.1:8000/api/health > /dev/null && echo refusé"
check "ports publiés : seulement Caddy et le faux service de test, sur 127.0.0.1" bash -c "
  for c in \$(docker ps -q --filter label=com.docker.compose.project=$PROJ); do
    docker port \$c | grep -v '127.0.0.1:' && exit 1
    s=\$(docker inspect -f '{{index .Config.Labels \"com.docker.compose.service\"}}' \$c)
    [ -n \"\$(docker port \$c)\" ] && case \$s in caddy|fakebitvavo) ;; *) echo \"\$s publié\"; exit 1 ;; esac
  done; echo ok"
login() {  # login FICHIER_COOKIES : imprime le jeton CSRF
  "${CURL[@]}" -c "$1" -H 'Content-Type: application/json' -d "{\"password\":\"$PW\"}" "$U/api/login" | jget csrf
}
CSRF=$(login "$WORK/cj")
owner_login_ok() { local c; c=$(login "$1"); [ -n "$c" ] && [ "$c" != null ] && echo "connexion 200"; }
check "connexion légitime (HTTPS, mot de passe factice)" bash -c "[ -n '$CSRF' ] && [ '$CSRF' != null ] && echo ok"
cookie_ok() {
  local c; c=$("${CURL[@]}" -D - -o /dev/null -H 'Content-Type: application/json' -d "{\"password\":\"$PW\"}" "$U/api/login" | grep -i '^set-cookie:')
  echo "$c" | grep -qi 'secure' && echo "$c" | grep -qi 'httponly' && echo "$c" | grep -qi 'samesite=strict' && echo ok
}
check "cookie de session : Secure, HttpOnly, SameSite=Strict" cookie_ok
code() { "${CURL[@]}" -o /dev/null -w '%{http_code}' "$@"; }
check "csrf : commande SANS jeton refusée (403)" [ "$(code -b "$WORK/cj" -X POST "$U/api/control/deactivate_real")" = 403 ]
check "csrf : commande avec un FAUX jeton refusée (403)" [ "$(code -b "$WORK/cj" -H 'X-CSRF-Token: faux' -X POST "$U/api/control/deactivate_real")" = 403 ]
# témoin : commande SANS effet sur l'état (un retour en simulation daterait une révocation et invaliderait la
# signature du test de rejeu ci-dessous)
check "csrf : commande avec le bon jeton acceptée (témoin)" [ "$(code -b "$WORK/cj" -H "X-CSRF-Token: $CSRF" -X POST "$U/api/control/liquidation_estimate")" = 200 ]
check "sans session : lecture refusée (401)" [ "$(code "$U/api/status")" = 401 ]
# rejeu d'une commande SIGNÉE (activation du réel) : le moteur consomme le nonce, la 2e soumission est un rejeu
SIGNED=$(docker run --rm -e NELYQOR_ROLE=lab -e PHRASE="$PHRASE" -e SALT="$SALT" -i "$IMAGE" python - sign \
         < scripts/incident_probe.py | tail -1)
submit_signed() {
  "${CURL[@]}" -b "$WORK/cj" -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
    -d "$(echo "$SIGNED" | python3 -c "import json,sys; d=json.load(sys.stdin); d['password']=sys.argv[1]; print(json.dumps(d))" "$PW")" \
    "$U/api/control/activate_real" | jget command_id
}
C1=$(submit_signed); C2=$(submit_signed)
res() { iprobe engine cmd_result "$1" | tail -1; }
cmd_done() { [ "$(res "$1" | jget status)" = done ]; }
wait_for 60 cmd_done "$C2"
R1=$(res "$C1"); R2=$(res "$C2")
echo "$R1" > "$OUT/commande_signee_1.json"; echo "$R2" > "$OUT/commande_signee_2.json"
first_ok() {  # signature ACCEPTÉE (nonce consommé) : aucun motif lié à la signature, refus par le drapeau serveur seul
  echo "$R1" | grep -q '"ok": false' && echo "$R1" | grep -q 'NELYQOR_REAL_TRADING_ALLOWED' \
    && ! echo "$R1" | grep -qE 'signature|non signée|commande_signee_par_le_proprietaire' && echo "signature valide, refus serveur"
}
second_replay() { echo "$R2" | grep -q 'déjà utilisée (rejeu)' && echo rejeu; }
check "rejeu : 1re commande signée vérifiée (nonce consommé, refusée par le drapeau serveur)" first_ok
check "rejeu : 2e soumission IDENTIQUE refusée « signature déjà utilisée (rejeu) »" second_replay
check "rejeu : état toujours SIMULATION" bash -c "[ \"\$(st etat)\" = SIMULATION ] && echo SIMULATION"
LOGOUT=$(code -b "$WORK/cj" -H "X-CSRF-Token: $CSRF" -X POST "$U/api/logout")
check "rejeu de session : cookie réutilisé après déconnexion refusé (401)" bash -c \
  "[ '$LOGOUT' = 200 ] && [ '$(code -b "$WORK/cj" "$U/api/status")' = 401 ] && echo 401"
CA_PEM=$(cat "$WORK/ca.crt")
B=$(dc exec -T -e URL="https://$DOMAIN/api/login" -e N=7 -e CA_PEM="$CA_PEM" fakebitvavo \
    sh -c "python - brute" < scripts/incident_probe.py 2>&1 | tail -1)
echo "$B" > "$OUT/rafale.json"
check "rafale : 5 refus (401) puis verrouillage (429) de l'adresse attaquante" bash -c \
  "[ \"\$(echo '$B' | jget codes)\" = '[401, 401, 401, 401, 401, 429, 429]' ] && echo '$B'"
B2=$(dc exec -T -e URL="https://$DOMAIN/api/login" -e N=1 -e CA_PEM="$CA_PEM" -e PW="$PW" fakebitvavo \
     sh -c "python - brute" < scripts/incident_probe.py 2>&1 | tail -1)
check "rafale : même le BON mot de passe est refusé depuis l'adresse verrouillée" bash -c "[ \"\$(echo '$B2' | jget codes)\" = '[429]' ] && echo 429"
check "rafale : le propriétaire (autre adresse) n'est pas verrouillé" owner_login_ok "$WORK/cj2"
B3=$(dc exec -T -e URL="http://api:8000/api/login" -e XFF=1 -e N=3 -e PW="$PW" fakebitvavo \
     sh -c "python - brute" < scripts/incident_probe.py 2>&1 | tail -1)
check "X-Forwarded-For usurpé (accès direct au réseau interne, hors Caddy) : ignoré, adresse réelle toujours verrouillée" \
  bash -c "[ \"\$(echo '$B3' | jget codes)\" = '[429, 429, 429]' ] && echo '$B3'"

# ================================================================== 4. sauvegarde, destruction, restauration
log "position simulée réelle (vrai code d'exécution) avant les incidents"
dc stop engine > /dev/null 2>&1
SEED=$(dc run --rm --no-deps -T engine python - seed < scripts/incident_probe.py 2>&1 | tail -1)
echo "$SEED" > "$OUT/seed.json"
check "seed : achat simulé de 30 € accepté (journal réel : intention + fill + stop)" bash -c "echo '$SEED' | grep -q '\"achat_accepte\": true' && echo ok"
dc start engine > /dev/null 2>&1
wait_for 120 fresh
sleep 70   # un cycle de décision et de gestion des positions
C0=$(counts); echo "$C0" > "$OUT/counts_initial.json"
check "journal initial : au moins 1 fill, 1 achat, position BTC" bash -c "[ \"\$(echo '$C0' | jget fills)\" -ge 1 ] && [ \"\$(echo '$C0' | jget achats)\" -ge 1 ] && echo '$C0' | grep -q 'BTC-EUR' && echo \"fills=\$(echo '$C0' | jget fills) intents=\$(echo '$C0' | jget intents)\""
check "journal initial : aucun doublon" no_dupes "$C0"

log "sauvegarde (moteur et API arrêtés), DESTRUCTION des volumes, restauration"
dc stop engine api > /dev/null 2>&1
CB=$(dc run --rm --no-deps -T engine python - counts < scripts/incident_probe.py 2>/dev/null | tail -1)
echo "$CB" > "$OUT/counts_avant_sauvegarde.json"
BKENV=(env NELYQOR_DB="$(vol engine_data)/engine.db" NELYQOR_ENGINE_STATE_HOST_DIR="$(vol engine_state)"
       NELYQOR_API_DB="$(vol api_data)/api.db" NELYQOR_CADDY_PKI_HOST_DIR="$(vol caddy_data)/caddy/pki"
       NELYQOR_BACKUP_PASS_FILE="$WORK/backup_passphrase" NELYQOR_ALERT_NTFY_FILE="$WORK/ntfy_hote")
mkdir -p "$WORK/backups"
check "sauvegarde chiffrée des VRAIS volumes (sauvegarde_auto.sh : backup + vérification)" \
  asroot "${BKENV[@]}" bash scripts/sauvegarde_auto.sh "$WORK/backups"
check "sauvegarde : fichiers chiffrés (aucune base SQLite en clair)" bash -c \
  "ls $WORK/backups/engine-*.db.enc > /dev/null && ! asroot grep -l 'SQLite format 3' $WORK/backups/* 2>/dev/null | grep -q . && echo chiffrés"
dc --profile lab down -v > /dev/null 2>&1
check "destruction : volumes engine_data, engine_state, api_data supprimés" bash -c \
  "! docker volume ls -q | grep -qE '^${PROJ}_(engine_data|engine_state|api_data)\$' && echo supprimés"
dc up --no-start engine api caddy fakebitvavo > /dev/null 2>&1
SRC=$(ls -1t "$WORK"/backups/engine-*.db.enc | head -1)
check "restauration (restore.sh) dans les volumes NEUFS" asroot "${BKENV[@]}" bash scripts/restore.sh "$SRC"
dc up -d --no-build engine api caddy fakebitvavo > /dev/null 2>&1
check "après restauration : moteur reparti, données fraîches" wait_for 180 fresh
sleep 70
C1=$(counts); echo "$C1" > "$OUT/counts_apres_restauration.json"
check "après restauration : fills et achats identiques (aucune transaction dupliquée ni perdue)" \
  same "$CB" "$C1" fills fills_empreinte achats achats_empreinte positions
check "après restauration : aucun doublon (client_order_id, stops), intégrité ok" no_dupes "$C1"
check "après restauration : état SIMULATION, pas d'INCIDENT d'intégrité" bash -c \
  "[ \"\$(st etat)\" = SIMULATION ] && ! docker logs $(cid engine) 2>&1 | grep -q falsification && echo SIMULATION"
check "après restauration : autorité interne de Caddy restaurée (l'ancienne racine valide encore le certificat)" wait_for 60 https_ok
check "après restauration : API reprise (connexion HTTPS possible)" wait_for 60 owner_login_ok "$WORK/cj3"

# ================================================================== 5. échec de sauvegarde, rétention, disque de l'hôte
log "échec de sauvegarde -> alerte ; rétention ; surveillance du disque de l'hôte"
N0=$(n_alerts)
mkdir -p "$WORK/bk_fail"
bk_fail() { ! asroot "${BKENV[@]}" "$@" bash scripts/sauvegarde_auto.sh "$WORK/bk_fail" > "$WORK/bk_fail.txt" 2>&1 && tail -1 "$WORK/bk_fail.txt"; }
check "échec de sauvegarde (phrase de passe absente) : code retour ≠ 0" bk_fail NELYQOR_BACKUP_PASS_FILE=/nonexistent
check "échec de sauvegarde : ALERTE reçue (ntfy)" alert_since "$N0" hote Sauvegarde
N0=$(n_alerts)
check "sauvegarde refusée si le disque dépasse le seuil critique" bk_fail NELYQOR_DISK_CRIT_PCT=1
check "sauvegarde refusée : ALERTE reçue" alert_since "$N0" hote Sauvegarde
for _ in 1 2; do asroot "${BKENV[@]}" NELYQOR_BACKUP_KEEP=2 bash scripts/sauvegarde_auto.sh "$WORK/backups" > /dev/null 2>&1; sleep 1; done
check "rétention : NELYQOR_BACKUP_KEEP=2 conserve 2 sauvegardes de chaque type" bash -c \
  "[ \$(ls $WORK/backups/engine-*.db.enc | wc -l) = 2 ] && [ \$(ls $WORK/backups/etat-moteur-*.tar.enc | wc -l) = 2 ] && echo 2"
dc --profile lab up -d lab > /dev/null 2>&1
N1=$(n_alerts)
NELYQOR_DISK_PATHS=/ NELYQOR_DISK_CRIT_PCT=1 NELYQOR_DISK_WARN_PCT=1 NELYQOR_DISK_STATE_FILE="$WORK/disque.etat" \
  NELYQOR_ALERT_NTFY_FILE="$WORK/ntfy_hote" bash scripts/surveillance_disque.sh > "$WORK/surv.txt" 2>&1; RC=$?
check "surveillance disque (seuil critique franchi) : code 2" [ "$RC" = 2 ]
check "surveillance disque : ALERTE reçue" alert_since "$N1" hote Disque
check "surveillance disque : laboratoire (écrivain non critique) arrêté, moteur intact" bash -c \
  "[ -z \"\$(docker ps -q --filter label=com.docker.compose.project=$PROJ --filter label=com.docker.compose.service=lab)\" ] && running engine && echo ok"
N2=$(n_alerts)
NELYQOR_DISK_PATHS=/ NELYQOR_DISK_CRIT_PCT=1 NELYQOR_DISK_WARN_PCT=1 NELYQOR_DISK_STATE_FILE="$WORK/disque.etat" \
  NELYQOR_ALERT_NTFY_FILE="$WORK/ntfy_hote" bash scripts/surveillance_disque.sh > /dev/null 2>&1
check "surveillance disque : pas de répétition d'alerte au même niveau" bash -c "[ \"\$(alerts | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')\" = $N2 ] && echo 'une seule alerte'"

# ================================================================== 6. Bitvavo indisponible, données incohérentes, Internet
alive_stale() {
  local s; s=$(state)
  python3 -c "import sys; sys.exit(0 if float('$(echo "$s" | jget heartbeat_age_s)') < 60 else 1)" && [ "$(echo "$s" | jget data_ok)" = false ] \
    && echo "battement $(echo "$s" | jget heartbeat_age_s) s, data_ok=false"
}
REF=$(counts)
for m in error ratelimit hang; do
  log "faux Bitvavo : mode $m (60 s)"
  RE=$(restarts engine); T=$(( $(date +%s) * 1000 ))
  fake_mode "$m" > /dev/null; sleep 60
  check "bitvavo $m : moteur en marche, jamais redémarré" bash -c "running engine && [ $(restarts engine) = $RE ] && echo ok"
  check "bitvavo $m : battement de cœur continu (< 60 s ; API : 120 s), données signalées périmées" alive_stale
  check "bitvavo $m : aucun achat" python3 -c "import sys; t='$(st dernier_achat_ts)'; sys.exit(0 if t in ('null','None','') or int(t) < $T else 1)"
  fake_mode normal > /dev/null
  check "bitvavo $m : reprise propre" wait_for 150 fresh
done
log "données INCOHÉRENTES (carnet croisé, prix négatifs/NaN, horodatages invalides, bougies désordonnées)"
RE=$(restarts engine); T=$(( $(date +%s) * 1000 ))
fake_mode incoherent > /dev/null; sleep 75
SI=$(state)
check "données incohérentes : moteur en marche, jamais redémarré" bash -c "running engine && [ $(restarts engine) = $RE ] && echo ok"
check "données incohérentes : aucun achat" python3 -c "import sys; t='$(echo "$SI" | jget dernier_achat_ts)'; sys.exit(0 if t in ('null','None','') or int(t) < $T else 1)"
check "données incohérentes : marchés écartés (carnet croisé / indisponible / périmé)" bash -c \
  "echo '$SI' | grep -qE 'carnet croisé|carnet indisponible|donnée périmée|horodatage' || [ \"\$(echo '$SI' | jget data_ok)\" = false ]; echo \"data_ok=\$(echo '$SI' | jget data_ok)\""
not_incident() { local e; e=$(echo "$1" | jget etat); [ -n "$e" ] && [ "$e" != INCIDENT ] && echo "$e"; }
check "données incohérentes : pas d'INCIDENT (pause « erreurs répétées » admise)" not_incident "$SI"
fake_mode normal > /dev/null
check "données incohérentes : reprise propre" wait_for 150 fresh
log "flux de prix FIGÉ (carnets identiques à chaque lecture, sans horodatage exploitable : clôture phase 7)"
RE=$(restarts engine); T=$(( $(date +%s) * 1000 ))
fake_mode fige > /dev/null; sleep 200
SF=$(state)
check "flux figé : moteur en marche, jamais redémarré" bash -c "running engine && [ $(restarts engine) = $RE ] && echo ok"
check "flux figé : données signalées périmées" bash -c "[ \"\$(echo '$SF' | jget data_ok)\" = false ] && echo data_ok=false"
check "flux figé : aucun achat" python3 -c "import sys; t='$(echo "$SF" | jget dernier_achat_ts)'; sys.exit(0 if t in ('null','None','') or int(t) < $T else 1)"
check "flux figé : battement de cœur continu" alive
fake_mode normal > /dev/null
check "flux figé : reprise propre dès que le flux change" wait_for 150 fresh
NET="${PROJ}_default"
log "coupure d'Internet du moteur (60 s)"
RE=$(restarts engine)
docker network disconnect "$NET" "$(cid engine)"; sleep 60
check "coupure Internet : moteur en marche, battement de cœur continu" bash -c "running engine && [ $(restarts engine) = $RE ] && alive && echo ok"
check "coupure Internet : données signalées périmées" bash -c "[ \"\$(st data_ok)\" = false ] && echo data_ok=false"
docker network connect --alias engine "$NET" "$(cid engine)"
check "coupure Internet : reprise propre" wait_for 150 fresh
C2=$(counts)
check "incidents réseau : aucune transaction dupliquée (fills et achats inchangés)" same "$REF" "$C2" fills fills_empreinte achats_empreinte positions
check "incidents réseau : aucun doublon, intégrité ok" no_dupes "$C2"

# ================================================================== 7. défaillances de processus, redémarrages complets
log "kill -9 du moteur, de l'API et de Caddy"
for s in engine api caddy; do
  R0=$(restarts "$s"); hardkill "$s"
  check "kill -9 $s : relancé par restart: unless-stopped" wait_for 90 bash -c "[ \"\$(restarts $s)\" -gt $R0 ] && running $s"
done
check "kill -9 : moteur reparti, données fraîches" wait_for 150 fresh
check "kill -9 : HTTPS de bout en bout revenu" wait_for 60 engine_alive_https
C3=$(counts)
check "kill -9 : aucune transaction dupliquée" same "$REF" "$C3" fills fills_empreinte achats_empreinte positions
check "kill -9 : aucun doublon, intégrité ok" no_dupes "$C3"
log "redémarrage complet de la pile (down puis up, volumes conservés)"
dc down > /dev/null 2>&1
dc up -d --no-build engine api caddy fakebitvavo > /dev/null 2>&1
check "redémarrage complet : moteur reparti, données fraîches" wait_for 180 fresh
check "redémarrage complet : HTTPS revenu (même racine interne)" wait_for 90 engine_alive_https
check "redémarrage complet : journal identique, aucun doublon" same_clean "$REF" "$(counts)"
DRC=${INCIDENT_DOCKERD_RESTART:-}
if [ -z "$DRC" ] && command -v systemctl > /dev/null && systemctl is-active --quiet docker 2>/dev/null; then
  DRC="sudo -n systemctl restart docker"
fi
if [ -n "$DRC" ]; then
  log "redémarrage du démon Docker (équivalent d'un redémarrage du serveur pour les conteneurs)"
  bash -c "$DRC" > "$OUT/dockerd_restart.txt" 2>&1
  wait_for 120 docker info > /dev/null 2>&1
  check "redémarrage de Docker : moteur relancé automatiquement, données fraîches" wait_for 240 fresh
  check "redémarrage de Docker : HTTPS revenu" wait_for 120 engine_alive_https
  check "redémarrage de Docker : journal identique, aucun doublon" same_clean "$REF" "$(counts)"
else
  record SKIP "redémarrage du démon Docker" "Docker non géré par systemd ici (INCIDENT_DOCKERD_RESTART pour le forcer)"
fi

# ================================================================== 8. saturation RÉELLE du disque du moteur
log "saturation du disque : engine_data sur un tmpfs de 64 Mo (seuils 24/12 Mo)"
dc --profile lab down -v > /dev/null 2>&1
DISK_WARN=24 DISK_CRIT=12 write_override tmpfs
dc up -d --no-build engine api caddy fakebitvavo > /dev/null 2>&1
check "disque : pile repartie avec engine_data en tmpfs 64 Mo" wait_for 180 fresh
wait_for 60 dc exec -T caddy test -s /data/caddy/pki/authorities/local/root.crt   # volumes neufs : nouvelle racine
dc exec -T caddy cat /data/caddy/pki/authorities/local/root.crt > "$WORK/ca.crt" 2>/dev/null
check "disque : niveau ok au départ" bash -c "[ \"\$(counts | jget disque.niveau)\" = ok ] && counts | jget disque"
sleep 65
NA=$(n_alerts)
free_mb() { dc exec -T engine python -c "import os; s=os.statvfs('/engine'); print(int(s.f_bavail*s.f_frsize/1e6))"; }
F=$(free_mb)
dc exec -T engine sh -c "dd if=/dev/zero of=/engine/remplissage bs=1M count=$((F - 6)) 2>/dev/null; sync"
log "espace libre ramené à $(free_mb) Mo (< seuil critique 12 Mo)"
check "disque critique : détecté par le moteur" wait_for 30 bash -c "[ \"\$(counts | jget disque.niveau)\" = critique ]"
check "disque critique : ALERTE envoyée par le moteur (ntfy)" wait_for 30 alert_since "$NA" moteur Disque
check "disque critique : l'alerte ntfy ne contient aucun détail (confidentialité par défaut)" bash -c \
  "alerts | python3 -c \"import json,sys; a=[x for x in json.load(sys.stdin)[$NA:] if x['topic']=='moteur']; sys.exit(0 if a and all('Mo' not in x['body'] for x in a) else 1)\" && echo minimal"
DA=$(counts); sleep 75; DB=$(counts)
check "disque critique : écritures non critiques ARRÊTÉES (instantanés, décisions)" same "$DA" "$DB" equity_snapshots decisions
hb_protect() { alive && [ "$(echo "$DB" | jget stops_actifs_max_par_marche)" -le 1 ] && echo "battement frais"; }
check "disque critique : battement de cœur maintenu" hb_protect
RE=$(restarts engine)
dc exec -T engine sh -c 'cat /dev/zero > /engine/remplissage2 2>/dev/null; sync; df -k /engine | tail -1'
log "disque du moteur rempli à 100 % (ENOSPC)"
sleep 45
check "disque plein (100 %) : moteur en marche, jamais redémarré" bash -c "running engine && [ $(restarts engine) = $RE ] && echo ok"
check "disque plein (100 %) : engine.db intacte (PRAGMA integrity_check)" bash -c "[ \"\$(counts | jget integrite)\" = ok ] && echo ok"
check "disque plein (100 %) : aucune transaction perdue ni dupliquée" same "$DB" "$(counts)" fills fills_empreinte achats_empreinte
dc exec -T engine rm -f /engine/remplissage /engine/remplissage2
check "disque libéré : écritures reprises (niveau ok)" wait_for 60 bash -c "[ \"\$(counts | jget disque.niveau)\" = ok ]"
check "disque libéré : nouveaux instantanés d'equity" wait_for 90 bash -c "[ \"\$(counts | jget equity_snapshots)\" -gt $(echo "$DB" | jget equity_snapshots) ]"
check "disque libéré : notification de reprise (journal du moteur)" wait_for 30 bash -c "counts | jget notif_disque_derniere | grep -q revenu"
check "disque : base intègre après l'épisode" bash -c "[ \"\$(counts | jget integrite)\" = ok ] && echo ok"
check "après la recette : moteur jamais en INCIDENT" bash -c "[ \"\$(st etat)\" != INCIDENT ] && echo \"\$(st etat)\""
alerts > "$OUT/alertes_recues.json" 2>/dev/null || true

# ================================================================== bilan
{
  echo "## Vérification des incidents ($PROJ) — $(date -u +%FT%TZ)"
  echo
  echo "PASS: $PASS — FAIL: $FAIL — SKIP: $SKIP"
  echo
  echo "| Résultat | Contrôle | Détail |"
  echo "|---|---|---|"
  awk -F'\t' '{gsub(/\|/,"/",$3); printf "| %s | %s | %s |\n", $1, $2, $3}' "$RESULTS"
} > "$OUT/resume.md"
echo
echo "PASS: $PASS — FAIL: $FAIL — SKIP: $SKIP (détail : $OUT/resume.md)"
[ "$FAIL" = 0 ]
