from motor.motor_asyncio import AsyncIOMotorClient
from app.config import MONGODB_URI, DATABASE_NAME

client = None
db = None


async def connect_db():
    global client, db
    client = AsyncIOMotorClient(MONGODB_URI)
    db = client[DATABASE_NAME]
    await _create_indexes()
    await _ensure_event_settings()
    return db


async def close_db():
    global client
    if client:
        client.close()


def get_db():
    return db


async def _create_indexes():
    await db.teams.create_index("team_code", unique=True)
    await db.teams.create_index("team_name", unique=True)
    await db.teams.create_index("bin_number", unique=True)
    await db.participants.create_index("email", unique=True)
    await db.participants.create_index("team_code")
    await db.challenges.create_index("challenge_code", unique=True)
    await db.allocations.create_index("team_code", unique=True)
    await db.allocations.create_index("challenge_code")
    await db.workspaces.create_index(["team_code", "challenge_code"], unique=True)
    await db.submissions.create_index("team_code")
    await db.admins.create_index("username", unique=True)
    await db.audit_logs.create_index("timestamp")
    await db.checkins.create_index("team_code", unique=True)
    await db.notifications.create_index("team_code")
    await db.announcements.create_index("created_at")
    await db.blocked_users.create_index("email", unique=True)


async def _ensure_event_settings():
    existing = await db.event_settings.find_one({})
    if not existing:
        await db.event_settings.insert_one({
            "status": "DRAFT",
            "event_start_time": None,
            "event_end_time": None,
            "event_duration_minutes": 300,
            "leaderboard_enabled": False,
            "allow_multiple_submissions": False,
            "created_at": __import__("datetime").datetime.utcnow()
        })
