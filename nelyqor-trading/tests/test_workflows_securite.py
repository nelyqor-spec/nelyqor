"""Sécurité des workflows GitHub Actions (phase 6) : aucun secret, aucune clé Bitvavo, jamais d'activation du mode
réel, permissions minimales, aucun déclencheur exposé aux forks, aucune entrée interpolée directement dans un script."""
import re

import yaml

from backend.core.config import ROOT

WF = sorted((ROOT.parent / ".github" / "workflows").glob("*.yml"))
ALLOWED_WRITE = {"nelyqor-prospective.yml": {"contents": "write", "issues": "write", "actions": "read"},
                 # phase 8 : import ponctuel d'un artefact de données (lecture seule)
                 "nelyqor-donnees.yml": {"contents": "read", "actions": "read"}}


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


SECRETS_EXPR = re.compile(r"\$\{\{[^}]*\bsecrets\b", re.I)
REAL_FLAG = re.compile(r"NELYQOR_REAL_TRADING_ALLOWED", re.I)


def _real_flag_lines_ok(text: str) -> list[str]:
    """Toute ligne de CODE (hors commentaire) qui nomme le drapeau du réel doit le fixer littéralement à faux."""
    bad = []
    for ln in text.splitlines():
        code = ln.split("#", 1)[0] if not ln.lstrip().startswith("#") else ""
        if REAL_FLAG.search(code) and not re.search(r"NELYQOR_REAL_TRADING_ALLOWED\s*[:=]\s*['\"]?false['\"]?\s*$",
                                                     code.strip(), re.I):
            bad.append(ln.strip())
    return bad


def test_secrets_and_real_flag_strict_forms():
    """Contre-expertise phase 7 (D8) : « secrets. » ne suffisait pas (toJSON(secrets), secrets['X']) ; le drapeau du
    réel pouvait être fixé par une expression, ou écrit dans $GITHUB_ENV."""
    for f in WF:
        t = f.read_text()
        assert not SECRETS_EXPR.search(t), f"{f.name} : expression utilisant secrets"
        assert not _real_flag_lines_ok(t), f"{f.name} : drapeau du réel hors « false » littéral : {_real_flag_lines_ok(t)}"
    # le contrôle lui-même attrape les formes contournées
    for evil in ("run: echo '${{ toJSON(secrets) }}'", "env: {K: \"${{ secrets['BITVAVO'] }}\"}"):
        assert SECRETS_EXPR.search(evil)
    for evil in ("NELYQOR_REAL_TRADING_ALLOWED: ${{ inputs.reel }}",
                 'echo "NELYQOR_REAL_TRADING_ALLOWED=true" >> "$GITHUB_ENV"',
                 "NELYQOR_REAL_TRADING_ALLOWED: 'TRUE'"):
        assert _real_flag_lines_ok(evil), evil
    assert not _real_flag_lines_ok('  NELYQOR_REAL_TRADING_ALLOWED: "false"')


def test_checkout_without_persisted_credentials():
    """Le jeton n'est laissé dans .git/config que par le workflow qui pousse l'archive prospective."""
    for f in WF:
        doc = yaml.safe_load(f.read_text())
        for name, job in doc["jobs"].items():
            for st in job.get("steps", []):
                if str(st.get("uses", "")).startswith("actions/checkout"):
                    persist = (st.get("with") or {}).get("persist-credentials", True)
                    if f.name == "nelyqor-prospective.yml":
                        continue
                    assert persist is False, f"{f.name}/{name} : persist-credentials non désactivé"


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


def test_research_workflows_never_triggered_automatically():
    """Phase 7 : un push touchant un fichier de workflow de recherche a relancé des recherches sur les historiques déjà
    exploités (annulées en 1 min). Les workflows de recherche ne se lancent plus qu'à la main."""
    research = [f for f in WF if f.name.startswith("nelyqor-research")]
    assert len(research) >= 4
    for f in research:
        on = _on(yaml.safe_load(f.read_text()))
        keys = set(on) if isinstance(on, dict) else set(on if isinstance(on, list) else [on])
        assert keys == {"workflow_dispatch"}, f"{f.name} : déclencheurs {keys}"
