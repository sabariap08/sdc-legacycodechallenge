from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.security import get_admin_user
from datetime import datetime
import re

router = APIRouter(prefix="/api/admin", tags=["checkin"])


class CheckinUpdate(BaseModel):
    team_code: str
    status: str


@router.post("/checkin")
async def update_checkin(body: CheckinUpdate, admin=Depends(get_admin_user)):
    db = get_db()
    valid_statuses = ["REGISTERED", "CHECKED-IN", "STARTED", "COMPLETED"]
    if body.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of {valid_statuses}")

    team = await db.teams.find_one({"team_code": body.team_code})
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    existing = await db.checkins.find_one({"team_code": body.team_code})
    if existing:
        await db.checkins.update_one(
            {"team_code": body.team_code},
            {"$set": {"status": body.status, "updated_at": datetime.utcnow()}}
        )
    else:
        await db.checkins.insert_one({
            "team_code": body.team_code,
            "status": body.status,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        })

    await db.teams.update_one({"team_code": body.team_code}, {"$set": {"status": body.status}})

    await db.audit_logs.insert_one({
        "action": "checkin_updated",
        "actor": admin.get("sub", "admin"),
        "details": f"Team {body.team_code} status -> {body.status}",
        "timestamp": datetime.utcnow()
    })

    return {"message": f"Team {body.team_code} marked as {body.status}"}


@router.get("/checkin")
async def list_checkins(
    search: Optional[str] = Query(None),
    admin=Depends(get_admin_user)
):
    db = get_db()
    query = {}
    if search:
        safe = re.escape(search.strip())
        team = await db.teams.find_one({"team_code": {"$regex": safe, "$options": "i"}})
        if team:
            query["team_code"] = team["team_code"]
        else:
            team = await db.teams.find_one({"team_name": {"$regex": safe, "$options": "i"}})
            if team:
                query["team_code"] = team["team_code"]
            else:
                return {"checkins": []}

    checkins = []
    async for c in db.checkins.find(query):
        c["_id"] = str(c["_id"])
        team = await db.teams.find_one({"team_code": c["team_code"]})
        c["team_name"] = team["team_name"] if team else ""
        c["bin_number"] = team.get("bin_number", "") if team else ""
        checkins.append(c)
    return {"checkins": checkins}
