#!/usr/bin/env bash
# Vérification de déploiement EN CONDITIONS RÉELLES (Docker) — SIMULATION uniquement, secrets FACTICES.
#
# Construit les images, démarre la pile de compose.yaml (moteur, API, Caddy) sous un nom de projet DISTINCT
# (volumes séparés de la production), avec un faux Bitvavo public (scripts/deploy_fake_bitvavo.py, données
# synthétiques) à la place d'internet, puis vérifie et affiche PASS/FAIL pour chaque contrôle :
# montages, isolation des bases, santé, redémarrages (restart, kill -9), interruptions réseau, arrêt d'urgence
# SSH (scripts/emergency_stop.sh) et recette PWA (scripts/e2e_pwa.cjs, si Node + Playwright sont disponibles).
#
# Aucune clé Bitvavo (fichiers vides), NELYQOR_REAL_TRADING_ALLOWED=false, aucun ordre réel possible.
# Usage : scripts/deploy_check.sh [--no-build] [--keep]
#   --no-build : utilise l'image de test déjà construite (DEPLOY_CHECK_IMAGE)
#   --keep     : laisse la pile en marche à la fin (sinon : docker compose down -v)
# Variables : DEPLOY_CHECK_PROJECT (nelyqor-check), DEPLOY_CHECK_PORT (18080), DEPLOY_CHECK_OUT
#             (reports/deploy_check), DEPLOY_CHECK_E2E (auto|0|1),
#             DEPLOY_CHECK_IMAGE (nelyqor-trading:deploy-check : JAMAIS le tag de production nelyqor-trading:local).
# Garde-fous : refuse le projet « nelyqor » (production) et refuse de tourner si la pile de production est active.
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
PROJ=${DEPLOY_CHECK_PROJECT:-nelyqor-check}
PORT=${DEPLOY_CHECK_PORT:-18080}
OUT=${DEPLOY_CHECK_OUT:-reports/deploy_check}
E2E=${DEPLOY_CHECK_E2E:-auto}
IMAGE=${DEPLOY_CHECK_IMAGE:-nelyqor-trading:deploy-check}
# ---- garde-fous (contre-expertise phase 6, D9) : ne jamais toucher la production
case "$PROJ" in
  nelyqor|"") echo "REFUS : projet « $PROJ » = production (volumes réels) ; choisir un autre DEPLOY_CHECK_PROJECT" >&2; exit 2 ;;
esac
[ "$IMAGE" = nelyqor-trading:local ] && { echo "REFUS : $IMAGE est le tag de l'image de production" >&2; exit 2; }
if [ -n "$(docker ps -q --filter label=com.docker.compose.project=nelyqor 2>/dev/null)" ]; then
  echo "REFUS : la pile de production « nelyqor » tourne sur cet hôte (même réseau 172.30.57.0/24, ressources) ;" \
       "lancer la vérification sur une autre machine ou en CI" >&2
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
export NELYQOR_DOMAIN=":80"              # Caddy en HTTP local (pas de certificat Let's Encrypt en test)
export NELYQOR_REAL_TRADING_ALLOWED=false

