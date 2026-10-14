"""Collect one pair of prices, then close all resources and exit."""

import asyncio
import logging

import aiohttp

from app.core.config import Settings
from app.infrastructure.database import create_db_engine, create_session_factory
from app.infrastructure.deribit_client import DeribitClient
from app.repositories.prices import PriceRepository
from app.services.price_service import CollectionResult, PriceService

logger = logging.getLogger(__name__)


async def collect_once(settings: Settings) -> CollectionResult:
    engine = create_db_engine(settings)
    try:
        session_factory = create_session_factory(engine)
        async with aiohttp.ClientSession() as http_session:
            with session_factory() as db_session:
                service = PriceService(
                    client=DeribitClient(http_session, settings),
                    repository=PriceRepository(db_session),
                    session=db_session,
                )
                return await service.collect_prices()
    finally:
        engine.dispose()


def main() -> None:
    settings = Settings()
    logging.basicConfig(level=settings.log_level)
    result = asyncio.run(collect_once(settings))
    logger.info(
        "One-shot collection complete: saved=%s failed=%s timestamp=%s",
        result.saved_tickers,
        result.failed_tickers,
        result.timestamp,
    )


if __name__ == "__main__":
    main()
