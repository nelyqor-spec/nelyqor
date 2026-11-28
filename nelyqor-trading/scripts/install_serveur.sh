#!/usr/bin/env bash
# Installation REPRODUCTIBLE du serveur NELYQOR TRADING (phase 7) — Debian 12 ou Ubuntu 24.04, amd64 ou arm64.
# Pour un DÉBUTANT : chaque étape dit ce qu'elle fait ; docs/INSTALLATION.md explique tout pas à pas.
#
# Ce que fait le script (dans l'ordre) :
#   1. vérifie le système (root, Debian 12 / Ubuntu 24.04, amd64 / arm64, systemd) ;
#   2. installe Docker Engine + Compose depuis le dépôt OFFICIEL Docker (empreinte de la clé GPG vérifiée) ;
#   3. configure Docker : rotation des journaux (json-file, max-size 10m, max-file 5), démarrage APRÈS la
#      synchronisation de l'horloge et le réseau ;
#   4. synchronisation horaire (chrony s'il est actif, sinon systemd-timesyncd), VÉRIFIÉE ;
#   5. utilisateur dédié `nelyqor` (uid 10001 = utilisateur des conteneurs, sans shell, sans mot de passe) ;
#   6. fichier .env (domaine ou certificat interne) et génération GUIDÉE des secrets (jamais affichés ni journalisés),
#      avec les droits EXACTS de docs/DEPLOIEMENT.md ;
#   7. construction de l'image ;
#   8. unités systemd : pile au démarrage (après time-sync.target et network-online.target), sauvegardes chiffrées
#      quotidiennes (rétention, alerte en cas d'échec), surveillance du disque toutes les 5 min ;
#   9. pare-feu UFW restrictif (Docker contourne UFW : seul Caddy publie des ports) ;
#  10. SSH par clé uniquement (seulement si une clé autorisée existe : jamais de blocage) ; fail2ban en option ;
#  11. démarrage de la pile et bilan (OK / ATTENTION) ;
#
# Idempotent : relancer le script ne refait que ce qui manque ; un secret existant n'est JAMAIS régénéré.
# Toute action destructive (suppression de paquets, remplacement d'un fichier non géré par ce script, activation du
# pare-feu, désactivation des mots de passe SSH, redémarrage de Docker avec la pile active) demande une
# confirmation INTERACTIVE, même avec --oui.
#
# Usage : sudo bash scripts/install_serveur.sh [options]
#   --dry-run              n'exécute RIEN : affiche ce qui serait fait (aucun droit root nécessaire)
#   --oui                  répond oui aux questions NON destructives
#   --domaine NOM          domaine public (HTTPS Let's Encrypt), ex. nelyqor.mondomaine.fr
#   --sans-domaine ADRESSE pas de domaine : certificat INTERNE de Caddy pour ADRESSE (IP Tailscale/WireGuard ; un NOM exige --ecoute IP)
#   --ecoute IP            n'écouter que sur cette IPv4 (défaut : toutes avec --domaine ; avec --sans-domaine : ADRESSE, jamais 0.0.0.0)
#   --ntfy | --sans-ntfy   créer (ou non) un sujet ntfy.sh aléatoire pour les alertes
#   --pas-de-pare-feu      ne pas toucher à UFW        --pas-de-ssh    ne pas toucher à la configuration SSH
#   --fail2ban             installer fail2ban (prison sshd)
#   --seulement fichiers   n'écrire QUE les fichiers de configuration (avec NELYQOR_PREFIX : tests)
# Variables (tests) : NELYQOR_OS_RELEASE (fichier os-release), NELYQOR_ARCH, NELYQOR_PREFIX (racine des fichiers
#   écrits, ex. dossier temporaire ; systemctl n'est alors jamais appelé).
set -euo pipefail
umask 077
APP=$(cd "$(dirname "$0")/.." && pwd)
DRY=0; OUI=0; DOMAINE=""; SANS_DOMAINE=""; ECOUTE=""; NTFY=""; UFW=1; SSH=1; F2B=0; SEULEMENT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --oui|--yes) OUI=1 ;;
    --domaine) DOMAINE=${2:?--domaine NOM}; shift ;;
    --sans-domaine) SANS_DOMAINE=${2:?--sans-domaine ADRESSE}; shift ;;
    --ecoute) ECOUTE=${2:?--ecoute IP}; shift ;;
    --ntfy) NTFY=1 ;;
    --sans-ntfy) NTFY=0 ;;
    --pas-de-pare-feu) UFW=0 ;;
    --pas-de-ssh) SSH=0 ;;
    --fail2ban) F2B=1 ;;
    --seulement) SEULEMENT=${2:?--seulement fichiers}; shift ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "option inconnue : $1 (voir --help)" >&2; exit 2 ;;
  esac
  shift
