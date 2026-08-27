from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.config import SECRET_KEY, JWT_ALGORITHM, JWT_EXPIRE_MINUTES
from app.database import get_db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security_scheme = HTTPBearer()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=JWT_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


async def get_admin_user(credentials: HTTPAuthorizationCredentials = Depends(security_scheme)):
    payload = decode_token(credentials.credentials)
    if payload.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return payload


async def get_participant_user(credentials: HTTPAuthorizationCredentials = Depends(security_scheme)):
    payload = decode_token(credentials.credentials)
    if payload.get("role") != "participant":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Participant access required")
    team_code = payload.get("sub")
    if team_code:
        db = get_db()
        team = await db.teams.find_one({"team_code": team_code})
        if team and team.get("status") == "BLOCKED":
            blocked_record = await db.blocked_users.find_one({"team_code": team_code})
            if blocked_record:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your account has been blocked. Contact the organizer.")
        participants = []
        async for p in db.participants.find({"team_code": team_code}):
            participants.append(p)
        for p in participants:
            blocked = await db.blocked_users.find_one({"email": p["email"].lower().strip()})
            if blocked:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your account has been blocked. Contact the organizer.")
    return payload
