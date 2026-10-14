"""Celery application factory; scheduled tasks will be added later."""

from celery import Celery

from app.core.config import Settings


def create_celery_app(settings: Settings) -> Celery:
    return Celery(main=settings.app_name, broker=settings.celery_broker_url)
