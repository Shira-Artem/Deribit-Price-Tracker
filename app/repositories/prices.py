"""Persistence operations for index prices."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.models import Price


class PriceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_price(self, ticker: str, price: Decimal, timestamp: int) -> Price:
        row = Price(ticker=ticker, price=price, timestamp=timestamp)
        self.session.add(row)
        self.session.flush()
        return row

    def get_latest(self, ticker: str) -> Price | None:
        statement = (
            select(Price)
            .where(Price.ticker == ticker)
            .order_by(Price.timestamp.desc(), Price.id.desc())
            .limit(1)
        )
        return self.session.scalar(statement)

    def list_by_ticker(self, ticker: str, limit: int, offset: int) -> list[Price]:
        statement = (
            select(Price)
            .where(Price.ticker == ticker)
            .order_by(Price.timestamp.desc(), Price.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(statement))

    def list_by_period(
        self,
        ticker: str,
        start_ts: int,
        end_ts: int,
        limit: int,
        offset: int,
    ) -> list[Price]:
        statement = (
            select(Price)
            .where(
                Price.ticker == ticker,
                Price.timestamp >= start_ts,
                Price.timestamp < end_ts,
            )
            .order_by(Price.timestamp.desc(), Price.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(statement))
