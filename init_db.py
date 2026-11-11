"""
Database setup and schema status, driven by Alembic.

    python init_db.py            # bring the database to the latest schema (alembic upgrade head)
    python init_db.py --status   # show the database revision and whether it is up to date

Schema changes are Alembic migrations in migrations/versions/. To add one:
    alembic revision --autogenerate -m "describe the change"
then review the generated file before running it.
"""

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent


def _alembic_config():
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return cfg


def schema_status() -> tuple:
    """Return (current_revision, head_revision) for the configured database."""
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from config.database import engine

    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    return current, head


def check_schema() -> None:
    """Log a warning at startup if the database isn't on the latest migration. Never raises."""
    try:
        current, head = schema_status()
        if current != head:
            logger.warning(
                f"[SCHEMA] Database is at revision {current or 'none'}, latest is {head}. "
                "Run: python init_db.py"
            )
    except Exception as e:
        logger.warning(f"[SCHEMA] Could not check the schema revision: {e}")


def upgrade() -> None:
    from alembic import command

    command.upgrade(_alembic_config(), "head")


if __name__ == "__main__":
    if "--status" in sys.argv:
        current, head = schema_status()
        print(f"database: {current or 'none'}   latest: {head}   {'up to date' if current == head else 'NEEDS UPGRADE'}")
    else:
        upgrade()
        logger.info("[DONE] Database is on the latest schema.")
