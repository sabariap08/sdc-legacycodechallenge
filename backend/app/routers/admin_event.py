from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.security import get_admin_user
from app.utils import compute_event_status
from datetime import datetime, timezone

router = APIRouter(prefix="/api/admin", tags=["event"])


@router.get("/event/status")
async def get_event_status(admin=Depends(get_admin_user)):
    db = get_db()
    settings = await db.event_settings.find_one({})
    if not settings:
        return {"settings": None}
    settings["_id"] = str(settings["_id"])
    computed_status = compute_event_status(settings)
    now = datetime.utcnow()
    start = settings.get("event_start_time")
    end = settings.get("event_end_time")
    remaining_seconds = None
    countdown_seconds = None
    if start:
        if isinstance(start, str):
            start = datetime.fromisoformat(start.replace("Z", "+00:00")).replace(tzinfo=None)
        if computed_status == "UPCOMING":
            countdown_seconds = max(0, int((start - now).total_seconds()))
        elif computed_status == "ONGOING" and end:
            if isinstance(end, str):
                end = datetime.fromisoformat(end.replace("Z", "+00:00")).replace(tzinfo=None)
            remaining_seconds = max(0, int((end - now).total_seconds()))
    return {
        "settings": settings,
        "computed_status": computed_status,
        "countdown_seconds": countdown_seconds,
        "remaining_seconds": remaining_seconds,
    }


class EventSettingsUpdate(BaseModel):
    event_start_time: Optional[str] = None
    event_end_time: Optional[str] = None
    event_duration_minutes: Optional[int] = None
    allow_multiple_submissions: Optional[bool] = None


@router.put("/event/settings")
async def update_event_settings(body: EventSettingsUpdate, admin=Depends(get_admin_user)):
    db = get_db()
    settings = await db.event_settings.find_one({})
    computed = compute_event_status(settings) if settings else "DRAFT"

    if computed in ("ONGOING", "COMPLETED"):
        raise HTTPException(status_code=400, detail="Cannot modify event settings while event is ongoing or completed")

    update = {}

    if body.event_start_time is not None:
        try:
            update["event_start_time"] = datetime.fromisoformat(body.event_start_time.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start time format")

    if body.event_end_time is not None:
        try:
            update["event_end_time"] = datetime.fromisoformat(body.event_end_time.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid end time format")

    if body.event_duration_minutes is not None:
        if body.event_duration_minutes < 5 or body.event_duration_minutes > 1440:
            raise HTTPException(status_code=400, detail="Duration must be between 5 and 1440 minutes")
        update["event_duration_minutes"] = body.event_duration_minutes

    if body.allow_multiple_submissions is not None:
        update["allow_multiple_submissions"] = body.allow_multiple_submissions

    if not update:
        raise HTTPException(status_code=400, detail="No settings provided")

    if "event_start_time" in update and "event_end_time" in update:
        if update["event_end_time"] <= update["event_start_time"]:
            raise HTTPException(status_code=400, detail="End time must be after start time")
    elif "event_start_time" in update or "event_end_time" in update:
        existing_start = settings.get("event_start_time") if settings else None
        existing_end = settings.get("event_end_time") if settings else None
        s = update.get("event_start_time", existing_start)
        e = update.get("event_end_time", existing_end)
        if s and e:
            if isinstance(s, str):
                s = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if isinstance(e, str):
                e = datetime.fromisoformat(e.replace("Z", "+00:00"))
            if hasattr(s, 'replace'):
                s = s.replace(tzinfo=None)
            if hasattr(e, 'replace'):
                e = e.replace(tzinfo=None)
            if e <= s:
                raise HTTPException(status_code=400, detail="End time must be after start time")

    await db.event_settings.update_one({}, {"$set": update})

    await db.audit_logs.insert_one({
        "action": "event_settings_updated",
        "actor": admin.get("sub", "admin"),
        "details": f"Updated: {list(update.keys())}",
        "timestamp": datetime.utcnow()
    })

    return {"message": "Event settings saved"}


class EventRestart(BaseModel):
    confirm: bool = False


@router.post("/event/restart")
async def restart_event(body: EventRestart, admin=Depends(get_admin_user)):
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Must confirm restart")

    db = get_db()
    settings = await db.event_settings.find_one({})
    computed = compute_event_status(settings) if settings else "DRAFT"

    await db.event_settings.update_one({}, {"$set": {
        "status": "DRAFT",
        "event_start_time": None,
        "event_end_time": None,
        "allow_multiple_submissions": False,
        "leaderboard_enabled": False,
    }})

    await db.allocations.delete_many({})
    await db.submissions.delete_many({})
    await db.workspaces.delete_many({})
    await db.checkins.delete_many({})

    await db.audit_logs.insert_one({
        "action": "event_restarted",
        "actor": admin.get("sub", "admin"),
        "details": f"Event restarted from status {computed}. Allocations, submissions, workspaces, and checkins cleared. Teams and repositories preserved.",
        "timestamp": datetime.utcnow()
    })

    return {"message": "Event restarted successfully"}


@router.get("/event/countdown")
async def get_event_countdown(admin=Depends(get_admin_user)):
    db = get_db()
    settings = await db.event_settings.find_one({})
    if not settings:
        return {"server_time": datetime.utcnow().isoformat(), "status": "DRAFT"}
    computed = compute_event_status(settings)
    return {
        "server_time": datetime.utcnow().isoformat(),
        "status": computed,
        "event_start_time": settings.get("event_start_time"),
        "event_end_time": settings.get("event_end_time"),
    }
