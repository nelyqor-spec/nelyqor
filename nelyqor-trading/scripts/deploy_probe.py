#!/usr/bin/env python3
"""Sondes exécutées DANS les conteneurs par scripts/deploy_check.sh (vérification de déploiement réelle).

Chaque sous-commande imprime un objet JSON {"nom du contrôle": bool | valeur} sur une ligne.
    engine  : montages et isolation vus du moteur (canal :ro -> EROFS, `channel_writable`, `bases_separated`).
    api     : montages et isolation vus de l'API (affichage :ro, ni /engine ni /state, engine.db inaccessible).
    common  : utilisateur non root, absence du socket Docker, système de fichiers racine en lecture seule.
    state   : état du moteur lu dans engine.db (moteur uniquement).
Aucune écriture dans les bases ; les fichiers d'essai sont supprimés.
"""
from __future__ import annotations

import errno
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _write_errno(path: str) -> int:
    """0 si l'écriture réussit (fichier supprimé ensuite), sinon le numéro d'erreur."""
    try:
        with open(path, "w") as f:
            f.write("x")
    except OSError as e:
        return e.errno or -1
    os.unlink(path)
    return 0


def _mounts() -> dict[str, dict[str, str]]:
    out = {}
    for line in Path("/proc/mounts").read_text().splitlines():
        p = line.split()
        if len(p) >= 4:
            out[p[1]] = {"src": p[0], "opts": p[3]}
    return out


def common() -> dict[str, Any]:
    m = _mounts()
    return {
        "uid_non_root": os.getuid() != 0,
        "uid": os.getuid(),
        "pas_de_socket_docker": not any(os.path.exists(p) for p in ("/var/run/docker.sock", "/run/docker.sock")),
        "racine_lecture_seule": "ro" in m.get("/", {}).get("opts", "").split(","),
        "racine_ecriture_refusee_erofs": _write_errno("/app/.essai_ecriture") == errno.EROFS,
        "no_new_privs": _status_field("NoNewPrivs") == "1",
        "capacites_effectives_nulles": int(_status_field("CapEff") or "1", 16) == 0,
    }


def _status_field(name: str) -> str | None:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith(name + ":"):
            return line.split(":", 1)[1].strip()
    return None


def engine() -> dict[str, Any]:
    from backend.runner import Engine
    from backend.storage.channels import channel_writable

    cmd = Path(os.environ.get("NELYQOR_COMMANDS_DB", "/commands/commandes.db"))
    disp = Path(os.environ.get("NELYQOR_DISPLAY_DB", "/display/affichage.db"))
    db = Path(os.environ.get("NELYQOR_DB", "/engine/engine.db"))
    m = _mounts()
    probe = SimpleNamespace(db=SimpleNamespace(path=str(db)), inbox=SimpleNamespace(path=cmd),
                            display=SimpleNamespace(path=disp))
    sec = Path("/run/secrets")
    return {
        "commands_monte_ro": "ro" in m.get("/commands", {}).get("opts", "").split(","),
        "commands_ecriture_erofs": _write_errno(str(cmd.parent / ".essai_moteur")) == errno.EROFS,
        "commands_db_ecriture_erofs": (not cmd.exists()) or _append_errno(cmd) == errno.EROFS,
        "channel_writable_faux": channel_writable(cmd) is False,
        "bases_separees_fonction_reelle": Engine.bases_separated(probe) is True,  # type: ignore[arg-type]
        "display_inscriptible_par_moteur": "rw" in m.get("/display", {}).get("opts", "").split(","),
        "engine_db_presente": db.exists(),
        "secret_etat_lisible": _readable(sec / "engine_state_key"),
        "secret_cle_commande_lisible": _readable(sec / "owner_command_key"),
        "secret_sel_lisible": _readable(sec / "owner_command_salt"),
    }


def _append_errno(p: Path) -> int:
    try:
        fd = os.open(p, os.O_WRONLY | os.O_APPEND)
    except OSError as e:
        return e.errno or -1
    os.close(fd)
    return 0


def _readable(p: Path) -> bool:
    try:
        return bool(p.read_text().strip())
    except OSError:
        return False


