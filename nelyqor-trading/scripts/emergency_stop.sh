#!/usr/bin/env bash
# Arrêt d'urgence SANS l'iPhone (SSH sur le serveur). Bloque les nouvelles entrées ; ne liquide pas ;
# protections conservées.
# Canal principal (phase 4) : fichier déposé dans le répertoire d'état du MOTEUR (/state), que l'API ne peut ni
# créer, ni effacer, ni marquer « traité ». Le moteur l'applique au prochain cycle (≤ 5 s).
# Canal secondaire : commande en base (peut être neutralisée par une API compromise — conservé en complément).
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose exec -T engine sh -c 'touch /state/ARRET_URGENCE && echo "drapeau d arret d urgence depose dans /state"'
docker compose exec -T engine python - <<'PY' || true
import json, sqlite3, time, uuid
c = sqlite3.connect("/data/nelyqor.db", timeout=10)
c.execute("INSERT INTO control_commands(id,ts,command,payload,status) VALUES(?,?,?,?,?)",
          (str(uuid.uuid4()), int(time.time()*1000), "emergency_stop", json.dumps({"actor": "ssh"}), "pending"))
c.commit(); print("commande d'arrêt également déposée en base")
PY
echo "Vérifier : docker compose exec engine ls /state  (ARRET_URGENCE.applique-* une fois appliqué)"
