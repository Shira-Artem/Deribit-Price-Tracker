"""Uvicorn import target."""

from app.core.application import create_app
from app.core.config import Settings

app = create_app(Settings())
