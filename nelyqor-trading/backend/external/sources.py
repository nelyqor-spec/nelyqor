"""Sources d'information externes gratuites — DÉSACTIVÉES par défaut, sans influence sur les décisions tant
qu'elles n'ont pas démontré leur utilité hors échantillon (statut « valide » attribué par le propriétaire
au vu d'un test d'ablation).

Règles :
- Point-in-time : chaque information porte `published_ms` (selon la source) ET `available_ms` (instant où NOUS
  l'avons reçue). Backtests et décisions n'utilisent que `available_ms <= t` (jamais d'historique reconstruit).
- Le contenu externe est une DONNÉE, jamais une instruction : texte nettoyé, tronqué, jamais exécuté ni
  transmis à un modèle génératif ; les formulations injonctives (« ignore… », « achetez… ») sont signalées
  et font baisser la crédibilité.
- Déduplication par empreinte du contenu normalisé ; corroboration requise (>= 2 sources indépendantes)
  pour qu'un événement soit considéré confirmé ; valeurs numériques aberrantes signalées.
- Aucune clé payante, aucun contournement d'accès ; licences à vérifier par le propriétaire (champ `licence`).
"""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET  # noqa: S405 — flux RSS publics ; défusé ci-dessous (pas d'entités externes)
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any

import numpy as np

from ..storage.db import Database

INJECTION = re.compile(r"(ignore (all|previous|the above)|disregard|system prompt|you are now|achetez|buy now|"
                       r"vendez tout|sell everything|guaranteed|garanti|100x|pump)", re.I)
CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮]")
URL = re.compile(r"https?://\S+")


@dataclass
class ExternalItem:
    source: str
    kind: str  # sentiment | banque_centrale | actualite_eco | actualite_crypto | blockchain | calendrier
    published_ms: int
    available_ms: int
    value: float | None = None
    title: str = ""
    credibility: float = 0.5
    flags: list[str] = field(default_factory=list)

    @property
    def content_hash(self) -> str:
        norm = re.sub(r"\W+", " ", self.title.lower()).strip()
        return hashlib.sha256(f"{self.kind}|{norm}|{self.value}|{self.published_ms // 60000}".encode()).hexdigest()


@dataclass(frozen=True)
class SourceSpec:
    name: str
    kind: str
    url: str
    licence: str
    requires_key: bool = False
    max_age_ms: int = 86_400_000
    prior_credibility: float = 0.5


SOURCES: dict[str, SourceSpec] = {
    "fear_greed": SourceSpec("fear_greed", "sentiment", "https://api.alternative.me/fng/?limit=1",
                             "Alternative.me — usage gratuit avec attribution (à vérifier)", max_age_ms=2 * 86_400_000,
                             prior_credibility=0.6),
    "bce_taux": SourceSpec("bce_taux", "banque_centrale",
                           "https://data-api.ecb.europa.eu/service/data/FM/B.U2.EUR.4F.KR.MRR_FR.LEV?format=csvdata&lastNObservations=1",
                           "BCE — données publiques réutilisables (vérifier conditions)", max_age_ms=40 * 86_400_000,
                           prior_credibility=0.95),
    "fed_communiques": SourceSpec("fed_communiques", "banque_centrale",
                                  "https://www.federalreserve.gov/feeds/press_all.xml",
                                  "Federal Reserve — domaine public (US Gov)", max_age_ms=7 * 86_400_000,
                                  prior_credibility=0.95),
    "mempool_frais": SourceSpec("mempool_frais", "blockchain", "https://mempool.space/api/v1/fees/recommended",
                                "mempool.space — API publique, limites de débit", max_age_ms=3_600_000,
                                prior_credibility=0.8),
    "gdelt_volume": SourceSpec("gdelt_volume", "actualite_eco",
                               "https://api.gdeltproject.org/api/v2/doc/doc?query=bitcoin&mode=timelinevolraw&format=json&timespan=1d",
                               "GDELT — usage libre avec citation", max_age_ms=86_400_000, prior_credibility=0.5),
    "coindesk_rss": SourceSpec("coindesk_rss", "actualite_crypto", "https://www.coindesk.com/arc/outboundfeeds/rss/",
                               "CoinDesk RSS — usage personnel ; redistribution interdite (à vérifier)",
                               max_age_ms=86_400_000, prior_credibility=0.5),
}


