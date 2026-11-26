"""API de contrôle et de visualisation (rôle « api »). N'a AUCUN accès aux clés Bitvavo.

Phase 5 — bases séparées (backend/storage/channels.py) : l'API n'ouvre JAMAIS la base du moteur.
- LECTURE : `affichage.db`, copie publiée par le moteur, ouverte en lecture seule (volume monté `:ro`) ;
- ÉCRITURE : `commandes.db` (table control_commands seule), lue par le moteur qui valide chaque commande
  (Guardian, signatures du propriétaire) ; et `api.db`, son propre journal d'audit.
Une API compromise peut mentir à l'affichage ou ne pas relayer une commande ; elle ne peut plus modifier le
journal, les flux, les intentions ni l'état du moteur."""
from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..accounting.fiscal import Acquisition, Cession, compute_2086, rows_to_csv
from ..accounting.ledger import twr
from ..core.clock import PARIS, new_id, paris_str, utc_iso
from ..core.config import ROOT, Settings
from ..core.num import D
from ..guardian.guardian import REAL_CONFIRMATION_PHRASE
from ..storage.channels import ReadOnlyView, open_api_db, open_command_writer
from ..storage.db import Database
from .auth import AuthManager

COOKIE = "nq_session"
FRONTEND = Path(os.environ.get("NELYQOR_FRONTEND", ROOT / "frontend"))


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class ReauthBody(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    confirmation: str = Field(default="", max_length=200)


class SignedFields(BaseModel):
    signature: str = Field(default="", max_length=128)
    sig_ts: int = 0
    sig_nonce: str = Field(default="", max_length=64)
    sig_fields: dict[str, Any] = Field(default_factory=dict)


class RealBody(ReauthBody, SignedFields):
    declare_retrait_desactive: bool = False
    declare_ip_fixe: bool = False
    declare_2fa: bool = False
    accepte_risque_perte_totale: bool = False


class LiquidationBody(ReauthBody, SignedFields):
    estimate_id: str = Field(max_length=64)


class AckFlowBody(SignedFields):
    flow_id: str = Field(max_length=128)
    note: str = Field(default="", max_length=500)


COMMAND_RETENTION_MS = 7 * 86_400_000


def create_app(settings: Settings | None = None, auth: AuthManager | None = None, display: ReadOnlyView | None = None,
               commands: Database | None = None, api_db: Database | None = None) -> FastAPI:
    s = settings or Settings(role="api")
    if s.role == "engine":
        s.role = "api"
    database = display or ReadOnlyView(s.display_file)  # LECTURE SEULE : copie d'affichage du moteur
    cmds = commands or open_command_writer(s.commands_file)  # seule base « moteur » que l'API écrit
    adb = api_db or open_api_db(s.api_file)
    cmds.exec("DELETE FROM control_commands WHERE ts < ?", (int(time.time() * 1000) - COMMAND_RETENTION_MS,))
    if auth is None:
        auth = AuthManager(s.read_secret("owner_password_hash"))  # erreur explicite si illisible (phase 6)
    app = FastAPI(title="Nelyqor Trading", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db, app.state.commands, app.state.api_db = database, cmds, adb
    app.state.auth, app.state.settings = auth, s

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        resp: Response = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline';"
                                                   " script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    def ip_of(req: Request) -> str:
        return req.client.host if req.client else "?"

    def audit(actor: str, action: str, ip: str, ok: bool, details: dict[str, Any]) -> None:
        adb.exec("INSERT INTO audit(ts,actor,action,ip,ok,details) VALUES(?,?,?,?,?,?)",
                 (int(time.time() * 1000), actor, action, ip, int(ok), json.dumps(details, ensure_ascii=False)))

    def require_session(request: Request) -> Any:
        sess = auth.get(request.cookies.get(COOKIE))
        if sess is None:
            raise HTTPException(401, "authentification requise")
        if request.method not in ("GET", "HEAD") and not _ct_eq(request.headers.get("X-CSRF-Token", ""), sess.csrf):
            raise HTTPException(403, "jeton CSRF invalide")
        return sess

    def command(name: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
        cid = new_id()
        payload = {**payload, "actor": "owner", "ip": ip_of(request)}
        cmds.exec("INSERT INTO control_commands(id,ts,command,payload,status) VALUES(?,?,?,?,?)",
                  (cid, int(time.time() * 1000), name, json.dumps(payload, ensure_ascii=False), "pending"))
        audit("owner", f"commande {name}", ip_of(request), True, {"id": cid})
        return {"command_id": cid, "status": "pending"}

    # ---------------------------------------------------------------- public
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        hb = database.kv_get("engine_heartbeat")
        age = (int(time.time() * 1000) - hb["ts"]) if hb else None
        return {"api": "ok", "engine_alive": age is not None and age < 120_000}

    @app.post("/api/login")
    def login(body: LoginBody, request: Request, response: Response) -> dict[str, Any]:
        ip = ip_of(request)
        if auth.locked(ip):
            audit("?", "login", ip, False, {"reason": "verrouillé"})
            raise HTTPException(429, "trop de tentatives, réessayer plus tard")
        sess = auth.login(body.password, ip)
        if sess is None:
            audit("?", "login", ip, False, {})
            raise HTTPException(401, "identifiants invalides")
        response.set_cookie(COOKIE, sess.sid, httponly=True, secure=s.cookie_secure, samesite="strict", path="/",
                            max_age=auth.ttl_s)
        audit("owner", "login", ip, True, {})
        return {"csrf": sess.csrf}

    @app.post("/api/logout")
    def logout(request: Request, response: Response, sess: Any = Depends(require_session)) -> dict[str, bool]:
        auth.logout(sess.sid)
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    # ---------------------------------------------------------------- lecture
    @app.get("/api/session")
    def session(sess: Any = Depends(require_session)) -> dict[str, str]:
        return {"csrf": sess.csrf}

    @app.get("/api/status")
    def status(_: Any = Depends(require_session)) -> dict[str, Any]:
        now = int(time.time() * 1000)
        hb = database.kv_get("engine_heartbeat")
        du = shutil.disk_usage(s.display_file.parent if s.display_file.parent.exists() else Path("."))
        return {
            "etat": _state_value(database.kv_get("guardian_state")),
            "etat_note": "affichage ; la signature de l'état est vérifiée par le moteur (rôle api sans clé)",
            "raison": database.kv_get("guardian_reason", ""),
            "mode": (hb or {}).get("mode", "simulation"),
            "reel_autorise_serveur": s.real_trading_server_flag,
            "dernier_heartbeat": paris_str(hb["ts"]) if hb else None,
            "heartbeat_age_s": (now - hb["ts"]) / 1000 if hb else None,
            "moteur_perime": hb is None or now - hb["ts"] > 120_000 or not hb.get("data_ok", True)
            or now - int(hb.get("last_data_ms") or hb["ts"]) > 120_000,
            "moteur_vivant": hb is not None and now - hb["ts"] <= 120_000,
            "derniere_donnee_marche": paris_str(hb["last_data_ms"]) if hb and hb.get("last_data_ms") else None,
            "hard_limits_sha256": database.kv_get("hard_limits_sha256"),
            "version_parametres": database.kv_get("strategy_params_version"),
            "serveur": {"disque_libre_go": round(du.free / 1e9, 2), "disque_total_go": round(du.total / 1e9, 2),
                        "charge": os.getloadavg()[0] if hasattr(os, "getloadavg") else None, "ram": _meminfo()},
            "ordres_inconnus": len(database.q("SELECT id FROM intents WHERE status IN ('UNKNOWN','SUBMITTING')")),
            "flux_non_identifies": len(database.q("SELECT id FROM external_flows WHERE identified=0")),
            "phrase_activation_reel": REAL_CONFIRMATION_PHRASE,
            "capital_authentifie": database.kv_get("capital_authentifie"),
            "affichage_disponible": database.available,
            "source": "copie d'affichage publiée par le moteur (lecture seule)",
        }

    @app.get("/api/portfolio")
    def portfolio(_: Any = Depends(require_session)) -> dict[str, Any]:
        hb = database.kv_get("engine_heartbeat") or {}
        mode = hb.get("mode", "simulation")
        snaps = [dict(r) for r in database.q(
            "SELECT ts, equity_eur, cash_eur, positions_eur FROM equity_snapshots WHERE mode=? ORDER BY ts DESC LIMIT 2000",
            (mode,))][::-1]
        flows = database.q("SELECT ts, kind, value_eur FROM external_flows WHERE mode=? AND identified=1 ORDER BY ts", (mode,))
        net = sum((D(f["value_eur"]) * (1 if f["kind"] == "deposit" else -1) for f in flows), D(0))
        initial = s.initial_capital_eur if mode == "simulation" else D(0)
        last = D(snaps[-1]["equity_eur"]) if snaps else initial
        series = _twr_series(snaps, flows)
        fees = database.one("SELECT COALESCE(SUM(CAST(fee_eur AS REAL)),0) f FROM fills WHERE mode=?", (mode,))
        months = _months_since(int(snaps[0]["ts"])) if snaps else 0
        hosting = s.monthly_hosting_eur * D(str(round(months, 3)))
        pnl = last - initial - net
        return {"mode": mode, "equity_eur": str(last), "capital_initial_eur": str(initial), "apports_nets_eur": str(net),
                "pnl_trading_eur": str(pnl), "twr": str(twr(series)) if len(series) > 1 else "0",
                "frais_eur": f"{fees['f'] if fees else 0:.4f}", "cout_hebergement_estime_eur": str(hosting.quantize(D('0.01'))),
                "resultat_economique_eur": str((pnl - hosting).quantize(D("0.01"))),
                "historique": [{"t": r["ts"], "v": r["equity_eur"]} for r in snaps[:: max(1, len(snaps) // 300)]],
                "positions": database.kv_get(f"position_meta_{mode}", {}),
                "protections": database.kv_get(f"protections_{mode}", {}),
                "protections_reelles": database.kv_get("protections_real", {}),
                "donnees": "réelles" if mode == "real" else "simulation · paper trading"}

    @app.get("/api/markets")
    def markets(_: Any = Depends(require_session)) -> dict[str, Any]:
        sc = database.kv_get("scanner") or {"ts": None, "rows": []}
        return {"maj": paris_str(sc["ts"]) if sc.get("ts") else None, "rows": sc["rows"],
                "note": "Scores et estimations non calibrés sauf mention contraire."}

    @app.get("/api/strategies")
    def strategies(_: Any = Depends(require_session)) -> dict[str, Any]:
        reg = [dict(r) for r in database.q("SELECT name,version,status,sha256,updated_ms FROM model_registry ORDER BY name")]
        exps = [dict(r) for r in database.q(
            "SELECT id,ts,kind,strategy,data_source,data_window,metrics,passed,notes FROM experiments "
            "WHERE kind IN ('holdout','oos') ORDER BY ts DESC LIMIT 100")]
        for e in exps:
            e["metrics"] = json.loads(e["metrics"])
        report = None
        lab_file = os.environ.get("NELYQOR_LAB_REPORT")
        if lab_file and Path(lab_file).exists():
            try:
                report = json.loads(Path(lab_file).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                report = None
        return {"registre": reg, "experiences": exps, "rapport_lab": report}

    @app.get("/api/orders")
    def orders(_: Any = Depends(require_session)) -> dict[str, Any]:
        it = [dict(r) for r in database.q("SELECT id,ts,mode,market,side,order_type,amount,amount_quote,price,post_only,"
                                          "strategy,status,last_error,details FROM intents ORDER BY ts DESC LIMIT 200")]
        fl = [dict(r) for r in database.q("SELECT * FROM fills ORDER BY ts DESC LIMIT 200")]
        return {"intentions": it, "fills": fl}

    @app.get("/api/decisions")
    def decisions(_: Any = Depends(require_session)) -> list[dict[str, Any]]:
        rows = [dict(r) for r in database.q("SELECT * FROM decisions ORDER BY ts DESC LIMIT 200")]
        for r in rows:
            r["reasons"], r["details"] = json.loads(r["reasons"]), json.loads(r["details"])
        return rows

    @app.get("/api/flows")
    def flows(_: Any = Depends(require_session)) -> list[dict[str, Any]]:
        return [dict(r) for r in database.q("SELECT * FROM external_flows ORDER BY ts DESC LIMIT 200")]

    @app.get("/api/incidents")
    def incidents(_: Any = Depends(require_session)) -> dict[str, Any]:
        engine_audit = [{**dict(r), "source": "moteur"} for r in database.q(
            "SELECT ts,actor,action,ok,details FROM audit ORDER BY ts DESC LIMIT 100")]
        api_audit = [{**dict(r), "source": "api"} for r in adb.q(
            "SELECT ts,actor,action,ok,details FROM audit ORDER BY ts DESC LIMIT 100")]
        merged = sorted(engine_audit + api_audit, key=lambda r: int(r["ts"] or 0), reverse=True)[:100]
        return {"notifications": [dict(r) for r in database.q("SELECT * FROM notifications ORDER BY ts DESC LIMIT 50")],
                "audit": merged}

    @app.get("/api/commands/{cid}")
    def command_status(cid: str, _: Any = Depends(require_session)) -> dict[str, Any]:
        # résultat : journal du MOTEUR (publié dans l'affichage) ; sinon la commande attend encore
        r = database.one("SELECT id,command,status,result FROM control_commands WHERE id=?", (cid,))
        if r is None:
            p = cmds.one("SELECT id,command FROM control_commands WHERE id=?", (cid,))
            if p is None:
                raise HTTPException(404)
            return {"id": p["id"], "command": p["command"], "status": "pending", "result": None}
        d = dict(r)
        d["result"] = json.loads(d["result"]) if d["result"] else None
        return d

    @app.get("/api/export/operations.csv")
    def export_ops(_: Any = Depends(require_session)) -> PlainTextResponse:
        rows = [{"date_utc": utc_iso(r["ts"]), "mode": r["mode"], "marche": r["market"], "sens": r["side"],
                 "quantite": r["amount"], "prix": r["price"], "montant_eur": str(D(r["amount"]) * D(r["price"])),
                 "commission": r["fee"], "devise_commission": r["fee_currency"], "commission_eur": r["fee_eur"],
                 "ordre": r["exchange_order_id"]} for r in database.q("SELECT * FROM fills ORDER BY ts")]
        rows += [{"date_utc": utc_iso(r["ts"]), "mode": r["mode"], "marche": r["symbol"], "sens": r["kind"],
                  "quantite": r["amount"], "prix": "", "montant_eur": r["value_eur"], "commission": "",
                  "devise_commission": "", "commission_eur": "", "ordre": r["id"]}
                 for r in database.q("SELECT * FROM external_flows ORDER BY ts")]
        return PlainTextResponse(rows_to_csv(rows) or "aucune opération\n", media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=operations.csv"})

    @app.get("/api/export/fiscal")
    def export_fiscal(year: int, _: Any = Depends(require_session)) -> dict[str, Any]:
        if not 2020 <= year <= 2100:
            raise HTTPException(422, "année invalide")
        # année civile en heure de Paris (indépendant du fuseau du serveur)
        a = int(datetime(year, 1, 1, tzinfo=PARIS).timestamp() * 1000)
        b = int(datetime(year + 1, 1, 1, tzinfo=PARIS).timestamp() * 1000)
        fills = database.q("SELECT * FROM fills WHERE mode='real' AND ts<? ORDER BY ts", (b,))
        acqs, cess = [], []
        for f in fills:
            gross = D(f["amount"]) * D(f["price"])
            fee = D(f["fee_eur"] or 0)
            if f["side"] == "buy":
                acqs.append(Acquisition(f["ts"], gross + fee))
            elif f["ts"] >= a:
                snap = database.one("SELECT positions_eur, equity_eur FROM equity_snapshots WHERE mode='real' AND ts<=? "
                                    "ORDER BY ts DESC LIMIT 1", (f["ts"],))
                v = D(snap["positions_eur"]) if snap else gross
                cess.append(Cession(f["ts"], f["market"].split("-")[0], gross - fee, fee, max(v, gross)))
        res = compute_2086(acqs, cess)
        res["note_valorisation"] = ("V approximée par le dernier snapshot de positions du robot ; inclure les actifs "
                                    "numériques détenus ailleurs par le foyer.")
        return json.loads(json.dumps(res, default=str))

    # ---------------------------------------------------------------- contrôle
    @app.post("/api/control/pause")
    def pause(request: Request, _: Any = Depends(require_session)) -> dict[str, Any]:
        return command("pause", {"reason": "pause manuelle (iPhone)"}, request)

    @app.post("/api/control/emergency_stop")
    def emergency(request: Request, _: Any = Depends(require_session)) -> dict[str, Any]:
        return command("emergency_stop", {}, request)

    @app.post("/api/control/resume")
    def resume(request: Request, body: SignedFields | None = None, _: Any = Depends(require_session)) -> dict[str, Any]:
        # signature transmise telle quelle : seul le moteur peut la vérifier (reprise vers le réel)
        return command("resume", body.model_dump() if body else {}, request)

    @app.post("/api/control/deactivate_real")
    def deactivate(request: Request, _: Any = Depends(require_session)) -> dict[str, Any]:
        return command("deactivate_real", {}, request)

    @app.post("/api/control/activate_real")
    def activate_real(body: RealBody, request: Request, _: Any = Depends(require_session)) -> dict[str, Any]:
        ok = auth.verify_reauth(body.password, ip_of(request))
        audit("owner", "demande activation réel", ip_of(request), ok, {})
        if not ok:
            raise HTTPException(401, "ré-authentification échouée")
        return command("activate_real", {"password_ok": True, "confirmation": body.confirmation,
                                         **body.model_dump(exclude={"password", "confirmation"})}, request)

    @app.get("/api/command_salt")
    def command_salt(_: Any = Depends(require_session)) -> dict[str, Any]:
        """Sel PUBLIC de dérivation de la clé de signature (la clé elle-même n'existe que côté moteur)."""
        from ..core.command_sig import ITER

        p = s.secrets_dir / "owner_command_salt"
        return {"salt": p.read_text().strip() if p.exists() else None, "iterations": ITER}

    @app.post("/api/control/liquidation_estimate")
    def liq_est(request: Request, _: Any = Depends(require_session)) -> dict[str, Any]:
        return command("liquidation_estimate", {}, request)

    @app.post("/api/control/liquidation_confirm")
    def liq_conf(body: LiquidationBody, request: Request, _: Any = Depends(require_session)) -> dict[str, Any]:
        ok = auth.verify_reauth(body.password, ip_of(request))
        if not ok:
            raise HTTPException(401, "ré-authentification échouée")
        return command("liquidation_confirm", {"password_ok": True, "confirmation": body.confirmation,
                                               "estimate_id": body.estimate_id,
                                               **body.model_dump(include={"signature", "sig_ts", "sig_nonce", "sig_fields"})},
                       request)

    @app.post("/api/control/ack_flow")
    def ack_flow(body: AckFlowBody, request: Request, _: Any = Depends(require_session)) -> dict[str, Any]:
        return command("ack_flow", body.model_dump(), request)

    @app.exception_handler(HTTPException)
    async def http_exc(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    # ---------------------------------------------------------------- PWA
    if FRONTEND.exists():
        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(FRONTEND / "index.html")

        app.mount("/", StaticFiles(directory=FRONTEND), name="static")
    return app


def _state_value(raw: Any) -> str:
    if isinstance(raw, dict) and "v" in raw:
        return str(raw["v"])
    return "SIMULATION" if raw is None else "INCIDENT"


def _ct_eq(a: str, b: str) -> bool:
    import hmac

    return bool(a) and hmac.compare_digest(a, b)


def _meminfo() -> dict[str, float] | None:
    try:
        d = {}
        for line in Path("/proc/meminfo").read_text().splitlines()[:3]:
            k, v = line.split(":")
            d[k] = round(int(v.split()[0]) / 1e6, 2)
        return d
    except OSError:
        return None


def _months_since(ts_ms: int) -> float:
    return max(0.0, (time.time() * 1000 - ts_ms) / (30.44 * 86_400_000))


def _twr_series(snaps: list[dict[str, Any]], flows: list[Any]) -> list[tuple[int, Decimal, Decimal]]:
    """Construit (ts, valeur_avant_flux, flux) : une sous-période se termine à chaque flux externe."""
    out: list[tuple[int, Decimal, Decimal]] = []
    fl = [(int(f["ts"]), D(f["value_eur"]) * (1 if f["kind"] == "deposit" else -1)) for f in flows]
    fi = 0
    for i, sn in enumerate(snaps):
        ts, v = int(sn["ts"]), D(sn["equity_eur"])
        f = D(0)
        while fi < len(fl) and fl[fi][0] <= ts:
            f += fl[fi][1]
            fi += 1
        if i == 0 or f != 0 or i == len(snaps) - 1:
            # la valeur du snapshot inclut déjà le flux : valeur avant flux = v - f
            out.append((ts, v - f, f))
    return out


app = create_app() if os.environ.get("NELYQOR_ROLE") == "api" else None
