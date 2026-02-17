import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from dotenv import load_dotenv

load_dotenv()

# ── URL handling ───────────────────────────────────────────────────────────────
# Never fall back to a hardcoded URL with credentials.
# Set DATABASE_URL in your .env file.
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is required. Set it in your .env file.")

# asyncpg requires the postgresql+asyncpg:// scheme
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)


# ── Engine ─────────────────────────────────────────────────────────────────────
engine = create_async_engine(
    DATABASE_URL,
    # Pool sizing: set pool_size to match your expected steady-state concurrency.
    # max_overflow allows bursting beyond pool_size under peak load.
    pool_size=10,
    max_overflow=20,
    # Pre-ping tests connections before use; avoids errors from stale/dropped connections.
    pool_pre_ping=True,
    # How long to wait for a connection from the pool before raising an error.
    pool_timeout=30,
    # Recycle connections every 30 minutes to avoid hitting DB-side idle timeouts.
    pool_recycle=1800,
    # Disable SQL echoing in production; enable for debugging only.
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",
)


# ── Session factory ────────────────────────────────────────────────────────────
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    # Prevents lazy-load errors after session closes; objects stay usable.
    expire_on_commit=False,
)

Base = declarative_base()


# ── Dependency ─────────────────────────────────────────────────────────────────
async def get_db():
    """FastAPI dependency that yields an async DB session with auto-rollback on error."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
