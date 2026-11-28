"""Apprentissage autonome EN OMBRE à partir des transactions gagnantes et perdantes (phase 8).

Principe (demandé par le propriétaire : apprendre de ses gains et de ses pertes, sans être excessivement bridé par la
peur de l'erreur, mais sans jamais promouvoir une amélioration non démontrée) :

1. APPRENDRE : pour chaque stratégie, à partir des allers-retours FERMÉS de la simulation (frais compris), estimer
   l'espérance nette par transaction avec un rétrécissement bayésien vers 0 (`prior_trades` transactions fictives
   neutres). Peu de transactions => peu d'effet ; beaucoup de transactions concordantes => effet plein.
2. PROPOSER : un multiplicateur de taille par stratégie, borné à [0,5 ; 1,5]. Une stratégie qui gagne reçoit PLUS,
   une stratégie qui perd reçoit moins : l'apprentissage peut AUGMENTER l'exposition, il ne fait pas que freiner.
   Le multiplicateur s'appliquerait À L'INTÉRIEUR des plafonds existants (Guardian, limites dures) : il ne les
   remplace jamais.
3. TESTER EN OMBRE : la proposition est figée (empreinte sha256, date). Elle n'est JAMAIS appliquée au compte simulé.
   Elle est évaluée uniquement sur les transactions dont l'ENTRÉE est POSTÉRIEURE à sa création (données inédites) :
   résultat « en ombre » = Σ multiplicateur × résultat net réel (la taille et les frais sont proportionnels).
4. PROMOUVOIR : critère fixé à l'avance — la borne basse de l'IC95 (bootstrap) de la différence journalière
   (ombre − réel) est > 0, ET la baisse maximale en ombre n'est pas pire de plus de 5 points que la réelle.
   Aucun nombre minimal ni maximal de transactions, aucun objectif quotidien : seule la preuve statistique compte
   (il faut au moins 2 jours distincts pour qu'un intervalle existe). La promotion passe par le registre
   (`backend/research/registry.py`) : « valide » au plus ; l'activation reste une décision du propriétaire.

Ne modifie aucune génération figée (v1, v2, v3, E1) et ne lit aucun historique de recherche : seulement le journal
de la simulation en cours. Le Short n'est pas concerné (spot uniquement).
"""
from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .bilan import Trade, max_drawdown

DAY = 86_400_000
M_MIN, M_MAX = 0.5, 1.5


@dataclass
class Policy:
    created_ms: int
    multipliers: dict[str, float]
    trained_on: int                       # nombre d'allers-retours d'entraînement
    prior_trades: float
    scale: float
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def sha256(self) -> str:
        body = {"created_ms": self.created_ms, "multipliers": self.multipliers, "trained_on": self.trained_on,
                "prior_trades": self.prior_trades, "scale": self.scale}
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {"created_ms": self.created_ms, "multipliers": self.multipliers, "trained_on": self.trained_on,
                "prior_trades": self.prior_trades, "scale": self.scale, "sha256": self.sha256}


def learn(trades: list[Trade], now_ms: int, prior_trades: float = 10.0, scale: float = 0.02) -> Policy:
    """Multiplicateur = 1 + espérance_rétrécie / scale, borné à [0,5 ; 1,5]. `scale` = espérance nette par
    transaction (2 %) qui justifierait le plafond. Seuls les allers-retours FERMÉS avant `now_ms` sont utilisés."""
    by: dict[str, list[float]] = defaultdict(list)
    used = 0
    for t in trades:
        if t.exit_ts <= now_ms:
            by[t.strategy].append(t.net_ret)
            used += 1
    mult = {}
    for s, rets in sorted(by.items()):
        shrunk = sum(rets) / (len(rets) + prior_trades)
        mult[s] = round(min(M_MAX, max(M_MIN, 1.0 + shrunk / scale)), 4)
    return Policy(now_ms, mult, used, prior_trades, scale)


