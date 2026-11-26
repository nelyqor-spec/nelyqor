"""Dictionnaires TRANSPARENTS (mots-clés et tickers en minuscules, sans accents, sans modèle opaque).

La détection se fait sur le texte normalisé (`safety.normalize_title`) par limites de mots. Les tickers
courts et ambigus (« ton », « link », « op », « sol » en français…) ne sont reconnus QUE sous forme de
cashtag (`$sol`) ; seuls les tickers non ambigus listés dans `PLAIN_TICKERS` sont reconnus nus.
"""
from __future__ import annotations

import re
from functools import lru_cache

# entité -> (type, mots-clés)
ENTITIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "elon_musk": ("personnalite", ("elon musk", "musk")),
    "michael_saylor": ("personnalite", ("michael saylor", "saylor")),
    "brian_armstrong": ("personnalite", ("brian armstrong",)),
    "changpeng_zhao": ("personnalite", ("changpeng zhao",)),
    "richard_teng": ("personnalite", ("richard teng",)),
    "vitalik_buterin": ("personnalite", ("vitalik buterin", "vitalik", "buterin")),
    "christine_lagarde": ("personnalite", ("christine lagarde", "lagarde")),
    "jerome_powell": ("personnalite", ("jerome powell", "powell")),
    "donald_trump": ("personnalite", ("donald trump", "trump")),
    "larry_fink": ("personnalite", ("larry fink",)),
    "tesla": ("entreprise", ("tesla",)),
    "strategy": ("entreprise", ("microstrategy", "strategy inc", "mstr")),
    "blackrock": ("entreprise", ("blackrock", "ishares")),
    "coinbase": ("plateforme", ("coinbase",)),
    "binance": ("plateforme", ("binance",)),
    "kraken": ("plateforme", ("kraken",)),
    "bitvavo": ("plateforme", ("bitvavo",)),
    "okx": ("plateforme", ("okx",)),
    "bybit": ("plateforme", ("bybit",)),
    "bce": ("banque_centrale", ("bce", "ecb", "banque centrale europeenne", "european central bank")),
    "fed": ("banque_centrale", ("fed", "federal reserve", "fomc", "reserve federale")),
    "boj": ("banque_centrale", ("bank of japan", "banque du japon", "boj")),
    "boe": ("banque_centrale", ("bank of england", "banque d angleterre")),
    "sec": ("autorite", ("sec", "securities and exchange commission", "gendarme boursier americain")),
    "cftc": ("autorite", ("cftc",)),
    "amf": ("autorite", ("amf", "autorite des marches financiers")),
    "esma": ("autorite", ("esma",)),
    "doj": ("autorite", ("department of justice", "doj", "ministere de la justice americain")),
}

CATEGORIES: dict[str, tuple[str, ...]] = {
    "piratage": ("hack", "hacked", "hacker", "hackers", "exploit", "exploited", "piratage", "pirate", "pirates",
                 "drained", "breach", "vol de fonds", "stolen", "attaque"),
    "faillite": ("bankruptcy", "bankrupt", "faillite", "insolvency", "insolvent", "insolvable", "chapter 11",
                 "liquidation", "redressement judiciaire"),
    "radiation": ("delist", "delisting", "delisted", "radiation", "retrait de la cote", "suspend trading",
                  "trading halted", "suspension des retraits", "withdrawals halted", "halts withdrawals"),
    "etf": ("etf", "etfs", "etp", "etps", "spot etf"),
    "reglementation": ("regulation", "regulatory", "reglementation", "regulateur", "regulator", "mica", "lawsuit",
                       "poursuite", "poursuites", "sanction", "sanctions", "ban", "bans", "interdiction",
                       "enforcement", "subpoena", "wells notice", "plainte", "amende"),
    "inflation": ("inflation", "cpi", "ipc", "pce", "prix a la consommation", "consumer price"),
    "taux": ("interest rate", "interest rates", "rate cut", "rate cuts", "rate hike", "rate hikes", "taux directeur",
             "taux directeurs", "baisse des taux", "hausse des taux", "politique monetaire", "monetary policy"),
    "tresorerie": ("treasury", "reserve strategique", "strategic reserve", "buys bitcoin", "bought bitcoin",
                   "acquires bitcoin", "achete du bitcoin", "achat de bitcoin", "bitcoin holdings"),
    "stablecoin": ("stablecoin", "stablecoins", "depeg", "depegged", "decrochage"),
    "panne": ("outage", "panne", "downtime", "maintenance", "degraded performance", "incident"),
    "emploi": ("nonfarm payrolls", "payrolls", "chomage", "unemployment", "jobs report"),
}

