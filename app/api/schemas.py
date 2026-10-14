"""HTTP response schemas for stored prices."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_serializer


class PriceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticker: str
    price: Decimal
    timestamp: int

    @field_serializer("price", when_used="json")
    def serialize_price(self, price: Decimal) -> str:
        return str(price)


class PricePage(BaseModel):
    items: list[PriceRead]
    limit: int
    offset: int
