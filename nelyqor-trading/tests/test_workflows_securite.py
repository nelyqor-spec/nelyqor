"""Sécurité des workflows GitHub Actions (phase 6) : aucun secret, aucune clé Bitvavo, jamais d'activation du mode
réel, permissions minimales, aucun déclencheur exposé aux forks, aucune entrée interpolée directement dans un script."""
import re

import yaml

from backend.core.config import ROOT

WF = sorted((ROOT.parent / ".github" / "workflows").glob("*.yml"))
ALLOWED_WRITE = {"nelyqor-prospective.yml": {"contents": "write", "issues": "write", "actions": "read"}}


def _on(doc):
    return doc.get("on", doc.get(True, {}))  # PyYAML lit « on » comme True


def test_workflows_found():
    assert len(WF) >= 8


def test_no_secrets_no_bitvavo_keys_no_real_flag():
    for f in WF:
        t = f.read_text()
        assert "secrets." not in t, f"{f.name} : usage de secrets"
        assert not re.search(r"BITVAVO_API_(KEY|SECRET)\s*[:=]", t), f.name
        assert not re.search(r"NELYQOR_REAL_TRADING_ALLOWED\s*[:=]\s*['\"]?true", t, re.I), f"{f.name} active le réel"


def test_no_fork_exposed_triggers():
    for f in WF:
        on = _on(yaml.safe_load(f.read_text()))
        keys = set(on) if isinstance(on, dict) else set(on if isinstance(on, list) else [on])
        assert not keys & {"pull_request_target", "workflow_run", "issue_comment", "pull_request"}, f.name


def test_minimal_permissions():
    for f in WF:
        doc = yaml.safe_load(f.read_text())
        assert doc.get("permissions") == {"contents": "read"}, f"{f.name} : permissions globales"
        for name, job in doc["jobs"].items():
            perms = job.get("permissions")
            if perms is None:
                continue
            assert perms == ALLOWED_WRITE.get(f.name), f"{f.name}/{name} : permissions {perms}"


def test_contents_write_only_on_default_branch_archive():
    t = (ROOT.parent / ".github/workflows/nelyqor-prospective.yml").read_text()
    assert "env.IS_DEFAULT == 'true'" in t and 'git push origin "$ARCHIVE_BRANCH"' in t


def test_no_untrusted_expression_in_run_scripts():
    """Aucune expression ${{ inputs.* }}, ${{ github.event.* }}, ${{ github.head_ref }} ni sortie de pas dans un bloc
    run (sauf entrées booléennes typées) : elles passent par l'environnement."""
    bad = re.compile(r"\$\{\{\s*(github\.event\.|github\.head_ref|steps\.[^}]*outputs)")
    for f in WF:
        doc = yaml.safe_load(f.read_text())
        for job in doc["jobs"].values():
            for st in job.get("steps", []):
                run = st.get("run", "")
                assert not bad.search(run), f"{f.name} : {st.get('name')}"
                for m in re.findall(r"\$\{\{\s*inputs\.(\w+)", run):
                    spec = (_on(doc).get("workflow_dispatch") or {}).get("inputs", {}).get(m, {})
                    assert spec.get("type") == "boolean", f"{f.name} : entrée {m} non booléenne dans un script"
