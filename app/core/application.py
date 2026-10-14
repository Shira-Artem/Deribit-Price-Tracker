"""FastAPI application factory."""

from fastapi import FastAPI

from app.api.health import router as health_router
from app.core.config import Settings


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.include_router(health_router)
    return app
