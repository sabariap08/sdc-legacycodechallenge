from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from app.database import get_db
from app.security import verify_password, create_token, hash_password
from datetime import datetime
from typing import Optional

router = APIRouter(prefix="/api/participant", tags=["participant_auth"])


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

    auth_record = await db.team_auth.find_one({"team_code": team["team_code"]})
    if not auth_record:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Team auth not configured")

    if not verify_password(password, auth_record["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_token({
        "sub": team["team_code"],
        "role": "participant",
        "team_name": team["team_name"]
    })

    await db.audit_logs.insert_one({
        "action": "participant_login",
        "actor": team["team_code"],
        "details": "Participant logged in via " + ("email" if "@" in password else "team_code"),
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
