"""Celery application factory and explicit price collection task registration."""

import asyncio
import logging

from celery import Celery, Task

from app.collect_once import collect_once
from app.core.config import Settings

logger = logging.getLogger(__name__)


class PriceCollectionError(RuntimeError):
    """Neither index price was collected in this cycle."""


def create_celery_app(settings: Settings) -> Celery:
    celery_app = Celery(main=settings.app_name, broker=settings.celery_broker_url)
    celery_app.conf.update(
        timezone="UTC",
        enable_utc=True,
        task_time_limit=45,
        worker_prefetch_multiplier=1,
        beat_schedule={
            "collect-prices-every-minute": {
                "task": "prices.collect",
                "schedule": 60.0,
                "options": {"expires": 50},
            }
        },
    )

    @celery_app.task(name="prices.collect", bind=True)
    def collect_prices(task: Task) -> dict[str, object]:
        task_id = task.request.id
        logger.info("Price collection started: task_id=%s", task_id)

        try:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                raise RuntimeError(
                    "A Celery worker task requires no running event loop"
                )

            result = asyncio.run(collect_once(settings))
            if not result.saved_tickers:
                raise PriceCollectionError(
                    f"No index prices collected; failed={result.failed_tickers}"
                )
        except Exception:
            logger.exception("Price collection failed: task_id=%s", task_id)
            raise

        logger.info(
            "Price collection finished: task_id=%s saved=%s failed=%s timestamp=%s",
            task_id,
            result.saved_tickers,
            result.failed_tickers,
            result.timestamp,
        )
        return {
            "saved_tickers": result.saved_tickers,
            "failed_tickers": result.failed_tickers,
            "timestamp": result.timestamp,
        }

    return celery_app
