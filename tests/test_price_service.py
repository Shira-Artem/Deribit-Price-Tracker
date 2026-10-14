"""One collection cycle with a fake Deribit client and real SQLite sessions."""

import asyncio
from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.infrastructure.database import create_db_engine, create_session_factory
from app.infrastructure.deribit_client import DeribitResponseError
from app.infrastructure.models import Base, Price
from app.repositories.prices import PriceRepository
from app.services.price_service import PricePersistenceError, PriceService


class FakeClient:
    def __init__(self, outcomes: dict[str, Decimal | BaseException]) -> None:
        self.outcomes = outcomes
        self.requested: list[str] = []

    async def get_index_price(self, ticker: str) -> Decimal:
        self.requested.append(ticker)
        outcome = self.outcomes[ticker]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:", _env_file=None)
    engine = create_db_engine(settings)
    Base.metadata.create_all(engine)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


def make_service(
    session: Session,
    outcomes: dict[str, Decimal | BaseException],
    repository: PriceRepository | None = None,
) -> tuple[PriceService, FakeClient]:
    client = FakeClient(outcomes)
    service = PriceService(
        client,
        repository or PriceRepository(session),
        session,
        clock=lambda: 1_760_000_000,
    )
    return service, client


def test_both_prices_are_committed_with_one_timestamp(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        service, client = make_service(
            session,
            {"btc_usd": Decimal("68000.25"), "eth_usd": Decimal("4000.75")},
        )
        result = asyncio.run(service.collect_prices())

    with session_factory() as session:
        repository = PriceRepository(session)
        btc = repository.get_latest("btc_usd")
        eth = repository.get_latest("eth_usd")
        assert btc is not None and eth is not None
        assert btc.price == Decimal("68000.25000000")
        assert eth.price == Decimal("4000.75000000")
        assert btc.timestamp == eth.timestamp == result.timestamp
        assert result.saved_tickers == ("btc_usd", "eth_usd")
        assert result.failed_tickers == ()
        assert client.requested == ["btc_usd", "eth_usd"]


@pytest.mark.parametrize(
    ("good_ticker", "bad_ticker"),
    [("btc_usd", "eth_usd"), ("eth_usd", "btc_usd")],
)
def test_one_fetch_failure_still_saves_the_other_ticker(
    session_factory: sessionmaker[Session], good_ticker: str, bad_ticker: str
) -> None:
    with session_factory() as session:
        service, _ = make_service(
            session,
            {
                good_ticker: Decimal("100.25"),
                bad_ticker: DeribitResponseError("invalid response"),
            },
        )
        result = asyncio.run(service.collect_prices())

    with session_factory() as session:
        repository = PriceRepository(session)
        assert repository.get_latest(good_ticker) is not None
        assert repository.get_latest(bad_ticker) is None
        assert result.saved_tickers == (good_ticker,)
        assert result.failed_tickers == (bad_ticker,)


def test_two_fetch_failures_do_not_write_or_commit(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        service, _ = make_service(
            session,
            {
                "btc_usd": DeribitResponseError("bad BTC"),
                "eth_usd": DeribitResponseError("bad ETH"),
            },
        )
        result = asyncio.run(service.collect_prices())
        assert not session.in_transaction()

    with session_factory() as session:
        assert PriceRepository(session).list_by_ticker("btc_usd", 10, 0) == []
        assert result.saved_tickers == ()
        assert result.failed_tickers == ("btc_usd", "eth_usd")
        assert result.timestamp is None


class FailingRepository(PriceRepository):
    def add_price(self, ticker: str, price: Decimal, timestamp: int) -> Price:
        row = super().add_price(ticker, price, timestamp)
        if ticker == "eth_usd":
            raise RuntimeError("simulated DB failure")
        return row


def test_database_failure_rolls_back_both_prices(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        service, _ = make_service(
            session,
            {"btc_usd": Decimal("1"), "eth_usd": Decimal("2")},
            FailingRepository(session),
        )
        with pytest.raises(PricePersistenceError, match="Could not save"):
            asyncio.run(service.collect_prices())
        assert not session.in_transaction()

    with session_factory() as session:
        repository = PriceRepository(session)
        assert repository.list_by_ticker("btc_usd", 10, 0) == []
        assert repository.list_by_ticker("eth_usd", 10, 0) == []


def test_invalid_price_is_not_saved(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        service, _ = make_service(
            session,
            {"btc_usd": Decimal("NaN"), "eth_usd": Decimal("2")},
        )
        result = asyncio.run(service.collect_prices())

    with session_factory() as session:
        repository = PriceRepository(session)
        assert repository.get_latest("btc_usd") is None
        assert repository.get_latest("eth_usd") is not None
        assert result.failed_tickers == ("btc_usd",)


def test_cancellation_is_not_hidden(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        service, _ = make_service(
            session,
            {"btc_usd": asyncio.CancelledError(), "eth_usd": Decimal("2")},
        )
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(service.collect_prices())

    with session_factory() as session:
        assert PriceRepository(session).get_latest("eth_usd") is None