done
[ -n "$DOMAINE" ] && [ -n "$SANS_DOMAINE" ] && { echo "--domaine et --sans-domaine sont exclusifs" >&2; exit 2; }
# --ecoute : une adresse IPv4 (contre-expertise phase 7, D7 : la valeur est écrite dans .env et lue par compose)
is_ipv4() {
  [[ "$1" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
  local o; for o in "${BASH_REMATCH[@]:1}"; do [ "$o" -le 255 ] || return 1; done
}
if [ -n "$ECOUTE" ] && ! is_ipv4 "$ECOUTE"; then echo "--ecoute : adresse IPv4 attendue (ex. 100.64.1.2), reçu : $ECOUTE" >&2; exit 2; fi
PREFIX=${NELYQOR_PREFIX:-}
OS_RELEASE=${NELYQOR_OS_RELEASE:-/etc/os-release}
MARQUE="# Géré par NELYQOR scripts/install_serveur.sh — modifications locales écrasées à la prochaine installation"
DOCKER_GPG_FPR="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"   # empreinte officielle de la clé du dépôt Docker
BACKUP_DIR=/var/backups/nelyqor
WARN=0
TS=$(date +%Y%m%d%H%M%S)

# ------------------------------------------------------------------ outils d'affichage et d'exécution
titre() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
info() { printf '   %s\n' "$*"; }
ok() { printf '   \033[32mOK\033[0m  %s\n' "$*"; }
attention() { printf '   \033[33mATTENTION\033[0m %s\n' "$*"; WARN=$((WARN + 1)); }
echec() { printf '\n\033[31mÉCHEC\033[0m %s\n' "$*" >&2; exit 1; }
run() {  # run CMD... : exécute (ou affiche en --dry-run). JAMAIS appelé avec un secret en argument.
  if [ "$DRY" = 1 ]; then printf '   [simulation] %s\n' "$*"; return 0; fi
  "$@"
}
interactif() { [ -t 0 ] && [ -t 1 ]; }
demander() {  # demander "question" : oui non destructif (--oui accepté)
  [ "$OUI" = 1 ] && return 0
  if [ "$DRY" = 1 ]; then info "[simulation] question : $1 (réponse supposée : oui)"; return 0; fi
  interactif || { info "$1 -> non (pas de terminal ; relancer avec --oui)"; return 1; }
  local r; read -r -p "   $1 [o/N] " r; [[ "$r" =~ ^[oOyY] ]]
}
confirmer_destructif() {  # confirmer_destructif "action" : TOUJOURS interactif (même avec --oui)
  if [ "$DRY" = 1 ]; then info "[simulation] CONFIRMATION DEMANDÉE : $1"; return 0; fi
  interactif || { attention "action destructive non confirmée (pas de terminal) : $1"; return 1; }
  local r; read -r -p "   ACTION DESTRUCTIVE : $1. Taper OUI en majuscules pour continuer : " r; [ "$r" = OUI ]
}
fichier() {  # fichier CHEMIN MODE : écrit l'entrée standard dans CHEMIN (préfixé) si le contenu diffère
  local dest="$PREFIX$1" mode=$2 tmp
  tmp=$(mktemp)
  cat > "$tmp"
  if [ -f "$dest" ] && cmp -s "$tmp" "$dest"; then
    ok "$1 : déjà à jour"; rm -f "$tmp"; CHANGE=0; return 0
  fi
  if [ "$DRY" = 1 ]; then
    info "[simulation] écrire $1 (mode $mode) :"; sed 's/^/      | /' "$tmp"; rm -f "$tmp"; CHANGE=1; return 0
  fi
  if [ -f "$dest" ] && ! grep -qF "$MARQUE" "$dest" && ! [[ "$dest" == *.json ]]; then
    if ! confirmer_destructif "remplacer $1 (fichier existant non géré par ce script ; copie gardée en $1.avant-nelyqor.$TS)"; then
      attention "$1 laissé tel quel"; rm -f "$tmp"; CHANGE=0; return 0
    fi
  fi
  (umask 022; mkdir -p "$(dirname "$dest")")
  [ -f "$dest" ] && cp -p "$dest" "$dest.avant-nelyqor.$TS"
  install -m "$mode" "$tmp" "$dest"; rm -f "$tmp"
  ok "$1 : écrit"; CHANGE=1
}
CHANGE=0
systemd_actif() { [ -z "$PREFIX" ] && [ "$DRY" = 0 ] && command -v systemctl > /dev/null; }
sc() { if [ -n "$PREFIX" ]; then info "(préfixe de test : systemctl $* non appelé)"; else run systemctl "$@"; fi; }

# ------------------------------------------------------------------ 1. système
titre "1. Vérification du système"
[ -r "$OS_RELEASE" ] || echec "fichier $OS_RELEASE illisible"
# shellcheck disable=SC1090
. "$OS_RELEASE"
OS_ID=${ID:-}; OS_VER=${VERSION_ID:-}; CODENAME=${VERSION_CODENAME:-}
case "$OS_ID:$OS_VER" in
  debian:12|ubuntu:24.04) ok "système : ${PRETTY_NAME:-$OS_ID $OS_VER} ($CODENAME)" ;;
  *) echec "système non pris en charge : ${PRETTY_NAME:-$OS_ID $OS_VER} (attendu : Debian 12 ou Ubuntu 24.04)" ;;
