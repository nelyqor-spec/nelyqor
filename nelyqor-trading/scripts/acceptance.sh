#!/usr/bin/env bash
# Recette : lint + typage + tests. En cas de succès, enregistre une PREUVE SIGNÉE (commit, empreintes du code,
# de la configuration et des résultats) : pré-requis vérifié par le moteur avant toute activation du réel.
# Usage : scripts/acceptance.sh [--record]
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-python3}"
mkdir -p reports
{
  echo "# Recette $(date -u +%FT%TZ) — commit $(git rev-parse --short HEAD 2>/dev/null || echo inconnu)"
  echo "## ruff"; $PY -m ruff check backend tests
  echo "## mypy"; $PY -m mypy
  echo "## pytest"; $PY -m pytest tests -v -p no:warnings
} 2>&1 | tee reports/acceptance_output.txt
if [ "${1:-}" = "--record" ]; then
  # Preuve SIGNÉE (clé secrets/engine_state_key) liée au commit, au code, à la configuration et à la sortie
  # des tests : le moteur la revérifie contre le code qu'il exécute (backend/core/attest.py).
  NELYQOR_ROLE=engine NELYQOR_SECRETS_DIR="${NELYQOR_SECRETS_DIR:-secrets}" $PY - <<'EOF2'
import os, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, ".")
from backend.core.attest import make_proof
from backend.core.config import Settings
from backend.storage.db import Database
key = Settings(role="engine").read_secret("engine_state_key")
if not key:
    sys.exit("secrets/engine_state_key absent : preuve de recette impossible")
commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
out = Path("reports/acceptance_output.txt").read_text()
rec = make_proof(Path("."), key.encode(), out, commit, int(time.time() * 1000))
if rec["proof"]["tests_failed"] or not rec["proof"]["tests_passed"]:
    sys.exit("recette non conforme : aucune preuve enregistrée")
db = Database(os.environ.get("NELYQOR_DB", "data/nelyqor.db"))
db.kv_set("acceptance_proof", rec)
print("preuve de recette signée enregistrée :", commit, rec["proof"]["tests_passed"], "tests,", rec["proof"]["code_sha256"][:12])
EOF2
fi
