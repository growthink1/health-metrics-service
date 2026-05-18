"""Async SQLAlchemy engine + session factory."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings

settings = get_settings()


def _async_url(url: str) -> str:
    """Coerce a sync postgresql:// URL to postgresql+asyncpg:// for create_async_engine.

    Railway's auto-injected DATABASE_URL uses the bare `postgresql://` scheme;
    SQLAlchemy's async engine needs an async driver. asyncpg is already in our
    requirements. This is a no-op if the URL is already `postgresql+asyncpg://`
    or any other scheme.
    """
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    return url


engine = create_async_engine(_async_url(settings.database_url), echo=False, future=True)

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
