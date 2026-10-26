"""
Database Initialization Script
Creates all tables in the Supabase PostgreSQL database
"""

from config.database import engine, Base, test_connection
from models.database_models import User, OptimizedResume, GenerationUsage, ParsedJDCache
import logging
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def run_migrations():
    """Apply safe, idempotent column migrations."""
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_byok_allowed BOOLEAN NOT NULL DEFAULT FALSE;"))
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE;"))
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_token_hash VARCHAR;"))
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_reset_expires_at TIMESTAMPTZ;"))
            conn.commit()
            logger.info("[OK] Verified/applied column migrations for users (is_byok_allowed, is_admin)")
        return True
    except Exception as e:
        logger.warning(f"[MIGRATION] Migration note: {e}")
        return False


def init_database():
    """Initialize database by creating all tables."""
    logger.info("[...] Starting database initialization...")

    if not test_connection():
        logger.error("[ERROR] Cannot connect to database. Check .env credentials.")
        return False

    try:
        Base.metadata.create_all(bind=engine)
        run_migrations()
        logger.info("[OK] Successfully created all tables:")
        logger.info("   - users               (auth + resume storage)")
        logger.info("   - optimized_resumes   (generation history)")
        logger.info("   - generation_usage    (weekly paywall tracking)")
        logger.info("   - parsed_jd_cache     (LLM JD parse cache)")
        return True
    except Exception as e:
        logger.error(f"[ERROR] Failed to create tables: {e}")
        return False


def drop_all_tables():
    """Drop all tables — use with caution!"""
    logger.warning("[WARN] Dropping all tables...")
    try:
        Base.metadata.drop_all(bind=engine)
        logger.info("[OK] All tables dropped.")
        return True
    except Exception as e:
        logger.error(f"[ERROR] Failed to drop tables: {e}")
        return False


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--drop":
        drop_all_tables()
    init_database()
    logger.info("[DONE] Database initialization complete!")
