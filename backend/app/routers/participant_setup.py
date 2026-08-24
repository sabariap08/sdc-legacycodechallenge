from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from app.database import get_db
from app.security import hash_password, get_admin_user, get_participant_user
from datetime import datetime

router = APIRouter(prefix="/api/participant", tags=["participant_setup"])


class SetupPassword(BaseModel):
    team_code: str
    new_password: str


@router.post("/setup-password")
async def setup_password(body: SetupPassword):
    db = get_db()
    team = await db.teams.find_one({"team_code": body.team_code.upper().strip()})
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    if len(body.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    existing = await db.team_auth.find_one({"team_code": team["team_code"]})

    auth_doc = {
        "team_code": team["team_code"],
        "password_hash": hash_password(body.new_password),
        "updated_at": datetime.utcnow()
    }

    if existing:
        await db.team_auth.update_one(
            {"team_code": team["team_code"]},
            {"$set": auth_doc}
        )
    else:
        auth_doc["created_at"] = datetime.utcnow()
        await db.team_auth.insert_one(auth_doc)

    return {"message": "Password set successfully. You can now log in."}


class AdminSetPassword(BaseModel):
    team_code: str
    password: str


@router.post("/admin-set-password")
async def admin_set_password_for_team(body: AdminSetPassword, admin=Depends(get_admin_user)):
    db = get_db()
    team = await db.teams.find_one({"team_code": body.team_code.upper().strip()})
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    auth_doc = {
        "team_code": team["team_code"],
        "password_hash": hash_password(body.password),
        "updated_at": datetime.utcnow()
    }

    existing = await db.team_auth.find_one({"team_code": team["team_code"]})
    if existing:
        await db.team_auth.update_one(
            {"team_code": team["team_code"]},
            {"$set": auth_doc}
        )
    else:
        auth_doc["created_at"] = datetime.utcnow()
        await db.team_auth.insert_one(auth_doc)

    return {"message": f"Password set for team {team['team_code']}"}


@router.post("/accept-tc")
async def accept_terms(user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")
    await db.teams.update_one(
        {"team_code": team_code},
        {"$set": {"tc_accepted": True, "tc_accepted_at": datetime.utcnow()}}
    )
    return {"message": "Terms and conditions accepted"}


@router.get("/tc-status")
async def tc_status(user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")
    team = await db.teams.find_one({"team_code": team_code})
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return {"accepted": team.get("tc_accepted", False)}
