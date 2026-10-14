"""Fetch index prices from Deribit's public JSON-RPC endpoint."""

import asyncio
import json
import logging
from decimal import Decimal

import aiohttp

from app.core.config import Settings

logger = logging.getLogger(__name__)


class DeribitClientError(Exception):
    """Base error for a failed Deribit price request."""


class DeribitRequestError(DeribitClientError):
    """HTTP or connection failure."""


class DeribitResponseError(DeribitClientError):
    """Invalid JSON-RPC response or index price."""


class DeribitClient:
    MAX_ATTEMPTS = 3

    def __init__(
        self,
        session: aiohttp.ClientSession,
        settings: Settings,
        retry_delay_seconds: float = 0.2,
    ) -> None:
        self._session = session
        self._url = (
            f"{settings.deribit_api_base_url.rstrip('/')}/public/get_index_price"
        )
        self._timeout = aiohttp.ClientTimeout(total=settings.http_timeout_seconds)
        self._retry_delay_seconds = retry_delay_seconds

    async def get_index_price(self, ticker: str) -> Decimal:
        for attempt in range(self.MAX_ATTEMPTS):
            try:
                async with self._session.get(
                    self._url,
                    params={"index_name": ticker},
                    timeout=self._timeout,
                ) as response:
                    if response.status == 429 or response.status >= 500:
                        if attempt == self.MAX_ATTEMPTS - 1:
                            raise DeribitRequestError(
                                f"Deribit returned HTTP {response.status} for {ticker}"
                            )
                    elif response.status >= 400:
                        raise DeribitRequestError(
                            f"Deribit returned HTTP {response.status} for {ticker}"
                        )
                    else:
                        price = self._parse_price(await response.text())
                        logger.info("Received Deribit price for %s: %s", ticker, price)
                        return price
            except (TimeoutError, aiohttp.ClientConnectionError) as exc:
                if attempt == self.MAX_ATTEMPTS - 1:
                    raise DeribitRequestError(
                        f"Deribit request failed for {ticker} after "
                        f"{self.MAX_ATTEMPTS} attempts"
                    ) from exc

            await asyncio.sleep(self._retry_delay_seconds * (attempt + 1))

        raise AssertionError("Unreachable retry state")

    @staticmethod
    def _parse_price(body: str) -> Decimal:
        try:
            payload = json.loads(body, parse_float=Decimal, parse_constant=Decimal)
        except (json.JSONDecodeError, ValueError) as exc:
            raise DeribitResponseError("Deribit returned invalid JSON") from exc

        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
            raise DeribitResponseError("Deribit returned an invalid JSON-RPC response")
        if payload.get("error") is not None:
            raise DeribitResponseError(f"Deribit JSON-RPC error: {payload['error']}")

        result = payload.get("result")
        if not isinstance(result, dict) or "index_price" not in result:
            raise DeribitResponseError("Deribit response has no index_price")

        raw_price = result["index_price"]
        if isinstance(raw_price, bool) or not isinstance(raw_price, (Decimal, int)):
            raise DeribitResponseError("Deribit returned a nonnumeric index_price")
        price = Decimal(raw_price)
        if not price.is_finite() or price <= 0:
            raise DeribitResponseError("Deribit returned an invalid index_price")
        return price
