"""Celery factory and task tests without external services."""

import asyncio

import pytest
from celery import Celery

from app.core.config import Settings
from app.services.price_service import CollectionResult, PricePersistenceError
from app.tasks.celery_app import PriceCollectionError, create_celery_app


def make_settings() -> Settings:
    return Settings(
        app_name="Test Tracker",
        celery_broker_url="redis://example.invalid:6379/5",
        _env_file=None,
    )


def test_factory_registers_task_and_schedule() -> None:
    settings = make_settings()

    app = create_celery_app(settings)

    assert isinstance(app, Celery)
    assert app.main == settings.app_name
    assert app.conf.broker_url == settings.celery_broker_url
    assert "prices.collect" in app.tasks
    assert len(app.conf.beat_schedule) == 1
    schedule = app.conf.beat_schedule["collect-prices-every-minute"]
    assert schedule["task"] == "prices.collect"
    assert schedule["schedule"] == 60.0
    assert schedule["options"]["expires"] == 50
    assert app.conf.timezone == "UTC"
    assert app.conf.enable_utc is True
    assert app.conf.task_time_limit == 45


def test_task_calls_existing_collector_with_explicit_settings(monkeypatch) -> None:
    settings = make_settings()
    received: list[Settings] = []

    async def fake_collect_once(given_settings: Settings) -> CollectionResult:
        received.append(given_settings)
        return CollectionResult(("btc_usd", "eth_usd"), (), 123)

    monkeypatch.setattr("app.tasks.celery_app.collect_once", fake_collect_once)
    task = create_celery_app(settings).tasks["prices.collect"]

    result = task.apply(throw=True).result

    assert received == [settings]
    assert result == {
        "saved_tickers": ("btc_usd", "eth_usd"),
        "failed_tickers": (),
        "timestamp": 123,
    }


def test_one_failed_ticker_is_partial_success(monkeypatch) -> None:
    async def fake_collect_once(_settings: Settings) -> CollectionResult:
        return CollectionResult(("btc_usd",), ("eth_usd",), 123)

    monkeypatch.setattr("app.tasks.celery_app.collect_once", fake_collect_once)
    task = create_celery_app(make_settings()).tasks["prices.collect"]

    result = task.apply(throw=True)

    assert result.successful()
    assert result.result["saved_tickers"] == ("btc_usd",)
    assert result.result["failed_tickers"] == ("eth_usd",)


def test_both_failed_tickers_fail_task_without_retry(monkeypatch) -> None:
    calls = 0

    async def fake_collect_once(_settings: Settings) -> CollectionResult:
        nonlocal calls
        calls += 1
        return CollectionResult((), ("btc_usd", "eth_usd"), None)

    monkeypatch.setattr("app.tasks.celery_app.collect_once", fake_collect_once)
    task = create_celery_app(make_settings()).tasks["prices.collect"]

    result = task.apply(throw=False)

    assert result.failed()
    assert isinstance(result.result, PriceCollectionError)
    assert calls == 1


def test_database_failure_propagates(monkeypatch) -> None:
    async def fake_collect_once(_settings: Settings) -> CollectionResult:
        raise PricePersistenceError("database unavailable")

    monkeypatch.setattr("app.tasks.celery_app.collect_once", fake_collect_once)
    task = create_celery_app(make_settings()).tasks["prices.collect"]

    result = task.apply(throw=False)

    assert result.failed()
    assert isinstance(result.result, PricePersistenceError)
    assert str(result.result) == "database unavailable"


def test_running_event_loop_does_not_start_another(monkeypatch) -> None:
    calls = 0

    async def fake_collect_once(_settings: Settings) -> CollectionResult:
        nonlocal calls
        calls += 1
        return CollectionResult(("btc_usd",), (), 123)

    monkeypatch.setattr("app.tasks.celery_app.collect_once", fake_collect_once)
    task = create_celery_app(make_settings()).tasks["prices.collect"]

    async def invoke_inside_running_loop() -> None:
        with pytest.raises(RuntimeError, match="no running event loop"):
            task.apply(throw=True)

    asyncio.run(invoke_inside_running_loop())
    assert calls == 0
