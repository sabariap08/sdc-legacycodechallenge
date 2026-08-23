from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from app.database import get_db
from app.security import get_admin_user, hash_password
from app.utils import generate_team_code, generate_bin_number
from datetime import datetime
import secrets

router = APIRouter(prefix="/api/admin", tags=["teams"])

DEFAULT_PARTICIPANT_PASSWORD = "participants@123"


class ParticipantCreate(BaseModel):
    name: str
    email: EmailStr
    roll_number: Optional[str] = ""
    college: Optional[str] = ""
    phone: Optional[str] = ""
    is_team_leader: bool = False


class TeamCreate(BaseModel):
    team_name: str
    participant_count: int
    participants: List[ParticipantCreate]


@router.post("/teams")
async def create_team(body: TeamCreate, admin=Depends(get_admin_user)):
    db = get_db()

    if not body.team_name or not body.team_name.strip():
        raise HTTPException(status_code=400, detail="Team name is required")
    if body.participant_count < 1 or body.participant_count > 4:
        raise HTTPException(status_code=400, detail="Participant count must be between 1 and 4")
    if len(body.participants) != body.participant_count:
        raise HTTPException(status_code=400, detail="Participant count mismatch")

    existing_team = await db.teams.find_one({
        "team_name": {"$regex": f"^{body.team_name.strip()}$", "$options": "i"}
    })
    if existing_team:
        raise HTTPException(status_code=400, detail="Team name already exists")

    emails = [p.email for p in body.participants]
    if len(set(emails)) != len(emails):
        raise HTTPException(status_code=400, detail="Duplicate emails within team not allowed")

    for email in emails:
        existing = await db.participants.find_one({"email": email})
        if existing:
            raise HTTPException(status_code=400, detail=f"Email {email} already registered")
        blocked = await db.blocked_users.find_one({"email": email})
        if blocked:
            raise HTTPException(status_code=400, detail=f"Email {email} is blocked")

    team_code = generate_team_code()
    attempts = 0
    while await db.teams.find_one({"team_code": team_code}) and attempts < 10:
        team_code = generate_team_code()
        attempts += 1

    used_bins = await db.teams.distinct("bin_number")
    all_bins = [generate_bin_number(i) for i in range(1, 41)]
    available_bins = [b for b in all_bins if b not in used_bins]
    if not available_bins:
        raise HTTPException(status_code=400, detail="No bins available")
    bin_number = secrets.choice(available_bins)

    team_doc = {
        "team_code": team_code,
        "team_name": body.team_name.strip(),
        "participant_count": body.participant_count,
        "bin_number": bin_number,
        "status": "REGISTERED",
        "created_at": datetime.utcnow()
    }
    await db.teams.insert_one(team_doc)

    default_hash = hash_password(DEFAULT_PARTICIPANT_PASSWORD)

    for i, p in enumerate(body.participants):
        participant_doc = {
            "team_code": team_code,
            "name": p.name.strip(),
            "email": p.email.strip(),
            "roll_number": p.roll_number or "",
            "college": p.college or "",
            "phone": p.phone or "",
            "is_team_leader": i == 0,
            "created_at": datetime.utcnow()
        }
        await db.participants.insert_one(participant_doc)

    auth_doc = {
        "team_code": team_code,
        "password_hash": default_hash,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    await db.team_auth.insert_one(auth_doc)

    await db.audit_logs.insert_one({
        "action": "team_created",
        "actor": admin.get("sub", "admin"),
        "details": f"Team {body.team_name} created with code {team_code}, bin {bin_number}",
        "timestamp": datetime.utcnow()
    })

    return {
        "message": "Registration Successful",
        "team_name": body.team_name,
        "team_code": team_code,
        "bin_number": bin_number,
        "participant_count": body.participant_count,
        "team_leader": body.participants[0].name,
        "default_password": DEFAULT_PARTICIPANT_PASSWORD
    }


@router.get("/teams")
async def list_teams(admin=Depends(get_admin_user)):
    db = get_db()
    teams = []
    async for team in db.teams.find().sort("created_at", -1):
        team["_id"] = str(team["_id"])
        blocked_count = await db.blocked_users.count_documents({"team_code": team["team_code"]})
        team["blocked_count"] = blocked_count
        team["is_blocked"] = team.get("status") == "BLOCKED" or blocked_count > 0
        teams.append(team)
    return {"teams": teams}


@router.get("/teams/{team_code}")
async def get_team(team_code: str, admin=Depends(get_admin_user)):
    db = get_db()
    team = await db.teams.find_one({"team_code": team_code})
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    team["_id"] = str(team["_id"])
    participants = []
    blocked_emails = set()
    async for bu in db.blocked_users.find({"team_code": team_code}):
        blocked_emails.add(bu["email"])
    async for p in db.participants.find({"team_code": team_code}):
        p["_id"] = str(p["_id"])
        p["is_blocked"] = p["email"] in blocked_emails
        participants.append(p)
    allocation = await db.allocations.find_one({"team_code": team_code})
    if allocation:
        allocation["_id"] = str(allocation["_id"])
    submission = await db.submissions.find_one({"team_code": team_code}, sort=[("submitted_at", -1)])
    if submission:
        submission["_id"] = str(submission["_id"])
    return {
        "team": team,
        "participants": participants,
        "allocation": allocation,
        "submission": submission
    }


@router.delete("/teams/{team_code}")
async def delete_team(team_code: str, admin=Depends(get_admin_user)):
    db = get_db()
    team = await db.teams.find_one({"team_code": team_code})
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    await db.teams.delete_one({"team_code": team_code})
    await db.participants.delete_many({"team_code": team_code})
    await db.team_auth.delete_one({"team_code": team_code})
    await db.allocations.delete_one({"team_code": team_code})
    await db.submissions.delete_one({"team_code": team_code})
    await db.checkins.delete_one({"team_code": team_code})
    await db.notifications.delete_many({"team_code": team_code})

    await db.audit_logs.insert_one({
        "action": "team_deleted",
        "actor": admin.get("sub", "admin"),
        "details": f"Deleted team {team_code} ({team['team_name']})",
        "timestamp": datetime.utcnow()
    })

    return {"message": f"Team {team_code} deleted"}


@router.get("/participants")
async def list_participants(admin=Depends(get_admin_user)):
    db = get_db()
    participants = []
    async for p in db.participants.find():
        p["_id"] = str(p["_id"])
        participants.append(p)
    return {"participants": participants}


@router.post("/block/team/{team_code}")
async def block_team(team_code: str, admin=Depends(get_admin_user)):
    db = get_db()
    team = await db.teams.find_one({"team_code": team_code})
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    participants = []
    async for p in db.participants.find({"team_code": team_code}):
        participants.append(p)
    for p in participants:
        existing = await db.blocked_users.find_one({"email": p["email"]})
        if not existing:
            await db.blocked_users.insert_one({
                "email": p["email"],
                "team_code": team_code,
                "blocked_by": admin.get("sub", "admin"),
                "blocked_at": datetime.utcnow(),
                "reason": f"Team {team_code} blocked"
            })
    await db.teams.update_one({"team_code": team_code}, {"$set": {"status": "BLOCKED"}})
    await db.audit_logs.insert_one({
        "action": "team_blocked",
        "actor": admin.get("sub", "admin"),
        "details": f"Blocked team {team_code} ({len(participants)} participants)",
        "timestamp": datetime.utcnow()
    })
    return {"message": f"Team {team_code} blocked", "blocked_count": len(participants)}


@router.post("/unblock/team/{team_code}")
async def unblock_team(team_code: str, admin=Depends(get_admin_user)):
    db = get_db()
    team = await db.teams.find_one({"team_code": team_code})
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    participants = []
    async for p in db.participants.find({"team_code": team_code}):
        participants.append(p)
    for p in participants:
        await db.blocked_users.delete_one({"email": p["email"]})
    await db.teams.update_one({"team_code": team_code}, {"$set": {"status": "REGISTERED"}})
    await db.audit_logs.insert_one({
        "action": "team_unblocked",
        "actor": admin.get("sub", "admin"),
        "details": f"Unblocked team {team_code}",
        "timestamp": datetime.utcnow()
    })
    return {"message": f"Team {team_code} unblocked"}


@router.post("/block/participant/{email}")
async def block_participant(email: str, admin=Depends(get_admin_user)):
    db = get_db()
    participant = await db.participants.find_one({"email": email.lower()})
    if not participant:
        raise HTTPException(status_code=404, detail="Participant not found")
    existing = await db.blocked_users.find_one({"email": email.lower()})
    if existing:
        raise HTTPException(status_code=400, detail="Participant already blocked")
    await db.blocked_users.insert_one({
        "email": email.lower(),
        "team_code": participant.get("team_code", ""),
        "blocked_by": admin.get("sub", "admin"),
        "blocked_at": datetime.utcnow(),
        "reason": "Individually blocked"
    })
    await db.audit_logs.insert_one({
        "action": "participant_blocked",
        "actor": admin.get("sub", "admin"),
        "details": f"Blocked participant {email}",
        "timestamp": datetime.utcnow()
    })
    return {"message": f"Participant {email} blocked"}


@router.post("/unblock/participant/{email}")
async def unblock_participant(email: str, admin=Depends(get_admin_user)):
    db = get_db()
    result = await db.blocked_users.delete_one({"email": email.lower()})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Blocked record not found")
    await db.audit_logs.insert_one({
        "action": "participant_unblocked",
        "actor": admin.get("sub", "admin"),
        "details": f"Unblocked participant {email}",
        "timestamp": datetime.utcnow()
    })
    return {"message": f"Participant {email} unblocked"}


@router.get("/user-management")
async def user_management_list(admin=Depends(get_admin_user)):
    db = get_db()
    teams = []
    async for t in db.teams.find().sort("created_at", -1):
        t["_id"] = str(t["_id"])
        team_code = t["team_code"]
        blocked_count = await db.blocked_users.count_documents({"team_code": team_code})
        t["blocked_count"] = blocked_count
        t["is_blocked"] = t.get("status") == "BLOCKED" or blocked_count > 0
        teams.append(t)

    all_participants = []
    async for p in db.participants.find().sort("created_at", -1):
        p["_id"] = str(p["_id"])
        blocked = await db.blocked_users.find_one({"email": p["email"]})
        p["is_blocked"] = blocked is not None
        all_participants.append(p)

    blocked_list = []
    async for bu in db.blocked_users.find():
        bu["_id"] = str(bu["_id"])
        participant = await db.participants.find_one({"email": bu["email"]})
        bu["name"] = participant.get("name", "") if participant else ""
        team = await db.teams.find_one({"team_code": bu.get("team_code", "")})
        bu["team_name"] = team.get("team_name", "") if team else ""
        blocked_list.append(bu)

    return {"teams": teams, "participants": all_participants, "blocked_users": blocked_list}
