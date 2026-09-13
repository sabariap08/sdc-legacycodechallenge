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
    await db.admins.create_index("username", unique=True)
    await db.challenges.create_index("challenge_code", unique=True)
    await db.teams.create_index("team_code", unique=True)
    await db.teams.create_index("team_name", unique=True)
    await db.allocations.create_index("team_code", unique=True)
    await db.releases.create_index("released_at")
    await db.releases.create_index("download_token", sparse=True, unique=True)
    await db.audit_logs.create_index("timestamp")
