"""Collect and persist one pair of Deribit index prices."""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.infrastructure.deribit_client import DeribitClient
from app.repositories.prices import PriceRepository

logger = logging.getLogger(__name__)


def _current_timestamp() -> int:
    return int(time.time())


class PricePersistenceError(Exception):
    """The database transaction for a collection cycle failed."""


@dataclass(frozen=True, slots=True)
class CollectionResult:
    saved_tickers: tuple[str, ...]
    failed_tickers: tuple[str, ...]
    timestamp: int | None


class PriceService:
    TICKERS = ("btc_usd", "eth_usd")

    def __init__(
        self,
        client: DeribitClient,
        repository: PriceRepository,
        session: Session,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self._client = client
        self._repository = repository
        self._session = session
        self._clock = clock or _current_timestamp

    async def collect_prices(self) -> CollectionResult:
        outcomes = await asyncio.gather(
            *(self._client.get_index_price(ticker) for ticker in self.TICKERS),
            return_exceptions=True,
        )

        prices: dict[str, Decimal] = {}
        failed_tickers: list[str] = []
        for ticker, outcome in zip(self.TICKERS, outcomes, strict=True):
            if isinstance(outcome, asyncio.CancelledError):
                raise outcome
            if isinstance(outcome, Exception):
                logger.error("Failed to fetch %s from Deribit: %s", ticker, outcome)
                failed_tickers.append(ticker)
            elif isinstance(outcome, BaseException):
                raise outcome
            elif (
                not isinstance(outcome, Decimal)
                or not outcome.is_finite()
                or outcome <= 0
            ):
                logger.error("Deribit returned an invalid price for %s", ticker)
                failed_tickers.append(ticker)
            else:
                prices[ticker] = outcome

        if not prices:
            result = CollectionResult((), tuple(failed_tickers), None)
            logger.info(
                "Collection cycle finished: saved=%s failed=%s",
                (),
                result.failed_tickers,
            )
            return result

        timestamp = self._clock()
        try:
            for ticker, price in prices.items():
                self._repository.add_price(ticker, price, timestamp)
            self._session.commit()
        except Exception as exc:
            self._session.rollback()
            logger.exception("Failed to save the Deribit price collection cycle")
            raise PricePersistenceError("Could not save collected prices") from exc

        result = CollectionResult(tuple(prices), tuple(failed_tickers), timestamp)
        logger.info(
            "Saved Deribit prices for %s at %s", result.saved_tickers, timestamp
        )
        logger.info(
            "Collection cycle finished: saved=%s failed=%s",
            result.saved_tickers,
            result.failed_tickers,
        )
        return result
