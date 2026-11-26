#!/usr/bin/env python3
"""Sondes de scripts/incident_check.sh (phase 7), exécutées DANS les conteneurs de la pile de TEST — jamais livrées
dans l'image (`.dockerignore`) ; transmises sur l'entrée standard : `python - <commande> < scripts/incident_probe.py`.

    counts      (moteur) : empreintes du journal (fills, intentions, positions, stops actifs, doublons), intégrité
                SQLite, niveau disque, compteurs d'écriture — pour comparer avant/après un incident.
    seed        (moteur, moteur ARRÊTÉ) : achat SIMULÉ de 30 € par le VRAI code d'exécution (papier), pour que les
                scénarios portent sur une position et des transactions réelles du journal.
    cmd_result  (moteur) <id> : statut et résultat d'une commande prise par le moteur.
    sign        (image seule) : charge utile `activate_real` signée avec la phrase FACTICE (env PHRASE, SALT).
    brute       (conteneur du réseau de la pile) : rafale de connexions au mauvais mot de passe (env URL, N, CA_PEM,
                HOST, XFF) ; imprime les codes HTTP.
Toutes les sorties : un objet JSON sur la dernière ligne. Aucune clé Bitvavo, aucun ordre réel (simulation seule).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, "/app" if Path("/app/backend").is_dir() else str(Path(__file__).resolve().parents[1]))

DB = os.environ.get("NELYQOR_DB", "/engine/engine.db")


def _h(rows: list[tuple[Any, ...]]) -> str:
    return hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest()[:16]


def counts() -> dict[str, Any]:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
    one = lambda q: c.execute(q).fetchone()[0]  # noqa: E731
    fills = c.execute("SELECT id, intent_id, market, side, amount, price FROM fills ORDER BY id").fetchall()
    buys = c.execute("SELECT id, client_order_id FROM intents WHERE side='buy' ORDER BY id").fetchall()
    pos: dict[str, float] = {}
    for _, _, mk, side, amt, _ in fills:
        pos[mk] = round(pos.get(mk, 0.0) + (float(amt) if side == "buy" else -float(amt)), 12)
    stops = c.execute("SELECT market, COUNT(*) FROM intents WHERE order_type IN ('stopLoss','stopLossLimit') AND "
                      "status IN ('awaitingTrigger','new','SUBMITTING','UNKNOWN') GROUP BY market").fetchall()
    hb = c.execute("SELECT v FROM kv WHERE k='engine_heartbeat'").fetchone()
    hbd = json.loads(hb[0]) if hb else {}
    return {
        "integrite": one("PRAGMA integrity_check"),
        "fills": len(fills), "fills_empreinte": _h(fills),
        "intents": one("SELECT COUNT(*) FROM intents"),
        "achats": len(buys), "achats_empreinte": _h(buys),
        "client_order_id_doublons": one("SELECT COUNT(*) - COUNT(DISTINCT client_order_id) FROM intents "
                                        "WHERE client_order_id IS NOT NULL"),
        "positions": pos, "stops_actifs_max_par_marche": max((n for _, n in stops), default=0),
        "equity_snapshots": one("SELECT COUNT(*) FROM equity_snapshots"),
        "decisions": one("SELECT COUNT(*) FROM decisions"),
        "notifications_disque": one("SELECT COUNT(*) FROM notifications WHERE kind='disque'"),
        "notif_disque_derniere": (c.execute("SELECT message FROM notifications WHERE kind='disque' ORDER BY ts DESC "
                                            "LIMIT 1").fetchone() or [None])[0],
        "disque": hbd.get("disque"), "heartbeat_age_s": (time.time() * 1000 - hbd["ts"]) / 1000 if hbd else None,
        "data_ok": hbd.get("data_ok"),
    }


async def _seed() -> dict[str, Any]:
    from decimal import Decimal as D

    from backend.core.config import Settings
    from backend.execution.engine import InstanceLock, OrderRequest
    from backend.market_data.bitvavo_rest import BitvavoREST
    from backend.runner import Engine, _load_state_key
    from backend.storage.db import Database

    s = Settings()
    lock = InstanceLock(Path(s.db_path).with_suffix(".lock"))
    lock.acquire()  # refuse si le moteur tourne (instance unique)
    ck = s.read_secret("owner_command_key")
    eng = Engine(s, Database(s.db_path), BitvavoREST(s.bitvavo_rest_url), state_key=_load_state_key(s),
                 command_key=bytes.fromhex(ck) if ck else None,
                 store_path=Path(os.environ.get("NELYQOR_ENGINE_STATE_DIR", "/state")) / "etat_moteur.json")
    eng.start()
    await eng.step()
    st = eng.states["BTC-EUR"]
    amt = D(30)
    r = await eng.paper_exec.submit(OrderRequest(  # type: ignore[union-attr]
        "BTC-EUR", "buy", "market", amount_quote=amt, purpose="entree",
        check=eng._check(st, "buy", amt, True, D(100), D(100), amt, "simulation")), D(100))
    if r.accepted:
        eng._kv_set("position_meta_simulation", {"BTC-EUR": {"entry_ts": eng.clock.now_ms(), "strategy": "incident",
                                                              "stop": str((st.best_bid or D(0)) * D("0.9"))}}, "simulation")
        await eng.manage_positions(eng.paper_exec)  # type: ignore[arg-type]  # pose le stop de protection
    lock.release()
    return {"achat_accepte": bool(r.accepted), "raisons": getattr(r, "reasons", None)}


def cmd_result(cid: str) -> dict[str, Any]:
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=20)
    r = c.execute("SELECT status, result FROM control_commands WHERE id=?", (cid,)).fetchone()
    return {"status": r[0], "result": json.loads(r[1]) if r and r[1] else None} if r else {"status": None}


def sign() -> dict[str, Any]:
    import secrets

    from backend.core.command_sig import derive_key
    from backend.core.command_sig import sign as _sign
    from backend.guardian.guardian import REAL_CONFIRMATION_PHRASE

    key = derive_key(os.environ["PHRASE"], os.environ["SALT"])
    fields = {"confirmation": REAL_CONFIRMATION_PHRASE, "declare_retrait_desactive": True, "declare_ip_fixe": True,
              "declare_2fa": True, "accepte_risque_perte_totale": True}
    ts, nonce = int(time.time() * 1000), secrets.token_hex(16)
    return {"confirmation": REAL_CONFIRMATION_PHRASE, **{k: v for k, v in fields.items() if k != "confirmation"},
            "signature": _sign(key, "activate_real", ts, nonce, fields), "sig_ts": ts, "sig_nonce": nonce,
            "sig_fields": fields}


def brute() -> dict[str, Any]:
    url, n = os.environ["URL"], int(os.environ.get("N", "7"))
    ctx = None
    if os.environ.get("CA_PEM"):
        ctx = ssl.create_default_context(cadata=os.environ["CA_PEM"])
    codes = []
    pw = os.environ.get("PW", "mauvais-mot-de-passe-0000")
    for i in range(n):
        h = {"Content-Type": "application/json"}
        if os.environ.get("HOST"):
            h["Host"] = os.environ["HOST"]
        if os.environ.get("XFF"):
            h["X-Forwarded-For"] = f"203.0.113.{i + 1}"  # adresse usurpée différente à chaque essai
        req = urllib.request.Request(url, data=json.dumps({"password": pw}).encode(), headers=h,  # noqa: S310
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:  # noqa: S310 — pile de test
                codes.append(r.status)
        except urllib.error.HTTPError as e:
            codes.append(e.code)
    return {"codes": codes}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "counts"
    if cmd == "seed":
        out = asyncio.run(_seed())
    elif cmd == "cmd_result":
        out = cmd_result(sys.argv[2])
    else:
        out = {"counts": counts, "sign": sign, "brute": brute}[cmd]()
    print(json.dumps(out, ensure_ascii=False, default=str))
