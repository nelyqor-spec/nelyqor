"""Client REST Bitvavo natif (v2).

- Signature HMAC-SHA256 : timestamp + METHOD + "/v2" + chemin(+query) + corps.
- Budget de poids (1000/min par défaut) suivi via les en-têtes bitvavo-ratelimit-*.
- Backoff exponentiel sur 429/5xx/erreurs réseau pour les requêtes idempotentes (GET) uniquement.
- Un POST /order en timeout lève OrderStatusUnknown : l'appelant DOIT rechercher l'ordre
  (par clientOrderId) avant toute nouvelle soumission.
- Les chemins de retrait/transfert sont bloqués au niveau du client.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from ..core.logging import get_logger, register_secret

log = get_logger("bitvavo.rest")

FORBIDDEN_PATH_PREFIXES = ("/withdrawal", "/deposit/", "/transfer", "/account/withdraw")
# Lecture seule autorisée sur l'historique des dépôts/retraits (détection des flux externes).
READONLY_ALLOWED = ("/depositHistory", "/withdrawalHistory")


class BitvavoError(Exception):
    def __init__(self, status: int, code: int | None, message: str) -> None:
        super().__init__(f"HTTP {status} code={code} {message}")
        self.status, self.code, self.message = status, code, message


class OrderStatusUnknown(Exception):
    """La requête d'ordre a pu être acceptée : rechercher avant de resoumettre."""


class ForbiddenOperation(Exception):
    pass


class RateBudget:
    def __init__(self, limit: int = 1000, reserve: int = 100) -> None:
        self.limit = limit
        self.reserve = reserve
        self.remaining = limit
        self.reset_at_ms = 0

    def update(self, headers: httpx.Headers) -> None:
        r = headers.get("bitvavo-ratelimit-remaining")
        t = headers.get("bitvavo-ratelimit-resetat")
        if r is not None:
            self.remaining = int(r)
        if t is not None:
            self.reset_at_ms = int(t)

    def wait_ms(self, weight: int, now_ms: int) -> int:
        if self.remaining - weight >= self.reserve or now_ms >= self.reset_at_ms:
            return 0
        return max(0, self.reset_at_ms - now_ms)

    def consume(self, weight: int) -> None:
        self.remaining -= weight


