#!/usr/bin/env bash
# Alerte critique de l'HÔTE (phase 7) : sauvegarde échouée, disque presque plein, pile arrêtée…
# Toujours : journal système (journalctl -t nelyqor) et sortie d'erreur. En plus, si configurés :
#   - ntfy : URL du sujet dans secrets/ntfy_url (NELYQOR_ALERT_NTFY_FILE) — le nom du sujet est le secret ;
#   - e-mail : NELYQOR_ALERT_EMAIL (.env) si la commande `mail` existe (paquet bsd-mailx + relais msmtp/postfix).
# Le message ne doit contenir AUCUN secret ni montant (il quitte le serveur). Code retour : 0 si au moins un canal
# externe a accepté l'alerte, 1 sinon (l'alerte reste dans le journal système).
# Usage : scripts/alerte.sh "Titre" "Message"
set -uo pipefail
cd "$(dirname "$0")/.."
TITRE=${1:?usage : scripts/alerte.sh "Titre" "Message"}
MSG=${2:-$TITRE}
NTFY_FILE=${NELYQOR_ALERT_NTFY_FILE:-secrets/ntfy_url}
HOTE=$(hostname -s 2>/dev/null || echo serveur)
ascii() { printf '%s' "$1" | iconv -f utf-8 -t ascii//TRANSLIT 2>/dev/null || printf '%s' "$1" | tr -cd '[:alnum:][:space:][:punct:]'; }
logger -t nelyqor -p user.crit -- "$TITRE : $MSG" 2>/dev/null || true
echo "ALERTE : $TITRE : $MSG" >&2
livre=1
if [ -r "$NTFY_FILE" ] && [ -s "$NTFY_FILE" ]; then
  url=$(head -1 "$NTFY_FILE" | tr -d '[:space:]')
  # corps lu sur l'entrée standard (--data-binary @-) : un message commençant par « @ » n'est jamais un fichier
  if printf '%s' "$MSG" | curl -fsS --max-time 15 -o /dev/null -H "Title: $(ascii "Nelyqor ($HOTE) : $TITRE")" \
       -H "Priority: high" -H "Tags: warning" --data-binary @- "$url"; then
    livre=0
  else
    echo "alerte.sh : échec d'envoi ntfy" >&2
  fi
fi
if [ -n "${NELYQOR_ALERT_EMAIL:-}" ] && command -v mail > /dev/null; then
  printf '%s\n' "$MSG" | mail -s "[Nelyqor $HOTE] $TITRE" "$NELYQOR_ALERT_EMAIL" && livre=0
fi
exit "$livre"
