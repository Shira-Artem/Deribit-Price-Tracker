"""Repository behavior with a disposable in-memory database."""

from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.infrastructure.database import create_db_engine, create_session_factory
from app.infrastructure.models import Base
from app.repositories.prices import PriceRepository


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", _env_file=None)
    engine = create_db_engine(settings)
    Base.metadata.create_all(engine)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


def test_saves_btc_and_eth_with_decimal_and_integer_timestamp(
    session_factory: sessionmaker[Session],
) -> None:
    timestamp = 2_147_483_648
    with session_factory.begin() as session:
        repository = PriceRepository(session)
        repository.add_price("btc_usd", Decimal("67500.12345678"), timestamp)
        repository.add_price("eth_usd", Decimal("3000.00000001"), timestamp)

    with session_factory() as session:
        repository = PriceRepository(session)
        btc = repository.get_latest("btc_usd")
        eth = repository.get_latest("eth_usd")

        assert btc is not None and eth is not None
        assert btc.price == Decimal("67500.12345678")
        assert eth.price == Decimal("3000.00000001")
        assert btc.timestamp == eth.timestamp == timestamp
        assert isinstance(btc.timestamp, int)


def test_latest_uses_id_to_break_timestamp_ties(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        repository = PriceRepository(session)
        repository.add_price("btc_usd", Decimal("1"), 100)
        repository.add_price("btc_usd", Decimal("2"), 200)
        newest = repository.add_price("btc_usd", Decimal("3"), 200)
        repository.add_price("eth_usd", Decimal("4"), 300)

    with session_factory() as session:
        latest = PriceRepository(session).get_latest("btc_usd")
        assert latest is not None
        assert latest.id == newest.id


def test_history_is_ticker_scoped_sorted_and_paginated(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        repository = PriceRepository(session)
        first = repository.add_price("btc_usd", Decimal("1"), 100)
        second = repository.add_price("btc_usd", Decimal("2"), 200)
        third = repository.add_price("btc_usd", Decimal("3"), 200)
        fourth = repository.add_price("btc_usd", Decimal("4"), 300)
        repository.add_price("eth_usd", Decimal("5"), 400)

    with session_factory() as session:
        repository = PriceRepository(session)
        all_btc = repository.list_by_ticker("btc_usd", limit=10, offset=0)
        page = repository.list_by_ticker("btc_usd", limit=2, offset=1)

        assert [row.id for row in all_btc] == [
            fourth.id,
            third.id,
            second.id,
            first.id,
        ]
        assert [row.id for row in page] == [third.id, second.id]


def test_period_is_half_open_and_paginated(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        repository = PriceRepository(session)
        repository.add_price("btc_usd", Decimal("1"), 99)
        start = repository.add_price("btc_usd", Decimal("2"), 100)
        end_inside = repository.add_price("btc_usd", Decimal("3"), 199)
        repository.add_price("btc_usd", Decimal("4"), 200)
        repository.add_price("eth_usd", Decimal("5"), 150)

    with session_factory() as session:
        repository = PriceRepository(session)
        interval = repository.list_by_period("btc_usd", 100, 200, 10, 0)
        page = repository.list_by_period("btc_usd", 100, 200, 1, 1)

        assert [row.id for row in interval] == [end_inside.id, start.id]
        assert [row.id for row in page] == [start.id]


def test_empty_results(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        repository = PriceRepository(session)
        assert repository.get_latest("btc_usd") is None
        assert repository.list_by_ticker("btc_usd", 10, 0) == []
        assert repository.list_by_period("btc_usd", 100, 200, 10, 0) == []


def test_caller_transaction_rolls_back_after_error(
    session_factory: sessionmaker[Session],
) -> None:
    with pytest.raises(IntegrityError):
        with session_factory.begin() as session:
            repository = PriceRepository(session)
            repository.add_price("btc_usd", Decimal("1"), 100)
            repository.add_price(None, Decimal("2"), 101)  # type: ignore[arg-type]

    with session_factory() as session:
        assert PriceRepository(session).list_by_ticker("btc_usd", 10, 0) == []
