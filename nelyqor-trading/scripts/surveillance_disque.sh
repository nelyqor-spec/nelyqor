#!/usr/bin/env bash
# Surveillance du disque de l'HÔTE (timer systemd nelyqor-disque.timer, toutes les 5 min — phase 7).
# Seuils en % d'occupation (blocs ET inodes) : NELYQOR_DISK_WARN_PCT (80) et NELYQOR_DISK_CRIT_PCT (90).
#   alerte   : notification (scripts/alerte.sh) ;
#   critique : notification + ARRÊT PROPRE des écritures NON critiques de l'hôte : laboratoire et collecteur
#              (`docker compose --profile lab stop lab collector`) ; les sauvegardes planifiées refusent de démarrer
#              (scripts/sauvegarde_auto.sh). Le MOTEUR a sa propre garde (backend/core/disk.py : décisions et entrées
#              suspendues, protections maintenues) : il n'est JAMAIS arrêté par ce script.
# Une alerte n'est renvoyée que si le niveau change, ou toutes les 6 h tant qu'il reste mauvais.
# Chemins surveillés : NELYQOR_DISK_PATHS (défaut : racine Docker, dossier des sauvegardes, /).
# Code retour : 0 ok, 1 alerte, 2 critique.
set -uo pipefail
cd "$(dirname "$0")/.."
WARN=${NELYQOR_DISK_WARN_PCT:-80}
CRIT=${NELYQOR_DISK_CRIT_PCT:-90}
ETAT=${NELYQOR_DISK_STATE_FILE:-/run/nelyqor-disque.etat}
REPEAT_S=${NELYQOR_DISK_REPEAT_S:-21600}
if [ -z "${NELYQOR_DISK_PATHS:-}" ]; then
  NELYQOR_DISK_PATHS="$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker) ${NELYQOR_BACKUP_DIR:-/var/backups/nelyqor} /"
fi
niveau=0; detail=""
for p in $NELYQOR_DISK_PATHS; do
  [ -e "$p" ] || continue
  b=$(df -P "$p" 2>/dev/null | awk 'NR==2 {gsub("%", "", $5); print $5}')
  i=$(df -Pi "$p" 2>/dev/null | awk 'NR==2 {gsub("%", "", $5); print ($5 == "-" ? 0 : $5)}')
  for v in "${b:-0}" "${i:-0}"; do
    if [ "$v" -ge "$CRIT" ]; then niveau=2; elif [ "$v" -ge "$WARN" ] && [ "$niveau" -lt 1 ]; then niveau=1; fi
  done
  detail="$detail $p=${b:-?}% (inodes ${i:-?}%)"
done
nom=(ok alerte critique)
echo "disque : ${nom[$niveau]} —$detail"
prev=ok; prev_ts=0
[ -r "$ETAT" ] && read -r prev prev_ts < "$ETAT" 2>/dev/null
now=$(date +%s)
if [ "$niveau" = 2 ]; then
  # écritures non critiques de l'hôte : laboratoire et collecteur (le moteur n'est jamais arrêté ici)
  docker compose --profile lab stop lab collector > /dev/null 2>&1 \
    && echo "laboratoire et collecteur arrêtés (écritures non critiques)"
fi
if [ "${nom[$niveau]}" != "$prev" ] || { [ "$niveau" -gt 0 ] && [ $((now - ${prev_ts:-0})) -ge "$REPEAT_S" ]; }; then
  case "$niveau" in
    2) bash scripts/alerte.sh "Disque CRITIQUE" "Occupation >= $CRIT % :$detail. Laboratoire arrêté, sauvegardes suspendues. Libérer de l'espace (docs/RUNBOOKS.md, Disque plein)." ;;
    1) bash scripts/alerte.sh "Disque presque plein" "Occupation >= $WARN % :$detail." ;;
    0) [ "$prev" != ok ] && bash scripts/alerte.sh "Disque revenu a la normale" "Occupation <$WARN % :$detail." ;;
  esac
  echo "${nom[$niveau]} $now" > "$ETAT" 2>/dev/null || true
fi
exit "$niveau"
