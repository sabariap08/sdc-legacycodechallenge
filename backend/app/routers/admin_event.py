from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.security import get_admin_user
from app.utils import compute_event_status, generate_event_code
from datetime import datetime

router = APIRouter(prefix="/api/admin", tags=["event"])


@router.get("/event/current")
async def get_current_event(admin=Depends(get_admin_user)):
    db = get_db()
    settings = await db.event_settings.find_one({})
    if not settings:
        return {"event": None, "computed_status": "DRAFT"}
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
        "event": settings,
        "computed_status": computed_status,
        "countdown_seconds": countdown_seconds,
        "remaining_seconds": remaining_seconds,
    }


@router.get("/event/status")
async def get_event_status(admin=Depends(get_admin_user)):
    return await get_current_event(admin)


class EventStart(BaseModel):
    event_start_time: str
    event_end_time: str
    event_duration_minutes: int = 300
    allow_multiple_submissions: bool = False


@router.post("/event/start")
async def start_event(body: EventStart, admin=Depends(get_admin_user)):
    db = get_db()
    try:
        start_dt = datetime.fromisoformat(body.event_start_time.replace("Z", "+00:00")).replace(tzinfo=None)
        end_dt = datetime.fromisoformat(body.event_end_time.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")
    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="End time must be after start time")
    if body.event_duration_minutes < 5 or body.event_duration_minutes > 1440:
        raise HTTPException(status_code=400, detail="Duration must be between 5 and 1440 minutes")

    existing = await db.event_settings.find_one({})
    old_status = compute_event_status(existing) if existing else "DRAFT"
    event_code = generate_event_code()

    event_doc = {
        "event_code": event_code,
        "event_start_time": start_dt,
        "event_end_time": end_dt,
        "event_duration_minutes": body.event_duration_minutes,
        "allow_multiple_submissions": body.allow_multiple_submissions,
        "leaderboard_enabled": False,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "created_by": admin.get("sub", "admin"),
    }

    if existing:
        old_code = existing.get("event_code", "")
        if old_status in ("ONGOING", "UPCOMING"):
            raise HTTPException(status_code=400, detail="Cannot start a new event while one is active or upcoming. Restart the current event first.")
        await db.event_settings.update_one({}, {"$set": event_doc})
    else:
        await db.event_settings.insert_one(event_doc)

    await db.audit_logs.insert_one({
        "action": "event_started",
        "actor": admin.get("sub", "admin"),
        "details": f"New event {event_code} started. Previous status: {old_status}. Start: {start_dt.isoformat()}, End: {end_dt.isoformat()}",
        "timestamp": datetime.utcnow()
    })

    return {"message": "Event started", "event_code": event_code, "event_start_time": start_dt.isoformat(), "event_end_time": end_dt.isoformat()}


class EventUpdate(BaseModel):
    event_start_time: Optional[str] = None
    event_end_time: Optional[str] = None
    event_duration_minutes: Optional[int] = None
    allow_multiple_submissions: Optional[bool] = None


@router.patch("/event/current")
async def update_current_event(body: EventUpdate, admin=Depends(get_admin_user)):
    db = get_db()
    settings = await db.event_settings.find_one({})
    if not settings:
        raise HTTPException(status_code=404, detail="No event configured")
    computed = compute_event_status(settings)
    if computed == "ONGOING":
        raise HTTPException(status_code=400, detail="Cannot modify event while it is ongoing")
    if computed == "COMPLETED":
        raise HTTPException(status_code=400, detail="Cannot modify a completed event. Start a new event instead.")

    update = {}
    if body.event_start_time is not None:
        try:
            update["event_start_time"] = datetime.fromisoformat(body.event_start_time.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start time format")
    if body.event_end_time is not None:
        try:
            update["event_end_time"] = datetime.fromisoformat(body.event_end_time.replace("Z", "+00:00")).replace(tzinfo=None)
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

    if "event_start_time" in update or "event_end_time" in update:
        existing_start = settings.get("event_start_time")
        existing_end = settings.get("event_end_time")
        if isinstance(existing_start, str):
            existing_start = datetime.fromisoformat(existing_start.replace("Z", "+00:00")).replace(tzinfo=None)
        if isinstance(existing_end, str):
            existing_end = datetime.fromisoformat(existing_end.replace("Z", "+00:00")).replace(tzinfo=None)
        s = update.get("event_start_time", existing_start)
        e = update.get("event_end_time", existing_end)
        if s and e and hasattr(s, 'replace') and hasattr(e, 'replace'):
            if e <= s:
                raise HTTPException(status_code=400, detail="End time must be after start time")

    update["updated_at"] = datetime.utcnow()
    await db.event_settings.update_one({}, {"$set": update})

    await db.audit_logs.insert_one({
        "action": "event_settings_updated",
        "actor": admin.get("sub", "admin"),
        "details": f"Updated: {list(update.keys())}",
        "timestamp": datetime.utcnow()
    })

    return {"message": "Event settings updated"}


@router.put("/event/settings")
async def update_event_settings(body: EventUpdate, admin=Depends(get_admin_user)):
    return await update_current_event(body, admin)


class EventRestart(BaseModel):
    confirm: bool = False


@router.post("/event/restart")
async def restart_event(body: EventRestart, admin=Depends(get_admin_user)):
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Must confirm restart")

    db = get_db()
    settings = await db.event_settings.find_one({})
    computed = compute_event_status(settings) if settings else "DRAFT"
    old_code = settings.get("event_code", "N/A") if settings else "N/A"

    await db.event_settings.update_one({}, {"$set": {
        "event_code": generate_event_code(),
        "status": "DRAFT",
        "event_start_time": None,
        "event_end_time": None,
        "allow_multiple_submissions": False,
        "leaderboard_enabled": False,
        "updated_at": datetime.utcnow(),
    }})

    await db.allocations.delete_many({})
    await db.submissions.delete_many({})
    await db.workspaces.delete_many({})
    await db.checkins.delete_many({})

    new_settings = await db.event_settings.find_one({})
    new_code = new_settings.get("event_code", "N/A") if new_settings else "N/A"

    await db.audit_logs.insert_one({
        "action": "event_restarted",
        "actor": admin.get("sub", "admin"),
        "details": f"Event restarted from status {computed} (was {old_code}). New code: {new_code}. Allocations, submissions, workspaces, and checkins cleared. Teams and repositories preserved.",
        "timestamp": datetime.utcnow()
    })

    return {"message": "Event restarted successfully", "new_event_code": new_code}


@router.get("/event/history")
async def get_event_history(admin=Depends(get_admin_user)):
    db = get_db()
    history = []
    async for log in db.audit_logs.find({"action": {"$in": ["event_started", "event_restarted", "event_settings_updated"]}}).sort("timestamp", -1).limit(50):
        log["_id"] = str(log["_id"])
        history.append(log)
    return {"history": history}


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
