"""WebSocket privé : authentification signée, souscription account, événements order/fill, reconnexion avec
re-authentification, re-souscription et resynchronisation REST. Serveur WebSocket LOCAL simulé (pas Bitvavo)."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

import websockets

from backend.market_data.bitvavo_ws import AccountStream


async def test_account_stream_auth_events_and_reconnect():
    seen = {"auth": [], "subs": [], "conn": 0}

    async def server(ws):
        seen["conn"] += 1
        auth = json.loads(await ws.recv())
        exp = hmac.new(b"secret", f"{auth['timestamp']}GET/v2/websocket".encode(), hashlib.sha256).hexdigest()
        seen["auth"].append(auth["signature"] == exp and auth["key"] == "key")
        await ws.send(json.dumps({"event": "authenticate", "authenticated": True}))
        seen["subs"].append(json.loads(await ws.recv()))
        await ws.send(json.dumps({"event": "fill", "market": "BTC-EUR", "orderId": "o1", "fillId": f"f{seen['conn']}"}))
        await ws.send(json.dumps({"event": "order", "market": "BTC-EUR", "orderId": "o1", "status": "filled"}))
        if seen["conn"] == 1:
            await ws.close()  # coupure brutale après le 1er lot d'événements
        else:
            await asyncio.sleep(0.2)

    events, resyncs = [], []

    async def on_event(m):
        events.append(m)

    async def on_reconnect():
        resyncs.append(1)

    async with websockets.serve(server, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]
        st = AccountStream(f"ws://127.0.0.1:{port}", "key", "secret", ["BTC-EUR"], on_event, on_reconnect,
                           max_backoff_s=0.05)
        await asyncio.wait_for(st.run_forever(max_connections=2), timeout=10)
    assert seen["conn"] == 2 and all(seen["auth"])  # ré-authentifié à la reconnexion
    assert all(s["channels"][0] == {"name": "account", "markets": ["BTC-EUR"]} for s in seen["subs"])
    assert [e["event"] for e in events].count("fill") == 2
    assert resyncs == [1]  # resynchronisation REST demandée après la reconnexion uniquement


async def test_rejected_auth_does_not_spin():
    async def server(ws):
        await ws.recv()
        await ws.send(json.dumps({"event": "authenticate", "authenticated": False, "error": "bad"}))

    async def noop(_m):
        return None

    async with websockets.serve(server, "127.0.0.1", 0) as srv:
        port = srv.sockets[0].getsockname()[1]
        st = AccountStream(f"ws://127.0.0.1:{port}", "key", "bad", ["BTC-EUR"], noop)
        task = asyncio.create_task(st.run_forever())
        await asyncio.sleep(0.5)
        assert st.connections == 1 and not st.authenticated  # pas de boucle de reconnexion agressive
        task.cancel()