def sanitize(text: str, max_len: int = 240) -> tuple[str, list[str]]:
    flags = []
    t = CTRL.sub("", text or "")
    if URL.search(t):
        flags.append("url_retiree")
        t = URL.sub("", t)
    if INJECTION.search(t):
        flags.append("formulation_injonctive")
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > max_len:
        flags.append("tronque")
        t = t[:max_len]
    return t, flags


def _item(spec: SourceSpec, published_ms: int, now_ms: int, value: float | None = None, title: str = "") -> ExternalItem:
    clean, flags = sanitize(title)
    cred = spec.prior_credibility * (0.3 if "formulation_injonctive" in flags else 1.0)
    if published_ms > now_ms + 300_000:
        flags.append("date_future")
        cred *= 0.2
    return ExternalItem(spec.name, spec.kind, published_ms, now_ms, value, clean, round(cred, 3), flags)


# --- analyseurs (testés sur des fixtures ; le format réel est à confirmer au premier téléchargement)
def parse_fear_greed(payload: dict[str, Any], now_ms: int) -> list[ExternalItem]:
    spec = SOURCES["fear_greed"]
    out = []
    for d in payload.get("data", []):
        v = float(d["value"])
        if not 0 <= v <= 100:
            continue
        out.append(_item(spec, int(d["timestamp"]) * 1000, now_ms, v, d.get("value_classification", "")))
    return out


def parse_ecb_csv(text: str, now_ms: int) -> list[ExternalItem]:
    from datetime import UTC, datetime

    spec = SOURCES["bce_taux"]
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return []
    head = lines[0].split(",")
    ti, vi = head.index("TIME_PERIOD"), head.index("OBS_VALUE")
    out = []
    for ln in lines[1:]:
        cols = ln.split(",")
        day = datetime.strptime(cols[ti][:10], "%Y-%m-%d").replace(tzinfo=UTC)
        out.append(_item(spec, int(day.timestamp() * 1000), now_ms, float(cols[vi]), "taux refi BCE"))
    return out


def parse_rss(xml_text: str, source: str, now_ms: int, limit: int = 50) -> list[ExternalItem]:
    if "<!ENTITY" in xml_text or "<!DOCTYPE" in xml_text:
        raise ValueError("flux RSS avec DTD/entités refusé (protection XXE / bombe XML)")
    spec = SOURCES[source]
    root = ET.fromstring(xml_text)  # noqa: S314 — DTD refusée ci-dessus
    out = []
    for it in root.iter("item"):
        title = it.findtext("title") or ""
        pub = it.findtext("pubDate")
        try:
            pub_ms = int(parsedate_to_datetime(pub).timestamp() * 1000) if pub else now_ms
        except (TypeError, ValueError):
            pub_ms = now_ms
        out.append(_item(spec, pub_ms, now_ms, None, title))
        if len(out) >= limit:
            break
    return out


def parse_mempool_fees(payload: dict[str, Any], now_ms: int) -> list[ExternalItem]:
    v = payload.get("halfHourFee")
    return [_item(SOURCES["mempool_frais"], now_ms, now_ms, float(v), "frais BTC sat/vB (30 min)")] if v is not None else []


def parse_gdelt_volume(payload: dict[str, Any], now_ms: int) -> list[ExternalItem]:
    from datetime import UTC, datetime

    out = []
    for serie in payload.get("timeline", []):
        for pt in serie.get("data", [])[-24:]:
            ts = datetime.strptime(pt["date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            out.append(_item(SOURCES["gdelt_volume"], int(ts.timestamp() * 1000), now_ms, float(pt["value"]),
                             "volume d'articles « bitcoin »"))
    return out


