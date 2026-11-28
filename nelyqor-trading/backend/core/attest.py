"""Preuve de recette SIGNÉE, liée à la version exacte du code, à la configuration et aux résultats des tests.

Avant la phase 4, la recette n'écrivait qu'un booléen non signé `acceptance_tests_ok` en base : n'importe quel
processus capable d'écrire dans la base (ou une recette ancienne) suffisait à satisfaire ce pré-requis du réel.

Désormais `scripts/acceptance.sh --record` produit une preuve :
  {commit, empreinte du code (backend, frontend, scripts, dépendances), de la configuration effective, de la
   sortie des tests, nombre de tests réussis / échoués, horodatage}
signée HMAC-SHA256 avec `secrets/engine_state_key` (lisible par le propriétaire sur le serveur et par le moteur,
jamais par l'API ni par le LAB). Le moteur n'accepte la preuve que si la signature est valide, qu'aucun test
n'a échoué, qu'elle a moins de `MAX_AGE_MS`, et que les empreintes correspondent au code et à la configuration
qu'il exécute EFFECTIVEMENT : modifier un seul fichier invalide la recette.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any

MAX_AGE_MS = 30 * 86_400_000
# Code effectivement embarqué dans l'image (Dockerfile) : moteur, PWA (code de signature), scripts, dépendances.
CODE_GLOBS = ("backend/**/*.py", "frontend/**/*", "scripts/**/*", "requirements.lock", "pyproject.toml")
CONFIG_GLOBS = ("*.yaml", "*.yml", "*.json")
TEST_GLOBS = ("tests/**/*.py",)  # sources des tests : empreinte INFORMATIVE (l'image de production ne les contient pas)


def _tree_sha256(root: Path, globs: tuple[str, ...]) -> str:
    h = hashlib.sha256()
    files = sorted({p for g in globs for p in root.glob(g) if p.is_file() and "__pycache__" not in p.parts})
    for p in files:
        h.update(str(p.relative_to(root)).encode() + b"\0" + hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def code_sha256(root: Path) -> str:
    return _tree_sha256(root, CODE_GLOBS)


def config_sha256(root: Path) -> str:
    """Configuration EFFECTIVE : le répertoire réellement lu par le moteur (NELYQOR_CONFIG_DIR éventuel)."""
    from .config import CONFIG_DIR
    cfg = CONFIG_DIR if root.resolve() == _repo_root() else root / "config"
    return _tree_sha256(cfg, CONFIG_GLOBS)


def _repo_root() -> Path:
    from .config import ROOT
    return Path(ROOT).resolve()


def parse_pytest(output: str) -> tuple[int, int]:
    """(réussis, échoués+erreurs) d'après la dernière ligne de résumé de pytest."""
    passed = failed = 0
    for line in output.splitlines()[::-1]:
        if re.search(r"\b(passed|failed|error)", line) and " in " in line:
            for n, kind in re.findall(r"(\d+) (passed|failed|errors?)", line):
                if kind == "passed":
                    passed = int(n)
                else:
                    failed += int(n)
            break
    return passed, failed


DOMAIN = b"nelyqor-acceptance-proof-v1\0"  # séparation de domaine avec les autres usages de engine_state_key


def _canon(proof: dict[str, Any]) -> bytes:
    return DOMAIN + json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()


def make_proof(root: Path, key: bytes, test_output: str, commit: str, now_ms: int) -> dict[str, Any]:
    passed, failed = parse_pytest(test_output)
    proof = {"commit": commit, "code_sha256": code_sha256(root), "config_sha256": config_sha256(root),
             "tests_output_sha256": hashlib.sha256(test_output.encode()).hexdigest(), "tests_passed": passed,
             "tests_sources_sha256_informatif": _tree_sha256(root, TEST_GLOBS),
             "tests_failed": failed, "ts_ms": now_ms, "version": 1}
    return {"proof": proof, "sig": hmac.new(key, _canon(proof), hashlib.sha256).hexdigest()}


def verify_proof(rec: Any, key: bytes | None, root: Path, now_ms: int, max_age_ms: int = MAX_AGE_MS) -> list[str]:
    """Liste des raisons de refus (vide = preuve valide pour le code et la configuration en cours)."""
    if key is None:
        return ["clé d'intégrité absente : preuve de recette invérifiable"]
    if not isinstance(rec, dict) or not isinstance(rec.get("proof"), dict) or not isinstance(rec.get("sig"), str):
        return ["aucune preuve de recette signée (lancer scripts/acceptance.sh --record)"]
    p = rec["proof"]
    if not hmac.compare_digest(rec["sig"], hmac.new(key, _canon(p), hashlib.sha256).hexdigest()):
        return ["signature de la preuve de recette invalide"]
    why = []
    if int(p.get("tests_failed", 1)) != 0 or int(p.get("tests_passed", 0)) <= 0:
        why.append("la recette signée contient des tests échoués ou aucun test réussi")
    if now_ms - int(p.get("ts_ms", 0)) > max_age_ms:
        why.append("preuve de recette trop ancienne (> 30 jours)")
    if p.get("code_sha256") != code_sha256(root):
        why.append("le code exécuté diffère du code recetté")
    if p.get("config_sha256") != config_sha256(root):
        why.append("la configuration diffère de celle recettée")
    return why
