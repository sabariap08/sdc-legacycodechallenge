from fastapi import APIRouter, Depends, Query
from app.database import get_db
from app.security import get_admin_user
import re

router = APIRouter(prefix="/api/admin", tags=["audit"])


@router.get("/audit-logs")
async def get_audit_logs(admin=Depends(get_admin_user), search: str = Query(default="")):
    db = get_db()
    query = {}
    if search.strip():
        safe = re.escape(search.strip())
        query = {
            "$or": [
                {"action": {"$regex": safe, "$options": "i"}},
                {"actor": {"$regex": safe, "$options": "i"}},
                {"details": {"$regex": safe, "$options": "i"}},
            ]
        }
    logs = []
    async for log in db.audit_logs.find(query).sort("timestamp", -1).limit(500):
        log["_id"] = str(log["_id"])
        logs.append(log)
    return {"logs": logs}
