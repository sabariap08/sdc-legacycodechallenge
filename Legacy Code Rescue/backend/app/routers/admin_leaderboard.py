from fastapi import APIRouter, Depends
from app.database import get_db
from app.security import get_admin_user

router = APIRouter(prefix="/api/admin", tags=["leaderboard"])


@router.get("/leaderboard")
async def get_leaderboard(admin=Depends(get_admin_user)):
    db = get_db()
    entries = []
    async for sub in db.submissions.find({"status": "evaluated"}).sort("score", -1):
        team = await db.teams.find_one({"team_code": sub["team_code"]})
        alloc = await db.allocations.find_one({"team_code": sub["team_code"]})
        entries.append({
            "team_code": sub["team_code"],
            "team_name": team["team_name"] if team else "",
            "challenge_code": alloc.get("challenge_code", "") if alloc else "",
            "score": sub.get("score", 0),
            "submitted_at": sub.get("submitted_at"),
            "status": sub.get("status", "")
        })
    for i, e in enumerate(entries, 1):
        e["rank"] = i
    return {"leaderboard": entries}
