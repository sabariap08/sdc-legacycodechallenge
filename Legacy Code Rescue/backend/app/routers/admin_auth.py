from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from app.database import get_db
from app.security import verify_password, create_token, hash_password
from app.utils import generate_team_code
from datetime import datetime

router = APIRouter(prefix="/api/admin", tags=["admin"])


class AdminLogin(BaseModel):
    username: str
    password: str


@router.post("/login")
async def admin_login(body: AdminLogin):
    db = get_db()
    admin = await db.admins.find_one({"username": body.username})
    if not admin or not verify_password(body.password, admin["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = create_token({"sub": admin["username"], "role": "admin"})
    await db.audit_logs.insert_one({
        "action": "admin_login",
        "actor": body.username,
        "details": "Admin logged in",
        "timestamp": datetime.utcnow()
    })
    return {"access_token": token, "token_type": "bearer"}
