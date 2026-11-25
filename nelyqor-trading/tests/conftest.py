from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.clock import FakeClock  # noqa: E402
from backend.core.config import HardLimits  # noqa: E402
from backend.execution.engine import ExecutionEngine  # noqa: E402
from backend.execution.simulator import SimulatedExchange  # noqa: E402
from backend.guardian.guardian import Guardian  # noqa: E402
from backend.market_data.models import MarketInfo  # noqa: E402
from backend.storage.db import Database  # noqa: E402

BTC_RAW = {"market": "BTC-EUR", "status": "trading", "base": "BTC", "quote": "EUR", "pricePrecision": 5,
           "minOrderInBaseAsset": "0.0001", "minOrderInQuoteAsset": "5", "maxOrderInQuoteAsset": "1000000",
           "quantityDecimals": 8, "notionalDecimals": 2, "orderTypes": ["market", "limit", "stopLoss", "stopLossLimit"]}


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "t.db")


@pytest.fixture
def limits() -> HardLimits:
    return HardLimits.load()


@pytest.fixture
def btc() -> MarketInfo:
    return MarketInfo.from_bitvavo(BTC_RAW)


@pytest.fixture
def sim(btc: MarketInfo, clock: FakeClock) -> SimulatedExchange:
    ex = SimulatedExchange([btc], {"EUR": Decimal("100")}, now_ms=clock.now_ms)
    ex.set_book("BTC-EUR", [(Decimal("49990"), Decimal("0.5")), (Decimal("49980"), Decimal("1"))],
                [(Decimal("50010"), Decimal("0.0005")), (Decimal("50020"), Decimal("1"))])
    return ex


@pytest.fixture
def guardian(db: Database, limits: HardLimits, clock: FakeClock) -> Guardian:
    return Guardian(db, limits, real_server_flag=False, role="engine", now_ms=clock.now_ms)


@pytest.fixture
def engine(db: Database, sim: SimulatedExchange, guardian: Guardian, btc: MarketInfo, clock: FakeClock) -> ExecutionEngine:
    return ExecutionEngine(db, sim, guardian, {"BTC-EUR": btc}, "simulation", 42, clock.now_ms)