# actif (ticker Bitvavo en minuscules) -> mots-clés « noms » non ambigus
ASSETS: dict[str, tuple[str, ...]] = {
    "btc": ("bitcoin", "bitcoins", "btc", "xbt"),
    "eth": ("ethereum", "ether", "eth"),
    "sol": ("solana",),
    "xrp": ("xrp", "ripple"),
    "ada": ("cardano",),
    "doge": ("dogecoin", "doge"),
    "bnb": ("bnb",),
    "dot": ("polkadot",),
    "avax": ("avalanche avax", "avax"),
    "link": ("chainlink",),
    "ltc": ("litecoin", "ltc"),
    "trx": ("tron trx", "trx"),
    "usdt": ("tether", "usdt"),
    "usdc": ("usdc", "usd coin"),
    "shib": ("shiba inu", "shib"),
    "pepe": ("pepecoin",),
    "ton": ("toncoin",),
    "sui": ("sui network",),
    "xlm": ("stellar lumens", "xlm"),
}
PLAIN_TICKERS = frozenset({"btc", "eth", "xrp", "doge", "bnb", "ltc", "usdt", "usdc", "shib", "avax", "trx", "xlm"})

RUMOR_TERMS: tuple[str, ...] = (
    # français (dont conditionnel journalistique)
    "selon des sources", "selon nos informations", "d apres des sources", "de source proche", "rumeur", "rumeurs",
    "non confirme", "non confirmee", "aurait", "auraient", "serait", "seraient", "envisagerait", "pourrait",
    "pourraient", "selon plusieurs medias",
    # anglais
    "rumor", "rumors", "rumour", "rumours", "reportedly", "sources say", "according to sources",
    "people familiar", "familiar with the matter", "unconfirmed", "allegedly", "is said to", "speculation",
    "could be", "may be", "might",
)


def _pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    alts = sorted({re.escape(w) for w in words}, key=len, reverse=True)
    return re.compile(r"(?<![a-z0-9])(" + "|".join(alts) + r")(?![a-z0-9])")


@lru_cache(maxsize=1)
def _compiled() -> tuple[dict[str, re.Pattern[str]], dict[str, re.Pattern[str]], dict[str, re.Pattern[str]],
                         re.Pattern[str]]:
    ent = {k: _pattern(v[1]) for k, v in ENTITIES.items()}
    cat = {k: _pattern(v) for k, v in CATEGORIES.items()}
    ass = {}
    for k, words in ASSETS.items():
        ws = tuple(w for w in words if w != k or k in PLAIN_TICKERS)
        ass[k] = _pattern(ws)
    return ent, cat, ass, _pattern(RUMOR_TERMS)


_CASHTAG = re.compile(r"\$([a-z]{2,6})(?![a-z0-9])")


def detect(norm_text: str) -> tuple[list[str], list[str], list[str]]:
    """(entités, catégories, actifs) détectés dans un texte déjà normalisé."""
    ent, cat, ass, _ = _compiled()
    entities = sorted(k for k, p in ent.items() if p.search(norm_text))
    categories = sorted(k for k, p in cat.items() if p.search(norm_text))
    assets = {k for k, p in ass.items() if p.search(norm_text)}
    assets |= {m for m in _CASHTAG.findall(norm_text) if m in ASSETS}
    return entities, categories, sorted(assets)


def rumor_terms(norm_text: str) -> list[str]:
    return sorted(set(_compiled()[3].findall(norm_text)))


def entity_type(entity: str) -> str:
    return ENTITIES[entity][0] if entity in ENTITIES else ""
