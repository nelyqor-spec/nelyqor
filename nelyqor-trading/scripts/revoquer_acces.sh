#!/usr/bin/env bash
# RÉVOCATION des accès (phase 7) — en root sur le serveur, depuis le dépôt (ex. /opt/nelyqor/nelyqor-trading).
#   --sessions      : ferme TOUTES les sessions ouvertes (les sessions sont en mémoire de l'API : redémarrage de l'API)
#   --mot-de-passe  : nouveau mot de passe (saisie masquée) + fermeture de toutes les sessions
#   --phrase        : nouvelle phrase de signature (nouveau sel + nouvelle clé du moteur) : toute signature préparée
#                     avec l'ancienne phrase devient invalide ; redémarre le moteur, l'API et Caddy (sel servi)
#   --tout          : les trois
#   --bitvavo       : vide les clés Bitvavo (À FAIRE AUSSI chez Bitvavo : supprimer la clé API) ; moteur en simulation
# Les commandes signées antérieures à un arrêt d'urgence ou à un retour en simulation sont déjà refusées par le
# moteur (révocation signée auth_revoked_ms) ; ce script ajoute la révocation des SECRETS eux-mêmes.
# Rien n'est affiché ni journalisé : la saisie se fait dans un conteneur SANS réseau.
set -euo pipefail
cd "$(dirname "$0")/.."
[ "$(id -u)" = 0 ] || { echo "lancer avec sudo" >&2; exit 1; }
[ $# -gt 0 ] || { sed -n '2,11p' "$0"; exit 2; }
SESS=0; MDP=0; PHR=0; BV=0
for a in "$@"; do
  case "$a" in
    --sessions) SESS=1 ;; --mot-de-passe) MDP=1 ;; --phrase) PHR=1 ;; --bitvavo) BV=1 ;;
    --tout) SESS=1; MDP=1; PHR=1 ;;
    *) echo "option inconnue : $a" >&2; exit 2 ;;
  esac
done
SEC="$(pwd)/secrets"
IMG=nelyqor-trading:local
saisie() { docker run --rm -it --network none --user 0 -e NELYQOR_ROLE=lab -v "$SEC:/out" "$IMG" \
             python scripts/set_password.py /out/owner_password_hash "$1"; }
if [ "$MDP" = 1 ] && [ "$PHR" = 1 ]; then saisie --les-deux
elif [ "$MDP" = 1 ]; then saisie --mot-de-passe-seul
elif [ "$PHR" = 1 ]; then saisie --phrase-seule; fi
if [ "$BV" = 1 ]; then
  : > "$SEC/bitvavo_api_key"; : > "$SEC/bitvavo_api_secret"
  echo "clés Bitvavo vidées (supprimer AUSSI la clé dans Bitvavo › Paramètres › API)"
fi
chmod 600 "$SEC"/*
for n in bitvavo_api_key bitvavo_api_secret owner_password_hash engine_state_key owner_command_key owner_command_salt ntfy_url; do
  [ -e "$SEC/$n" ] && chown 10001:10001 "$SEC/$n"
done
[ -e "$SEC/owner_command_salt" ] && chmod 644 "$SEC/owner_command_salt"
chown root:root "$SEC/backup_passphrase" 2>/dev/null || true
if [ "$PHR" = 1 ] || [ "$BV" = 1 ]; then
  docker compose up -d --force-recreate engine
fi
if [ "$SESS" = 1 ] || [ "$MDP" = 1 ] || [ "$PHR" = 1 ]; then
  docker compose up -d --force-recreate api caddy
  echo "API redémarrée : toutes les sessions sont fermées (se reconnecter sur l'iPhone)."
fi
[ "$PHR" = 1 ] && echo "Sur l'iPhone : la nouvelle phrase sera demandée à la prochaine commande signée."
echo "Fait. Vérifier : docker compose ps ; PWA › connexion."
