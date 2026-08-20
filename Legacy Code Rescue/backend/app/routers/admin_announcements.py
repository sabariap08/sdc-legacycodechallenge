from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.security import get_admin_user
from datetime import datetime

router = APIRouter(prefix="/api/admin", tags=["announcements"])


class AnnouncementCreate(BaseModel):
    title: str
    message: str
    priority: Optional[str] = "normal"


@router.post("/announcements")
async def create_announcement(body: AnnouncementCreate, admin=Depends(get_admin_user)):
    db = get_db()
    doc = {
        "title": body.title,
        "message": body.message,
        "priority": body.priority,
        "author": admin.get("sub", "admin"),
        "created_at": datetime.utcnow(),
        "active": True
    }
    result = await db.announcements.insert_one(doc)
    await db.audit_logs.insert_one({
        "action": "announcement_created",
        "actor": admin.get("sub", "admin"),
        "details": f"Announcement: {body.title}",
        "timestamp": datetime.utcnow()
    })
    return {"message": "Announcement created", "id": str(result.inserted_id)}


@router.get("/announcements")
async def list_announcements(admin=Depends(get_admin_user)):
    db = get_db()
    items = []
    async for a in db.announcements.find().sort("created_at", -1):
        a["_id"] = str(a["_id"])
        items.append(a)
    return {"announcements": items}


@router.delete("/announcements/{announcement_id}")
async def delete_announcement(announcement_id: str, admin=Depends(get_admin_user)):
    db = get_db()
    from bson import ObjectId
    await db.announcements.delete_one({"_id": ObjectId(announcement_id)})
    return {"message": "Announcement deleted"}
