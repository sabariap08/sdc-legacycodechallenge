"""Event lifecycle helpers.

The ``events`` collection is the single source of truth for event state.
This module centralizes creation, deletion, restart, and status computation so
that every router (admin, participant, workspace, submission) shares one
consistent view of "the current event".
"""
from datetime import datetime

from app.database import get_db
from app.utils import generate_event_code, compute_event_status


def _now():
    return datetime.utcnow()


async def get_current_event():
    """Return the newest active (non-deleted) event document or None.

    ``deleted_datetime is null`` marks an event as active. If more than one
    active event exists (e.g. a stale event was never archived), the most
    recently created one wins so the frontend and routers always agree.
    """
    db = get_db()
    return await db.events.find_one(
        {"deleted_datetime": None},
        sort=[("created_datetime", -1)],
    )


async def get_event_by_code(event_code: str):
    db = get_db()
    return await db.events.find_one({"event_code": event_code})


async def get_event_by_id(event_id):
    db = get_db()
    if isinstance(event_id, str):
        return await db.events.find_one({"event_id": event_id})
    return await db.events.find_one({"event_id": str(event_id)})


async def compute_current_status():
    """Return (status, event) for the active event; ("DRAFT", None) if none."""
    event = await get_current_event()
    if not event:
        return "DRAFT", None
    status = compute_event_status(event)
    if status != event.get("status"):
        await db_events().update_one(
            {"event_id": event["event_id"]},
            {"$set": {"status": status, "updated_at": _now()}},
        )
    return status, event


def db_events():
    return get_db().events


def db_history():
    return get_db().event_history


def get_event_duration_minutes(event: dict) -> int:
    """Derive the event duration from persisted start/end times. Never stored."""
    start = event.get("event_start_time")
    end = event.get("event_end_time")
    if not start or not end:
        return 0
    if isinstance(start, str):
        try:
            start = datetime.fromisoformat(str(start).replace("Z", "+00:00")).replace(tzinfo=None)
        except (ValueError, TypeError):
            return 0
    if isinstance(end, str):
        try:
            end = datetime.fromisoformat(str(end).replace("Z", "+00:00")).replace(tzinfo=None)
        except (ValueError, TypeError):
            return 0
    return max(0, int((end - start).total_seconds() // 60))


async def create_event():
    """Soft-create a fresh event document. Existing non-deleted events are left
    untouched; callers decide whether to archive first.

    The event model intentionally holds only lifecycle fields: event code,
    start/end datetimes, status, created/timestamps and cancellation fields.
    Duration is always derived from start/end and is never persisted.
    """
    doc = {
        "event_id": str(int(_now().timestamp() * 1000)),
        "event_code": generate_event_code(),
        "status": "UPCOMING",
        "event_start_time": None,
        "event_end_time": None,
        "leaderboard_enabled": False,
        "created_datetime": _now(),
        "updated_at": _now(),
        "deleted_datetime": None,
        "deletion_reason": None,
    }
    await db_events().insert_one(doc)
    return doc


async def archive_current_event(reason: str = "Archived"):
    """Mark the current active event as deleted (soft delete) and persist it to
    history for the Event History page. Returns the archived event or None."""
    event = await get_current_event()
    if not event:
        return None

    status = compute_event_status(event)
    archive_doc = {
        "event_id": event["event_id"],
        "event_code": event["event_code"],
        "status": "CANCELLED" if event.get("status") == "CANCELLED" else status,
        "event_start_time": event.get("event_start_time"),
        "event_end_time": event.get("event_end_time"),
        "event_duration_minutes": event.get("event_duration_minutes"),
        "created_datetime": event.get("created_datetime"),
        "cancelled_at": event.get("cancelled_at"),
        "cancellation_reason": event.get("cancellation_reason"),
        "archived_at": _now(),
        "archive_reason": reason,
    }
    # Persist into history collection first (best-effort, tolerate duplicates)
    try:
        await db_history().update_one(
            {"event_id": event["event_id"]},
            {"$set": archive_doc},
            upsert=True,
        )
    except Exception:
        pass

    deleted = {
        "deleted_datetime": _now(),
        "deletion_reason": reason,
        "status": archive_doc["status"],
        "updated_at": _now(),
    }
    await db_events().update_one(
        {"event_id": event["event_id"]}, {"$set": deleted}
    )
    return {**event, **deleted}


async def cancel_current_event(reason: str = "Cancelled by admin"):
    """Cancel the active event: status -> CANCELLED with cancellation metadata.
    The record is preserved (never destroyed) so it stays visible in History.
    Requires a cancellation reason."""
    event = await get_current_event()
    if not event:
        return None
    await db_events().update_one(
        {"event_id": event["event_id"]},
        {"$set": {
            "status": "CANCELLED",
            "cancelled_at": _now(),
            "cancellation_reason": reason,
            "updated_at": _now(),
        }},
    )
    return await get_event_by_id(event["event_id"])


async def ensure_draft_event_exists():
    """If there is no active event at all, create one so the app is usable."""
    event = await get_current_event()
    if event:
        return event
    return await create_event()