# --- stockage, déduplication, fraîcheur, corroboration
def store_items(db: Database, items: list[ExternalItem]) -> int:
    n = 0
    with db.tx() as c:
        for it in items:
            cur = c.execute("INSERT OR IGNORE INTO external_items(id,source,kind,published_ms,available_ms,fetched_ms,"
                            "content_hash,value,credibility,flags) VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (it.content_hash[:32], it.source, it.kind, it.published_ms, it.available_ms, it.available_ms,
                             it.content_hash, json.dumps({"v": it.value, "t": it.title}, ensure_ascii=False),
                             it.credibility, json.dumps(it.flags)))
            n += cur.rowcount
    return n


def latest_value(db: Database, source: str, as_of_ms: int) -> tuple[float | None, bool]:
    """Dernière valeur DISPONIBLE à `as_of_ms` (point-in-time) et indicateur de fraîcheur."""
    r = db.one("SELECT value, available_ms FROM external_items WHERE source=? AND available_ms<=? "
               "ORDER BY available_ms DESC LIMIT 1", (source, as_of_ms))
    if r is None:
        return None, False
    v = json.loads(r["value"]).get("v")
    return v, as_of_ms - int(r["available_ms"]) <= SOURCES[source].max_age_ms


def corroborated(items: list[ExternalItem], keyword: str, window_ms: int = 6 * 3_600_000) -> bool:
    """Un événement n'est « confirmé » que si >= 2 SOURCES DIFFÉRENTES le rapportent dans la fenêtre."""
    hits = [i for i in items if keyword.lower() in i.title.lower() and "formulation_injonctive" not in i.flags]
    if not hits:
        return False
    t0 = max(i.available_ms for i in hits)
    return len({i.source for i in hits if t0 - i.available_ms <= window_ms}) >= 2


def is_outlier(history: list[float], value: float, k: float = 8.0) -> bool:
    if len(history) < 10:
        return False
    h = np.asarray(history, dtype=float)
    med = float(np.median(h))
    mad = float(np.median(np.abs(h - med))) * 1.4826 or 1e-9
    return abs(value - med) > k * mad


def ablation_test(df: Any, feature: Any, horizon: int = 6, cost: float = 0.005) -> dict[str, Any]:
    """Utilité d'une source : compétence de Brier AVEC la variable externe moins SANS, hors échantillon,
    IC 95 % par bootstrap. Une source n'est utile que si la borne basse du gain est > 0."""
    from ..features.indicators import feature_frame
    from ..models.predict import _calibrate, _model, brier_skill_ci, make_target

    X0 = feature_frame(df)
    X1 = X0.assign(externe=np.asarray(feature, dtype=float))
    y = make_target(df["close"].astype(float), horizon, cost, df["open"].astype(float))
    ok = X1.notna().all(axis=1) & y.notna()
    X0, X1, y = X0[ok], X1[ok], y[ok]
    n = len(y)
    a, b = int(n * 0.6), int(n * 0.8)
    res = {}
    preds = {}
    for name, X in (("sans", X0), ("avec", X1)):
        mdl = _model("logistic", 7).fit(X.iloc[: a - horizon], y.iloc[: a - horizon])
        cal = _calibrate(mdl, X.iloc[a: b - horizon], y.iloc[a: b - horizon])
        preds[name] = cal.predict_proba(X.iloc[b:])[:, 1]
    yt = y.iloc[b:].to_numpy()
    gain, lo, hi = brier_skill_ci(yt, preds["avec"], preds["sans"], block=max(24, horizon))
    res.update({"gain_brier": gain, "ic95": (lo, hi), "utile": lo > 0, "n_test": len(yt)})
    return res
