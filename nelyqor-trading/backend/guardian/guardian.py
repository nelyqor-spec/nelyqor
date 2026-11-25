"""GUARDIAN : machine d'états et droit de veto sur toute intention d'ordre.

États : SIMULATION (défaut), PAUSE_NOUVELLES_ENTREES, RECONCILIATION, REEL_AUTORISE, INCIDENT.
- Le mode réel exige : drapeau serveur NELYQOR_REAL_TRADING_ALLOWED=true, clés présentes,
  activation authentifiée du propriétaire (phrase de confirmation), tests critiques OK,
  et ne peut JAMAIS être demandé par le rôle LAB.
- Pause ≠ liquidation : la pause bloque les nouvelles entrées, les sorties/protections restent permises,
  y compris pour des positions RÉELLES lorsque le réel n'est plus autorisé (on ne laisse jamais une
  position réelle sans possibilité de protection ou de sortie).
- Pas d'arrêt définitif fondé sur la seule perte cumulée (préférence explicite du propriétaire).

Intégrité (phase 2) : l'état, l'état de reprise et la date d'activation sont SIGNÉS (HMAC-SHA256) avec une
clé que seul le moteur lit (`secrets/engine_state_key`). Une écriture directe dans la base par un autre
processus (API compromise, laboratoire, opérateur) est détectée : l'état lu devient INCIDENT.
Sans clé configurée, le mode réel est refusé (pré-requis « integrite_etat »).
"""
from __future__ import annotations

import hashlib
import hmac
import json
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
CONDITIONAL_TYPES = {"stopLoss", "stopLossLimit", "takeProfit", "takeProfitLimit"}

# Transitions autorisées (source -> destinations)
TRANSITIONS: dict[State, set[State]] = {
    State.SIMULATION: {State.PAUSE, State.RECONCILIATION, State.REAL, State.INCIDENT},
    State.PAUSE: {State.SIMULATION, State.RECONCILIATION, State.REAL, State.INCIDENT},
    State.RECONCILIATION: {State.PAUSE, State.SIMULATION, State.REAL, State.INCIDENT},
    State.REAL: {State.PAUSE, State.RECONCILIATION, State.INCIDENT, State.SIMULATION},
    State.INCIDENT: {State.PAUSE, State.RECONCILIATION, State.SIMULATION},
}
SIGNED_KEYS = ("guardian_state", "resume_state", "real_activated_ms", "auth_revoked_ms")


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
    # anti-vente dupliquée : quantité de base de CET ordre de vente, et quantité encore libre
    # (position - ventes déjà actives, ordres conditionnels compris)
    sell_amount_base: Decimal | None = None
    base_free_for_sale: Decimal | None = None
    min_order_base: Decimal = ZERO


@dataclass
class Verdict:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


