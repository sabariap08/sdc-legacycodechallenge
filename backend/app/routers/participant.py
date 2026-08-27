from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from app.database import get_db
from app.security import verify_password, create_token, hash_password, get_admin_user, get_participant_user
from datetime import datetime

router = APIRouter(prefix="/api/participant", tags=["participant"])


# ─────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────

class ParticipantLogin(BaseModel):
    team_code: str
    password: str


class ParticipantEmailLogin(BaseModel):
    email: str
    password: str


async def _authenticate_participant(team_code: str, password: str):
    db = get_db()
    team = await db.teams.find_one({"team_code": team_code.upper().strip()})
    if not team:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if team.get("status") == "BLOCKED":
        blocked_record = await db.blocked_users.find_one({"team_code": team["team_code"]})
        if blocked_record:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your account has been blocked. Contact the organizer.")

    auth_record = await db.team_auth.find_one({"team_code": team["team_code"]})
    if not auth_record:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Team auth not configured")

    if not verify_password(password, auth_record["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    participants = []
    async for p in db.participants.find({"team_code": team["team_code"]}):
        participants.append(p)
    for p in participants:
        blocked = await db.blocked_users.find_one({"email": p["email"].lower().strip()})
        if blocked:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your account has been blocked. Contact the organizer.")

    token = create_token({
        "sub": team["team_code"],
        "role": "participant",
        "team_name": team["team_name"]
    })

    await db.audit_logs.insert_one({
        "action": "participant_login",
        "actor": team["team_code"],
        "details": "Participant logged in via team_code",
        "timestamp": datetime.utcnow()
    })

    return {"access_token": token, "token_type": "bearer", "team_code": team["team_code"]}


@router.post("/login")
async def participant_login(body: ParticipantLogin):
    return await _authenticate_participant(body.team_code, body.password)


@router.post("/login-email")
async def participant_login_email(body: ParticipantEmailLogin):
    db = get_db()
    participant = await db.participants.find_one({"email": body.email.lower().strip()})
    if not participant:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    team_code = participant.get("team_code")
    if not team_code:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No team associated with this email")

    blocked = await db.blocked_users.find_one({"email": body.email.lower().strip()})
    if blocked:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your account has been blocked. Contact the organizer.")

    return await _authenticate_participant(team_code, body.password)


# ─────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────

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

    from app.events import get_current_event, compute_event_status
    event = await get_current_event()
    computed = compute_event_status(event) if event else "NO_EVENT"

    now = datetime.utcnow()
    remaining_seconds = None
    countdown_seconds = None
    if event:
        start = event.get("event_start_time")
        end = event.get("event_end_time")
        if start:
            if isinstance(start, str):
                start = datetime.fromisoformat(str(start).replace("Z", "+00:00")).replace(tzinfo=None)
            if computed == "UPCOMING":
                countdown_seconds = max(0, int((start - now).total_seconds()))
            elif computed == "ONGOING" and end:
                if isinstance(end, str):
                    end = datetime.fromisoformat(str(end).replace("Z", "+00:00")).replace(tzinfo=None)
                remaining_seconds = max(0, int((end - now).total_seconds()))

    announcements = []
    async for a in db.announcements.find({"active": True}).sort("created_at", -1).limit(10):
        a.pop("_id", None)
        announcements.append(a)

    submission = await db.submissions.find_one(
        {"team_code": team_code, "event_id": event["event_id"]} if event else {"team_code": team_code},
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

    return {
        "team_code": team.get("team_code"),
        "team_name": team.get("team_name"),
        "participants": participants,
        "event_status": computed,
        "event_name": event.get("event_name") if event else None,
        "event_code": event.get("event_code") if event else None,
        "event_id": event.get("event_id") if event else None,
        "event_start_time": event.get("event_start_time") if event else None,
        "event_end_time": event.get("event_end_time") if event else None,
        "countdown_seconds": countdown_seconds,
        "remaining_seconds": remaining_seconds,
        "announcements": announcements,
        "allocation": {
            "challenge_code": allocation.get("challenge_code") if allocation else None,
            "released": allocation.get("released", False) if allocation else False,
        },
        "challenge": challenge_info,
        "submission": submission,
        "leaderboard_enabled": (event.get("leaderboard_enabled", False) if event else False) if computed in ("ONGOING", "UPCOMING") else False,
    }


@router.get("/event-countdown")
async def participant_event_countdown(user=Depends(get_participant_user)):
    db = get_db()
    from app.events import get_current_event, compute_event_status
    event = await get_current_event()
    now = datetime.utcnow()
    computed = compute_event_status(event) if event else "NO_EVENT"
    countdown_seconds = None
    remaining_seconds = None
    if event:
        start = event.get("event_start_time")
        end = event.get("event_end_time")
        if start:
            if isinstance(start, str):
                start = datetime.fromisoformat(str(start).replace("Z", "+00:00")).replace(tzinfo=None)
            if computed == "UPCOMING":
                countdown_seconds = max(0, int((start - now).total_seconds()))
            elif computed == "ONGOING" and end:
                if isinstance(end, str):
                    end = datetime.fromisoformat(str(end).replace("Z", "+00:00")).replace(tzinfo=None)
                remaining_seconds = max(0, int((end - now).total_seconds()))
    return {
        "server_time": now.isoformat(),
        "status": computed,
        "event_code": event.get("event_code") if event else None,
        "countdown_seconds": countdown_seconds,
        "remaining_seconds": remaining_seconds,
    }


@router.get("/leaderboard")
async def participant_leaderboard(user=Depends(get_participant_user)):
    db = get_db()
    from app.events import get_current_event
    event = await get_current_event()
    if not event or not event.get("leaderboard_enabled") or event.get("status") == "CANCELLED":
        return {"leaderboard": [], "message": "Leaderboard not enabled yet"}
    event_id = event["event_id"]

    entries = []
    async for sub in db.submissions.find({"status": "evaluated", "event_id": event_id}).sort("score", -1):
        team = await db.teams.find_one({"team_code": sub["team_code"]})
        alloc = await db.allocations.find_one({"team_code": sub["team_code"], "event_id": event_id})
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


# ─────────────────────────────────────────────
# PASSWORD / T&C SETUP
# ─────────────────────────────────────────────

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
