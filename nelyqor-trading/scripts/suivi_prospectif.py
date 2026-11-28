#!/usr/bin/env python3
"""Tableau de SUIVI PROSPECTIF (phase 7, point 8) — LECTURE SEULE, hors ligne, aucune clé, aucun ordre.

Lit, sans jamais les modifier :
- les journaux prospectifs `data/prospective/journal*.jsonl` (formats de scripts/prospective*.py, figés) ;
- les gels `reports/prospective/**/candidats_figes*.json` (départ prospectif, candidats, intervalle) ;
- la sortie des évaluations : journaux d'exécution (`--log-v1 … --log-e1`, blocs *_JSON_DEBUT/FIN) et/ou
  `resultats.json` de scripts/prospective_check.py (`--resultats`).

Produit un tableau par génération (v1, v2, v3, E1) et par candidat, en Markdown et en JSON : performance nette
depuis le départ, frais, risques (drawdown, exposition, levier effectif E1), références (cash, BTC passif,
exposition constante), fiabilité des données (créneaux attendus / prédits / manqués, divergences de rejeu, chaîne du
journal, disponibilité des événements) et critères figés à 6 et 12 mois (« non évaluable avant échéance »).

Règles : aucune recherche historique, aucune donnée inventée. Une valeur absente est écrite « non disponible » avec
sa raison ; aucun calcul statistique des critères n'est fait ici (il relève de l'évaluation formelle à échéance).

  python scripts/suivi_prospectif.py [--journal-dir data/prospective] [--resultats ../resultats.json]
         [--log-v1 ../v1.log --log-v2 ../v2.log --log-v3 ../v3.log --log-e1 ../e1.log]
         [--out-md suivi.md --out-json suivi.json] [--maintenant-ms N]
Le résumé Markdown est ajouté à GITHUB_STEP_SUMMARY si cette variable est définie. Code de sortie 0 sauf erreur
d'arguments : ce tableau INFORME, il ne remplace pas scripts/prospective_check.py (qui, lui, alerte).
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ND = "non disponible"
DAY = 86_400_000
STEPS = {"1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "8h": 28_800_000, "12h": 43_200_000, "1d": DAY}
GENERATIONS: dict[str, dict[str, str]] = {
    "v1": {"journal": "journal.jsonl", "gel": "candidats_figes.json", "tag": "PROSPECTIF",
           "statut": "figée (25/11/2026), suivi seulement"},
    "v2": {"journal": "journal_v2.jsonl", "gel": "v2/candidats_figes_v2.json", "tag": "PROSPECTIF_V2",
           "statut": "figée (phase 4), suivi seulement"},
    "v3": {"journal": "journal_v3.jsonl", "gel": "v3/candidats_figes_v3.json", "tag": "PROSPECTIF_V3",
           "statut": "figée (phase 5), suivi seulement"},
    "e1": {"journal": "journal_e1.jsonl", "gel": "e1/candidats_figes_e1.json", "tag": "PROSPECTIF_E1",
           "statut": "EXPÉRIMENTALE, NON DÉPLOYABLE (Short indisponible par l'API Bitvavo)"},
}
CONST_REF = "v3_ref_btc_exposition_constante"
CASH_REF_E1 = "e1_cash"


# ------------------------------------------------------------------------------------------------ lecture
def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def read_journal(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Lignes JSON du journal (ouverture en lecture seule) et lignes illisibles signalées (jamais corrigées)."""
    if not path.is_file():
        return [], []
    rows, bad = [], []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                v = json.loads(line)
            except ValueError:
                bad.append(f"ligne {n} illisible")
                continue
            if isinstance(v, dict):
                rows.append(v)
            else:
                bad.append(f"ligne {n} : objet attendu")
    return rows, bad


