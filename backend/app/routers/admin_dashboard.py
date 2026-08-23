from fastapi import APIRouter, Depends, HTTPException
from app.database import get_db
from app.security import get_admin_user
from app.utils import compute_event_status

router = APIRouter(prefix="/api/admin", tags=["dashboard"])


@router.get("/dashboard")
async def get_dashboard(admin=Depends(get_admin_user)):
    db = get_db()

    total_teams = await db.teams.count_documents({})
    total_participants = await db.participants.count_documents({})
    total_challenges = await db.challenges.count_documents({})
    ready_challenges = await db.challenges.count_documents({"status": "READY"})
    checked_in = await db.checkins.count_documents({"status": {"$in": ["CHECKED-IN", "STARTED", "COMPLETED"]}})
    completed = await db.checkins.count_documents({"status": "COMPLETED"})

    event_settings = await db.event_settings.find_one({})
    event_status = compute_event_status(event_settings) if event_settings else "DRAFT"

    challenge_distribution = []
    async for ch in db.challenges.find():
        alloc_count = await db.allocations.count_documents({"challenge_code": ch["challenge_code"]})
        challenge_distribution.append({
            "challenge_code": ch["challenge_code"],
            "challenge_name": ch.get("challenge_name", ch.get("name", ch.get("title", ch["challenge_code"]))),
            "status": ch.get("status", "UNKNOWN"),
            "allocated_teams": alloc_count
        })

    allocated_teams = await db.allocations.count_documents({})
    released_allocations = await db.allocations.count_documents({"released": True})

    total_submissions = await db.submissions.count_documents({})
    evaluated_submissions = await db.submissions.count_documents({"status": "evaluated"})
    notifications_count = await db.notifications.count_documents({})

    alloc_state = "PENDING"
    if event_settings:
        alloc_state = event_settings.get("allocation_state", "PENDING")

    return {
        "total_teams": total_teams,
        "total_participants": total_participants,
        "imported_challenges": total_challenges,
        "ready_challenges": ready_challenges,
        "registered_teams": total_teams,
        "checked_in_teams": checked_in,
        "completed_teams": completed,
        "event_status": event_status,
        "challenge_distribution": challenge_distribution,
        "total_submissions": total_submissions,
        "evaluated_submissions": evaluated_submissions,
        "notifications_count": notifications_count,
        "allocated_teams": allocated_teams,
        "released_allocations": released_allocations,
        "allocation_state": alloc_state,
    }
