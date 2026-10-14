"""Collect and persist one pair of Deribit index prices."""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from sqlalchemy.orm import Session

from app.core.tickers import Ticker
from app.repositories.prices import PriceRepository

logger = logging.getLogger(__name__)


def _current_timestamp() -> int:
    return int(time.time())


class PricePersistenceError(Exception):
    """The database transaction for a collection cycle failed."""


class PriceClient(Protocol):
    async def get_index_price(self, ticker: str) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class CollectionResult:
    saved_tickers: tuple[str, ...]
    failed_tickers: tuple[str, ...]
    timestamp: int | None


class PriceService:
    def __init__(
        self,
        client: PriceClient,
        repository: PriceRepository,
        session: Session,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self._client = client
        self._repository = repository
        self._session = session
        self._clock = clock or _current_timestamp

    async def collect_prices(self) -> CollectionResult:
        prices, failed_tickers = await self._fetch_prices()
        timestamp = self._save_prices(prices) if prices else None
        result = CollectionResult(tuple(prices), tuple(failed_tickers), timestamp)
        logger.info(
            "Collection cycle finished: saved=%s failed=%s timestamp=%s",
            result.saved_tickers,
            result.failed_tickers,
            result.timestamp,
        )
        return result

    async def _fetch_prices(self) -> tuple[dict[str, Decimal], list[str]]:
        tickers = tuple(Ticker)
        outcomes = await asyncio.gather(
            *(self._client.get_index_price(ticker.value) for ticker in tickers),
            return_exceptions=True,
        )

        prices: dict[str, Decimal] = {}
        failed_tickers: list[str] = []
        for ticker, outcome in zip(tickers, outcomes, strict=True):
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            if isinstance(outcome, Exception):
                logger.error("Failed to fetch %s from Deribit: %s", ticker, outcome)
                failed_tickers.append(ticker.value)
            elif isinstance(outcome, BaseException):
                raise outcome
            elif (
                not isinstance(outcome, Decimal)
                or not outcome.is_finite()
                or outcome <= 0
            ):
                logger.error("Deribit returned an invalid price for %s", ticker)
                failed_tickers.append(ticker.value)
            else:
                prices[ticker.value] = outcome

        return prices, failed_tickers

    def _save_prices(self, prices: dict[str, Decimal]) -> int:
        timestamp = self._clock()
        try:
            for ticker, price in prices.items():
                self._repository.add_price(ticker, price, timestamp)
            self._session.commit()
        except Exception as exc:
            self._session.rollback()
            logger.exception("Failed to save the Deribit price collection cycle")
            raise PricePersistenceError("Could not save collected prices") from exc
        return timestamp