esac
ARCH=${NELYQOR_ARCH:-$(dpkg --print-architecture 2>/dev/null || uname -m)}
case "$ARCH" in
  amd64|x86_64) ARCH=amd64 ;; arm64|aarch64) ARCH=arm64 ;;
  *) echec "architecture non prise en charge : $ARCH (attendu : amd64 ou arm64)" ;;
esac
ok "architecture : $ARCH"
if [ "$DRY" = 0 ] && [ -z "$PREFIX" ] && [ "$(id -u)" != 0 ]; then echec "lancer avec sudo (droits root nécessaires)"; fi
[ -f "$APP/compose.yaml" ] && [ -f "$APP/Caddyfile" ] || echec "lancer depuis le dépôt cloné (compose.yaml introuvable dans $APP)"
ok "dépôt : $APP"
[ "$DRY" = 1 ] && info "MODE SIMULATION (--dry-run) : rien ne sera modifié"

# ------------------------------------------------------------------ fichiers de configuration (idempotents)
ecrire_config() {
  titre "3. Configuration de Docker (rotation des journaux, ordre de démarrage)"
  daemon_json
  fichier /etc/systemd/system/docker.service.d/10-nelyqor-horloge.conf 0644 <<EOF
$MARQUE
# Docker (et donc les conteneurs relancés par « restart: unless-stopped ») démarre APRÈS le réseau et la
# synchronisation de l'horloge : le moteur ne doit pas horodater ses ordres avec une heure fausse.
[Unit]
Wants=network-online.target time-sync.target
After=network-online.target time-sync.target
EOF
  DOCKER_DROPIN_CHANGE=$CHANGE
  fichier /etc/systemd/system/systemd-time-wait-sync.service.d/10-nelyqor-delai.conf 0644 <<EOF
$MARQUE
# time-sync.target attend la première synchronisation NTP, mais jamais plus de 3 minutes (sans Internet au
# démarrage, la pile démarre quand même ; le moteur mesure lui-même le décalage avec Bitvavo).
[Service]
TimeoutStartSec=180
EOF
  titre "8. Unités systemd (démarrage, sauvegardes, disque)"
  local bind_wait=""
  if [[ "${BIND_IP:-}" =~ ^[0-9.]+$ ]] && [ "$BIND_IP" != 0.0.0.0 ] && [ "$BIND_IP" != 127.0.0.1 ]; then
    bind_wait="ExecStartPre=/bin/sh -c 'for i in \$(seq 120); do ip -o addr show | grep -q \" $BIND_IP/\" && exit 0; sleep 1; done; echo \"adresse $BIND_IP absente\"; exit 1'"
  fi
  fichier /etc/systemd/system/nelyqor.service 0644 <<EOF
$MARQUE
[Unit]
Description=NELYQOR TRADING (pile Docker Compose : moteur, API, Caddy)
Documentation=file://$APP/docs/INSTALLATION.md
Requires=docker.service
Wants=network-online.target time-sync.target
After=docker.service network-online.target time-sync.target
OnFailure=nelyqor-alerte@%n.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$APP
$bind_wait
ExecStart=/usr/bin/docker compose up -d --remove-orphans
ExecStop=/usr/bin/docker compose stop
TimeoutStartSec=600
TimeoutStopSec=180

[Install]
WantedBy=multi-user.target
EOF
  fichier /etc/systemd/system/nelyqor-alerte@.service 0644 <<EOF
$MARQUE
[Unit]
Description=NELYQOR : alerte d'échec de %i

[Service]
Type=oneshot
WorkingDirectory=$APP
EnvironmentFile=-$APP/.env
ExecStart=/bin/bash $APP/scripts/alerte.sh "Service %i en échec" "Le service %i a échoué sur le serveur : journalctl -u %i"
EOF
  fichier /etc/systemd/system/nelyqor-backup.service 0644 <<EOF
$MARQUE
[Unit]
Description=NELYQOR : sauvegarde chiffrée (engine.db, magasin d'état, api.db)
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=$APP
EnvironmentFile=-$APP/.env
Environment=NELYQOR_BACKUP_DIR=$BACKUP_DIR
ExecStart=/bin/bash $APP/scripts/sauvegarde_auto.sh $BACKUP_DIR
Nice=10
IOSchedulingClass=idle
EOF
  fichier /etc/systemd/system/nelyqor-backup.timer 0644 <<EOF
$MARQUE
[Unit]
Description=NELYQOR : sauvegarde quotidienne (03:17, rattrapée au démarrage si manquée)

[Timer]
OnCalendar=*-*-* 03:17:00
RandomizedDelaySec=10min
Persistent=true

[Install]
WantedBy=timers.target
EOF
  fichier /etc/systemd/system/nelyqor-disque.service 0644 <<EOF
$MARQUE
[Unit]
Description=NELYQOR : surveillance de l'espace disque (alerte, arrêt du laboratoire avant saturation)

[Service]
Type=oneshot
WorkingDirectory=$APP
EnvironmentFile=-$APP/.env
Environment=NELYQOR_BACKUP_DIR=$BACKUP_DIR
# code 1 (alerte) et 2 (critique) : signalés par scripts/alerte.sh, pas des échecs du service
SuccessExitStatus=1 2
ExecStart=/bin/bash $APP/scripts/surveillance_disque.sh
EOF
  fichier /etc/systemd/system/nelyqor-disque.timer 0644 <<EOF
$MARQUE
[Unit]
Description=NELYQOR : surveillance du disque toutes les 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF
  if [ "$SSH" = 1 ]; then
    titre "10. SSH par clé uniquement"
    ssh_config
  fi
}

daemon_json() {  # fusionne la rotation des journaux dans /etc/docker/daemon.json (sans écraser le reste)
  local f="$PREFIX/etc/docker/daemon.json" new
  new=$(python3 - "$f" <<'PY'
import json, sys
p = sys.argv[1]
try:
    d = json.load(open(p))
except FileNotFoundError:
    d = {}
except ValueError:
    print("ILLISIBLE"); sys.exit(0)
d["log-driver"] = "json-file"
d.setdefault("log-opts", {}).update({"max-size": "10m", "max-file": "5"})
print(json.dumps(d, indent=2, sort_keys=True))
PY
)
  [ "$new" = ILLISIBLE ] && { attention "/etc/docker/daemon.json n'est pas du JSON valide : rotation à configurer à la main"; DAEMON_CHANGE=0; return; }
  if [ -f "$f" ] && python3 -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d.get('log-driver', 'json-file') == 'json-file' else 1)" "$f"; then
    :
  elif [ -f "$f" ]; then
    confirmer_destructif "remplacer le pilote de journaux Docker existant par json-file (rotation)" || { attention "daemon.json inchangé"; DAEMON_CHANGE=0; return; }
  fi
  fichier /etc/docker/daemon.json 0644 <<< "$new"
  DAEMON_CHANGE=$CHANGE
}