def verify_chain(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Même règle que Journal.verify (scripts/prospective_v2.py / v3) : prev = hash précédent, hash = sha256 du corps."""
    prev = "0" * 64
    for i, r in enumerate(rows):
        body = {k: v for k, v in r.items() if k != "hash"}
        if r.get("prev") != prev or r.get("hash") != sha(json.dumps(body, sort_keys=True, default=str).encode()):
            return {"intacte": False, "premiere_rupture_ligne": i + 1, "lignes": len(rows)}
        prev = str(r["hash"])
    return {"intacte": True, "lignes": len(rows), "tete": rows[-1]["hash"] if rows else None}


def extract_block(path: Path, tag: str) -> dict[str, Any] | None:
    """Bloc JSON entre {tag}_JSON_DEBUT et {tag}_JSON_FIN (le tag exact : PROSPECTIF ≠ PROSPECTIF_V2)."""
    if not path.is_file():
        return None
    cur: list[str] | None = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s == f"{tag}_JSON_DEBUT":
            cur = []
        elif s == f"{tag}_JSON_FIN" and cur is not None:
            try:
                v = json.loads(" ".join(cur))
            except ValueError:
                return None
            return v if isinstance(v, dict) else None
        elif cur is not None:
            cur.append(line)
    return None


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        v = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return v if isinstance(v, dict) else None


# ------------------------------------------------------------------------------------------------ utilitaires
def num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def add_months(ms: int, months: int) -> int:
    d = datetime.fromtimestamp(ms / 1000, UTC)
    y, m = divmod(d.month - 1 + months, 12)
    day = min(d.day, calendar.monthrange(d.year + y, m + 1)[1])
    return int(d.replace(year=d.year + y, month=m + 1, day=day).timestamp() * 1000)


def iso(ms: int | None) -> str | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def criteria(cutoff_ms: int | None, now_ms: int) -> dict[str, Any]:
    if cutoff_ms is None:
        return {"6_mois": "non évaluable : départ prospectif inconnu", "12_mois": "non évaluable : départ prospectif inconnu"}
    out: dict[str, Any] = {}
    for k, n in (("6_mois", 6), ("12_mois", 12)):
        due = add_months(cutoff_ms, n)
        out[k] = (f"non évaluable avant échéance ({iso(due)[:10]})" if now_ms < due  # type: ignore[index]
                  else f"échéance atteinte ({iso(due)[:10]}) : évaluation formelle requise, non calculée par ce tableau")  # type: ignore[index]
        out[f"echeance_{k}"] = iso(due)
    return out


def expected_slots(cutoff_ms: int | None, last_ts: int | None, step: int | None) -> int | None:
    """Bougies du départ à la dernière bougie évaluée (None si inconnue : jamais estimée sans donnée)."""
    if cutoff_ms is None or last_ts is None or not step:
        return None
    return 0 if last_ts < cutoff_ms else (last_ts - cutoff_ms) // step + 1


def days_expected(cutoff_ms: int | None, now_ms: int) -> int | None:
    """Une évaluation par jour UTC est prévue (planification quotidienne) depuis le jour du départ inclus."""
    if cutoff_ms is None or now_ms < cutoff_ms:
        return 0 if cutoff_ms is not None else None
    return int(now_ms // DAY - cutoff_ms // DAY) + 1


# ------------------------------------------------------------------------------------------------ générations
def gen_v1(rows: list[dict[str, Any]], ev: dict[str, Any] | None, gel: dict[str, Any] | None, now: int) -> dict[str, Any]:
    cutoff = int(gel["cutoff_ms"]) if gel and gel.get("cutoff_ms") else None
    ids = [c["id"] for c in (gel or {}).get("candidats", [])] or sorted({str(r.get("candidat")) for r in rows})
    last: dict[str, dict[str, Any]] = {}
    for r in rows:
        last[str(r.get("candidat"))] = r
    exec_days = {int(r["execution_ts"]) // DAY for r in rows if num(r.get("execution_ts")) is not None and int(r["execution_ts"]) >= (cutoff or 0)}
    expected = days_expected(cutoff, now)
    cands = {}
    for cid in ids:
        lr = last.get(cid)
        res = (lr or {}).get("resultat") or {}
        evr = ((ev or {}).get("resultats") or {}).get(cid) or {}
        src = res if res else evr
        stat = str(src.get("statut", ND if not src else "ok"))
        cands[cid] = {
            "statut": stat,
            "performance_nette": num(src.get("rendement_net")),
            "source_performance": "journal v1 (dernière évaluation, simulation sur bougies ≥ départ)" if res else
            ("sortie d'évaluation v1" if evr else ND),
            "frais_eur": num(src.get("frais_eur")),
            "drawdown_max": num(src.get("max_drawdown")),
            "exposition": ND + " (non journalisée en v1)",
            "transactions": src.get("trades"),
            "btc_passif": num(src.get("achat_passif_btc")),
            "bougies_prospectives": src.get("bougies"),
            "code_modifie_depuis_gel": (lr or {}).get("code_modifie_depuis_gel"),
        }
    return {"candidats": cands, "fiabilite": {
        "type_creneau": "évaluation quotidienne (v1 n'enregistre pas de prédiction notée)",
        "creneaux_attendus": expected, "creneaux_realises": len(exec_days),
        "creneaux_manques": None if expected is None else max(0, expected - len(exec_days)),
        "chaine": "non chaîné (format v1)", "divergences_rejeu": "sans objet (pas de rejeu en v1)"}}


def gen_v2(rows: list[dict[str, Any]], ev: dict[str, Any] | None, gel: dict[str, Any] | None, now: int) -> dict[str, Any]:
    ids = [c["id"] for c in (gel or {}).get("candidats", [])] or sorted({str(r.get("candidat")) for r in rows})
    cands, tot_exp, tot_pred, tot_miss, known = {}, 0, 0, 0, True
    for cid in ids:
        preds = [r for r in rows if r.get("type") == "prediction" and r.get("candidat") == cid]
        scores = [r.get("score") or {} for r in rows if r.get("type") == "score" and r.get("candidat") == cid]
        evr = ((ev or {}).get("resultats") or {}).get(cid) or {}
        retro = evr.get("retrospectif") or {}
        pinfo = evr.get("predictions") or {}
        net_sc = [num(s.get("rendement_net_aller_retour_taker")) for s in scores]
        gross_sc = [num(s.get("rendement_brut")) for s in scores]
        btc_sc = [num(s.get("btc")) for s in scores]
        cum_net = _compound(net_sc) if scores and None not in net_sc else None
        cum_btc = _compound([b or 0.0 for b in btc_sc]) if scores else None
        fees_frac = sum((g or 0.0) - (n or 0.0) for g, n in zip(gross_sc, net_sc, strict=True)) if scores else None
        expos = [num((r.get("prediction") or {}).get("exposition")) for r in preds]
        expos_ok = [e for e in expos if e is not None]
        exp_slots = pinfo.get("creneaux_attendus")
        missed = pinfo.get("predictions_manquees")
        if exp_slots is None:
            known = False
        else:
            tot_exp += int(exp_slots)
            tot_miss += int(missed or 0)
        tot_pred += len(preds)
        cands[cid] = {
            "statut": str(evr.get("statut", "ok" if evr else ND + " (sortie d'évaluation absente)")),
            "performance_nette": num(retro.get("rendement_net")),
            "source_performance": "sortie d'évaluation v2 (rétrospectif sur bougies ≥ départ, ce n'est PAS une prédiction)"
            if retro.get("rendement_net") is not None else ND,
            "predictions_notees_net_cumule": cum_net,
            "frais_eur": num(retro.get("frais_eur")),
            "frais_predictions_notees": fees_frac,
            "drawdown_max": num(retro.get("max_drawdown")),
            "exposition_moyenne_predite": sum(expos_ok) / len(expos_ok) if expos_ok else None,
            "exposition_derniere_prediction": expos[-1] if expos else None,
            "transactions": retro.get("trades"),
            "btc_passif": cum_btc, "source_btc": "cumul BTC sur les horizons des prédictions notées" if scores else ND,
            "predictions": len(preds), "notees": len(scores),
            "creneaux_attendus": exp_slots, "creneaux_manques": missed,
        }
    return {"candidats": cands, "fiabilite": {
        "type_creneau": "une prédiction par période de rééquilibrage",
        "creneaux_attendus": tot_exp if known and ids else None, "creneaux_predits": tot_pred,
        "creneaux_manques": tot_miss if known and ids else None,
        "divergences_rejeu": "sans objet (pas de rejeu en v2)"}}


def _compound(xs: list[float | None]) -> float | None:
    p = 1.0
    for x in xs:
        if x is None:
            return None
        p *= 1 + x
    return p - 1


def gen_v3(rows: list[dict[str, Any]], ev: dict[str, Any] | None, gel: dict[str, Any] | None, now: int) -> dict[str, Any]:
    cutoff = int(gel["cutoff_ms"]) if gel and gel.get("cutoff_ms") else None
    cmeta = {c["id"]: c for c in (gel or {}).get("candidats", [])}
    ids = list(cmeta) or sorted({str(r.get("candidat")) for r in rows})
    cands, tot_exp, tot_pred, tot_miss = {}, 0, 0, 0
    exp_known = True
    for cid in ids:
        preds = [r for r in rows if r.get("type") == "prediction" and r.get("candidat") == cid]
        evr = ((ev or {}).get("resultats") or {}).get(cid) or {}
        step = STEPS.get(str(cmeta.get(cid, {}).get("interval", "")), None) or num(evr.get("pas_ms"))
        by_acc: dict[str, dict[str, Any]] = {}
        slots: dict[str, set[int]] = {}
        for r in preds:
            acc = str(r.get("compte"))
            by_acc[acc] = r
            slots.setdefault(acc, set()).add(int(r.get("bougie_ts", 0)))
        eqs, expos = [], []
        for r in by_acc.values():
            et = r.get("etat") or {}
            e, c = num(et.get("equite")), num(et.get("tresorerie"))
            if e is not None:
                eqs.append(e)
                if c is not None and e > 0:
                    expos.append((e - c) / e)
        last_ts = max((int(r.get("bougie_ts", 0)) for r in preds), default=None)
        per_acc = evr.get("creneaux_attendus_par_compte")
        if per_acc is None:
            per_acc = expected_slots(cutoff, last_ts, int(step) if step else None)
        n_acc = evr.get("comptes") or len(by_acc)
        exp = None if per_acc is None else int(per_acc) * int(n_acc)
        predicted = sum(len(s) for s in slots.values())
        if exp is None:
            exp_known = False
        else:
            tot_exp += exp
            tot_miss += max(0, exp - predicted)
        tot_pred += predicted
        perf = num(evr.get("rendement_moyen"))
        cands[cid] = {
            "statut": str(evr.get("statut", "ok" if evr else ND + " (sortie d'évaluation absente)")),
            "performance_nette": perf if perf is not None else (sum(eqs) / len(eqs) / 100 - 1 if eqs else None),
            "source_performance": "sortie d'évaluation v3 (moyenne des comptes)" if perf is not None else
            ("journal v3 : équité de la dernière prédiction par compte (100 € au départ)" if eqs else ND),
            "frais_eur": ND + " (non publiés par l'évaluation v3)",
            "drawdown_max": num(evr.get("dd_median")), "drawdown_note": "médiane des comptes",
            "exposition": sum(expos) / len(expos) if expos else None,
            "transactions": evr.get("trades_cumules"),
            "comptes": n_acc, "creneaux_attendus": exp, "creneaux_predits": predicted,
            "creneaux_manques": None if exp is None else max(0, exp - predicted),
        }
    rej = (ev or {}).get("rejeu") or {}
    return {"candidats": cands, "fiabilite": {
        "type_creneau": "une décision complète par bougie 4 h et par compte",
        "creneaux_attendus": tot_exp if exp_known and ids else None, "creneaux_predits": tot_pred,
        "creneaux_manques": tot_miss if exp_known and ids else None,
        "divergences_rejeu": rej.get("n_divergences", ND + " (sortie d'évaluation absente)"),
        "divergences_exemples": (rej.get("divergences") or [])[:5]},
        "btc_passif": num((ev or {}).get("btc_achat_passif_depuis_depart"))}


def gen_e1(rows: list[dict[str, Any]], ev: dict[str, Any] | None, gel: dict[str, Any] | None, now: int) -> dict[str, Any]:
    cutoff = int(gel["cutoff_ms"]) if gel and gel.get("cutoff_ms") else None
    ids = [c["id"] for c in (gel or {}).get("candidats", [])] or sorted({str(r.get("candidat")) for r in rows})
    cands, tot_exp, tot_pred, exp_known = {}, 0, 0, True
    blocked_preds = 0
    for cid in ids:
        preds = [r for r in rows if r.get("type") == "prediction" and r.get("candidat") == cid]
        evr = ((ev or {}).get("resultats") or {}).get(cid) or {}
        last = preds[-1] if preds else None
        dec = (last or {}).get("decision") or {}
        pos = dec.get("positions") or {}
        blocked_preds += sum(1 for r in preds if ((r.get("decision") or {}).get("groupes_bloquants")) or r.get("blocages_historique"))
        slots = {int(r.get("bougie_ts", 0)) for r in preds}
        exp = evr.get("creneaux_4h_depuis_depart")
        if exp is None:
            exp = expected_slots(cutoff, max(slots) if slots else None, STEPS["4h"])
        if exp is None:
            exp_known = False
        else:
            tot_exp += int(exp)
        tot_pred += len(slots)
        perf = num(evr.get("rendement"))
        eq_j = num(dec.get("equite"))
        cands[cid] = {
            "statut": str(evr.get("statut", "ok" if evr else ND + " (sortie d'évaluation absente)")),
            "performance_nette": perf if perf is not None else (eq_j / 100 - 1 if eq_j is not None else None),
            "source_performance": "sortie d'évaluation E1" if perf is not None else
            ("journal E1 : équité de la dernière décision (100 € au départ)" if eq_j is not None else ND),
            "frais_eur": ND + " (non publiés par l'évaluation E1)",
            "drawdown_max": num(evr.get("dd")),
            "levier_effectif_max": num(evr.get("levier_effectif_max")),
            "exposition": ", ".join(f"{m} {p.get('sens')}" for m, p in sorted(pos.items())) or ("aucune position" if last else ND),
            "transactions": evr.get("trades"),
            "decisions_modifiees_par_evenements": evr.get("decisions_modifiees_par_evenements"),
            "creneaux_attendus": exp, "creneaux_predits": len(slots),
            "creneaux_manques": None if exp is None else max(0, int(exp) - len(slots)),
        }
    rej = (ev or {}).get("rejeu") or {}
    evd = (ev or {}).get("evenements_disponibles")
    return {"candidats": cands, "fiabilite": {
        "type_creneau": "une décision par bougie 4 h (environ 1 sur 6 enregistrée : une évaluation par jour)",
        "creneaux_attendus": tot_exp if exp_known and ids else None, "creneaux_predits": tot_pred,
        "creneaux_manques": max(0, tot_exp - tot_pred) if exp_known and ids else None,
        "divergences_rejeu": rej.get("n_divergences", ND + " (sortie d'évaluation absente)"),
        "divergences_exemples": (rej.get("divergences") or [])[:5],
        "evenements_disponibles": ND + " (sortie d'évaluation absente)" if evd is None else bool(evd),
        "groupes_evenementiels": (ev or {}).get("groupes_evenementiels"),
        "predictions_avec_blocage_enregistre": blocked_preds},
        "btc_passif": num((ev or {}).get("btc_achat_passif_depuis_depart"))}


BUILDERS = {"v1": gen_v1, "v2": gen_v2, "v3": gen_v3, "e1": gen_e1}


# ------------------------------------------------------------------------------------------------ assemblage
def build(journal_dir: Path, reports_dir: Path, evals: dict[str, dict[str, Any] | None], now_ms: int) -> dict[str, Any]:
    out: dict[str, Any] = {"genere_utc": iso(now_ms), "lecture_seule": True,
                           "avertissement": "Suivi informatif en SIMULATION. Aucune performance n'est une validation avant "
                                            "les échéances figées de 6 et 12 mois. Aucune donnée n'est inventée : "
                                            "« non disponible » signifie absente des journaux et des sorties lues.",
                           "generations": {}}
    for g, meta in GENERATIONS.items():
        jp = journal_dir / meta["journal"]
        rows, bad = read_journal(jp)
        gel = read_json(reports_dir / meta["gel"])
        ev = evals.get(g)
        cutoff = int(gel["cutoff_ms"]) if gel and gel.get("cutoff_ms") else None
        doc: dict[str, Any] = {
            "statut": meta["statut"], "depart_utc": iso(cutoff), "gel_present": gel is not None,
            "journal": {"fichier": meta["journal"], "present": jp.is_file(), "lignes": len(rows), "lignes_illisibles": bad},
            "evaluation": {"presente": ev is not None, "validite": (ev or {}).get("validite", ND),
                           "execution_utc": (ev or {}).get("execution_utc")},
            "criteres_figes": criteria(cutoff, now_ms),
        }
        ts = [int(r["execution_ts"]) for r in rows if num(r.get("execution_ts")) is not None]
        doc["journal"]["derniere_execution_utc"] = iso(max(ts)) if ts else None
        doc["journal"]["age_heures"] = round((now_ms - max(ts)) / 3_600_000, 1) if ts else None
        if g != "v1":
            doc["journal"]["chaine"] = verify_chain(rows) if rows else {"intacte": None, "lignes": 0, "note": "journal vide ou absent"}
        if not rows and ev is None:
            doc["donnees"] = "aucune donnée prospective (journal absent ou vide, aucune sortie d'évaluation)"
        if cutoff is not None and now_ms < cutoff:
            doc["donnees"] = f"départ prospectif pas encore atteint ({iso(cutoff)})"
        body = BUILDERS[g](rows, ev, gel, now_ms)
        doc.update(body)
        out["generations"][g] = doc
    out["references"] = references(out["generations"])
    return out


def references(gens: dict[str, Any]) -> dict[str, Any]:
    """Références : cash (0 % par définition), BTC passif (publié par chaque évaluation), exposition constante
    (candidat de référence v3, même départ que E1 ; départ différent pour v1/v2 : non comparable)."""
    v3c = gens.get("v3", {}).get("candidats", {})
    const = (v3c.get(CONST_REF) or {}).get("performance_nette")
    e1c = gens.get("e1", {}).get("candidats", {})
    out = {}
    for g, d in gens.items():
        btc = d.get("btc_passif")
        if g in ("v1", "v2"):
            vals = [c.get("btc_passif") for c in d.get("candidats", {}).values() if c.get("btc_passif") is not None]
            btc = vals[0] if g == "v1" and vals else None
        same_start = d.get("depart_utc") == gens.get("v3", {}).get("depart_utc")
        out[g] = {"cash": 0.0, "cash_note": "100 € conservés : 0 % (hors inflation)",
                  "btc_passif": btc if btc is not None else (ND if g != "v2" else "voir chaque candidat (horizons des prédictions notées)"),
                  "exposition_constante": const if (same_start and const is not None) else
                  (ND if const is None else "non comparable (départ différent de v3)")}
        if g == "e1":
            out[g]["cash_moteur_e1"] = (e1c.get(CASH_REF_E1) or {}).get("performance_nette")
    return out


# ------------------------------------------------------------------------------------------------ rendu
def f_pct(x: Any) -> str:
    v = num(x)
    return "—" if v is None else f"{v * 100:+.2f} %"


def f_any(x: Any) -> str:
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "oui" if x else "NON"
    if isinstance(x, float):
        return f"{x:.4g}"
    return str(x).replace("|", "/")


def markdown(doc: dict[str, Any]) -> str:
    L = ["## Suivi prospectif (lecture seule)", "", f"Généré le {doc['genere_utc']}. {doc['avertissement']}", "",
         "« — » = non disponible dans les journaux ou sorties lus (jamais estimé).", ""]
    for g, d in doc["generations"].items():
        ref = doc["references"].get(g, {})
        L += [f"### Génération {g.upper()} — {d['statut']}", ""]
        j = d["journal"]
        ch = j.get("chaine")
        L.append(f"- Départ : {d['depart_utc'] or '—'} · évaluation : {f_any(d['evaluation']['validite'])} · journal : "
                 f"{j['lignes']} ligne(s){' (absent)' if not j['present'] else ''}, dernière exécution {j['derniere_execution_utc'] or '—'}")
        if ch is not None:
            L.append(f"- Chaîne du journal : {'intacte' if ch.get('intacte') else ('—' if ch.get('intacte') is None else 'ROMPUE (ligne ' + str(ch.get('premiere_rupture_ligne')) + ')')}")
        if j["lignes_illisibles"]:
            L.append(f"- ⚠ Lignes illisibles : {', '.join(j['lignes_illisibles'][:5])}")
        if d.get("donnees"):
            L.append(f"- **{d['donnees']}**")
        fi = d["fiabilite"]
        L.append(f"- Fiabilité : {fi['type_creneau']} — attendus {f_any(fi.get('creneaux_attendus'))}, "
                 f"prédits/réalisés {f_any(fi.get('creneaux_predits', fi.get('creneaux_realises')))}, manqués {f_any(fi.get('creneaux_manques'))} ; "
                 f"divergences de rejeu : {f_any(fi.get('divergences_rejeu'))}")
        if "evenements_disponibles" in fi:
            L.append(f"- Événements : disponibles {f_any(fi['evenements_disponibles'])}, groupes {f_any(fi.get('groupes_evenementiels'))}, "
                     f"prédictions avec blocage enregistré {fi.get('predictions_avec_blocage_enregistre')}")
        L.append(f"- Références : cash {f_pct(ref.get('cash'))} · BTC passif {f_pct(ref.get('btc_passif')) if num(ref.get('btc_passif')) is not None else f_any(ref.get('btc_passif'))}"
                 f" · exposition constante {f_pct(ref.get('exposition_constante')) if num(ref.get('exposition_constante')) is not None else f_any(ref.get('exposition_constante'))}")
        cr = d["criteres_figes"]
        L += [f"- Critères figés : 6 mois — {cr['6_mois']} ; 12 mois — {cr['12_mois']}", ""]
        cands = d["candidats"]
        if not cands:
            L += ["Aucun candidat lisible (gel absent).", ""]
            continue
        lev = g == "e1"
        L.append("| Candidat | Statut | Perf. nette | Frais | Drawdown max | Exposition | " + ("Levier eff. max | " if lev else "")
                 + "Transactions | Créneaux att./prédits/manqués |")
        L.append("|---|---|---|---|---|---|" + ("---|" if lev else "") + "---|---|")
        for cid, c in cands.items():
            expo = c.get("exposition", c.get("exposition_moyenne_predite"))
            fees = c.get("frais_eur")
            L.append(f"| `{cid}` | {f_any(c.get('statut'))} | {f_pct(c.get('performance_nette'))} | "
                     f"{(f'{fees:.2f} €' if isinstance(fees, float) else f_any(fees))} | {f_pct(c.get('drawdown_max'))} | "
                     f"{f_pct(expo) if isinstance(expo, float) else f_any(expo)} | "
                     + (f"{f_any(c.get('levier_effectif_max'))} | " if lev else "")
                     + f"{f_any(c.get('transactions'))} | {f_any(c.get('creneaux_attendus'))} / "
                     f"{f_any(c.get('creneaux_predits', c.get('predictions')))} / {f_any(c.get('creneaux_manques'))} |")
        L.append("")
    return "\n".join(L) + "\n"


def load_evals(a: argparse.Namespace) -> dict[str, dict[str, Any] | None]:
    res = read_json(Path(a.resultats)) if a.resultats else None
    out: dict[str, dict[str, Any] | None] = {}
    for g, meta in GENERATIONS.items():
        log = getattr(a, f"log_{g}")
        doc = extract_block(Path(log), meta["tag"]) if log else None
        if doc is None and res is not None and isinstance(res.get(g), dict):
            doc = res[g]
        out[g] = doc
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--journal-dir", default=str(ROOT / "data" / "prospective"))
    ap.add_argument("--reports-dir", default=str(ROOT / "reports" / "prospective"))
    ap.add_argument("--resultats", default="", help="resultats.json de scripts/prospective_check.py")
    for g in GENERATIONS:
        ap.add_argument(f"--log-{g}", default="", help=f"journal d'exécution de l'évaluation {g}")
    ap.add_argument("--out-md", default="")
    ap.add_argument("--out-json", default="")
    ap.add_argument("--maintenant-ms", type=int, default=0)
    a = ap.parse_args(argv)
    now = a.maintenant_ms or int(time.time() * 1000)
    jd = Path(a.journal_dir).resolve()
    for p in (a.out_md, a.out_json):
        if p and Path(p).resolve().is_relative_to(jd):
            ap.error(f"refus : {p} est dans le dossier des journaux (lecture seule)")
    doc = build(Path(a.journal_dir), Path(a.reports_dir), load_evals(a), now)
    md = markdown(doc)
    for p, text in ((a.out_md, md), (a.out_json, json.dumps(doc, ensure_ascii=False, indent=1, default=str))):
        if p:
            Path(p).parent.mkdir(parents=True, exist_ok=True)
            Path(p).write_text(text, encoding="utf-8")
    sys.stdout.write(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
