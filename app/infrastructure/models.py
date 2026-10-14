"""Database mappings shared by API and background workers."""

from decimal import Decimal

from sqlalchemy import BigInteger, Index, Integer, Numeric, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative metadata for Alembic and isolated tests."""


class Price(Base):
    __tablename__ = "prices"
    __table_args__ = (Index("ix_prices_ticker_timestamp", "ticker", "timestamp"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    timestamp: Mapped[int] = mapped_column(BigInteger, nullable=False)