ssh_config() {
  local user=${SUDO_USER:-root} home keys
  home=$(getent passwd "$user" 2>/dev/null | cut -d: -f6 || true)
  keys="${home:-/root}/.ssh/authorized_keys"
  if [ -z "$PREFIX" ] && ! { [ -s "$keys" ] || [ -s /root/.ssh/authorized_keys ]; }; then
    attention "aucune clé SSH autorisée ($keys) : mots de passe SSH LAISSÉS actifs pour ne pas vous bloquer dehors."
    info "Ajouter votre clé (docs/INSTALLATION.md, étape 2), puis relancer le script."
    return 0
  fi
  local f=/etc/ssh/sshd_config.d/10-nelyqor.conf
  if [ -z "$PREFIX" ] && [ "$DRY" = 0 ] && [ -f "$f" ] && grep -qF "$MARQUE" "$f"; then :; else
    if [ -z "$PREFIX" ]; then
      confirmer_destructif "désactiver la connexion SSH par MOT DE PASSE (clé uniquement ; garder votre session ouverte et tester une 2e connexion)" \
        || { attention "SSH inchangé (mots de passe toujours acceptés)"; return 0; }
    fi
  fi
  fichier "$f" 0644 <<EOF
$MARQUE
# Lu AVANT les autres fichiers (préfixe 10-) : la première valeur l'emporte (ex. 50-cloud-init.conf).
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
PubkeyAuthentication yes
MaxAuthTries 3
LoginGraceTime 30
X11Forwarding no
EOF
  if [ "$CHANGE" = 1 ] && [ -z "$PREFIX" ]; then
    if [ "$DRY" = 1 ] || sshd -t; then run systemctl reload ssh || run systemctl reload sshd || true
    else rm -f "$f"; attention "configuration SSH refusée par sshd -t : retirée"; fi
  fi
}

