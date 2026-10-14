"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from app.api.health import router as health_router
from app.api.prices import router as prices_router
from app.core.config import Settings
from app.infrastructure.database import create_db_engine, create_session_factory

logger = logging.getLogger(__name__)


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_db_engine(settings)
        try:
            app.state.session_factory = create_session_factory(engine)
            yield
        finally:
            engine.dispose()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.settings = settings

    @app.exception_handler(SQLAlchemyError)
    async def handle_database_error(
        _request: Request, exc: SQLAlchemyError
    ) -> JSONResponse:
        logger.error(
            "Database operation failed",
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Database operation failed"},
        )

    app.include_router(health_router)
    app.include_router(prices_router)
    return app
