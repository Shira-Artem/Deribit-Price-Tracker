"""HTTP contract for stored prices, backed by an isolated SQLite database."""

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.application import create_app
from app.core.config import Settings
from app.core.dependencies import get_price_repository
from app.infrastructure.models import Base, Price
from app.repositories.prices import PriceRepository


@pytest.fixture
def api() -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app(
        Settings(database_url="sqlite+pysqlite:///:memory:", _env_file=None)
    )

    def override_repository() -> Iterator[PriceRepository]:
        with factory() as session:
            yield PriceRepository(session)

    app.dependency_overrides[get_price_repository] = override_repository
    try:
        with TestClient(app) as client:
            yield client, factory
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def seed_prices(factory: sessionmaker[Session]) -> dict[str, int]:
    start = int(datetime(2026, 10, 14, tzinfo=UTC).timestamp())
    with factory.begin() as session:
        repository = PriceRepository(session)
        rows = {
            "previous": repository.add_price(
                "btc_usd", Decimal("1.00000001"), start - 1
            ),
            "start": repository.add_price("btc_usd", Decimal("2.00000002"), start),
            "tie_first": repository.add_price(
                "btc_usd", Decimal("3.00000003"), start + 60
            ),
            "tie_second": repository.add_price(
                "btc_usd", Decimal("4.00000004"), start + 60
            ),
            "last_second": repository.add_price(
                "btc_usd", Decimal("5.00000005"), start + 86_399
            ),
            "next_day": repository.add_price(
                "btc_usd", Decimal("6.00000006"), start + 86_400
            ),
            "eth": repository.add_price("eth_usd", Decimal("7.00000007"), start + 120),
        }
        return {name: row.id for name, row in rows.items()}


