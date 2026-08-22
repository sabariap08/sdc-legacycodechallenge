from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from app.database import get_db
from app.security import get_admin_user
from datetime import datetime
import secrets
import random

router = APIRouter(prefix="/api/admin", tags=["allocation"])


def _compute_event_status(settings: dict) -> str:
    now = datetime.utcnow()
    start = settings.get("event_start_time")
    end = settings.get("event_end_time")
    if not start or not end:
        return settings.get("status", "DRAFT")
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


async def _get_allocation_state() -> str:
    db = get_db()
    settings = await db.event_settings.find_one({})
    if not settings:
        return "PENDING"
    alloc_state = settings.get("allocation_state", "PENDING")
    if alloc_state not in ("PENDING", "GENERATED", "RELEASED", "COMPLETED"):
        alloc_state = "PENDING"
    return alloc_state


@router.post("/allocation/generate")
async def generate_allocation(admin=Depends(get_admin_user)):
    db = get_db()

    settings = await db.event_settings.find_one({})
    computed = _compute_event_status(settings) if settings else "DRAFT"

    if computed in ("ONGOING", "COMPLETED"):
        raise HTTPException(status_code=400, detail="Allocation is locked because the event has already started")

    alloc_state = await _get_allocation_state()
    if alloc_state == "GENERATED" and not settings.get("allow_regenerate"):
        raise HTTPException(status_code=400, detail="Allocations already generated. Use release to publish, or reset first.")

    teams = []
    async for team in db.teams.find():
        teams.append(team)

    if not teams:
        raise HTTPException(status_code=400, detail="No teams registered")

    challenges = []
    async for ch in db.challenges.find({"status": "READY"}):
        challenges.append(ch)

    if not challenges:
        raise HTTPException(status_code=400, detail="No challenges imported")

    await db.allocations.delete_many({})

    team_list = list(teams)
    random.shuffle(team_list)

    challenge_codes = [ch["challenge_code"] for ch in challenges]
    num_challenges = len(challenge_codes)
    num_teams = len(team_list)

    pool = challenge_codes * (num_teams // num_challenges + 1)
    random.shuffle(pool)

    target_counts = {}
    for cc in challenge_codes:
        target_counts[cc] = 0
    for cc in pool[:num_teams]:
        target_counts[cc] += 1

    allocations = []
    pool_idx = 0
    for team in team_list:
        cc = pool[pool_idx]
        pool_idx += 1
        alloc_doc = {
            "team_code": team["team_code"],
            "challenge_code": cc,
            "released": False,
            "state": "GENERATED",
            "allocated_at": datetime.utcnow()
        }
        await db.allocations.insert_one(alloc_doc)
        allocations.append(alloc_doc)

    if settings:
        await db.event_settings.update_one({}, {"$set": {"allocation_state": "GENERATED"}})
    else:
        await db.event_settings.insert_one({"allocation_state": "GENERATED"})

    await db.audit_logs.insert_one({
        "action": "allocation_generated",
        "actor": admin.get("sub", "admin"),
        "details": f"Generated allocations for {len(teams)} teams across {len(challenges)} challenges",
        "timestamp": datetime.utcnow()
    })

    for a in allocations:
        a["_id"] = str(a.get("_id", ""))

    return {"message": "Allocation generated", "allocations": allocations}


@router.post("/allocation/release")
async def release_allocation(admin=Depends(get_admin_user)):
    db = get_db()

    alloc_state = await _get_allocation_state()
    if alloc_state == "PENDING":
        raise HTTPException(status_code=400, detail="No allocations to release. Generate allocations first.")
    if alloc_state == "RELEASED":
        raise HTTPException(status_code=400, detail="Allocations already released.")
    if alloc_state == "COMPLETED":
        raise HTTPException(status_code=400, detail="Allocations already completed.")

    settings = await db.event_settings.find_one({})
    computed = _compute_event_status(settings) if settings else "DRAFT"
    if computed in ("ONGOING", "COMPLETED"):
        raise HTTPException(status_code=400, detail="Cannot release allocations during an ongoing or completed event.")

    result = await db.allocations.update_many({}, {"$set": {"released": True, "state": "RELEASED"}})
    await db.event_settings.update_one({}, {"$set": {"allocation_state": "RELEASED"}})

    await db.audit_logs.insert_one({
        "action": "allocation_released",
        "actor": admin.get("sub", "admin"),
        "details": f"Released {result.modified_count} allocations",
        "timestamp": datetime.utcnow()
    })

    return {"message": "Allocations released", "count": result.modified_count}


@router.get("/allocation")
async def get_allocations(admin=Depends(get_admin_user)):
    db = get_db()
    allocations = []
    async for alloc in db.allocations.find():
        alloc["_id"] = str(alloc["_id"])
        allocations.append(alloc)

    settings = await db.event_settings.find_one({})
    alloc_state = settings.get("allocation_state", "PENDING") if settings else "PENDING"
    computed = _compute_event_status(settings) if settings else "DRAFT"

    return {"allocations": allocations, "allocation_state": alloc_state, "event_status": computed}
