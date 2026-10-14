"""Deribit HTTP behavior without network requests."""

import asyncio
from collections.abc import Iterator
from decimal import Decimal
from typing import cast

import aiohttp
import pytest

from app.core.config import Settings
from app.infrastructure.deribit_client import (
    DeribitClient,
    DeribitRequestError,
    DeribitResponseError,
)


class FakeResponse:
    def __init__(self, status: int, body: str = "") -> None:
        self.status = status
        self.body = body

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def text(self) -> str:
        return self.body


class FakeSession:
    def __init__(self, actions: list[FakeResponse | Exception]) -> None:
        self.actions: Iterator[FakeResponse | Exception] = iter(actions)
        self.requests: list[tuple[str, str, float | None]] = []

    def get(
        self,
        url: str,
        *,
        params: dict[str, str],
        timeout: aiohttp.ClientTimeout,
    ) -> FakeResponse:
        self.requests.append((url, params["index_name"], timeout.total))
        action = next(self.actions)
        if isinstance(action, Exception):
            raise action
        return action


def make_client(fake_session: FakeSession) -> DeribitClient:
    settings = Settings(http_timeout_seconds=5, _env_file=None)
    return DeribitClient(
        cast(aiohttp.ClientSession, fake_session), settings, retry_delay_seconds=0
    )


@pytest.mark.parametrize(
    ("ticker", "number"),
    [("btc_usd", "68000.12345678"), ("eth_usd", "4000.00000001")],
)
def test_success_preserves_decimal_precision(ticker: str, number: str) -> None:
    fake_session = FakeSession(
        [FakeResponse(200, f'{{"jsonrpc":"2.0","result":{{"index_price":{number}}}}}')]
    )

    price = asyncio.run(make_client(fake_session).get_index_price(ticker))

    assert price == Decimal(number)
    assert isinstance(price, Decimal)
    assert fake_session.requests == [
        ("https://www.deribit.com/api/v2/public/get_index_price", ticker, 5)
    ]


@pytest.mark.parametrize(
    "body",
    [
        '{"jsonrpc":"2.0","result":{}}',
        "not JSON",
        '{"jsonrpc":"2.0","error":{"code":10000,"message":"bad"}}',
    ],
)
def test_invalid_responses_are_not_retried(body: str) -> None:
    fake_session = FakeSession([FakeResponse(200, body)])

    with pytest.raises(DeribitResponseError):
        asyncio.run(make_client(fake_session).get_index_price("btc_usd"))

    assert len(fake_session.requests) == 1


@pytest.mark.parametrize("number", ["0", "-1", "NaN", "Infinity", "-Infinity"])
def test_invalid_prices_are_rejected(number: str) -> None:
    fake_session = FakeSession(
        [FakeResponse(200, f'{{"jsonrpc":"2.0","result":{{"index_price":{number}}}}}')]
    )

    with pytest.raises(DeribitResponseError):
        asyncio.run(make_client(fake_session).get_index_price("btc_usd"))

    assert len(fake_session.requests) == 1


def test_429_and_500_are_retried() -> None:
    fake_session = FakeSession(
        [
            FakeResponse(429),
            FakeResponse(500),
            FakeResponse(200, '{"jsonrpc":"2.0","result":{"index_price":1.25}}'),
        ]
    )

    price = asyncio.run(make_client(fake_session).get_index_price("eth_usd"))

    assert price == Decimal("1.25")
    assert len(fake_session.requests) == 3


def test_400_is_not_retried() -> None:
    fake_session = FakeSession([FakeResponse(400)])

    with pytest.raises(DeribitRequestError, match="HTTP 400"):
        asyncio.run(make_client(fake_session).get_index_price("btc_usd"))

    assert len(fake_session.requests) == 1


def test_timeout_is_retried() -> None:
    fake_session = FakeSession(
        [
            TimeoutError(),
            FakeResponse(200, '{"jsonrpc":"2.0","result":{"index_price":2}}'),
        ]
    )

    assert asyncio.run(make_client(fake_session).get_index_price("btc_usd")) == 2
    assert len(fake_session.requests) == 2


@pytest.mark.parametrize(
    "actions",
    [
        [FakeResponse(500), FakeResponse(503), FakeResponse(429)],
        [TimeoutError(), TimeoutError(), TimeoutError()],
        [aiohttp.ClientConnectionError("offline") for _ in range(3)],
    ],
)
def test_transient_errors_stop_after_three_attempts(
    actions: list[FakeResponse | Exception],
) -> None:
    fake_session = FakeSession(actions)

    with pytest.raises(DeribitRequestError):
        asyncio.run(make_client(fake_session).get_index_price("btc_usd"))

    assert len(fake_session.requests) == 3
