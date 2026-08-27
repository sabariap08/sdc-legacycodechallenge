import asyncio
import ssl
import logging
import certifi
from motor.motor_asyncio import AsyncIOMotorClient
from app.config import MONGODB_URI, DATABASE_NAME

logger = logging.getLogger(__name__)

client = None
db = None
_db_available = False
_reconnect_task = None


def is_db_available() -> bool:
    return _db_available and db is not None


def _make_client():
    return AsyncIOMotorClient(
        MONGODB_URI,
        tls=True,
        tlsCAFile=certifi.where(),
        tlsAllowInvalidCertificates=False,
        tlsAllowInvalidHostnames=False,
        serverSelectionTimeoutMS=10000,
        connectTimeoutMS=10000,
        socketTimeoutMS=10000,
    )


async def connect_db():
    global client, db, _db_available

    if not MONGODB_URI:
        logger.error("MONGODB_URI is missing. Set the MONGODB_URI environment variable.")
        _schedule_reconnect()
        return None

    logger.info("Connecting to MongoDB...")

    try:
        client = _make_client()
        db = client[DATABASE_NAME]
        await db.command("ping")
        _db_available = True
        logger.info("MongoDB connected successfully")
        await _create_indexes()
        await _ensure_event_settings()
        return db
    except Exception as e:
        _db_available = False
        logger.error("MongoDB unavailable: %s", e)
        _schedule_reconnect()
        return None


def _schedule_reconnect():
    global _reconnect_task
    if _reconnect_task and not _reconnect_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
        _reconnect_task = loop.create_task(_reconnect_loop())
    except RuntimeError:
        pass


async def _reconnect_loop():
    global client, db, _db_available
    while not _db_available:
        await asyncio.sleep(15)
        try:
            if client is not None:
                client.close()
            client = _make_client()
            db = client[DATABASE_NAME]
            await db.command("ping")
            _db_available = True
            logger.info("MongoDB reconnected successfully")
            await _create_indexes()
            await _ensure_event_settings()
        except Exception as e:
            _db_available = False
            logger.warning("MongoDB reconnect attempt failed: %s", e)


async def close_db():
    global client, _db_available, _reconnect_task
    if _reconnect_task and not _reconnect_task.done():
        _reconnect_task.cancel()
    _db_available = False
    if client:
        client.close()
        client = None


def get_db():
    if not _db_available or db is None:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=503,
            detail="Database service temporarily unavailable. Please try again shortly."
        )
    return db


async def _create_indexes():
    await db.teams.create_index("team_code", unique=True)
    await db.teams.create_index("team_name", unique=True)
    await db.teams.create_index("bin_number", unique=True)
    await db.participants.create_index("email", unique=True)
    await db.participants.create_index("team_code")
    await db.challenges.create_index("challenge_code", unique=True)
    # unique: a team may only be allocated to one challenge per event
    await db.allocations.create_index(["team_code", "event_id"], unique=True)
    await db.allocations.create_index("event_id")
    await db.allocations.create_index("challenge_code")
    await db.submissions.create_index("team_code")
    await db.submissions.create_index("challenge_code")
    await db.submissions.create_index("event_id")
    await db.admins.create_index("username", unique=True)
    await db.audit_logs.create_index("timestamp")
    await db.announcements.create_index("created_at")
    await db.blocked_users.create_index("email", unique=True)
    # New: events collection is the source of truth for event lifecycle
    await db.events.create_index("event_code", unique=True)
    await db.events.create_index("status")
    await db.events.create_index("created_datetime")
    # Compatibility accessor kept for any legacy reads
    await db.event_settings.create_index("event_code", sparse=True)
    await db.event_history.create_index("archived_at")
    await db.event_history.create_index("event_code", sparse=True)


async def _ensure_event_settings():
    """Bootstrap the events collection as the single source of truth.

    A legacy event_settings doc (if present) is migrated into the events
    collection so no historical state is lost.
    """
    from app.utils import generate_event_code
    from datetime import datetime as _dt

    existing_event = await db.events.find_one({})
    if existing_event:
        return

    legacy = await db.event_settings.find_one({})
    if legacy:
        await db.events.insert_one({
            "event_id": legacy.get("event_id") or str(_dt.utcnow().timestamp()),
            "event_code": legacy.get("event_code") or generate_event_code(),
            "event_name": legacy.get("event_name", "Legacy Code Rescue"),
            "status": legacy.get("status", "UPCOMING"),
            "event_start_time": legacy.get("event_start_time"),
            "event_end_time": legacy.get("event_end_time"),
            "event_duration_minutes": legacy.get("event_duration_minutes", 300),
            "leaderboard_enabled": legacy.get("leaderboard_enabled", False),
            "created_datetime": _dt.utcnow(),
            "updated_at": _dt.utcnow(),
            "deleted_datetime": None,
            "deletion_reason": None,
        })
        return

    await db.events.insert_one({
        "event_id": str(_dt.utcnow().timestamp()),
        "event_code": generate_event_code(),
        "event_name": "Legacy Code Rescue",
        "status": "UPCOMING",
        "event_start_time": None,
        "event_end_time": None,
        "event_duration_minutes": 300,
        "leaderboard_enabled": False,
        "created_datetime": _dt.utcnow(),
        "updated_at": _dt.utcnow(),
        "deleted_datetime": None,
        "deletion_reason": None,
    })


async def get_active_event():
    """Return the current (non-deleted) event document, or None."""
    return await db.events.find_one({"deleted_datetime": None})