log() { echo "[$(date -u +%T)] $*"; }
record() {  # record STATUT NOM DÉTAIL
  printf '%s\t%s\t%s\n' "$1" "$2" "${3:-}" >> "$RESULTS"
  case "$1" in PASS) PASS=$((PASS + 1)) ;; FAIL) FAIL=$((FAIL + 1)) ;; *) SKIP=$((SKIP + 1)) ;; esac
  printf '%-5s %s %s\n' "$1" "$2" "${3:+— $3}"
}
check() {  # check NOM commande... : PASS si la commande réussit
  local name=$1; shift
  local o
  if o=$("$@" 2>&1); then record PASS "$name" "$(echo "$o" | tail -1 | cut -c1-160)"
  else record FAIL "$name" "$(echo "$o" | tail -1 | cut -c1-160)"; fi
}
dc() { docker compose "$@"; }
cid() { dc ps -q "$1" | head -1; }
probe() { dc exec -T "$1" python - "$2" < "$ROOT_DIR/scripts/deploy_probe.py"; }  # sonde NON livrée dans l'image
jget() {  # jget CLÉ : valeur d'une clé du dernier objet JSON lu sur l'entrée (vide si absent ou illisible)
  python3 -c '
import json, sys
try:
    v = json.loads(sys.stdin.read().strip().splitlines()[-1]).get(sys.argv[1])
except Exception:
    sys.exit(0)
print(v if isinstance(v, str) else json.dumps(v))' "$1"
}
state() { probe engine state 2>/dev/null | tail -1; }
st() { state | jget "$1"; }
fake_mode() { dc exec -T fakebitvavo python -c "import urllib.request as u; print(u.urlopen(u.Request('http://127.0.0.1:8080/control/mode/$1', method='POST')).read().decode())"; }
wait_for() {  # wait_for SECONDES commande... : réessaie toutes les 3 s
  local t=$1; shift
  local end=$(( $(date +%s) + t ))
  while [ "$(date +%s)" -lt "$end" ]; do "$@" >/dev/null 2>&1 && return 0; sleep 3; done
  "$@"
}
fresh() { [ "$(st data_ok)" = "true" ] && python3 -c "import sys; sys.exit(0 if float('$(st heartbeat_age_s)') < 30 else 1)"; }
health_json() { curl -fsS --max-time 5 "http://localhost:$PORT/api/health"; }
engine_alive_http() { health_json | grep -q '"engine_alive":true'; }
api_down_http() { ! curl -fsS --max-time 5 "http://localhost:$PORT/api/health" >/dev/null 2>&1; }
container_health() { [ "$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$(cid "$1")")" = "healthy" ]; }
restarts() { docker inspect -f '{{.RestartCount}}' "$(cid "$1")"; }
running() { [ "$(docker inspect -f '{{.State.Running}}' "$(cid "$1")")" = "true" ]; }
hardkill() {  # SIGKILL du processus principal du conteneur depuis l'hôte (panne brutale ; sudo sur le runner CI)
  local pid; pid=$(docker inspect -f '{{.State.Pid}}' "$(cid "$1")")
  kill -9 "$pid" 2>/dev/null || sudo -n kill -9 "$pid"
}
expect_json() {  # expect_json JSON CLÉ VALEUR_ATTENDUE
  local v; v=$(echo "$1" | jget "$2")
  [ "$v" = "$3" ] || { echo "$2=$v (attendu $3)"; return 1; }
  echo "$2=$v"
}

export -f dc cid probe jget state st fresh health_json engine_alive_http api_down_http container_health restarts running
export PORT ROOT_DIR

cleanup() {
  dc logs --no-color --timestamps > "$OUT/compose_logs.txt" 2>&1 || true
  for s in engine api caddy fakebitvavo; do dc logs --no-color "$s" > "$OUT/log_$s.txt" 2>&1 || true; done
  if [ "$KEEP" = 0 ]; then dc down -v --remove-orphans > /dev/null 2>&1 || true; fi
  docker run --rm --user 0 -v "$WORK:/w" "$IMAGE" sh -c 'rm -rf /w/secrets' > /dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

# ------------------------------------------------------------------ 0. image et secrets FACTICES
if [ "$BUILD" = 1 ]; then
  log "construction de l'image (Dockerfile du dépôt)"
  if docker build -t "$IMAGE" . > "$OUT/build.txt" 2>&1; then record PASS "construction de l'image" "Dockerfile → $IMAGE"
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
openssl rand -hex 32 > "$WORK/secrets/engine_state_key"
: > "$WORK/secrets/bitvavo_api_key"; : > "$WORK/secrets/bitvavo_api_secret"   # AUCUNE clé : simulation seule
rm -f "$WORK/gen.txt"
# Permissions EXACTES de docs/DEPLOIEMENT.md : fichiers en 600, propriétaire = uid 10001 des conteneurs
# (chown fait par un conteneur root jetable : fonctionne aussi sans sudo sur le runner CI)
chmod 600 "$WORK/secrets"/*
docker run --rm --user 0 -v "$WORK/secrets:/s" "$IMAGE" chown 10001:10001 \
  /s/bitvavo_api_key /s/bitvavo_api_secret /s/owner_password_hash /s/engine_state_key /s/owner_command_key \
  /s/owner_command_salt || { echo "chown des secrets factices impossible" >&2; exit 1; }
chmod 644 "$WORK/secrets/owner_command_salt"   # sel PUBLIC : lisible par Caddy sans capacités (D3)

cat > "$WORK/override.yaml" <<EOF
# Surcharge de TEST (générée par deploy_check.sh) : faux Bitvavo, secrets factices, Caddy en HTTP local.
services:
  engine:
    image: $IMAGE
    environment:
      BITVAVO_REST_URL: http://fakebitvavo:8080/v2
    depends_on: [fakebitvavo]
  api:
    image: $IMAGE
  caddy:
    ports: !override ["127.0.0.1:${PORT}:80"]
    volumes:
      - $WORK/secrets/owner_command_salt:/srv/meta/owner_command_salt:ro
  fakebitvavo:
    image: $IMAGE
    command: ["python", "/fake/deploy_fake_bitvavo.py", "8080"]   # monté : non livré dans l'image
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
EOF
check "configuration compose valide" dc config -q
check "image : outils de test NON livrés (faux Bitvavo, sondes)" \
  docker run --rm "$IMAGE" sh -c 'test ! -e scripts/deploy_fake_bitvavo.py && test ! -e scripts/deploy_probe.py && echo absents'

# ------------------------------------------------------------------ 1. démarrage
dc down -v --remove-orphans > /dev/null 2>&1 || true
log "démarrage de la pile ($PROJ) en SIMULATION"
if ! dc up -d --no-build > "$OUT/up.txt" 2>&1; then
  record FAIL "démarrage de la pile" "$(tail -1 "$OUT/up.txt")"; cat "$OUT/up.txt"; exit 1
fi
record PASS "démarrage de la pile" "engine, api, caddy, fakebitvavo"
wait_for 180 fresh
check "moteur : heartbeat frais et données de marché fraîches" fresh
check "API : /api/health via Caddy, moteur vivant" wait_for 60 engine_alive_http
check "moteur : état SIMULATION au démarrage" expect_json "$(state)" etat SIMULATION
check "moteur : aucun journal « NON distincts » (bases séparées au démarrage)" \
  bash -c "! docker logs $(cid engine) 2>&1 | grep -q 'NON distincts'"
check "moteur : aucun exécuteur réel (aucune clé, drapeau serveur faux)" expect_json "$(state)" real_exec false
SALT_HTTP=$(curl -s -o "$WORK/salt.txt" -w '%{http_code}' "http://localhost:$PORT/meta/command_salt")
check "caddy : /meta/command_salt servi (200, sel exact) sans capacités" \
  bash -c "[ '$SALT_HTTP' = 200 ] && cmp -s '$WORK/salt.txt' '$WORK/secrets/owner_command_salt' && echo 200"
check "caddy : adresse fixe 172.30.57.10 (seule source X-Forwarded-For acceptée par l'API)" bash -c \
  "docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' $(cid caddy) | grep -qx 172.30.57.10"

# ------------------------------------------------------------------ 2. montages et durcissement
E=$(probe engine engine | tail -1); A=$(probe api api | tail -1)
echo "$E" > "$OUT/probe_engine.json"; echo "$A" > "$OUT/probe_api.json"
for k in commands_monte_ro commands_ecriture_erofs commands_db_ecriture_erofs channel_writable_faux \
         bases_separees_fonction_reelle display_inscriptible_par_moteur engine_db_presente \
         secret_etat_lisible secret_cle_commande_lisible secret_sel_lisible; do
  check "moteur : $k" expect_json "$E" "$k" true
done
for k in display_monte_ro display_ecriture_erofs display_db_ecriture_erofs pas_de_montage_engine \
         pas_de_montage_state engine_db_absente engine_db_inouvrable etat_moteur_absent aucun_secret_moteur \
         secret_mdp_lisible commands_inscriptible_par_api; do
  check "api : $k" expect_json "$A" "$k" true
done
for s in engine api fakebitvavo; do
  C=$(probe "$s" common | tail -1); echo "$C" > "$OUT/probe_common_$s.json"
  for k in uid_non_root pas_de_socket_docker racine_lecture_seule racine_ecriture_refusee_erofs no_new_privs \
           capacites_effectives_nulles; do
    check "$s : $k" expect_json "$C" "$k" true
  done
done
for s in engine api caddy; do
  I=$(docker inspect "$(cid "$s")")
  check "$s : aucun montage du socket Docker (docker inspect)" bash -c "! echo '$I' | grep -q 'docker.sock'"
  check "$s : non privilégié" bash -c "[ \"\$(docker inspect -f '{{.HostConfig.Privileged}}' $(cid "$s"))\" = false ]"
done
for s in engine api; do
  check "$s : read_only + cap_drop ALL + no-new-privileges (docker inspect)" sh -c \
    "docker inspect -f '{{.HostConfig.ReadonlyRootfs}} {{.HostConfig.CapDrop}} {{.HostConfig.SecurityOpt}}' $(cid "$s") | grep -q 'true \[ALL\] \[no-new-privileges:true\]'"
  check "$s : aucun port publié sur l'hôte" bash -c "[ -z \"\$(docker port $(cid "$s"))\" ]"
done
check "caddy : cap_drop ALL + no-new-privileges (docker inspect)" sh -c \
  "docker inspect -f '{{.HostConfig.CapDrop}} {{.HostConfig.SecurityOpt}}' $(cid caddy) | grep -q '\[ALL\] \[no-new-privileges:true\]'"
check "api : ne voit pas la clé Bitvavo (Settings.read_secret refuse le rôle api)" \
  dc exec -T api python -c "
from backend.core.config import Settings
try:
    Settings().read_secret('bitvavo_api_key'); raise SystemExit(1)
except PermissionError:
    print('refusé')"

# ------------------------------------------------------------------ 3. santé Docker
check "moteur : healthcheck Docker « healthy »" wait_for 200 container_health engine
check "api : healthcheck Docker « healthy »" wait_for 120 container_health api

# ------------------------------------------------------------------ 4. redémarrages
S0=$(state); PID0=$(echo "$S0" | jget pid); I0=$(echo "$S0" | jget intents_total)
log "redémarrage du moteur (docker compose restart)"
dc restart engine > /dev/null 2>&1
sleep 5
wait_for 120 bash -c "[ \"\$(probe engine state | tail -1 | jget pid)\" != \"$PID0\" ] || [ \"\$(probe engine state | tail -1 | jget heartbeat_ts)\" -gt $(echo "$S0" | jget heartbeat_ts) ]"
wait_for 120 fresh
S1=$(state)
check "restart moteur : heartbeat repris, données fraîches" fresh
check "restart moteur : état SIMULATION conservé (pas d'INCIDENT)" expect_json "$S1" etat SIMULATION
check "restart moteur : journal des intentions conservé" python3 -c "import sys; sys.exit(0 if int('$(echo "$S1" | jget intents_total)') >= int('$I0') else 1)"
check "restart moteur : API voit le moteur vivant" wait_for 60 engine_alive_http

log "redémarrage de l'API (docker compose restart puis kill -9)"
dc restart api > /dev/null 2>&1
check "restart api : /api/health revient" wait_for 60 engine_alive_http
R0=$(restarts api)
hardkill api
check "kill -9 api : relancée par la politique de redémarrage" wait_for 60 bash -c "[ \"\$(docker inspect -f '{{.RestartCount}}' $(cid api))\" -gt $R0 ]"
check "kill -9 api : /api/health revient" wait_for 60 engine_alive_http
LOGIN_HTTP=$(curl -s -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' \
  -d "{\"password\":\"$PW\"}" "http://localhost:$PORT/api/login")
check "api : connexion avec le mot de passe factice (secret lisible par l'utilisateur 10001)" [ "$LOGIN_HTTP" = 200 ]

# ------------------------------------------------------------------ 5. interruptions réseau
NET="${PROJ}_default"
RE0=$(restarts engine); T_CUT=$(( $(date +%s) * 1000 ))
log "coupure réseau du moteur (docker network disconnect)"
docker network disconnect "$NET" "$(cid engine)"
sleep 45
SC=$(state)
check "coupure moteur : processus toujours en marche, aucun redémarrage" bash -c "running engine && [ $(restarts engine) = $RE0 ]"
check "coupure moteur : heartbeat continue, données signalées périmées" expect_json "$SC" data_ok false
check "coupure moteur : aucun achat pendant la coupure" python3 -c "import sys; t='$(echo "$SC" | jget dernier_achat_ts)'; sys.exit(0 if t in ('null','None','') or int(t) < $T_CUT else 1)"
check "coupure moteur : arrêt d'urgence toujours applicable hors réseau (probe d'état lisible)" [ -n "$SC" ]
docker network connect --alias engine "$NET" "$(cid engine)"   # l'alias DNS du service est perdu à la déconnexion
check "coupure moteur : reprise propre (données fraîches)" wait_for 120 fresh
check "coupure moteur : toujours SIMULATION après reprise" expect_json "$(state)" etat SIMULATION

log "faux Bitvavo en panne (HTTP 503) puis données PÉRIMÉES (carnet horodaté 1 h dans le passé)"
fake_mode error > /dev/null; T_ERR=$(( $(date +%s) * 1000 ))
sleep 40
SE=$(state)
check "panne 503 : moteur en marche, données signalées périmées" bash -c "running engine && [ \"$(echo "$SE" | jget data_ok)\" = false ]"
check "panne 503 : aucun achat" python3 -c "import sys; t='$(echo "$SE" | jget dernier_achat_ts)'; sys.exit(0 if t in ('null','None','') or int(t) < $T_ERR else 1)"
fake_mode stale > /dev/null; T_ST=$(( $(date +%s) * 1000 ))
sleep 75
SS=$(state)
check "données périmées : aucun achat" python3 -c "import sys; t='$(echo "$SS" | jget dernier_achat_ts)'; sys.exit(0 if t in ('null','None','') or int(t) < $T_ST else 1)"
check "données périmées : marchés inéligibles « donnée périmée » (scanner)" bash -c "echo '$SS' | grep -q 'donnée périmée'"
fake_mode normal > /dev/null
check "panne échange : reprise propre" wait_for 120 fresh
check "panne échange : moteur jamais redémarré" [ "$(restarts engine)" = "$RE0" ]

log "coupure réseau de l'API"
docker network disconnect "$NET" "$(cid api)"
check "coupure api : Caddy ne joint plus l'API (erreur, pas de contenu)" wait_for 30 api_down_http
check "coupure api : API toujours en marche" running api
check "coupure api : moteur non affecté (heartbeat frais)" fresh
docker network connect --alias api "$NET" "$(cid api)"   # sans l'alias, Caddy ne résout plus « api »
check "coupure api : reprise via Caddy" wait_for 60 engine_alive_http

# ------------------------------------------------------------------ 6. recette PWA (Playwright) contre la pile réelle
# AVANT l'arrêt d'urgence SSH : une signature postérieure de moins de 30 s à une révocation est refusée
# (tolérance d'horloge, docs/DEPLOIEMENT.md § 8), ce qui ferait échouer le parcours « activation signée »
run_e2e=0
if [ "$E2E" = 1 ]; then run_e2e=1
elif [ "$E2E" = auto ] && command -v node > /dev/null && node -e "require('playwright')" > /dev/null 2>&1; then run_e2e=1; fi
if [ "$run_e2e" = 1 ]; then
  log "recette PWA (Playwright) sur http://localhost:$PORT"
  if node scripts/e2e_pwa.cjs "http://localhost:$PORT" "$PW" "$OUT/screenshots" "$PHRASE" > "$OUT/e2e_pwa.txt" 2>&1; then
    record PASS "recette PWA e2e_pwa.cjs" "$(grep RÉSULTAT "$OUT/e2e_pwa.txt")"
  else
    record FAIL "recette PWA e2e_pwa.cjs" "$(grep -E 'RÉSULTAT|Error' "$OUT/e2e_pwa.txt" | head -1)"
  fi
  sed 's/^/    /' "$OUT/e2e_pwa.txt" | grep -E 'OK|ÉCHEC' || true
else
  record SKIP "recette PWA e2e_pwa.cjs" "Node/Playwright indisponible"
fi
# ------------------------------------------------------------------ 7. arrêt d'urgence SSH
log "arrêt d'urgence SSH (scripts/emergency_stop.sh)"
AU0=$(st arret_urgence_ok); AU0=${AU0:-0}
check "emergency_stop.sh : exécution" bash scripts/emergency_stop.sh
check "arrêt d'urgence : drapeau appliqué par le moteur" wait_for 60 bash -c "probe engine state | tail -1 | grep -q 'ARRET_URGENCE.applique-'"
check "arrêt d'urgence : état PAUSE_NOUVELLES_ENTREES" wait_for 30 bash -c "[ \"\$(probe engine state | tail -1 | jget etat)\" = PAUSE_NOUVELLES_ENTREES ]"
check "arrêt d'urgence : commande du canal secondaire traitée" wait_for 60 bash -c "[ \"\$(probe engine state | tail -1 | jget arret_urgence_ok)\" -gt $AU0 ]"
check "arrêt d'urgence : drapeau consommé (pas de ré-application en boucle)" expect_json "$(state)" drapeau_urgence_en_attente false

log "kill -9 du moteur (panne brutale) : l'arrêt d'urgence doit survivre"
RE1=$(restarts engine)
hardkill engine
check "kill -9 moteur : relancé par la politique de redémarrage" wait_for 90 bash -c "[ \"\$(docker inspect -f '{{.RestartCount}}' $(cid engine))\" -gt $RE1 ]"
check "kill -9 moteur : heartbeat repris" wait_for 120 fresh
SK=$(state)
check "kill -9 moteur : état PAUSE conservé (signé, magasin /state)" expect_json "$SK" etat PAUSE_NOUVELLES_ENTREES
check "kill -9 moteur : pas d'INCIDENT d'intégrité" bash -c "! docker logs $(cid engine) 2>&1 | grep -q 'falsification'"
check "kill -9 moteur : verrou d'instance repris (pas d'erreur de verrou)" bash -c "! docker logs --since 2m $(cid engine) 2>&1 | grep -qi 'verrou.*déjà'"

check "après la recette : moteur jamais en INCIDENT" bash -c "[ \"\$(probe engine state | tail -1 | jget etat)\" != INCIDENT ]"
state > "$OUT/etat_final.json"

# ------------------------------------------------------------------ bilan
{
  echo "## Vérification de déploiement ($PROJ) — $(date -u +%FT%TZ)"
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
