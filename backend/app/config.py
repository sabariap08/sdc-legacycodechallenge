import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MONGODB_URI = os.getenv("MONGODB_URI", "")
DATABASE_NAME = os.getenv("DATABASE_NAME", "legacy_code_rescue")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin@123")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))
CHALLENGE_STORAGE_PATH = os.path.join(PROJECT_ROOT, os.getenv("CHALLENGE_STORAGE_DIR", "challenge_storage"))
TEAM_WORKSPACE_PATH = os.path.join(PROJECT_ROOT, os.getenv("TEAM_WORKSPACE_DIR", "team_workspaces"))
EVALUATOR_PATH = os.path.join(PROJECT_ROOT, os.getenv("EVALUATOR_DIR", "evaluator"))

if not MONGODB_URI:
    logger.error("MONGODB_URI is missing. Set the MONGODB_URI environment variable.")
elif not MONGODB_URI.startswith("mongodb"):
    logger.error("MONGODB_URI appears malformed (does not start with 'mongodb').")
elif MONGODB_URI.startswith("mongodb://localhost") or MONGODB_URI.startswith("mongodb://127.0.0.1"):
    logger.warning("MONGODB_URI points to localhost. This is expected only for local development.")
else:
    logger.info("MONGODB_URI configured (Atlas URI detected).")
