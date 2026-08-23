from fastapi import APIRouter, Depends, HTTPException
from app.database import get_db
from app.security import get_admin_user
from app.utils import compute_event_status
from datetime import datetime
import random

router = APIRouter(prefix="/api/admin", tags=["allocation"])


def _get_event_status(settings) -> str:
    return compute_event_status(settings) if settings else "DRAFT"


@router.post("/allocation/generate")
async def generate_allocation(admin=Depends(get_admin_user)):
    db = get_db()

    settings = await db.event_settings.find_one({})
    event_status = _get_event_status(settings)

    if event_status == "ONGOING":
        raise HTTPException(
            status_code=403,
            detail="Challenge allocation is locked because the event is live."
        )

    teams = []
    async for team in db.teams.find():
        if team.get("status") != "BLOCKED":
            teams.append(team)

    if not teams:
        raise HTTPException(status_code=400, detail="No eligible teams registered")

    challenges = []
    async for ch in db.challenges.find({"status": "READY"}):
        challenges.append(ch)

    if not challenges:
        raise HTTPException(status_code=400, detail="No challenges imported")

    await db.allocations.delete_many({})

    random.shuffle(teams)
    challenge_codes = [ch["challenge_code"] for ch in challenges]
    random.shuffle(challenge_codes)
    num_challenges = len(challenge_codes)

    allocations = []
    for i, team in enumerate(teams):
        cc = challenge_codes[i % num_challenges]
        alloc_doc = {
            "team_code": team["team_code"],
            "challenge_code": cc,
            "released": True,
            "allocated_at": datetime.utcnow()
        }
        await db.allocations.insert_one(alloc_doc)
        allocations.append(alloc_doc)

    await db.event_settings.update_one(
        {},
        {"$set": {"allocation_state": "RELEASED"}},
        upsert=True
    )

    await db.audit_logs.insert_one({
        "action": "allocation_generated",
        "actor": admin.get("sub", "admin"),
        "details": f"Generated and released allocations for {len(teams)} teams across {len(challenges)} challenges",
        "timestamp": datetime.utcnow()
    })

    for a in allocations:
        a["_id"] = str(a.get("_id", ""))

    return {
        "message": "Allocations generated and released successfully",
        "allocations": allocations,
        "count": len(allocations)
    }


@router.post("/allocation/release")
async def release_allocation(admin=Depends(get_admin_user)):
    db = get_db()

    settings = await db.event_settings.find_one({})
    event_status = _get_event_status(settings)

    if event_status == "ONGOING":
        raise HTTPException(status_code=403, detail="Cannot release allocations during an ongoing event.")

    result = await db.allocations.update_many({}, {"$set": {"released": True}})
    await db.event_settings.update_one(
        {},
        {"$set": {"allocation_state": "RELEASED"}},
        upsert=True
    )

    await db.audit_logs.insert_one({
        "action": "allocation_released",
        "actor": admin.get("sub", "admin"),
        "details": f"Released {result.modified_count} allocations",
        "timestamp": datetime.utcnow()
    })

    return {"message": "Allocations released", "count": result.modified_count}


@router.post("/allocation/reset")
async def reset_allocation(admin=Depends(get_admin_user)):
    db = get_db()

    settings = await db.event_settings.find_one({})
    event_status = _get_event_status(settings)

    if event_status == "ONGOING":
        raise HTTPException(status_code=403, detail="Cannot reset allocations during an ongoing event.")

    await db.allocations.delete_many({})
    await db.event_settings.update_one(
        {},
        {"$set": {"allocation_state": "PENDING"}},
        upsert=True
    )

    await db.audit_logs.insert_one({
        "action": "allocation_reset",
        "actor": admin.get("sub", "admin"),
        "details": "All allocations have been reset",
        "timestamp": datetime.utcnow()
    })

    return {"message": "Allocations reset successfully"}


@router.get("/allocation")
async def get_allocations(admin=Depends(get_admin_user)):
    db = get_db()
    allocations = []
    async for alloc in db.allocations.find():
        alloc["_id"] = str(alloc["_id"])
        allocations.append(alloc)

    settings = await db.event_settings.find_one({})
    alloc_state = settings.get("allocation_state", "PENDING") if settings else "PENDING"
    computed = _get_event_status(settings)

    return {
        "allocations": allocations,
        "allocation_state": alloc_state,
        "event_status": computed,
        "count": len(allocations)
    }
