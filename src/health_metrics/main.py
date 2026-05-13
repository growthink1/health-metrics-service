"""FastAPI app entry — health-metrics-service."""

import sys

import structlog
from fastapi import FastAPI

from .config import get_settings
from .routes import health as health_route


def configure_logging(log_level: str) -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.dev.ConsoleRenderer()
            if sys.stderr.isatty()
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


settings = get_settings()
configure_logging(settings.log_level)
log = structlog.get_logger()

app = FastAPI(title="health-metrics-service", version="0.1.0")
app.include_router(health_route.router)

log.info("app_initialized", version="0.1.0", user_id=settings.user_id)
