import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from shared import db
from shared.config import get_settings
from shared.redis import get_redis
from shared.utils import logging as slog
from shared.utils.latency import ObservabilityMiddleware, recorder

logger = logging.getLogger("app")


def create_app(service: str, *, title: str, uses_db: bool = True, uses_redis: bool = True,
               on_startup=None, on_shutdown=None) -> FastAPI:
    settings = get_settings()
    slog.setup_logging(service, settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # warm-up: open the DB pool / Redis connection before the first request
        if uses_db:
            async with db.sessionmaker()() as s:
                await s.execute(text("SELECT 1"))
        if uses_redis:
            await get_redis().ping()
        if on_startup:
            await on_startup(app)
        slog.log(logger, "startup complete", service=service)
        yield
        if on_shutdown:
            await on_shutdown(app)

    app = FastAPI(title=title, lifespan=lifespan)
    app.add_middleware(ObservabilityMiddleware, service=service, slow_ms=settings.slow_request_ms)

    @app.get("/health", tags=["ops"])
    async def health():
        checks = {"service": service, "status": "ok"}
        if uses_db:
            try:
                async with db.sessionmaker()() as s:
                    await s.execute(text("SELECT 1"))
                checks["db"] = "ok"
            except Exception as e:  # noqa: BLE001
                checks["db"], checks["status"] = f"error: {e.__class__.__name__}", "degraded"
        if uses_redis:
            try:
                await get_redis().ping()
                checks["redis"] = "ok"
            except Exception as e:  # noqa: BLE001
                checks["redis"], checks["status"] = f"error: {e.__class__.__name__}", "degraded"
        return JSONResponse(checks, status_code=200 if checks["status"] == "ok" else 503)

    @app.get("/metrics", tags=["ops"])
    async def metrics():
        return {"service": service, **recorder.snapshot()}

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        logger.exception("unhandled error")
        return JSONResponse({"detail": "internal error", "request_id": slog.get_ctx().get("request_id")}, status_code=500)

    return app