class Guardian:
    def __init__(self, db: Database, limits: HardLimits, real_server_flag: bool, role: str = "engine",
                 now_ms: Any = None, state_key: bytes | None = None) -> None:
        self.db = db
        self.limits = limits
        self.real_server_flag = real_server_flag
        self.role = role
        self._now = now_ms or (lambda: 0)
        self._key = state_key
        self.tamper_detected = False

    # --- stockage signé
    def _sig(self, key: str, value: Any) -> str:
        assert self._key is not None
        msg = json.dumps([key, value], sort_keys=True, default=str).encode()
        return hmac.new(self._key, msg, hashlib.sha256).hexdigest()

    @property
    def integrity_enabled(self) -> bool:
        return self._key is not None

    def _put(self, key: str, value: Any) -> None:
        rec: dict[str, Any] = {"v": value}
        if self._key is not None:
            rec["sig"] = self._sig(key, value)
        self.db.kv_set(key, rec, self._now())

    def _get(self, key: str, default: Any) -> Any:
        raw = self.db.kv_get(key)
        if raw is None:
            return default
        if not isinstance(raw, dict) or "v" not in raw:
            return self._tampered(key, default)
        if self._key is not None and not hmac.compare_digest(str(raw.get("sig", "")), self._sig(key, raw["v"])):
            return self._tampered(key, default)
        return raw["v"]

    def _tampered(self, key: str, default: Any) -> Any:
        self.tamper_detected = True
        if key == "guardian_state":
            return State.INCIDENT.value
        return default

    def verify_integrity(self) -> bool:
        """Vérifie toutes les clés signées ; enregistre un incident si une falsification est détectée."""
        self.tamper_detected = False
        for k in SIGNED_KEYS:
            self._get(k, None)
        if self.tamper_detected and self.role == "engine":
            self._put("guardian_state", State.INCIDENT.value)
            self.db.kv_set("guardian_reason", "falsification de l'état détectée (signature invalide)", self._now())
            self._audit("guardian", "falsification_etat", False, {})
        return not self.tamper_detected

    # --- état
    @property
    def state(self) -> State:
        return State(self._get("guardian_state", State.SIMULATION.value))

    @property
    def resume_state(self) -> State:
        return State(self._get("resume_state", State.SIMULATION.value))

    @property
    def revoked_ms(self) -> int:
        """Instant de la dernière RÉVOCATION (retour en simulation, arrêt d'urgence) : toute signature du
        propriétaire antérieure ou égale est refusée, même encore dans sa fenêtre de validité."""
        return int(self._get("auth_revoked_ms", 0) or 0)

    def _revoke(self) -> None:
        self._put("auth_revoked_ms", max(self._now(), self.revoked_ms))

    @property
    def is_real(self) -> bool:
        return self.state == State.REAL

    def init_state(self) -> None:
        if self.db.kv_get("guardian_state") is None:
            self._put("guardian_state", State.SIMULATION.value)

    def _set_state(self, st: State, reason: str) -> None:
        self._put("guardian_state", st.value)
        self.db.kv_set("guardian_reason", reason, self._now())

    def _audit(self, actor: str, action: str, ok: bool, details: dict[str, Any]) -> None:
        self.db.exec("INSERT INTO audit(ts,actor,action,ip,ok,details) VALUES(?,?,?,?,?,?)",
                     (self._now(), actor, action, details.pop("ip", None), int(ok), json.dumps(details, default=str)))

    def _require_engine(self) -> None:
        if self.role != "engine":
            raise PermissionError(f"Le rôle {self.role} ne peut pas changer l'état de fonctionnement")

    def transition(self, to: State, actor: str, reason: str) -> None:
        self._require_engine()
        frm = self.state
        if to == frm:
            return
        if to == State.REAL:
            raise PermissionError("Utiliser activate_real() : activation explicite et distincte requise")
        if to not in TRANSITIONS[frm]:
            raise ValueError(f"Transition interdite {frm.value} -> {to.value}")
        self._set_state(to, reason)
        self._audit(actor, f"transition {frm.value}->{to.value}", True, {"reason": reason})

    def enter_reconciliation(self, reason: str) -> None:
        self._require_engine()
        cur = self.state
        if cur not in (State.PAUSE, State.RECONCILIATION, State.INCIDENT):
            self._put("resume_state", cur.value)
        self._set_state(State.RECONCILIATION, reason)

    def activate_real(self, actor: str, confirmation: str, password_ok: bool, preflight: dict[str, bool]) -> Verdict:
        reasons: list[str] = []
        if self.role != "engine":
            reasons.append(f"rôle {self.role} non autorisé")
        if not self.real_server_flag:
            reasons.append("drapeau serveur NELYQOR_REAL_TRADING_ALLOWED absent")
        if not self.integrity_enabled:
            reasons.append("clé d'intégrité de l'état absente (secrets/engine_state_key)")
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
            self._set_state(State.REAL, "activation explicite du propriétaire")
            self._put("real_activated_ms", self._now())
        return Verdict(ok, reasons)

    def deactivate_real(self, actor: str) -> None:
        """Retour en simulation : aucune nouvelle entrée réelle ; les positions réelles restantes
        continuent d'être protégées et peuvent être vendues (ordres protecteurs)."""
        self._require_engine()
        self._put("resume_state", State.SIMULATION.value)
        self._put("real_activated_ms", None)
        self._revoke()
        self._set_state(State.SIMULATION, "retour manuel en simulation")
        self._audit(actor, "desactivation_reel", True, {})

    def emergency_stop(self, actor: str) -> None:
        """Bloque immédiatement les nouvelles entrées. N'annule PAS les protections, ne liquide pas."""
        self._require_engine()
        prev = self.state
        if prev not in (State.PAUSE, State.INCIDENT, State.RECONCILIATION):
            self._put("resume_state", prev.value)
        self._revoke()
        self._set_state(State.PAUSE, "arrêt d'urgence")
        self._audit(actor, "arret_urgence", True, {"from": prev.value})

    def resume(self, actor: str, checks: dict[str, bool]) -> Verdict:
        self._require_engine()
        failed = [k for k, ok in checks.items() if not ok]
        if failed:
            self._audit(actor, "reprise", False, {"failed": failed})
            return Verdict(False, [f"contrôle échoué : {k}" for k in failed])
        target = self.resume_state
        if target == State.REAL and (not self.real_server_flag or self._get("real_activated_ms", None) is None):
            target = State.SIMULATION
        self._set_state(target, "reprise après contrôles")
        self._audit(actor, "reprise", True, {"to": target.value})
        return Verdict(True, [])

    def pause(self, actor: str, reason: str) -> None:
        if self.state not in (State.PAUSE, State.INCIDENT):
            if self.state != State.RECONCILIATION:
                self._put("resume_state", self.state.value)
            self._require_engine()
            self._set_state(State.PAUSE, reason)
            self._audit(actor, "pause", True, {"reason": reason})

    def incident(self, reason: str) -> None:
        self._require_engine()
        if self.state != State.INCIDENT:
            if self.state not in (State.PAUSE, State.RECONCILIATION):
                self._put("resume_state", self.state.value)
            self._set_state(State.INCIDENT, reason)
            self._audit("guardian", "incident", True, {"reason": reason})

    # --- veto
    def check(self, it: IntentCheck) -> Verdict:
        L = self.limits
        r: list[str] = []
        st = self.state
        conditional = it.order_type in CONDITIONAL_TYPES
        if self.role != "engine":
            r.append(f"rôle {self.role} sans droit d'exécution")
        if self.tamper_detected:
            r.append("intégrité de l'état compromise")
        if it.mode == "real" and st != State.REAL and not it.is_protective:
            r.append(f"mode réel non autorisé (état {st.value})")
        if it.is_entry and it.is_protective:
            r.append("incohérence : une entrée ne peut pas être protectrice")
        if it.is_entry and st in (State.PAUSE, State.RECONCILIATION, State.INCIDENT):
            r.append(f"nouvelles entrées bloquées ({st.value})")
        if st == State.INCIDENT and not it.is_protective:
            r.append("incident : seules les sorties de protection sont permises")
        if it.margin or it.leverage:
            r.append("marge/levier interdits")
        if it.side not in ("buy", "sell"):
            r.append("sens invalide")
        if conditional and it.side != "sell":
            r.append("ordres conditionnels autorisés uniquement en vente (protection)")
        if not it.order_type_supported:
            r.append(f"type d'ordre {it.order_type} non supporté nativement sur ce marché")
        if not it.market_trading:
            r.append("marché non actif")
        # Poser un stop hébergé sur des données anciennes reste prudent ; acheter/vendre au marché non.
        if it.data_age_ms > L.max_stale_ms and not conditional:
            r.append(f"donnée périmée ({it.data_age_ms} ms)")
        if it.side == "sell":
            if it.sell_amount_base is not None and it.base_free_for_sale is not None \
                    and it.sell_amount_base > it.base_free_for_sale:
                r.append("vente supérieure à la quantité libre (vente dupliquée ou déjà réservée)")
            if it.sell_amount_base is not None and it.sell_amount_base < it.min_order_base:
                r.append("sous le minimum Bitvavo (base)")
        if it.is_entry:
            if it.side != "buy":
                r.append("une entrée est un achat (pas de vente à découvert)")
            if it.equity <= 0:
                r.append("equity inconnue ou nulle : limites non évaluables")
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
        if it.amount_quote < it.min_order_quote and not conditional:
            r.append("sous le minimum Bitvavo")
        return Verdict(not r, r)