def api() -> dict[str, Any]:
    m = _mounts()
    disp = Path(os.environ.get("NELYQOR_DISPLAY_DB", "/display/affichage.db"))
    try:
        c = sqlite3.connect("file:/engine/engine.db?mode=ro", uri=True)
        c.execute("SELECT 1 FROM kv").fetchall()
        engine_db_ouverte = True
    except sqlite3.Error:
        engine_db_ouverte = False
    sec = Path("/run/secrets")
    names = sorted(p.name for p in sec.iterdir()) if sec.exists() else []
    return {
        "display_monte_ro": "ro" in m.get("/display", {}).get("opts", "").split(","),
        "display_ecriture_erofs": _write_errno(str(disp.parent / ".essai_api")) == errno.EROFS,
        "display_db_ecriture_erofs": (not disp.exists()) or _append_errno(disp) == errno.EROFS,
        "pas_de_montage_engine": "/engine" not in m,
        "pas_de_montage_state": "/state" not in m,
        "engine_db_absente": not Path("/engine/engine.db").exists(),
        "engine_db_inouvrable": not engine_db_ouverte,
        "etat_moteur_absent": not Path("/state/etat_moteur.json").exists(),
        "aucun_secret_moteur": not any(n.startswith(("bitvavo", "engine_", "owner_command_key", "telegram"))
                                       for n in names),
        "secrets_api": names,
        "secret_mdp_lisible": _readable(sec / "owner_password_hash"),
        "commands_inscriptible_par_api": _write_errno("/commands/.essai_api") == 0,
    }


def state() -> dict[str, Any]:
    db = sqlite3.connect(f"file:{os.environ.get('NELYQOR_DB', '/engine/engine.db')}?mode=ro", uri=True)

    def kv(k: str) -> Any:
        r = db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else None
    gs = kv("guardian_state")
    hb = kv("engine_heartbeat") or {}
    now = int(time.time() * 1000)
    sd = Path(os.environ.get("NELYQOR_ENGINE_STATE_DIR", "/state"))
    one = lambda q, a=(): db.execute(q, a).fetchone()[0]  # noqa: E731
    return {
        "etat": gs.get("v") if isinstance(gs, dict) else gs,
        "raison": kv("guardian_reason"),
        "heartbeat_age_s": (now - hb["ts"]) / 1000 if hb else None,
        "heartbeat_ts": hb.get("ts"),
        "pid": hb.get("pid"),
        "data_ok": hb.get("data_ok"),
        "last_data_age_s": (now - hb["last_data_ms"]) / 1000 if hb.get("last_data_ms") else None,
        "n_marches": hb.get("n_markets"),
        "real_exec": hb.get("real_exec"),
        "achats_total": one("SELECT COUNT(*) FROM intents WHERE side='buy'"),
        "achats_acceptes": one("SELECT COUNT(*) FROM intents WHERE side='buy' AND status NOT IN ('VETOED','rejected')"),
        "dernier_achat_ts": one("SELECT MAX(ts) FROM intents WHERE side='buy' AND status NOT IN ('VETOED','rejected')"),
        "intents_total": one("SELECT COUNT(*) FROM intents"),
        "fills_total": one("SELECT COUNT(*) FROM fills"),
        "decisions_total": one("SELECT COUNT(*) FROM decisions"),
        "commandes_traitees": one("SELECT COUNT(*) FROM control_commands"),
        "arret_urgence_ok": one("SELECT COUNT(*) FROM control_commands WHERE command='emergency_stop' AND status='done' "
                                "AND result LIKE '%\"ok\": true%'"),
        "magasin_etat_present": (sd / "etat_moteur.json").exists(),
        "drapeau_urgence_en_attente": (sd / "ARRET_URGENCE").exists(),
        "drapeaux_urgence_appliques": sorted(p.name for p in sd.glob("ARRET_URGENCE.applique-*")),
        "scanner_raisons": sorted({r for row in (kv("scanner") or {}).get("rows", []) for r in row.get("reasons", [])}),
        "scanner_ts": (kv("scanner") or {}).get("ts"),
        "now": now,
    }


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "common"
    res = {"engine": engine, "api": api, "common": common, "state": state}[cmd]()
    print(json.dumps(res, ensure_ascii=False, default=str))
