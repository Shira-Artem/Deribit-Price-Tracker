"""Read-only HTTP routes for saved Deribit index prices."""

from datetime import UTC, date, datetime, time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.schemas import PricePage, PriceRead
from app.core.dependencies import get_price_repository
from app.repositories.prices import PriceRepository

Ticker = Literal["btc_usd", "eth_usd"]
Repository = Annotated[PriceRepository, Depends(get_price_repository)]
TickerQuery = Annotated[Ticker, Query(description="Deribit index ticker")]
LimitQuery = Annotated[int, Query(ge=1, le=1000, description="Page size")]
OffsetQuery = Annotated[int, Query(ge=0, description="Rows to skip")]

router = APIRouter(
    prefix="/api/v1/prices",
    tags=["prices"],
    responses={500: {"description": "Database operation failed"}},
)


@router.get(
    "",
    response_model=PricePage,
    summary="List saved prices",
    description="Return one ticker's prices, newest first, with limit/offset paging.",
)
def list_prices(
    ticker: TickerQuery,
    repository: Repository,
    limit: LimitQuery = 100,
    offset: OffsetQuery = 0,
) -> PricePage:
    rows = repository.list_by_ticker(ticker, limit, offset)
    return PricePage(
        items=[PriceRead.model_validate(row) for row in rows],
        limit=limit,
        offset=offset,
    )


@router.get(
    "/latest",
    response_model=PriceRead,
    summary="Get the latest saved price",
    description=(
        "Return the newest stored price for one ticker; no exchange request is made."
    ),
    responses={404: {"description": "No saved price for this ticker"}},
)
def get_latest_price(ticker: TickerQuery, repository: Repository) -> PriceRead:
    row = repository.get_latest(ticker)
    if row is None:
        raise HTTPException(status_code=404, detail="No saved price for this ticker")
    return PriceRead.model_validate(row)


@router.get(
    "/by-date",
    response_model=PricePage,
    summary="List prices for a UTC date",
    description=(
        "Return prices from 00:00 UTC inclusive to the next 00:00 UTC exclusive."
    ),
)
def list_prices_by_date(
    ticker: TickerQuery,
    repository: Repository,
    date_value: Annotated[
        str,
        Query(
            alias="date",
            pattern=r"^\d{4}-\d{2}-\d{2}$",
            description="UTC date (YYYY-MM-DD)",
        ),
    ],
    limit: LimitQuery = 100,
    offset: OffsetQuery = 0,
) -> PricePage:
    try:
        selected_date = date.fromisoformat(date_value)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="date must be a valid YYYY-MM-DD date"
        ) from exc

    start_ts = int(datetime.combine(selected_date, time.min, tzinfo=UTC).timestamp())
    rows = repository.list_by_period(ticker, start_ts, start_ts + 86_400, limit, offset)
    return PricePage(
        items=[PriceRead.model_validate(row) for row in rows],
        limit=limit,
        offset=offset,
    )
