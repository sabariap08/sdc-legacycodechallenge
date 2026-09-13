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

MAX_ZIP_SIZE_MB = int(os.getenv("MAX_ZIP_SIZE_MB", "100"))

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
SENDER_DISPLAY_NAME = os.getenv("SENDER_DISPLAY_NAME", "Legacy Code Rescue")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "") or EMAIL_ADDRESS
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "") or EMAIL_PASSWORD
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes")
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "false").lower() in ("1", "true", "yes")

if EMAIL_ADDRESS and SENDER_DISPLAY_NAME:
    _default_from = f"{SENDER_DISPLAY_NAME} <{EMAIL_ADDRESS}>"
elif EMAIL_ADDRESS:
    _default_from = EMAIL_ADDRESS
else:
    _default_from = ""
SMTP_FROM = os.getenv("SMTP_FROM", "") or _default_from

if not MONGODB_URI:
    logger.error("MONGODB_URI is missing. Set the MONGODB_URI environment variable.")
elif not MONGODB_URI.startswith("mongodb"):
    logger.error("MONGODB_URI appears malformed (does not start with 'mongodb').")
elif MONGODB_URI.startswith("mongodb://localhost") or MONGODB_URI.startswith("mongodb://127.0.0.1"):
    logger.warning("MONGODB_URI points to localhost. This is expected only for local development.")
else:
    logger.info("MONGODB_URI configured (Atlas URI detected).")
