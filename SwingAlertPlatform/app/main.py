from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routers import alerts, backtests, health, signals, symbols, users
from app.logging_conf import configure_logging
from app.scheduler.bootstrap import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    start_scheduler()
    yield
    stop_scheduler()


def create_app() -> FastAPI:
    app = FastAPI(title="SwingAlertPlatform", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(signals.router)
    app.include_router(alerts.router)
    app.include_router(users.router)
    app.include_router(symbols.router)
    app.include_router(backtests.router)
    return app


app = create_app()
