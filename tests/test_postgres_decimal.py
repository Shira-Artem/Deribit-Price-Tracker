"""Optional PostgreSQL check for exact NUMERIC persistence."""

import os
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema

from app.core.config import Settings
from app.infrastructure.models import Base, Price
from app.repositories.prices import PriceRepository


def test_postgresql_preserves_large_decimal_exactly() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")

    test_url = make_url(database_url)
    main_url = make_url(Settings().database_url)
    if test_url.get_backend_name() != "postgresql":
        pytest.fail("TEST_DATABASE_URL must point to PostgreSQL")
    if test_url.database == main_url.database:
        pytest.fail("TEST_DATABASE_URL must name a separate database")

    engine = create_engine(test_url)
    schema_name = f"test_decimal_{uuid4().hex}"
    expected = Decimal("12345678901.12345678")
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(CreateSchema(schema_name))
                connection.exec_driver_sql(f"SET LOCAL search_path TO {schema_name}")
                Base.metadata.create_all(connection)
                with Session(
                    connection, join_transaction_mode="create_savepoint"
                ) as session:
                    saved = PriceRepository(session).add_price("btc_usd", expected, 1)
                    saved_id = saved.id
                    session.expunge_all()
                    loaded = session.get(Price, saved_id)
                    assert loaded is not None
                    assert loaded.price == expected
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
