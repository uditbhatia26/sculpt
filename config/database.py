from sqlalchemy import create_engine, pool
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from pathlib import Path
import os
from dotenv import load_dotenv
import logging

# Explicitly load backend/.env (not config/.env which is the old Supabase stub)
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_env_path)

logger = logging.getLogger("Sculpt")

# Neon Database — single connection string from .env
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError(
        "DATABASE_URL is not set. Add it to your .env file.\n"
        "Format: postgresql://user:password@host/dbname?sslmode=require"
    )

# Ensure the URL uses the psycopg2 driver dialect
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

# SQLAlchemy engine — QueuePool works well with Neon's PgBouncer pooler
engine = create_engine(
    DATABASE_URL,
    poolclass=pool.QueuePool,
    pool_size=5,           # Keep 5 persistent connections
    max_overflow=10,       # Allow up to 10 extra connections under load
    pool_pre_ping=True,    # Drop and recreate stale connections (Neon idles after 5 min)
    pool_recycle=300,      # Recycle connections every 5 minutes
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Test database connection on startup
def test_connection():
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("[OK] Successfully connected to Neon database")
        return True
    except Exception as e:
        logger.error(f"[ERROR] Failed to connect to Neon database: {str(e)}")
        return False