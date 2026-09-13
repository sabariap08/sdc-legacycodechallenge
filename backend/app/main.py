import os
import logging
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel
from app.database import connect_db, close_db, is_db_available, get_db
from app.security import hash_password, verify_password, create_token
from app.storage import get_challenge_zip_for_download

logger = logging.getLogger(__name__)

app = FastAPI(title="Legacy Code Rescue - Admin Platform")

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
        return response


app.add_middleware(NoCacheMiddleware)

from app.routers import admin

app.include_router(admin.router)

frontend_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "frontend")
app.mount("/static", StaticFiles(directory=frontend_path), name="static")


def page(html_path: str) -> FileResponse:
    return FileResponse(
        os.path.join(frontend_path, html_path),
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/login")
async def admin_login(body: LoginRequest):
    db = get_db()
    username = body.username.strip()
    password = body.password

    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")

    admin_user = await db.admins.find_one({"username": username})
    if admin_user and verify_password(password, admin_user["password_hash"]):
        token = create_token({"sub": admin_user["username"], "role": "admin"})
        await db.audit_logs.insert_one({
            "action": "admin_login",
            "actor": admin_user["username"],
            "details": "Admin logged in",
            "timestamp": datetime.utcnow(),
        })
        return {"access_token": token, "token_type": "bearer", "role": "admin"}

    raise HTTPException(status_code=401, detail="Invalid username or password")


@app.get("/")
async def root():
    return RedirectResponse(url="/login")


@app.get("/login")
async def login_page():
    return page("login.html")


@app.get("/admin/dashboard")
async def admin_dashboard_page():
    return page(os.path.join("admin", "dashboard.html"))


@app.get("/admin/challenges")
async def admin_challenges_page():
    return page(os.path.join("admin", "challenges.html"))


@app.get("/admin/teams")
async def admin_teams_page():
    return page(os.path.join("admin", "teams.html"))


@app.get("/admin/allocation")
async def admin_allocation_page():
    return page(os.path.join("admin", "allocation.html"))


@app.get("/admin/reports")
async def admin_reports_page():
    return page(os.path.join("admin", "reports.html"))


@app.get("/api/health")
async def health_check():
    db_ok = is_db_available()
    return {"status": "ok" if db_ok else "degraded", "database": "connected" if db_ok else "unavailable"}


@app.get("/api/releases/download/{token}")
async def download_release(token: str):
    db = get_db()
    release = await db.releases.find_one({"download_token": token, "status": "SENT"})
    if not release:
        raise HTTPException(status_code=404, detail="Download link is invalid or expired")

    challenge_code = release.get("challenge_id")
    if not challenge_code:
        raise HTTPException(status_code=404, detail="Challenge not found")

    challenge = await db.challenges.find_one({"challenge_code": challenge_code})
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")

    zip_file = await get_challenge_zip_for_download(challenge_code)
    if not zip_file:
        raise HTTPException(status_code=404, detail="Challenge ZIP is no longer available")

    original_filename, content = zip_file
    filename = original_filename if original_filename and "." in original_filename else f"{challenge_code}.zip"

    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


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
                    "created_at": datetime.utcnow(),
                })
                logger.info("Default admin created")
        except Exception as e:
            logger.error("Admin seed failed: %s", e)
        logger.info("Startup complete!")
    else:
        logger.warning("Startup complete (MongoDB unavailable - will retry in background)")


@app.on_event("shutdown")
async def shutdown():
    await close_db()