class BitvavoREST:
    def __init__(self, base_url: str = "https://api.bitvavo.com/v2", api_key: str | None = None,
                 api_secret: str | None = None, access_window_ms: int = 10000, timeout_s: float = 10.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._key = api_key
        self._secret = api_secret
        register_secret(api_key)
        register_secret(api_secret)
        self.window = access_window_ms
        self.budget = RateBudget()
        self.time_offset_ms = 0  # heure serveur - heure locale (signature)
        self.client = httpx.AsyncClient(timeout=timeout_s, transport=transport)

    @property
    def has_credentials(self) -> bool:
        return bool(self._key and self._secret)

    def _sign(self, ts: int, method: str, path: str, body: str) -> str:
        assert self._secret
        msg = f"{ts}{method}/v2{path}{body}"
        return hmac.new(self._secret.encode(), msg.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _check_path(path: str) -> None:
        if path.startswith(READONLY_ALLOWED):
            return
        if path.startswith(FORBIDDEN_PATH_PREFIXES):
            raise ForbiddenOperation(f"Opération interdite par conception : {path}")

    async def request(self, method: str, path: str, params: dict[str, Any] | None = None,
                      body: dict[str, Any] | None = None, private: bool = False, weight: int = 1,
                      retries: int = 4) -> Any:
        self._check_path(path)
        if method != "GET" and path.startswith(READONLY_ALLOWED):
            raise ForbiddenOperation(path)
        query = ("?" + urlencode(params)) if params else ""
        full_path = path + query
        body_str = json.dumps(body, separators=(",", ":")) if body else ""
        idempotent = method == "GET"
        attempt = 0
        while True:
            wait = self.budget.wait_ms(weight, int(time.time() * 1000))
            if wait:
                log.warning("budget API bas, attente", extra={"data": {"wait_ms": wait}})
                await asyncio.sleep(wait / 1000)
            headers = {"Content-Type": "application/json"}
            if private:
                if not self.has_credentials:
                    raise PermissionError("Aucune clé API configurée")
                ts = int(time.time() * 1000) + self.time_offset_ms
                headers.update({
                    "Bitvavo-Access-Key": self._key or "",
                    "Bitvavo-Access-Signature": self._sign(ts, method, full_path, body_str),
                    "Bitvavo-Access-Timestamp": str(ts),
                    "Bitvavo-Access-Window": str(self.window),
                })
            try:
                resp = await self.client.request(method, self.base_url + full_path, content=body_str or None,
                                                 headers=headers)
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if not idempotent:
                    raise OrderStatusUnknown(f"{method} {path}: {type(e).__name__}") from e
                attempt += 1
                if attempt > retries:
                    raise
                await asyncio.sleep(min(30.0, 0.5 * 2 ** attempt))
                continue
            self.budget.consume(weight)
            self.budget.update(resp.headers)
            if resp.status_code == 429 or resp.status_code >= 500:
                if not idempotent and resp.status_code >= 500:
                    raise OrderStatusUnknown(f"{method} {path}: HTTP {resp.status_code}")
                attempt += 1
                if attempt > retries:
                    raise BitvavoError(resp.status_code, None, resp.text[:200])
                await asyncio.sleep(min(60.0, 1.0 * 2 ** attempt))
                continue
            try:
                data = resp.json() if resp.content else None
            except ValueError:
                data = None
            if resp.status_code >= 400 or (isinstance(data, dict) and "errorCode" in data):
                code = data.get("errorCode") if isinstance(data, dict) else None
                msg = data.get("error", "") if isinstance(data, dict) else resp.text[:200]
                if code == 105:
                    # bannissement temporaire (« ... at <ms> ») : budget à zéro jusqu'à la date indiquée
                    self.budget.remaining = 0
                    try:
                        self.budget.reset_at_ms = int(str(msg).split(" at ")[1].split(".")[0])
                    except (IndexError, ValueError):
                        self.budget.reset_at_ms = int(time.time() * 1000) + 60_000
                if code in (304, 305) and private and attempt < retries and idempotent:
                    # horodatage hors fenêtre : resynchroniser l'horloge puis réessayer (GET uniquement)
                    await self.sync_time()
                    attempt += 1
                    continue
                raise BitvavoError(resp.status_code, code, str(msg))
            return data

    # --- public
    async def time(self) -> int:
        return int((await self.request("GET", "/time"))["time"])

    async def sync_time(self) -> int:
        local = int(time.time() * 1000)
        server = await self.time()
        self.time_offset_ms = server - local
        return self.time_offset_ms

    async def markets(self) -> list[dict[str, Any]]:
        res: list[dict[str, Any]] = await self.request("GET", "/markets")
        return res

    async def assets(self) -> list[dict[str, Any]]:
        res: list[dict[str, Any]] = await self.request("GET", "/assets")
        return res

    async def ticker_24h(self) -> list[dict[str, Any]]:
        res: list[dict[str, Any]] = await self.request("GET", "/ticker/24h", weight=25)
        return res

    async def ticker_book(self) -> list[dict[str, Any]]:
        res: list[dict[str, Any]] = await self.request("GET", "/ticker/book")
        return res

    async def book(self, market: str, depth: int = 50) -> dict[str, Any]:
        res: dict[str, Any] = await self.request("GET", f"/{market}/book", {"depth": depth})
        return res

    async def candles(self, market: str, interval: str, limit: int = 1440, start: int | None = None,
                      end: int | None = None) -> list[list[Any]]:
        p: dict[str, Any] = {"interval": interval, "limit": limit}
        if start is not None:
            p["start"] = start
        if end is not None:
            p["end"] = end
        res: list[list[Any]] = await self.request("GET", f"/{market}/candles", p)
        return res

    async def public_trades(self, market: str, limit: int = 1000, start: int | None = None,
                            end: int | None = None) -> list[dict[str, Any]]:
        """Transactions PUBLIQUES du marché (aucune clé) : id, timestamp, amount, price, side (agresseur).
        Plus récentes d'abord ; pagination par `end`. Poids 5."""
        p: dict[str, Any] = {"limit": limit}
        if start is not None:
            p["start"] = start
        if end is not None:
            p["end"] = end
        res: list[dict[str, Any]] = await self.request("GET", f"/{market}/trades", p, weight=5)
        return res

    # --- privé (lecture)
    async def balance(self) -> list[dict[str, Any]]:
        res: list[dict[str, Any]] = await self.request("GET", "/balance", private=True, weight=5)
        return res

    async def account(self) -> dict[str, Any]:
        res: dict[str, Any] = await self.request("GET", "/account", private=True)
        return res

    async def market_fees(self, market: str) -> dict[str, Any]:
        res: dict[str, Any] = await self.request("GET", "/account/fees", {"market": market}, private=True)
        return res

    async def get_order(self, market: str, order_id: str | None = None, client_order_id: str | None = None
                        ) -> dict[str, Any]:
        p: dict[str, Any] = {"market": market}
        if order_id:
            p["orderId"] = order_id
        if client_order_id:
            p["clientOrderId"] = client_order_id
        res: dict[str, Any] = await self.request("GET", "/order", p, private=True)
        return res

    async def open_orders(self, market: str | None = None) -> list[dict[str, Any]]:
        p = {"market": market} if market else None
        res: list[dict[str, Any]] = await self.request("GET", "/ordersOpen", p, private=True, weight=25 if not market else 1)
        return res

    async def trades(self, market: str, limit: int = 500, start: int | None = None) -> list[dict[str, Any]]:
        p: dict[str, Any] = {"market": market, "limit": limit}
        if start is not None:
            p["start"] = start
        res: list[dict[str, Any]] = await self.request("GET", "/trades", p, private=True, weight=5)
        return res

    async def deposit_history(self, start: int | None = None) -> list[dict[str, Any]]:
        res: list[dict[str, Any]] = await self.request("GET", "/depositHistory", {"start": start} if start else None,
                                                       private=True, weight=5)
        return res

    async def withdrawal_history(self, start: int | None = None) -> list[dict[str, Any]]:
        res: list[dict[str, Any]] = await self.request("GET", "/withdrawalHistory", {"start": start} if start else None,
                                                       private=True, weight=5)
        return res

    # --- privé (trading) — appelé uniquement par le service d'exécution
    async def create_order(self, body: dict[str, Any]) -> dict[str, Any]:
        res: dict[str, Any] = await self.request("POST", "/order", body=body, private=True)
        return res

    async def update_order(self, body: dict[str, Any]) -> dict[str, Any]:
        """PUT /order : modification en place (déclencheur d'un stop non déclenché, prix d'un limité...)."""
        res: dict[str, Any] = await self.request("PUT", "/order", body=body, private=True)
        return res

    async def cancel_order(self, market: str, order_id: str, operator_id: int) -> dict[str, Any]:
        res: dict[str, Any] = await self.request(
            "DELETE", "/order", {"market": market, "orderId": order_id, "operatorId": operator_id}, private=True)
        return res

    async def aclose(self) -> None:
        await self.client.aclose()
