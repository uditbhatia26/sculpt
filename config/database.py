from sqlalchemy import create_engine, pool
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv
import logging

load_dotenv()


logger = logging.getLogger("Sculpt")

# Supabase Database Connection - Fetch variables as per Supabase docs
USER = os.getenv("user")
PASSWORD = os.getenv("password")
HOST = os.getenv("host")
PORT = os.getenv("port")
DBNAME = os.getenv("dbname")

# Validate all required variables are present
if not all([USER, PASSWORD, HOST, PORT, DBNAME]):
    raise ValueError("Database credentials are incomplete. Please check your .env file for: user, password, host, port, dbname")

# Construct connection string
DATABASE_URL = f"postgresql+psycopg2://{USER}:{PASSWORD}@{HOST}:{PORT}/{DBNAME}?sslmode=require"

# Supabase PostgreSQL configuration
engine = create_engine(
    DATABASE_URL,
    poolclass=pool.NullPool,  # Recommended for serverless/cloud databases
    pool_pre_ping=True,  # Verify connections before using them
    echo=False,  # Set to True for SQL query logging (debugging)
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
        logger.info("[OK] Successfully connected to Supabase database")
        return True
    except Exception as e:
        logger.error(f"[ERROR] Failed to connect to Supabase database: {str(e)}")
        return False