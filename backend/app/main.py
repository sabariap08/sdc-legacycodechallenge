import os
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel
from datetime import datetime
from app.database import connect_db, close_db, is_db_available
from app.security import hash_password, verify_password, create_token
from app.database import get_db

app = FastAPI(title="Legacy Code Rescue Portal")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
            response.headers["Vary"] = "Accept-Encoding"
        return response


app.add_middleware(NoCacheMiddleware)

from app.routers import admin, participant, workspace, execution, submission

app.include_router(admin.router)
app.include_router(participant.router)
app.include_router(workspace.router)
app.include_router(execution.router)
app.include_router(submission.router)

frontend_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "frontend")
app.mount("/static", StaticFiles(directory=frontend_path), name="static")


class LoginRequest(BaseModel):
    identifier: str
    password: str


@app.post("/api/login")
async def unified_login(body: LoginRequest):
    db = get_db()
    identifier = body.identifier.strip()
    password = body.password

    if not identifier or not password:
        raise HTTPException(status_code=400, detail="Email/username and password are required")

    admin = await db.admins.find_one({"username": identifier})
    if admin and verify_password(password, admin["password_hash"]):
        token = create_token({"sub": admin["username"], "role": "admin"})
        await db.audit_logs.insert_one({
            "action": "admin_login",
            "actor": admin["username"],
            "details": "Admin logged in via unified login",
            "timestamp": datetime.utcnow()
        })
        return {"access_token": token, "token_type": "bearer", "role": "admin"}

    participant = await db.participants.find_one({"email": identifier.lower()})
    if participant:
        team_code = participant.get("team_code")
        if team_code:
            blocked = await db.blocked_users.find_one({"email": identifier.lower()})
            if blocked:
                raise HTTPException(status_code=403, detail="Your account has been blocked. Contact the organizer.")

            team = await db.teams.find_one({"team_code": team_code})
            if team and team.get("status") == "BLOCKED":
                team_blocked = await db.blocked_users.find_one({"team_code": team_code})
                if team_blocked:
                    raise HTTPException(status_code=403, detail="Your account has been blocked. Contact the organizer.")

            auth_record = await db.team_auth.find_one({"team_code": team_code})
            if auth_record and verify_password(password, auth_record["password_hash"]):
                token = create_token({
                    "sub": team_code,
                    "role": "participant",
                    "team_name": team["team_name"] if team else ""
                })
                await db.audit_logs.insert_one({
                    "action": "participant_login",
                    "actor": team_code,
                    "details": "Participant logged in via unified login",
                    "timestamp": datetime.utcnow()
                })
                return {"access_token": token, "token_type": "bearer", "role": "participant"}

    raise HTTPException(status_code=401, detail="Invalid email or password")


@app.get("/api/server-time")
async def server_time():
    return {"server_time": datetime.utcnow().isoformat()}


@app.get("/")
async def root():
    return RedirectResponse(url="/login")


@app.get("/login")
async def login_page():
    return FileResponse(os.path.join(frontend_path, "login.html"))


@app.get("/admin/login")
async def admin_login_page():
    return FileResponse(os.path.join(frontend_path, "login.html"))


@app.get("/admin/dashboard")
async def admin_dashboard_page():
    return FileResponse(os.path.join(frontend_path, "admin", "dashboard.html"))


@app.get("/admin/teams")
async def admin_teams_page():
    return FileResponse(os.path.join(frontend_path, "admin", "teams.html"))


@app.get("/admin/new-team")
async def admin_new_team_page():
    return FileResponse(os.path.join(frontend_path, "admin", "new_team.html"))


@app.get("/admin/challenges")
async def admin_challenges_page():
    return FileResponse(os.path.join(frontend_path, "admin", "challenges.html"))


@app.get("/admin/allocation")
async def admin_allocation_page():
    return FileResponse(os.path.join(frontend_path, "admin", "allocation.html"))


@app.get("/admin/leaderboard")
async def admin_leaderboard_page():
    return FileResponse(os.path.join(frontend_path, "admin", "leaderboard.html"))


@app.get("/admin/event")
async def admin_event_page():
    return FileResponse(os.path.join(frontend_path, "admin", "event.html"))


@app.get("/admin/history")
async def admin_history_page():
    return FileResponse(os.path.join(frontend_path, "admin", "history.html"))


@app.get("/admin/audit")
async def admin_audit_page():
    return FileResponse(os.path.join(frontend_path, "admin", "audit.html"))


@app.get("/admin/users")
async def admin_users_page():
    return FileResponse(os.path.join(frontend_path, "admin", "users.html"))


@app.get("/admin/submissions")
async def admin_submissions_page():
    return FileResponse(os.path.join(frontend_path, "admin", "submissions.html"))


@app.get("/admin/repo-viewer")
async def admin_repo_viewer_page():
    return FileResponse(os.path.join(frontend_path, "admin", "repo_viewer.html"))


@app.get("/participant/login")
async def participant_login_page():
    return FileResponse(os.path.join(frontend_path, "login.html"))


@app.get("/participant/dashboard")
async def participant_dashboard_page():
    return FileResponse(os.path.join(frontend_path, "participant", "dashboard.html"))


@app.get("/participant/ide")
async def participant_ide_page():
    return FileResponse(os.path.join(frontend_path, "participant", "ide.html"))


@app.get("/api/health")
async def health_check():
    db_ok = is_db_available()
    return {"status": "ok" if db_ok else "degraded", "database": "connected" if db_ok else "unavailable"}


@app.on_event("startup")
async def startup():
    logger.info("Starting up...")

    db = await connect_db()
    if db is not None:
        try:
            from app.config import ADMIN_USERNAME, ADMIN_PASSWORD
            existing_admin = await db.admins.find_one({"username": ADMIN_USERNAME})
            if not existing_admin:
                await db.admins.insert_one({
                    "username": ADMIN_USERNAME,
                    "password_hash": hash_password(ADMIN_PASSWORD),
                    "created_at": __import__("datetime").datetime.utcnow()
                })
                logger.info("Default admin created")
        except Exception as e:
            logger.error("Admin seed failed: %s", e)
        try:
            from app.storage import sync_evaluators_from_disk
            await sync_evaluators_from_disk()
        except Exception as e:
            logger.error("Evaluator sync failed: %s", e)
        logger.info("Startup complete!")
    else:
        logger.warning("Startup complete (MongoDB unavailable - will retry in background)")


@app.on_event("shutdown")
async def shutdown():
    await close_db()
