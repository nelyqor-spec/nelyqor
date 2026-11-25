"""GUARDIAN : machine d'états et droit de veto sur toute intention d'ordre.

États : SIMULATION (défaut), PAUSE_NOUVELLES_ENTREES, RECONCILIATION, REEL_AUTORISE, INCIDENT.
- Le mode réel exige : drapeau serveur NELYQOR_REAL_TRADING_ALLOWED=true, clés présentes,
  activation authentifiée du propriétaire (phrase de confirmation), tests critiques OK,
  et ne peut JAMAIS être demandé par le rôle LAB.
- Pause ≠ liquidation : la pause bloque les nouvelles entrées, les sorties/protections restent permises.
- Pas d'arrêt définitif fondé sur la seule perte cumulée (préférence explicite du propriétaire).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from ..core.config import HardLimits
from ..core.num import ZERO
from ..storage.db import Database


class State(StrEnum):
    SIMULATION = "SIMULATION"
    PAUSE = "PAUSE_NOUVELLES_ENTREES"
    RECONCILIATION = "RECONCILIATION"
    REAL = "REEL_AUTORISE"
    INCIDENT = "INCIDENT"


REAL_CONFIRMATION_PHRASE = "J'ACCEPTE DE PERDRE TOUT LE CAPITAL ENGAGE"

# Transitions autorisées (source -> destinations)
TRANSITIONS: dict[State, set[State]] = {
    State.SIMULATION: {State.PAUSE, State.RECONCILIATION, State.REAL, State.INCIDENT},
    State.PAUSE: {State.SIMULATION, State.RECONCILIATION, State.REAL, State.INCIDENT},
    State.RECONCILIATION: {State.PAUSE, State.SIMULATION, State.REAL, State.INCIDENT},
    State.REAL: {State.PAUSE, State.RECONCILIATION, State.INCIDENT, State.SIMULATION},
    State.INCIDENT: {State.PAUSE, State.RECONCILIATION, State.SIMULATION},
}


@dataclass
class IntentCheck:
    market: str
    side: str  # buy | sell
    order_type: str
    amount_quote: Decimal  # valeur EUR estimée
    is_entry: bool  # achat qui augmente l'exposition
    is_protective: bool = False
    data_age_ms: int = 0
    market_trading: bool = True
    spread_bps: Decimal | None = None
    anomaly: Decimal = ZERO
    min_order_quote: Decimal = ZERO
    available_quote: Decimal = ZERO
    equity: Decimal = ZERO
    exposure_after: Decimal = ZERO
    position_after: Decimal = ZERO
    margin: bool = False
    leverage: bool = False
    reinforcement_of_loser: bool = False
    new_independent_thesis: bool = False
    order_type_supported: bool = True
    mode: str = "simulation"  # simulation | real


@dataclass
class Verdict:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


class Guardian:
    def __init__(self, db: Database, limits: HardLimits, real_server_flag: bool, role: str = "engine",
                 now_ms: Any = None) -> None:
        self.db = db
        self.limits = limits
        self.real_server_flag = real_server_flag
        self.role = role
        self._now = now_ms or (lambda: 0)

    # --- état
    @property
    def state(self) -> State:
        return State(self.db.kv_get("guardian_state", State.SIMULATION.value))

    @property
    def is_real(self) -> bool:
        return self.state == State.REAL

    def _audit(self, actor: str, action: str, ok: bool, details: dict[str, Any]) -> None:
        import json

        self.db.exec("INSERT INTO audit(ts,actor,action,ip,ok,details) VALUES(?,?,?,?,?,?)",
                     (self._now(), actor, action, details.pop("ip", None), int(ok), json.dumps(details, default=str)))

    def transition(self, to: State, actor: str, reason: str) -> None:
        if self.role == "lab":
            raise PermissionError("Le laboratoire ne peut pas changer l'état de fonctionnement")
        frm = self.state
        if to == frm:
            return
        if to == State.REAL:
            raise PermissionError("Utiliser activate_real() : activation explicite et distincte requise")
        if to not in TRANSITIONS[frm]:
            raise ValueError(f"Transition interdite {frm.value} -> {to.value}")
        # Retour au réel après pause : uniquement si le réel avait été autorisé et reste autorisé.
        self.db.kv_set("guardian_state", to.value, self._now())
        self.db.kv_set("guardian_reason", reason, self._now())
        self._audit(actor, f"transition {frm.value}->{to.value}", True, {"reason": reason})

    def activate_real(self, actor: str, confirmation: str, password_ok: bool, preflight: dict[str, bool]) -> Verdict:
        reasons: list[str] = []
        if self.role != "engine":
            reasons.append(f"rôle {self.role} non autorisé")
        if not self.real_server_flag:
            reasons.append("drapeau serveur NELYQOR_REAL_TRADING_ALLOWED absent")
        if not password_ok:
            reasons.append("ré-authentification du propriétaire échouée")
        if confirmation != REAL_CONFIRMATION_PHRASE:
            reasons.append("phrase de confirmation incorrecte")
        for k, ok in preflight.items():
            if not ok:
                reasons.append(f"pré-requis non satisfait : {k}")
        if self.state in (State.INCIDENT, State.RECONCILIATION):
            reasons.append(f"état {self.state.value} : résoudre d'abord")
        ok = not reasons
        self._audit(actor, "activation_reel", ok, {"reasons": reasons, "preflight": preflight})
        if ok:
            self.db.kv_set("guardian_state", State.REAL.value, self._now())
            self.db.kv_set("real_activated_ms", self._now(), self._now())
            self.db.kv_set("guardian_reason", "activation explicite du propriétaire", self._now())
        return Verdict(ok, reasons)

    def emergency_stop(self, actor: str) -> None:
        """Bloque immédiatement les nouvelles entrées. N'annule PAS les protections, ne liquide pas."""
        prev = self.state
        self.db.kv_set("resume_state", prev.value if prev != State.PAUSE else self.db.kv_get("resume_state", "SIMULATION"),
                       self._now())
        self.db.kv_set("guardian_state", State.PAUSE.value, self._now())
        self.db.kv_set("guardian_reason", "arrêt d'urgence", self._now())
        self._audit(actor, "arret_urgence", True, {"from": prev.value})

    def resume(self, actor: str, checks: dict[str, bool]) -> Verdict:
        failed = [k for k, ok in checks.items() if not ok]
        if failed:
            self._audit(actor, "reprise", False, {"failed": failed})
            return Verdict(False, [f"contrôle échoué : {k}" for k in failed])
        target = State(self.db.kv_get("resume_state", State.SIMULATION.value))
        if target == State.REAL and not self.real_server_flag:
            target = State.SIMULATION
        self.db.kv_set("guardian_state", target.value, self._now())
        self.db.kv_set("guardian_reason", "reprise après contrôles", self._now())
        self._audit(actor, "reprise", True, {"to": target.value})
        return Verdict(True, [])

    def pause(self, actor: str, reason: str) -> None:
        if self.state not in (State.PAUSE, State.INCIDENT):
            self.db.kv_set("resume_state", self.state.value, self._now())
            self.transition(State.PAUSE, actor, reason)

    def incident(self, reason: str) -> None:
        if self.state != State.INCIDENT:
            if self.state not in (State.PAUSE, State.RECONCILIATION):
                self.db.kv_set("resume_state", self.state.value, self._now())
            self.db.kv_set("guardian_state", State.INCIDENT.value, self._now())
            self.db.kv_set("guardian_reason", reason, self._now())
            self._audit("guardian", "incident", True, {"reason": reason})

    # --- veto
    def check(self, it: IntentCheck) -> Verdict:
        L = self.limits
        r: list[str] = []
        st = self.state
        if self.role != "engine":
            r.append(f"rôle {self.role} sans droit d'exécution")
        if it.mode == "real" and st != State.REAL:
            r.append(f"mode réel non autorisé (état {st.value})")
        if it.is_entry and st in (State.PAUSE, State.RECONCILIATION, State.INCIDENT):
            r.append(f"nouvelles entrées bloquées ({st.value})")
        if st == State.INCIDENT and not it.is_protective:
            r.append("incident : seules les sorties de protection sont permises")
        if it.margin or it.leverage:
            r.append("marge/levier interdits")
        if it.side not in ("buy", "sell"):
            r.append("sens invalide")
        if not it.order_type_supported:
            r.append(f"type d'ordre {it.order_type} non supporté nativement sur ce marché")
        if not it.market_trading:
            r.append("marché non actif")
        if it.data_age_ms > L.max_stale_ms:
            r.append(f"donnée périmée ({it.data_age_ms} ms)")
        if it.is_entry:
            if it.spread_bps is None or it.spread_bps > L.max_spread_bps:
                r.append(f"spread trop large ou inconnu ({it.spread_bps})")
            if it.anomaly >= L.anomaly_refuse_threshold:
                r.append("suspicion d'anomalie/manipulation importante")
            if it.amount_quote > it.available_quote:
                r.append("fonds insuffisants")
            if it.amount_quote > L.max_order_eur:
                r.append("dépasse le montant max par ordre")
            if it.equity > 0 and it.position_after > it.equity * L.max_position_fraction:
                r.append("dépasse la limite par position")
            if it.equity > 0 and it.exposure_after > it.equity * L.max_total_exposure_fraction:
                r.append("dépasse l'exposition totale")
            if it.reinforcement_of_loser and not it.new_independent_thesis:
                r.append("renforcement d'une position perdante sans thèse indépendante (anti-martingale)")
        if it.amount_quote < it.min_order_quote:
            r.append("sous le minimum Bitvavo")
        return Verdict(not r, r)
