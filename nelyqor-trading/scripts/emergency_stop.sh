#!/usr/bin/env bash
# Arrêt d'urgence SANS l'iPhone (SSH sur le serveur) : dépose une commande traitée par le moteur
# avant tout accès réseau. Bloque les nouvelles entrées ; ne liquide pas ; protections conservées.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose exec -T engine python - <<'PY'
import json, sqlite3, time, uuid
c = sqlite3.connect("/data/nelyqor.db", timeout=10)
c.execute("INSERT INTO control_commands(id,ts,command,payload,status) VALUES(?,?,?,?,?)",
          (str(uuid.uuid4()), int(time.time()*1000), "emergency_stop", json.dumps({"actor": "ssh"}), "pending"))
c.commit(); print("Arrêt d'urgence déposé ; le moteur l'applique au prochain cycle (≤ 5 s).")
PY
