"""Smoke tests for the first project stage."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.application import create_app
from app.core.config import Settings


def test_create_app() -> None:
    settings = Settings(_env_file=None)

    app = create_app(settings)

    assert isinstance(app, FastAPI)
    assert app.state.settings is settings


def test_health_returns_ok() -> None:
    app = create_app(Settings(_env_file=None))

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_settings_can_be_replaced_per_app() -> None:
    first_settings = Settings(app_name="Tracker A", _env_file=None)
    second_settings = Settings(app_name="Tracker B", _env_file=None)

    first_app = create_app(first_settings)
    second_app = create_app(second_settings)

    assert first_app.title == "Tracker A"
    assert second_app.title == "Tracker B"
    assert first_app.state.settings is first_settings
    assert second_app.state.settings is second_settings