def _daily(trades: list[Trade], weight: dict[str, float] | None) -> dict[int, float]:
    out: dict[int, float] = defaultdict(float)
    for t in trades:
        w = 1.0 if weight is None else weight.get(t.strategy, 1.0)
        out[t.exit_ts // DAY] += w * t.net_pnl_eur
    return out


def _bootstrap_lower(xs: list[float], n_boot: int = 2000, seed: int = 7, block: int = 5) -> float | None:
    if len(xs) < 2:
        return None
    rng = random.Random(seed)
    means = []
    k = max(1, min(block, len(xs)))
    for _ in range(n_boot):
        sample: list[float] = []
        while len(sample) < len(xs):
            start = rng.randrange(0, len(xs) - k + 1)
            sample.extend(xs[start:start + k])
        sample = sample[:len(xs)]
        means.append(sum(sample) / len(sample))
    means.sort()
    return means[int(0.025 * (n_boot - 1))]


def evaluate_shadow(policy: Policy, trades: list[Trade], initial_eur: float, dd_tolerance: float = 0.05) -> dict[str, Any]:
    """Évaluation sur données INÉDITES : seulement les allers-retours dont l'entrée est postérieure à la proposition."""
    unseen = [t for t in trades if t.entry_ts > policy.created_ms]
    real_d, shadow_d = _daily(unseen, None), _daily(unseen, policy.multipliers)
    days = sorted(set(real_d) | set(shadow_d))
    diff = [shadow_d.get(d, 0.0) - real_d.get(d, 0.0) for d in days]

    def curve(daily: dict[int, float]) -> list[float]:
        eq, out = initial_eur, [initial_eur]
        for d in days:
            eq += daily.get(d, 0.0)
            out.append(eq)
        return out
    mdd_real, mdd_shadow = max_drawdown(curve(real_d)), max_drawdown(curve(shadow_d))
    lower = _bootstrap_lower(diff)
    dd_ok = mdd_real is None or mdd_shadow is None or mdd_shadow >= mdd_real - dd_tolerance
    promoted = lower is not None and lower > 0 and dd_ok
    return {"politique_sha256": policy.sha256, "allers_retours_inedits": len(unseen), "jours": len(days),
            "resultat_reel_eur": round(sum(real_d.values()), 4), "resultat_ombre_eur": round(sum(shadow_d.values()), 4),
            "difference_journaliere_ic95_basse": None if lower is None else round(lower, 6),
            "baisse_max_reelle": mdd_real, "baisse_max_ombre": mdd_shadow, "baisse_max_acceptable": dd_ok,
            "promotion": {"promu": promoted,
                          "critere": "borne basse IC95 (bootstrap par blocs) de la différence journalière ombre − réel "
                                     "> 0 ET baisse maximale en ombre ≥ réelle − 5 points ; données inédites seulement",
                          "raison": ("critère satisfait" if promoted else
                                     "pas encore d'intervalle (moins de 2 jours inédits)" if lower is None else
                                     "amélioration non démontrée" if lower <= 0 else "baisse maximale aggravée")}}


def register_and_judge(registry: Any, policy: Policy, evaluation: dict[str, Any]) -> str:
    """Inscrit la proposition au registre (candidat -> simulation) puis la passe « valide » SEULEMENT si le critère
    est satisfait. Jamais « actif » ici : le laboratoire ne peut pas activer, l'activation est au propriétaire."""
    name, version = "apprentissage_dimensionnement", policy.sha256[:16]
    registry.register(name, version, policy.to_dict())
    cur = registry.get(name, version)
    if cur["status"] == "candidat":
        registry.set_status(name, version, "simulation", {"ombre": evaluation})
        cur = registry.get(name, version)
    if cur["status"] == "simulation" and evaluation["promotion"]["promu"]:
        registry.set_status(name, version, "valide", evaluation)
    return str(registry.get(name, version)["status"])


def policy_from_dict(p: dict[str, Any]) -> Policy:
    """Relit une proposition figée ; refuse toute proposition altérée après coup (empreinte sha256)."""
    pol = Policy(int(p["created_ms"]), dict(p["multipliers"]), int(p["trained_on"]), float(p["prior_trades"]),
                 float(p["scale"]))
    if pol.sha256 != p.get("sha256"):
        raise ValueError(f"proposition altérée ({p.get('sha256')})")
    return pol


def cycle(trades: list[Trade], stored: list[dict[str, Any]], registry: Any, initial_eur: float, now_ms: int,
          interval_days: float = 7.0) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Un cycle d'apprentissage : évalue chaque proposition figée sur l'inédit, l'inscrit au registre, puis fige une
    nouvelle proposition si la dernière a plus de `interval_days`. Renvoie (propositions à stocker, rapport).
    Les propositions déjà figées ne sont jamais réécrites (ajout seul)."""
    policies = [policy_from_dict(p) for p in stored]
    report = []
    for pol in policies:
        ev = evaluate_shadow(pol, trades, initial_eur)
        report.append({**pol.to_dict(), "evaluation": ev, "statut_registre": register_and_judge(registry, pol, ev)})
    if not policies or now_ms - policies[-1].created_ms >= interval_days * DAY:
        pol = learn(trades, now_ms)
        policies.append(pol)
        report.append({**pol.to_dict(), "evaluation": None, "statut_registre": "nouvelle proposition (en ombre)"})
    return [p.to_dict() for p in policies], report
