from fastapi import APIRouter, Depends
from app.database import get_db
from app.security import get_participant_user
from datetime import datetime

router = APIRouter(prefix="/api/participant", tags=["participant_dashboard"])


def _compute_event_status(settings):
    now = datetime.utcnow()
    start = settings.get("event_start_time") if settings else None
    end = settings.get("event_end_time") if settings else None
    if not start or not end:
        return settings.get("status", "DRAFT") if settings else "DRAFT"
    if isinstance(start, str):
        start = datetime.fromisoformat(start.replace("Z", "+00:00")).replace(tzinfo=None)
    if isinstance(end, str):
        end = datetime.fromisoformat(end.replace("Z", "+00:00")).replace(tzinfo=None)
    if now < start:
        return "UPCOMING"
    elif now < end:
        return "ONGOING"
    else:
        return "COMPLETED"


@router.get("/dashboard")
async def participant_dashboard(user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")

    team = await db.teams.find_one({"team_code": team_code})
    if not team:
        return {"error": "Team not found"}

    participants = []
    async for p in db.participants.find({"team_code": team_code}):
        p.pop("_id", None)
        participants.append(p)

    allocation = await db.allocations.find_one({"team_code": team_code})

    event_settings = await db.event_settings.find_one({})
    computed = _compute_event_status(event_settings)

    now = datetime.utcnow()
    remaining_seconds = None
    countdown_seconds = None
    if event_settings:
        start = event_settings.get("event_start_time")
        end = event_settings.get("event_end_time")
        if start:
            if isinstance(start, str):
                start = datetime.fromisoformat(start.replace("Z", "+00:00")).replace(tzinfo=None)
            if computed == "UPCOMING":
                countdown_seconds = max(0, int((start - now).total_seconds()))
            elif computed == "ONGOING" and end:
                if isinstance(end, str):
                    end = datetime.fromisoformat(end.replace("Z", "+00:00")).replace(tzinfo=None)
                remaining_seconds = max(0, int((end - now).total_seconds()))

    announcements = []
    async for a in db.announcements.find({"active": True}).sort("created_at", -1).limit(10):
        a.pop("_id", None)
        announcements.append(a)

    submission = await db.submissions.find_one(
        {"team_code": team_code},
        sort=[("submitted_at", -1)]
    )
    if submission:
        submission.pop("_id", None)

    challenge_info = None
    if allocation and allocation.get("released"):
        ch = await db.challenges.find_one({"challenge_code": allocation["challenge_code"]})
        if ch:
            challenge_info = {
                "challenge_code": ch["challenge_code"],
                "challenge_name": ch.get("challenge_name", ch.get("name", ch.get("title", ""))),
                "language": ch.get("language", ""),
                "difficulty": ch.get("difficulty", ""),
                "description": ch.get("description", ""),
            }

    allow_multiple = event_settings.get("allow_multiple_submissions", False) if event_settings else False

    return {
        "team_code": team.get("team_code"),
        "team_name": team.get("team_name"),
        "bin_number": team.get("bin_number"),
        "participants": participants,
        "event_status": computed,
        "event_start_time": event_settings.get("event_start_time") if event_settings else None,
        "event_end_time": event_settings.get("event_end_time") if event_settings else None,
        "countdown_seconds": countdown_seconds,
        "remaining_seconds": remaining_seconds,
        "announcements": announcements,
        "allocation": {
            "challenge_code": allocation.get("challenge_code") if allocation else None,
            "released": allocation.get("released", False) if allocation else False,
        },
        "challenge": challenge_info,
        "submission": submission,
        "allow_multiple_submissions": allow_multiple,
        "leaderboard_enabled": event_settings.get("leaderboard_enabled", False) if event_settings else False,
    }


@router.get("/event-countdown")
async def participant_event_countdown(user=Depends(get_participant_user)):
    db = get_db()
    event_settings = await db.event_settings.find_one({})
    now = datetime.utcnow()
    computed = _compute_event_status(event_settings)
    countdown_seconds = None
    remaining_seconds = None
    if event_settings:
        start = event_settings.get("event_start_time")
        end = event_settings.get("event_end_time")
        if start:
            if isinstance(start, str):
                start = datetime.fromisoformat(start.replace("Z", "+00:00")).replace(tzinfo=None)
            if computed == "UPCOMING":
                countdown_seconds = max(0, int((start - now).total_seconds()))
            elif computed == "ONGOING" and end:
                if isinstance(end, str):
                    end = datetime.fromisoformat(end.replace("Z", "+00:00")).replace(tzinfo=None)
                remaining_seconds = max(0, int((end - now).total_seconds()))
    return {
        "server_time": now.isoformat(),
        "status": computed,
        "countdown_seconds": countdown_seconds,
        "remaining_seconds": remaining_seconds,
    }


@router.get("/leaderboard")
async def participant_leaderboard(user=Depends(get_participant_user)):
    db = get_db()
    event_settings = await db.event_settings.find_one({})
    if not event_settings or not event_settings.get("leaderboard_enabled"):
        return {"leaderboard": [], "message": "Leaderboard not enabled yet"}

    entries = []
    async for sub in db.submissions.find({"status": "evaluated"}).sort("score", -1):
        team = await db.teams.find_one({"team_code": sub["team_code"]})
        alloc = await db.allocations.find_one({"team_code": sub["team_code"]})
        entries.append({
            "team_code": sub["team_code"],
            "team_name": team["team_name"] if team else "",
            "challenge_code": alloc.get("challenge_code", "") if alloc else "",
            "score": sub.get("score", 0),
            "submitted_at": sub.get("submitted_at"),
        })
    for i, e in enumerate(entries, 1):
        e["rank"] = i
    return {"leaderboard": entries}
