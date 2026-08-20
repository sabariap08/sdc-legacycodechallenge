import os
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
DATABASE_NAME = os.getenv("DATABASE_NAME", "legacy_code_rescue")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin@123")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))
MAX_BINS = int(os.getenv("MAX_BINS", "40"))
TOTAL_CHALLENGES = int(os.getenv("TOTAL_CHALLENGES", "10"))
EVENT_DURATION_MINUTES = int(os.getenv("EVENT_DURATION_MINUTES", "300"))
CHALLENGE_STORAGE_PATH = os.path.join(PROJECT_ROOT, os.getenv("CHALLENGE_STORAGE_DIR", "challenge_storage"))
TEAM_WORKSPACE_PATH = os.path.join(PROJECT_ROOT, os.getenv("TEAM_WORKSPACE_DIR", "team_workspaces"))
EVALUATOR_PATH = os.path.join(PROJECT_ROOT, os.getenv("EVALUATOR_DIR", "evaluator"))
