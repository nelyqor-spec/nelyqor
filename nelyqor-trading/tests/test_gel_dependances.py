"""Garde-fou des générations prospectives figées (v3, E1) : dépendances TRANSITIVES non incluses dans leurs
empreintes. Si ce test échoue, ne pas « mettre à jour la valeur » : la modification change potentiellement les
décisions d'une génération figée ; il faut une NOUVELLE génération (v1–v3 et E1 restent intactes)."""
import hashlib
import json
from pathlib import Path

from backend.core.config import ROOT

TRANSITIVES = {
    # importé par backend/events/hygiene.py (E1), figé au gel E1 (26/11/2026 16:17 UTC)
    "backend/events/safety.py": "890adfa37a6b18722192902eef333c23ffba48227d35b540e37ebf0f101fe6c2",
    # chargement des bougies (v1, v2, v3, E1), inchangé depuis le 25/11/2026
    "backend/data/store.py": "2e9884bd5ab14dc863894e5598a61debeebd380e0e8558a6a3ad0afbf94fe0ed",
}


def test_transitive_dependencies_of_frozen_generations_unchanged():
    for f, h in TRANSITIVES.items():
        assert hashlib.sha256((ROOT / f).read_bytes()).hexdigest() == h, f"{f} modifié : nouvelle génération requise"


def test_scripts_only_use_root_from_config():
    """Les scripts prospectifs n'utilisent de backend.core.config que ROOT (racine du projet) : les changements de
    HardLimits/Settings (phase 6) n'affectent pas leurs décisions."""
    for s in ("scripts/prospective_v3.py", "scripts/prospective_e1.py"):
        src = (ROOT / s).read_text()
        assert "from backend.core.config import ROOT  # noqa: E402" in src
        assert "HardLimits" not in src and "Settings" not in src
    assert ROOT == Path(__file__).resolve().parents[1]


def test_frozen_fingerprints_match_current_tree():
    """Empreintes de code et de candidats de v1, v2, v3 et E1 recalculées sur l'arbre courant."""
    sha = lambda p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()  # noqa: E731
    cand = lambda c: hashlib.sha256(json.dumps(c, sort_keys=True, ensure_ascii=False).encode()).hexdigest()  # noqa: E731
    for f in ("reports/prospective/candidats_figes.json", "reports/prospective/v2/candidats_figes_v2.json",
              "reports/prospective/v3/candidats_figes_v3.json", "reports/prospective/e1/candidats_figes_e1.json"):
        d = json.loads((ROOT / f).read_text())
        code = d.get("empreinte_code") or {}
        assert code and all(sha(k) == h for k, h in code.items()), f
        ec = d.get("empreintes_candidats") or {}
        assert all(ec.get(c["id"]) == cand(c) for c in d["candidats"]) if ec else True, f
