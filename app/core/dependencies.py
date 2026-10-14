"""Request-scoped dependencies for the HTTP API."""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from app.repositories.prices import PriceRepository


def get_price_repository(request: Request) -> Iterator[PriceRepository]:
    session_factory: sessionmaker[Session] = request.app.state.session_factory
    with session_factory() as session:
        yield PriceRepository(session)
