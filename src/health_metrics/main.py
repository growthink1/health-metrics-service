"""FastAPI app entry — health-metrics-service."""

import sys

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .jobs.scheduler import build_scheduler
from .routes import health as health_route, ingest as ingest_route, api as api_route
from .routes.chat import router as chat_router
from .routes.goals import router as goals_router
from .routes.meals import router as meals_router
from .routes.session_brief import router as session_brief_router
from .routes.workout_sets import router as workout_sets_router
from .routes.workouts_manual import router as workouts_manual_router


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
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(health_route.router)
app.include_router(ingest_route.router)
app.include_router(api_route.router)
app.include_router(chat_router)
app.include_router(meals_router)
app.include_router(workout_sets_router)
app.include_router(workouts_manual_router)
app.include_router(goals_router)
app.include_router(session_brief_router)

log.info("app_initialized", version="0.1.0", user_id=settings.user_id)

_scheduler = None


@app.on_event("startup")
async def _start_scheduler():
    global _scheduler
    _scheduler = build_scheduler(settings.user_id)
    _scheduler.start()


@app.on_event("shutdown")
async def _stop_scheduler():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
