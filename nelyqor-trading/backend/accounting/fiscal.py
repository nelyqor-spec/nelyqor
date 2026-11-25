"""Aide fiscale France (particuliers, régime de l'art. 150 VH bis CGI) — AIDE et EXPORT, pas de déclaration.

- Seules les cessions contre monnaie ayant cours légal (EUR) ou contre biens/services sont des faits
  générateurs ; les échanges crypto-crypto sans soulte bénéficient d'un sursis.
- Seuil d'exonération : total annuel des PRIX DE CESSION <= 305 EUR (pas le bénéfice).
- Plus-value d'une cession : PV = C - (PTA x C / V), où C = prix de cession (net de frais),
  PTA = prix total d'acquisition du portefeuille (net des fractions déjà imputées),
  V = valeur globale du portefeuille juste avant la cession.
- Formulaire 2086 ; compte détenu à l'étranger (Bitvavo B.V., Pays-Bas) : vérifier l'obligation 3916-bis.
La qualification de l'activité algorithmique (habituelle / professionnelle) et le calcul exact dépendent
du foyer fiscal et des autres comptes : à faire vérifier.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from decimal import Decimal

from ..core.clock import utc_iso
from ..core.num import ZERO, round_eur

EXEMPTION_THRESHOLD_EUR = Decimal("305")


@dataclass
class Cession:
    ts: int
    symbol: str
    proceeds_eur: Decimal  # C : prix de cession net de frais
    fees_eur: Decimal
    portfolio_value_eur: Decimal  # V : valeur globale du portefeuille d'actifs numériques juste avant


@dataclass
class Acquisition:
    ts: int
    cost_eur: Decimal  # prix payé en EUR, frais inclus


def compute_2086(acqs: list[Acquisition], cessions: list[Cession]) -> dict[str, object]:
    events = sorted([(a.ts, "a", a) for a in acqs] + [(c.ts, "c", c) for c in cessions], key=lambda e: (e[0], e[1]))
    pta = ZERO
    rows = []
    total_c = ZERO
    total_pv = ZERO
    for _, kind, ev in events:
        if kind == "a":
            assert isinstance(ev, Acquisition)
            pta += ev.cost_eur
            continue
        assert isinstance(ev, Cession)
        c, v = ev.proceeds_eur, ev.portfolio_value_eur
        frac = pta * c / v if v > 0 else ZERO
        pv = c - frac
        rows.append({"date": utc_iso(ev.ts), "actif": ev.symbol, "prix_cession_C": round_eur(c),
                     "frais": round_eur(ev.fees_eur), "valeur_portefeuille_V": round_eur(v),
                     "prix_total_acquisition_PTA": round_eur(pta), "fraction_imputee": round_eur(frac),
                     "plus_value": round_eur(pv)})
        pta -= frac
        total_c += c
        total_pv += pv
    return {
        "lignes": rows, "total_prix_cession": round_eur(total_c), "total_plus_values": round_eur(total_pv),
        "sous_seuil_305": total_c <= EXEMPTION_THRESHOLD_EUR,
        "avertissement": "Aide au calcul uniquement. Vérifier la qualification fiscale, les autres comptes du foyer "
                         "et l'obligation 3916-bis (compte à l'étranger).",
    }


def rows_to_csv(rows: list[dict[str, object]]) -> str:
    if not rows:
        return ""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()), delimiter=";")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()