if [ "$SEULEMENT" = fichiers ]; then
  BIND_IP=$ECOUTE
  ecrire_config
  titre "Terminé (fichiers seulement)"; exit 0
fi
[ -n "$SEULEMENT" ] && { echo "--seulement : seule valeur possible « fichiers »" >&2; exit 2; }
[ -n "$PREFIX" ] && echec "NELYQOR_PREFIX n'est utilisable qu'avec --seulement fichiers"

# ------------------------------------------------------------------ 2. paquets et Docker officiel
titre "2. Paquets de base et Docker (dépôt officiel)"
export DEBIAN_FRONTEND=noninteractive
BASE_PKGS=(ca-certificates curl gnupg git openssl python3 ufw iproute2)
command -v chronyc > /dev/null || BASE_PKGS+=(systemd-timesyncd)
[ "$F2B" = 1 ] && BASE_PKGS+=(fail2ban)
MANQUANTS=()
for p in "${BASE_PKGS[@]}"; do dpkg -s "$p" > /dev/null 2>&1 || MANQUANTS+=("$p"); done
if [ ${#MANQUANTS[@]} -gt 0 ]; then
  info "à installer : ${MANQUANTS[*]}"
  run apt-get update -q
  run apt-get install -y -q "${MANQUANTS[@]}"
else ok "paquets de base présents"; fi
if dpkg -s docker-ce > /dev/null 2>&1 && docker compose version > /dev/null 2>&1; then
  ok "Docker officiel déjà installé : $(docker --version 2>/dev/null) / $(docker compose version --short 2>/dev/null)"
else
  CONFLITS=()
  for p in docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc; do
    dpkg -s "$p" > /dev/null 2>&1 && CONFLITS+=("$p")
  done
  if [ ${#CONFLITS[@]} -gt 0 ]; then
    if confirmer_destructif "désinstaller les paquets Docker de la distribution (${CONFLITS[*]}) ; images et volumes de /var/lib/docker sont CONSERVÉS"; then
      run apt-get remove -y "${CONFLITS[@]}"
    else echec "paquets Docker non officiels présents : installation arrêtée"; fi
  fi
  run install -m 0755 -d /etc/apt/keyrings
  run curl -fsSL "https://download.docker.com/linux/$OS_ID/gpg" -o /etc/apt/keyrings/docker.asc
  run chmod a+r /etc/apt/keyrings/docker.asc
  if [ "$DRY" = 0 ]; then
    FPR=$(gpg --show-keys --with-colons /etc/apt/keyrings/docker.asc 2>/dev/null | awk -F: '/^fpr/ {print $10; exit}')
    [ "$FPR" = "$DOCKER_GPG_FPR" ] || { rm -f /etc/apt/keyrings/docker.asc; echec "empreinte de la clé Docker inattendue ($FPR) : téléchargement refusé"; }
    ok "clé du dépôt Docker vérifiée ($DOCKER_GPG_FPR)"
  else info "[simulation] vérification de l'empreinte $DOCKER_GPG_FPR"; fi
  fichier /etc/apt/sources.list.d/docker.list 0644 \
    <<< "deb [arch=$ARCH signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$OS_ID $CODENAME stable"
  run apt-get update -q
  run apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  run systemctl enable --now docker
fi

# ------------------------------------------------------------------ 4. horloge
titre "4. Synchronisation horaire"
if systemctl is-active --quiet chrony 2>/dev/null || systemctl is-active --quiet chronyd 2>/dev/null; then
  ok "chrony actif"
  systemctl list-unit-files chrony-wait.service > /dev/null 2>&1 && run systemctl enable chrony-wait.service
else
  run timedatectl set-ntp true
  run systemctl enable --now systemd-timesyncd.service
  run systemctl enable systemd-time-wait-sync.service
fi

# ------------------------------------------------------------------ 5. utilisateur dédié
titre "5. Utilisateur dédié « nelyqor » (uid 10001, celui des conteneurs)"
if id nelyqor > /dev/null 2>&1; then
  [ "$(id -u nelyqor)" = 10001 ] || echec "l'utilisateur nelyqor existe avec l'uid $(id -u nelyqor) (attendu 10001)"
  ok "utilisateur nelyqor (10001) présent"
elif getent passwd 10001 > /dev/null; then
  echec "l'uid 10001 est déjà pris par $(getent passwd 10001 | cut -d: -f1) : le libérer (les conteneurs l'utilisent)"
else
  run useradd --system --uid 10001 --user-group --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin nelyqor
fi
info "Il possède les secrets lus par les conteneurs ; il n'est PAS dans le groupe docker (équivalent root) et ne se connecte pas."

# ------------------------------------------------------------------ 6. .env et secrets
titre "6. Configuration (.env) et secrets"
cd "$APP"
ENVF="$APP/.env"
if [ ! -f "$ENVF" ]; then run install -m 600 .env.example "$ENVF"; fi
env_get() { { [ -f "$ENVF" ] && sed -n "s/^$1=//p" "$ENVF" | tail -1; } || true; }
env_set() {  # env_set CLÉ VALEUR (valeurs NON secrètes uniquement)
  if [ "$(env_get "$1")" = "$2" ]; then ok ".env : $1 déjà à « $2 »"; return; fi
  if [ "$DRY" = 1 ]; then info "[simulation] .env : $1=$2"; return; fi
  if grep -q "^$1=" "$ENVF"; then sed -i "s|^$1=.*|$1=$2|" "$ENVF"; else echo "$1=$2" >> "$ENVF"; fi
  ok ".env : $1=$2"
}
if [ -z "$DOMAINE" ] && [ -z "$SANS_DOMAINE" ]; then
  if [ -n "$(env_get NELYQOR_DOMAIN)" ]; then DOMAINE_ACTUEL=$(env_get NELYQOR_DOMAIN)
    info "domaine déjà configuré : $DOMAINE_ACTUEL (mode TLS : $(env_get NELYQOR_TLS || true))"
  elif interactif && [ "$DRY" = 0 ]; then
    read -r -p "   Nom de domaine pointant vers ce serveur (vide = pas de domaine) : " DOMAINE
    if [ -z "$DOMAINE" ]; then read -r -p "   Adresse d'accès sans domaine (IP Tailscale/WireGuard, ex. 100.64.1.2) : " SANS_DOMAINE; fi
  else
    echec "indiquer --domaine NOM ou --sans-domaine ADRESSE (aucun terminal pour le demander)"
  fi
fi
if [ -n "$DOMAINE" ]; then
  [[ "$DOMAINE" =~ ^[A-Za-z0-9.-]+$ ]] || echec "domaine invalide : $DOMAINE"
  env_set NELYQOR_DOMAIN "$DOMAINE"; env_set NELYQOR_TLS acme
  BIND_IP=${ECOUTE:-0.0.0.0}
elif [ -n "$SANS_DOMAINE" ]; then
  [[ "$SANS_DOMAINE" =~ ^[A-Za-z0-9.:-]+$ ]] || echec "adresse invalide : $SANS_DOMAINE"
  env_set NELYQOR_DOMAIN "$SANS_DOMAINE"; env_set NELYQOR_TLS internal
  # Sans domaine = accès PRIVÉ (Tailscale/WireGuard) : jamais d'écoute sur toutes les interfaces, car Docker
  # contourne UFW pour les ports publiés (contre-expertise phase 7, D4).
  if [ -n "$ECOUTE" ]; then BIND_IP=$ECOUTE
  elif is_ipv4 "$SANS_DOMAINE"; then BIND_IP=$SANS_DOMAINE
  else echec "--sans-domaine $SANS_DOMAINE est un NOM : ajouter --ecoute <IP Tailscale du serveur> (commande : tailscale ip -4), sinon Caddy serait joignable depuis tout Internet"; fi
  [ "$BIND_IP" = 0.0.0.0 ] && echec "--sans-domaine avec --ecoute 0.0.0.0 exposerait Caddy à tout Internet : indiquer l'IP privée"
else
  BIND_IP=${ECOUTE:-$(env_get NELYQOR_BIND_IP)}; BIND_IP=${BIND_IP:-0.0.0.0}
fi
env_set NELYQOR_BIND_IP "$BIND_IP"
env_set NELYQOR_REAL_TRADING_ALLOWED false  # à CHAQUE passage, volontairement : l'installation ne laisse jamais le réel permis
info "drapeau du réel : NELYQOR_REAL_TRADING_ALLOWED=false (remis à faux à chaque installation ; docs/DEPLOIEMENT.md § 6)"
[ "$DRY" = 0 ] && chmod 600 "$ENVF"

SEC="$APP/secrets"
run install -d -m 700 -o root -g root "$SEC"
cree_vide() { [ -e "$SEC/$1" ] && { ok "secret $1 : présent (conservé)"; return; }; run install -m 600 /dev/null "$SEC/$1"; }
cree_aleatoire() {  # cree_aleatoire NOM COMMANDE... : contenu aléatoire écrit DIRECTEMENT dans le fichier
  local n=$1; shift
  if [ -s "$SEC/$n" ]; then ok "secret $n : présent (JAMAIS régénéré)"; return; fi
  if [ "$DRY" = 1 ]; then info "[simulation] générer $n (aléatoire, jamais affiché)"; return; fi
  ( umask 077; "$@" > "$SEC/$n" ); ok "secret $n : généré (non affiché)"
}
cree_vide bitvavo_api_key; cree_vide bitvavo_api_secret   # vides : SIMULATION tant que le propriétaire n'a rien décidé
cree_aleatoire engine_state_key openssl rand -hex 32
cree_aleatoire backup_passphrase openssl rand -base64 32
if [ -e "$SEC/ntfy_url" ]; then ok "secret ntfy_url : présent (conservé)"
elif [ "$NTFY" = 1 ] || { [ -z "$NTFY" ] && demander "Créer un sujet ntfy.sh ALÉATOIRE pour les alertes critiques (gratuit) ?"; }; then
  cree_aleatoire ntfy_url sh -c 'printf "https://ntfy.sh/nelyqor-%s\n" "$(openssl rand -hex 16)"'
  info "Pour l'iPhone : app « ntfy », s'abonner au sujet affiché par : sudo cat $SEC/ntfy_url (à faire vous-même)"
else cree_vide ntfy_url; info "alertes ntfy désactivées (fichier vide) ; activables plus tard (docs/INSTALLATION.md)"; fi

titre "7. Construction de l'image Docker"
if [ "$DRY" = 1 ]; then run docker compose build
elif BUILD_LOG=$(mktemp /tmp/nelyqor-build.XXXXXX.log) && docker compose build > "$BUILD_LOG" 2>&1; then ok "image nelyqor-trading:local construite"
else tail -20 "${BUILD_LOG:-/dev/null}"; echec "construction de l'image impossible (journal : ${BUILD_LOG:-absent})"; fi

if [ -s "$SEC/owner_password_hash" ] && [ -s "$SEC/owner_command_key" ] && [ -s "$SEC/owner_command_salt" ]; then
  ok "mot de passe et phrase de signature : déjà définis (changer : scripts/revoquer_acces.sh)"
elif [ "$DRY" = 1 ]; then
  info "[simulation] saisie guidée du mot de passe (12 car. min.) et de la phrase de signature (16 car. min.)"
elif interactif; then
  info "Saisie MASQUÉE (rien ne s'affiche quand vous tapez) ; conteneur SANS réseau ; rien n'est journalisé."
  docker run --rm -it --network none --user 0 -e NELYQOR_ROLE=lab -v "$SEC:/out" nelyqor-trading:local \
    python scripts/set_password.py /out/owner_password_hash || echec "saisie du mot de passe interrompue : relancer le script"
else
  attention "mot de passe non défini (pas de terminal) : relancer le script dans un terminal"
fi
if [ "$DRY" = 0 ]; then
  chmod 600 "$SEC"/*
  for n in bitvavo_api_key bitvavo_api_secret owner_password_hash engine_state_key owner_command_key owner_command_salt ntfy_url; do
    [ -e "$SEC/$n" ] && chown 10001:10001 "$SEC/$n"
  done
  [ -e "$SEC/owner_command_salt" ] && chmod 644 "$SEC/owner_command_salt"   # sel PUBLIC (Caddy sans capacités)
  chown root:root "$SEC/backup_passphrase"
  info "droits (nom, mode, propriétaire — jamais le contenu) :"
  stat -c '      %a %u %n' "$SEC"/* | sed "s|$SEC/||"
fi
run install -d -m 700 -o root -g root "$BACKUP_DIR"

# ------------------------------------------------------------------ 3 et 8 : fichiers systemd et Docker
DAEMON_CHANGE=0; DOCKER_DROPIN_CHANGE=0
ecrire_config
run systemctl daemon-reload
if [ "$DAEMON_CHANGE" = 1 ] || [ "$DOCKER_DROPIN_CHANGE" = 1 ]; then
  if [ "$DRY" = 1 ] || [ -z "$(docker ps -q --filter label=com.docker.compose.project=nelyqor 2>/dev/null)" ] \
     || confirmer_destructif "redémarrer Docker (la pile Nelyqor active sera arrêtée puis relancée)"; then
    run systemctl restart docker
  else attention "Docker non redémarré : rotation des journaux appliquée au prochain redémarrage"; fi
fi
run systemctl enable nelyqor.service nelyqor-backup.timer nelyqor-disque.timer
run systemctl start nelyqor-backup.timer nelyqor-disque.timer

# ------------------------------------------------------------------ 9. pare-feu
if [ "$UFW" = 1 ]; then
  titre "9. Pare-feu UFW"
  PORTS_SSH=$( { sshd -T 2>/dev/null || true; } | awk '/^port / {print $2}' | sort -u); PORTS_SSH=${PORTS_SSH:-22}
  run ufw default deny incoming
  run ufw default allow outgoing
  for p in $PORTS_SSH; do run ufw limit "$p/tcp" comment "SSH (limité : 6 connexions / 30 s)"; done
  if [ "$(env_get NELYQOR_TLS)" = acme ] || [ -n "$DOMAINE" ]; then
    run ufw allow 80/tcp comment "HTTP (Let's Encrypt, redirection)"; run ufw allow 443/tcp comment "HTTPS (PWA)"
  fi
  if ufw status 2>/dev/null | grep -q "Status: active"; then ok "UFW déjà actif"
  elif confirmer_destructif "activer le pare-feu UFW (SSH port(s) $PORTS_SSH autorisé(s) ; tout autre accès entrant refusé)"; then
    run ufw --force enable
  else attention "UFW non activé"; fi
  info "Docker contourne UFW pour les ports PUBLIÉS : seul Caddy publie (80/443${BIND_IP:+ sur $BIND_IP}) ; moteur et API n'ont aucun port."
fi
if [ "$F2B" = 1 ]; then
  titre "10 bis. fail2ban (prison SSH)"
  fichier /etc/fail2ban/jail.d/nelyqor.local 0644 <<EOF
$MARQUE
[sshd]
enabled = true
backend = systemd
maxretry = 5
findtime = 10m
bantime = 1h
EOF
  run systemctl enable --now fail2ban
  [ "$CHANGE" = 1 ] && run systemctl restart fail2ban
fi

# ------------------------------------------------------------------ 11. démarrage et bilan
titre "11. Démarrage de la pile et bilan"
if [ -s "$SEC/owner_password_hash" ] || [ "$DRY" = 1 ]; then run systemctl start nelyqor.service
else attention "pile NON démarrée : mot de passe à définir (relancer le script dans un terminal)"; fi
[ "$DRY" = 1 ] && { titre "Simulation terminée : rien n'a été modifié"; exit 0; }
bilan() { if eval "$2" > /dev/null 2>&1; then ok "$1"; else attention "$1 : NON vérifié"; fi; }
for i in $(seq 30); do [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = yes ] && break; sleep 3; done
bilan "horloge synchronisée (NTP)" '[ "$(timedatectl show -p NTPSynchronized --value)" = yes ]'
bilan "Docker officiel $(docker --version 2>/dev/null | cut -d, -f1)" 'dpkg -s docker-ce'
bilan "rotation des journaux Docker (json-file 10m x 5)" 'docker info -f "{{.LoggingDriver}}" | grep -qx json-file'
bilan "Docker démarre après time-sync.target" 'systemctl show docker -p After | grep -q time-sync.target'
bilan "pile activée au démarrage (nelyqor.service)" 'systemctl is-enabled nelyqor.service'
bilan "sauvegardes quotidiennes (nelyqor-backup.timer)" 'systemctl is-active nelyqor-backup.timer'
bilan "surveillance du disque (nelyqor-disque.timer)" 'systemctl is-active nelyqor-disque.timer'
bilan "secrets : 600/10001 (sel 644, backup_passphrase root)" '[ "$(stat -c %a:%u "$SEC/engine_state_key")" = 600:10001 ] && [ "$(stat -c %a "$SEC/owner_command_salt")" = 644 ] && [ "$(stat -c %a:%u "$SEC/backup_passphrase")" = 600:0 ]'
[ "$UFW" = 1 ] && bilan "pare-feu UFW actif" 'ufw status | grep -q "Status: active"'
[ "$SSH" = 1 ] && bilan "SSH : mots de passe refusés" 'sshd -T | grep -qx "passwordauthentication no"'
bilan "conteneurs moteur, API, Caddy en marche" '[ "$(docker compose ps --status running -q | wc -l)" -ge 3 ]'
bilan "seul Caddy publie des ports" '! docker ps --filter label=com.docker.compose.project=nelyqor --format "{{.Label \"com.docker.compose.service\"}} {{.Ports}}" | grep -v "^caddy " | grep -q -- "->"'
echo
if [ "$WARN" = 0 ]; then echo "Installation terminée sans avertissement. Suite : docs/INSTALLATION.md (étape 8, iPhone)."
else echo "Installation terminée avec $WARN avertissement(s) ci-dessus : voir docs/INSTALLATION.md (« Si ça échoue »)."; fi