def test_history_is_scoped_sorted_and_paginated(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    ids = seed_prices(factory)

    btc = client.get("/api/v1/prices", params={"ticker": "btc_usd"})
    eth = client.get("/api/v1/prices", params={"ticker": "eth_usd"})
    page = client.get(
        "/api/v1/prices", params={"ticker": "btc_usd", "limit": 2, "offset": 2}
    )

    assert btc.status_code == eth.status_code == page.status_code == 200
    assert [item["id"] for item in btc.json()["items"]] == [
        ids["next_day"],
        ids["last_second"],
        ids["tie_second"],
        ids["tie_first"],
        ids["start"],
        ids["previous"],
    ]
    assert btc.json()["limit"] == 100
    assert btc.json()["offset"] == 0
    assert [item["id"] for item in eth.json()["items"]] == [ids["eth"]]
    assert [item["id"] for item in page.json()["items"]] == [
        ids["tie_second"],
        ids["tie_first"],
    ]
    assert page.json()["limit"] == 2
    assert page.json()["offset"] == 2
    assert isinstance(page.json()["items"][0]["timestamp"], int)


def test_latest_and_empty_results(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api

    assert client.get("/api/v1/prices", params={"ticker": "btc_usd"}).json() == {
        "items": [],
        "limit": 100,
        "offset": 0,
    }
    missing = client.get("/api/v1/prices/latest", params={"ticker": "btc_usd"})
    assert missing.status_code == 404

    ids = seed_prices(factory)
    latest = client.get("/api/v1/prices/latest", params={"ticker": "btc_usd"})
    assert latest.status_code == 200
    assert latest.json()["id"] == ids["next_day"]
    assert latest.json()["ticker"] == "btc_usd"


def test_utc_date_includes_start_excludes_end_and_supports_paging(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    ids = seed_prices(factory)

    response = client.get(
        "/api/v1/prices/by-date",
        params={"ticker": "btc_usd", "date": "2026-10-14"},
    )
    page = client.get(
        "/api/v1/prices/by-date",
        params={"ticker": "btc_usd", "date": "2026-10-14", "limit": 2, "offset": 2},
    )
    empty = client.get(
        "/api/v1/prices/by-date",
        params={"ticker": "eth_usd", "date": "2026-10-13"},
    )

    assert response.status_code == page.status_code == empty.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [
        ids["last_second"],
        ids["tie_second"],
        ids["tie_first"],
        ids["start"],
    ]
    assert [item["id"] for item in page.json()["items"]] == [
        ids["tie_first"],
        ids["start"],
    ]
    assert empty.json()["items"] == []


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/v1/prices", {}),
        ("/api/v1/prices", {"ticker": "other"}),
        ("/api/v1/prices/latest", {"ticker": "BTC_USD"}),
        ("/api/v1/prices/by-date", {"ticker": "btc_usd"}),
        ("/api/v1/prices", {"ticker": "btc_usd", "limit": 0}),
        ("/api/v1/prices", {"ticker": "btc_usd", "limit": 1001}),
        ("/api/v1/prices", {"ticker": "btc_usd", "offset": -1}),
        (
            "/api/v1/prices/by-date",
            {"ticker": "btc_usd", "date": "2026-02-30"},
        ),
        (
            "/api/v1/prices/by-date",
            {"ticker": "btc_usd", "date": "2026-2-3"},
        ),
    ],
)
def test_invalid_queries_return_422(
    api: tuple[TestClient, sessionmaker[Session]],
    path: str,
    params: dict[str, str | int],
) -> None:
    client, _factory = api
    assert client.get(path, params=params).status_code == 422


def test_decimal_is_serialized_as_exact_string(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    ids = seed_prices(factory)

    response = client.get("/api/v1/prices/latest", params={"ticker": "eth_usd"})

    assert response.status_code == 200
    assert response.json()["id"] == ids["eth"]
    assert response.json()["price"] == "7.00000007"
    assert isinstance(response.json()["price"], str)


def test_docs_and_openapi_describe_routes(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, _factory = api

    assert client.get("/docs").status_code == 200
    schema_response = client.get("/openapi.json")

    assert schema_response.status_code == 200
    paths = schema_response.json()["paths"]
    assert set(paths) >= {
        "/api/v1/prices",
        "/api/v1/prices/latest",
        "/api/v1/prices/by-date",
    }
    for path in ("/api/v1/prices", "/api/v1/prices/latest", "/api/v1/prices/by-date"):
        operation = paths[path]["get"]
        assert any(
            parameter["name"] == "ticker" for parameter in operation["parameters"]
        )
        assert "200" in operation["responses"]
        assert "422" in operation["responses"]
        assert "500" in operation["responses"]
    assert "404" in paths["/api/v1/prices/latest"]["get"]["responses"]


def test_get_requests_do_not_change_database(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    seed_prices(factory)
    with factory() as session:
        before = session.scalar(select(func.count()).select_from(Price))

    for path, params in (
        ("/api/v1/prices", {"ticker": "btc_usd"}),
        ("/api/v1/prices/latest", {"ticker": "eth_usd"}),
        ("/api/v1/prices/by-date", {"ticker": "btc_usd", "date": "2026-10-14"}),
    ):
        assert client.get(path, params=params).status_code == 200

    with factory() as session:
        after = session.scalar(select(func.count()).select_from(Price))
    assert before == after == 7


def test_database_error_is_500_without_internal_details(
    api: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = api
    Base.metadata.drop_all(factory.kw["bind"])

    response = client.get("/api/v1/prices", params={"ticker": "btc_usd"})

    assert response.status_code == 500
    assert response.json() == {"detail": "Database operation failed"}
    assert "sqlite" not in response.text.lower()
    assert client.get("/health").json() == {"status": "ok"}


def test_health_does_not_require_postgresql() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://invalid:secret@127.0.0.1:1/missing",
        _env_file=None,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_real_dependency_closes_sessions_after_success_and_failure(monkeypatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    closed_sessions: list[Session] = []

    class TrackingSession(Session):
        def close(self) -> None:
            closed_sessions.append(self)
            super().close()

    factory = sessionmaker(bind=engine, class_=TrackingSession)
    created_for: list[Settings] = []

    def tracked_create_engine(settings: Settings) -> Engine:
        created_for.append(settings)
        return engine

    monkeypatch.setattr("app.core.application.create_db_engine", tracked_create_engine)
    monkeypatch.setattr(
        "app.core.application.create_session_factory", lambda _engine: factory
    )
    settings = Settings(database_url="sqlite+pysqlite://", _env_file=None)
    app = create_app(settings)
    assert created_for == []

    with TestClient(app) as client:
        assert created_for == [settings]
        assert (
            client.get("/api/v1/prices", params={"ticker": "btc_usd"}).status_code
            == 200
        )
        Base.metadata.drop_all(engine)
        assert (
            client.get("/api/v1/prices", params={"ticker": "btc_usd"}).status_code
            == 500
        )

    assert len(closed_sessions) == 2
