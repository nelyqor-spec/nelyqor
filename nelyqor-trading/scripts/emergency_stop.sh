#!/usr/bin/env bash
# Arrêt d'urgence SANS l'iPhone (SSH sur le serveur). Bloque les nouvelles entrées ; ne liquide pas ;
# protections conservées.
# Canal principal (phase 4) : fichier déposé dans le répertoire d'état du MOTEUR (/state), que l'API ne peut ni
# créer, ni effacer, ni marquer « traité ». Le moteur l'applique au prochain cycle (≤ 5 s).
# Canal secondaire (phase 5) : commande déposée dans le canal de commandes (/commands/commandes.db, écrit par
# l'API, lu en lecture seule par le moteur). Une API compromise peut la supprimer avant lecture : complément
# seulement. Le moteur, lui, monte ce volume en lecture seule et ne peut pas y écrire.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose exec -T engine sh -c 'touch /state/ARRET_URGENCE && echo "drapeau d arret d urgence depose dans /state"'
docker compose exec -T api python - <<'PY' || true
import json, sys, time, uuid
sys.path.insert(0, "/app")
from backend.storage.channels import open_command_writer
c = open_command_writer("/commands/commandes.db")
c.exec("INSERT INTO control_commands(id,ts,command,payload,status) VALUES(?,?,?,?,?)",
       (str(uuid.uuid4()), int(time.time() * 1000), "emergency_stop", json.dumps({"actor": "ssh"}), "pending"))
print("commande d'arrêt également déposée dans le canal de commandes")
PY
echo "Vérifier : docker compose exec engine ls /state  (ARRET_URGENCE.applique-* une fois appliqué)"
