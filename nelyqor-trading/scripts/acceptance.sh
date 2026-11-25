#!/usr/bin/env bash
# Recette : lint + typage + tests. En cas de succès, enregistre « acceptance_tests_ok » (avec le commit)
# dans la base : c'est l'un des pré-requis vérifiés par GUARDIAN avant toute activation du réel.
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
  $PY - <<'EOF'
import os, subprocess, sys
sys.path.insert(0, ".")
from backend.storage.db import Database
commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
db = Database(os.environ.get("NELYQOR_DB", "data/nelyqor.db"))
db.kv_set("acceptance_tests_ok", True)
db.kv_set("acceptance_commit", commit)
print("acceptance_tests_ok enregistré pour", commit)
EOF
fi
