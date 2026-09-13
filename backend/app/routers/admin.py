import os
import io
import logging
import secrets
import shutil
import tempfile
import zipfile
import asyncio
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request
from pydantic import BaseModel, EmailStr
from app.database import get_db, is_db_available
from app.security import get_admin_user
from app.utils import generate_team_code
from app.storage import (
    save_challenge_files_to_db,
    get_challenge_file_tree_from_db,
    get_challenge_file_content_from_db,
    delete_challenge_files_from_db,
    delete_challenge_zip_from_db,
    save_challenge_zip_to_db,
    get_challenge_zip_from_db,
)
from app.emailer import email_configured, email_missing_config, _send_sync
from app.reporting import (
    challenge_report_pdf,
    teams_report_pdf,
    allocations_report_pdf,
    status_report_pdf,
)
from app.config import MAX_ZIP_SIZE_MB, PUBLIC_BASE_URL
from datetime import datetime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _download_base_url(request: Request) -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL.rstrip("/")
    return str(request.base_url).rstrip("/")


@router.get("/dashboard")
async def get_dashboard(admin=Depends(get_admin_user)):
    db = get_db()
    total_challenges = await db.challenges.count_documents({})
    total_teams = await db.teams.count_documents({})

    total_members = 0
    async for t in db.teams.find({}, {"members": 1}):
        total_members += len(t.get("members", []) or [])

    allocated = await db.allocations.count_documents(
        {"challenge_id": {"$ne": None, "$nin": ["", None]}}
    )
    if allocated is None:
        allocated = 0
    unallocated_teams = total_teams - allocated
    allocated_challenges = allocated

    return {
        "total_challenges": total_challenges,
        "total_teams": total_teams,
        "total_team_members": total_members,
        "total_allocated_challenges": allocated_challenges,
        "total_unallocated_challenges": total_challenges or 0,
        "allocated_teams": allocated,
        "unallocated_teams": unallocated_teams if unallocated_teams >= 0 else 0,
    }


# CHALLENGES

DANGEROUS_EXTENSIONS = {
    '.exe', '.msi', '.dll', '.so', '.dylib', '.bin', '.cmd', '.com',
    '.scr', '.pif', '.vbs', '.vbe', '.jsf', '.jse', '.wsf', '.wsh',
    '.ps1', '.psm1', '.psd1', '.reg', '.inf',
    '.sh', '.bash', '.csh', '.ksh', '.zsh',
}


def _is_safe_zip_member(member_path: str) -> bool:
    if ".." in member_path or member_path.startswith("/"):
        return False
    parts = member_path.replace("\\", "/").split("/")
    return all(part not in ("", ".", "..") for part in parts)


def _is_dangerous_file(filename: str) -> bool:
    _, ext = os.path.splitext(filename.lower())
    if ext in DANGEROUS_EXTENSIONS:
        return True
    basename = os.path.basename(filename).lower()
    dangerous_names = {'passwd', 'shadow', 'hosts', 'sudoers', '.ssh', 'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519'}
    return basename in dangerous_names


@router.post("/challenges")
async def upload_challenge(
    challenge_name: str = Form(...),
    challenge_code: str = Form(...),
    file: UploadFile = File(...),
    admin=Depends(get_admin_user),
):
    db = get_db()

    challenge_code = challenge_code.strip()
    challenge_name = challenge_name.strip()

    if not challenge_code:
        raise HTTPException(status_code=400, detail="Challenge code is required")
    if not challenge_name:
        raise HTTPException(status_code=400, detail="Challenge name is required")
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded")
    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only ZIP files are supported")

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    max_bytes = MAX_ZIP_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=400, detail=f"File too large (max {MAX_ZIP_SIZE_MB}MB)")

    storage_path = tempfile.mkdtemp(prefix="lcr_upload_")

    try:
        with zipfile.ZipFile(io.BytesIO(content), "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if not _is_safe_zip_member(info.filename):
                    raise HTTPException(status_code=400, detail=f"Path traversal detected: {info.filename}")
                if _is_dangerous_file(info.filename):
                    raise HTTPException(status_code=400, detail=f"Unsupported file type: {info.filename}")
            zf.extractall(storage_path)
    except zipfile.BadZipFile:
        shutil.rmtree(storage_path, ignore_errors=True)
        raise HTTPException(status_code=400, detail="Invalid or corrupted ZIP file")
    except HTTPException:
        shutil.rmtree(storage_path, ignore_errors=True)
        raise

    has_files = False
    for root, dirs, files in os.walk(storage_path):
        if files:
            has_files = True
            break
    if not has_files:
        shutil.rmtree(storage_path, ignore_errors=True)
        raise HTTPException(status_code=400, detail="ZIP file is empty (no files found)")

    try:
        existing = await db.challenges.find_one({"challenge_code": challenge_code})
        if existing:
            await delete_challenge_files_from_db(challenge_code)
            await db.challenges.update_one(
                {"challenge_code": challenge_code},
                {"$set": {
                    "challenge_name": challenge_name,
                    "updated_at": datetime.utcnow(),
                    "file_count": 0,
                }},
            )
        else:
            await db.challenges.insert_one({
                "challenge_code": challenge_code,
                "challenge_name": challenge_name,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "file_count": 0,
            })

        files_persisted = await save_challenge_files_to_db(challenge_code, storage_path)
        await save_challenge_zip_to_db(challenge_code, file.filename, content)
        await db.challenges.update_one(
            {"challenge_code": challenge_code},
            {"$set": {"file_count": files_persisted, "updated_at": datetime.utcnow()}},
        )
        if files_persisted == 0 and is_db_available():
            logger.error("Upload %s persisted 0 files to MongoDB", challenge_code)

        await db.audit_logs.insert_one({
            "action": "challenge_uploaded",
            "actor": admin.get("sub", "admin"),
            "details": f"Uploaded challenge {challenge_code} ({challenge_name}), {files_persisted} files",
            "timestamp": datetime.utcnow(),
        })

        return {
            "message": f"Challenge {challenge_code} uploaded successfully",
            "challenge_code": challenge_code,
            "challenge_name": challenge_name,
            "files_persisted": files_persisted,
        }
    finally:
        shutil.rmtree(storage_path, ignore_errors=True)


@router.get("/challenges")
async def list_challenges(admin=Depends(get_admin_user)):
    db = get_db()
    challenges = []
    async for ch in db.challenges.find().sort("created_at", -1):
        ch["_id"] = str(ch["_id"])
        challenges.append(ch)
    return {"challenges": challenges}


@router.delete("/challenges/{challenge_code}")
async def delete_challenge(challenge_code: str, admin=Depends(get_admin_user)):
    db = get_db()
    challenge = await db.challenges.find_one({"challenge_code": challenge_code})
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")

    await delete_challenge_files_from_db(challenge_code)
    await delete_challenge_zip_from_db(challenge_code)
    await db.challenges.delete_one({"challenge_code": challenge_code})
    await db.allocations.delete_many({"challenge_id": challenge_code})

    await db.audit_logs.insert_one({
        "action": "challenge_deleted",
        "actor": admin.get("sub", "admin"),
        "details": f"Deleted challenge {challenge_code} ({challenge.get('challenge_name', '')})",
        "timestamp": datetime.utcnow(),
    })

    return {"message": f"Challenge {challenge_code} deleted successfully"}


@router.get("/challenges/{challenge_code}/file-tree")
async def get_challenge_tree(challenge_code: str, admin=Depends(get_admin_user)):
    challenge = await get_db().challenges.find_one({"challenge_code": challenge_code})
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")

    tree = await get_challenge_file_tree_from_db(challenge_code)
    if not tree:
        return {"tree": [], "challenge_code": challenge_code, "challenge_name": challenge.get("challenge_name", challenge_code), "empty": True}

    return {
        "tree": tree,
        "challenge_code": challenge_code,
        "challenge_name": challenge.get("challenge_name", challenge_code),
        "empty": False,
    }


@router.get("/challenges/{challenge_code}/file")
async def get_challenge_file(challenge_code: str, path: str, admin=Depends(get_admin_user)):
    challenge = await get_db().challenges.find_one({"challenge_code": challenge_code})
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")

    parts = path.replace("\\", "/").split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise HTTPException(status_code=400, detail="Invalid file path")

    data = await get_challenge_file_content_from_db(challenge_code, path)
    if data is None:
        raise HTTPException(status_code=404, detail="File not found")

    return {
        "content": data["content"].decode("utf-8", errors="replace") if not data["binary"] else "",
        "path": path,
        "binary": data["binary"],
        "size": data["size"],
    }


# TEAMS

class TeamMember(BaseModel):
    name: str
    email: EmailStr


class TeamCreate(BaseModel):
    team_name: str
    team_count: int
    members: List[TeamMember]


@router.post("/teams")
async def create_team(body: TeamCreate, admin=Depends(get_admin_user)):
    db = get_db()

    if not body.team_name or not body.team_name.strip():
        raise HTTPException(status_code=400, detail="Team name is required")
    if body.team_count < 1:
        raise HTTPException(status_code=400, detail="Team count must be at least 1")
    if body.team_count > 4:
        raise HTTPException(status_code=400, detail="Maximum team size is 4 members")
    if len(body.members) != body.team_count:
        raise HTTPException(status_code=400, detail=f"Expected {body.team_count} members but received {len(body.members)}")

    existing = await db.teams.find_one({"team_name": {"$regex": f"^{body.team_name.strip()}$", "$options": "i"}})
    if existing:
        raise HTTPException(status_code=400, detail="Team name already exists")

    names_ok = all(m.name and m.name.strip() for m in body.members)
    if not names_ok:
        raise HTTPException(status_code=400, detail="All member names are required")

    emails = [m.email.lower().strip() for m in body.members]
    if len(set(emails)) != len(emails):
        raise HTTPException(status_code=400, detail="Duplicate member emails within the same team are not allowed")

    team_code = generate_team_code()
    attempts = 0
    while await db.teams.find_one({"team_code": team_code}) and attempts < 10:
        team_code = generate_team_code()
        attempts += 1

    members_doc = [
        {"name": m.name.strip(), "email": m.email.lower().strip()}
        for m in body.members
    ]

    await db.teams.insert_one({
        "team_code": team_code,
        "team_name": body.team_name.strip(),
        "team_count": body.team_count,
        "members": members_doc,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    })

    await db.audit_logs.insert_one({
        "action": "team_created",
        "actor": admin.get("sub", "admin"),
        "details": f"Team {body.team_name} created with code {team_code}",
        "timestamp": datetime.utcnow(),
    })

    return {"message": "Team created", "team_code": team_code, "team_name": body.team_name}


@router.get("/teams")
async def list_teams(admin=Depends(get_admin_user)):
    db = get_db()
    teams = []
    async for t in db.teams.find().sort("created_at", -1):
        t["_id"] = str(t["_id"])
        alloc = await db.allocations.find_one({"team_code": t["team_code"]})
        t["allocated_challenge"] = alloc.get("challenge_id") if alloc else None
        teams.append(t)
    return {"teams": teams}


@router.get("/teams/{team_code}")
async def get_team(team_code: str, admin=Depends(get_admin_user)):
    db = get_db()
    t = await db.teams.find_one({"team_code": team_code})
    if not t:
        raise HTTPException(status_code=404, detail="Team not found")
    t["_id"] = str(t["_id"])
    alloc = await db.allocations.find_one({"team_code": team_code})
    if alloc:
        alloc["_id"] = str(alloc["_id"])
    return {"team": t, "allocation": alloc}


@router.put("/teams/{team_code}")
async def update_team(team_code: str, body: TeamCreate, admin=Depends(get_admin_user)):
    db = get_db()
    t = await db.teams.find_one({"team_code": team_code})
    if not t:
        raise HTTPException(status_code=404, detail="Team not found")

    if body.team_count < 1:
        raise HTTPException(status_code=400, detail="Team count must be at least 1")
    if body.team_count > 4:
        raise HTTPException(status_code=400, detail="Maximum team size is 4 members")
    if len(body.members) != body.team_count:
        raise HTTPException(status_code=400, detail=f"Expected {body.team_count} members but received {len(body.members)}")

    existing = await db.teams.find_one({
        "team_name": {"$regex": f"^{body.team_name.strip()}$", "$options": "i"},
        "team_code": {"$ne": team_code},
    })
    if existing:
        raise HTTPException(status_code=400, detail="Team name already exists")

    emails = [m.email.lower().strip() for m in body.members]
    if len(set(emails)) != len(emails):
        raise HTTPException(status_code=400, detail="Duplicate member emails within the same team are not allowed")

    await db.teams.update_one(
        {"team_code": team_code},
        {"$set": {
            "team_name": body.team_name.strip(),
            "team_count": body.team_count,
            "members": [{"name": m.name.strip(), "email": m.email.lower().strip()} for m in body.members],
            "updated_at": datetime.utcnow(),
        }},
    )

    await db.audit_logs.insert_one({
        "action": "team_updated",
        "actor": admin.get("sub", "admin"),
        "details": f"Updated team {body.team_name} ({team_code})",
        "timestamp": datetime.utcnow(),
    })

    return {"message": "Team updated", "team_code": team_code}


@router.delete("/teams/{team_code}")
async def delete_team(team_code: str, admin=Depends(get_admin_user)):
    db = get_db()
    t = await db.teams.find_one({"team_code": team_code})
    if not t:
        raise HTTPException(status_code=404, detail="Team not found")

    team_name = t.get("team_name", team_code)

    await db.teams.delete_one({"team_code": team_code})
    await db.allocations.delete_many({"team_code": team_code})

    await db.audit_logs.insert_one({
        "action": "team_deleted",
        "actor": admin.get("sub", "admin"),
        "details": f"Deleted team {team_name} ({team_code}) and its allocation",
        "timestamp": datetime.utcnow(),
    })

    return {"message": f"Team {team_name} deleted successfully", "team_code": team_code}


# ALLOCATIONS

class AllocationItem(BaseModel):
    team_code: str
    challenge_id: Optional[str] = ""


@router.get("/allocations")
async def get_allocations(admin=Depends(get_admin_user)):
    db = get_db()
    rows = []
    async for team in db.teams.find().sort("created_at", -1):
        alloc = await db.allocations.find_one({"team_code": team["team_code"]})
        ch = await db.challenges.find_one({"challenge_code": alloc["challenge_id"]}) if alloc and alloc.get("challenge_id") else None
        rows.append({
            "team_code": team["team_code"],
            "team_name": team["team_name"],
            "team_count": team.get("team_count", len(team.get("members", []))),
            "members": team.get("members", []),
            "allocated_challenge": alloc.get("challenge_id") if alloc else None,
            "allocated_challenge_name": ch.get("challenge_name") if ch else None,
        })
    return {"allocations": rows}


@router.get("/allocations/options")
async def get_allocation_options(admin=Depends(get_admin_user)):
    db = get_db()
    challenges = []
    async for ch in db.challenges.find().sort("created_at", -1):
        challenges.append({"challenge_code": ch["challenge_code"], "challenge_name": ch.get("challenge_name", ch["challenge_code"])})
    return {"challenges": challenges}


@router.put("/allocations")
async def save_allocations(items: List[AllocationItem], admin=Depends(get_admin_user)):
    db = get_db()

    for item in items:
        team = await db.teams.find_one({"team_code": item.team_code})
        if not team:
            raise HTTPException(status_code=400, detail=f"Team {item.team_code} not found")

        if item.challenge_id:
            ch = await db.challenges.find_one({"challenge_code": item.challenge_id})
            if not ch:
                raise HTTPException(status_code=400, detail=f"Challenge {item.challenge_id} does not exist")

        ch_name = None
        if item.challenge_id:
            ch = await db.challenges.find_one({"challenge_code": item.challenge_id})
            ch_name = ch.get("challenge_name") if ch else None

        await db.allocations.update_one(
            {"team_code": item.team_code},
            {"$set": {
                "team_code": item.team_code,
                "team_name": team["team_name"],
                "member_count": team.get("team_count", len(team.get("members", []))),
                "challenge_id": item.challenge_id or None,
                "challenge_name": ch_name,
                "updated_at": datetime.utcnow(),
            }},
            upsert=True,
        )

    await db.audit_logs.insert_one({
        "action": "allocations_saved",
        "actor": admin.get("sub", "admin"),
        "details": f"Saved allocations for {len(items)} teams",
        "timestamp": datetime.utcnow(),
    })

    return {"message": "Allocations saved", "count": len(items)}


# RELEASE (email)

@router.get("/releases")
async def get_releases(admin=Depends(get_admin_user)):
    db = get_db()
    releases = []
    async for r in db.releases.find().sort("released_at", -1).limit(500):
        r["_id"] = str(r["_id"])
        releases.append(r)
    return {"releases": releases}


@router.post("/releases/send")
async def send_releases(request: Request, admin=Depends(get_admin_user)):
    db = get_db()

    if not email_configured():
        missing = email_missing_config() or "SMTP configuration"
        raise HTTPException(
            status_code=503,
            detail=(
                f"Email is not configured. Missing: {missing}. "
                "Add them to backend/.env and restart the server."
            ),
        )

    teams = []
    async for t in db.teams.find():
        alloc = await db.allocations.find_one({"team_code": t["team_code"]})
        if not alloc or not alloc.get("challenge_id"):
            continue
        ch = await db.challenges.find_one({"challenge_code": alloc["challenge_id"]})
        if not ch:
            continue
        teams.append((t, alloc, ch))

    if not teams:
        raise HTTPException(status_code=400, detail="No allocated teams found to release. Please allocate challenges first.")

    base_url = _download_base_url(request)
    results = []

    async def _send_one(team, alloc, ch):
        team_code = team["team_code"]
        members = team.get("members", [])
        emails = [m["email"] for m in members if m.get("email")]

        if not emails:
            return {"team_code": team_code, "team_name": team["team_name"], "status": "FAILED", "error": "No member emails"}

        token = secrets.token_urlsafe(32)
        download_url = f"{base_url}/api/releases/download/{token}"
        challenge_name = ch.get("challenge_name", alloc["challenge_id"])
        subject = "Legacy Code Rescue - Challenge Assignment"
        body_text, body_html = _release_email(team["team_name"], challenge_name, alloc["challenge_id"], download_url)

        try:
            await asyncio.to_thread(_send_sync, emails, subject, body_html, None, body_text)
            await db.releases.insert_one({
                "team_code": team_code,
                "team_name": team["team_name"],
                "challenge_id": alloc["challenge_id"],
                "challenge_name": challenge_name,
                "recipients": emails,
                "download_token": token,
                "status": "SENT",
                "released_at": datetime.utcnow(),
            })
            return {"team_code": team_code, "team_name": team["team_name"], "status": "SENT"}
        except Exception as e:
            logger.error("Release email failed for team %s: %s", team_code, e)
            await db.releases.insert_one({
                "team_code": team_code,
                "team_name": team["team_name"],
                "challenge_id": alloc["challenge_id"],
                "challenge_name": challenge_name,
                "recipients": emails,
                "status": "FAILED",
                "error": str(e),
                "released_at": datetime.utcnow(),
            })
            return {"team_code": team_code, "team_name": team["team_name"], "status": "FAILED", "error": str(e)}

    results = await asyncio.gather(*[_send_one(t, a, c) for (t, a, c) in teams])

    success_count = sum(1 for r in results if r["status"] == "SENT")
    failure_count = sum(1 for r in results if r["status"] == "FAILED")

    return {
        "message": f"Release complete: {success_count} sent, {failure_count} failed",
        "success_count": success_count,
        "failure_count": failure_count,
        "results": results,
    }


def _release_email(team_name, challenge_name, challenge_code, download_url):
    text = f"""\
Dear {team_name},

Your team has been assigned the following Legacy Code Rescue challenge:

Challenge:    {challenge_name}
Challenge Code: {challenge_code}

Download your challenge files using the secure link below (expires when the
challenge is no longer available):

{download_url}

If the link does not work, copy and paste it into your browser.

Regards,
Legacy Code Rescue Admin"""

    html = f"""\
<html><body style="font-family:Arial,helvetica,sans-serif;background:#f4f6f8;margin:0;padding:24px;">
<div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:10px;overflow:hidden;border:1px solid #e5e7eb;">
  <div style="background:#0f1117;padding:20px 24px;color:#ffffff;">
    <h2 style="margin:0;font-size:20px;">Legacy Code Rescue</h2>
    <div style="font-size:13px;opacity:.8;">Challenge Assignment</div>
  </div>
  <div style="padding:24px;">
    <p style="margin:0 0 16px;color:#1f2937;">Dear {team_name},</p>
    <p style="margin:0 0 16px;color:#1f2937;">Your team has been assigned the following Legacy Code Rescue challenge:</p>
    <table cellpadding="10" style="border-collapse:collapse;margin:0 0 20px;width:100%;">
      <tr>
        <td style="border:1px solid #e5e7eb;background:#f9fafb;font-weight:600;">Challenge</td>
        <td style="border:1px solid #e5e7eb;">{challenge_name}</td>
      </tr>
      <tr>
        <td style="border:1px solid #e5e7eb;background:#f9fafb;font-weight:600;">Challenge Code</td>
        <td style="border:1px solid #e5e7eb;">{challenge_code}</td>
      </tr>
    </table>
    <p style="margin:0 0 16px;color:#1f2937;">Use the button below to download your challenge files:</p>
    <a href="{download_url}" style="display:inline-block;background:#1f2937;color:#ffffff;padding:12px 22px;border-radius:6px;text-decoration:none;font-weight:600;">CLICK HERE TO DOWNLOAD YOUR CHALLENGE</a>
    <p style="margin:20px 0 0;color:#6b7280;font-size:13px;">If the button does not work, copy and paste this link into your browser:<br>{download_url}</p>
  </div>
  <div style="padding:16px 24px;border-top:1px solid #e5e7eb;font-size:12px;color:#9ca3af;">Regards,<br>Legacy Code Rescue Admin</div>
</div>
</body></html>"""
    return text, html


# REPORTS

@router.get("/reports/data")
async def get_report_data(admin=Depends(get_admin_user)):
    db = get_db()

    challenges = []
    async for c in db.challenges.find().sort("created_at", -1):
        c["_id"] = str(c["_id"])
        challenges.append({"challenge_code": c["challenge_code"], "challenge_name": c.get("challenge_name", c["challenge_code"])})

    teams = []
    async for t in db.teams.find().sort("created_at", -1):
        t["_id"] = str(t["_id"])
        teams.append({
            "team_code": t["team_code"],
            "team_name": t["team_name"],
            "team_count": t.get("team_count", len(t.get("members", []))),
            "members": t.get("members", []),
        })

    allocations = []
    async for a in db.allocations.find():
        a["_id"] = str(a["_id"])
        allocations.append(a)

    releases = []
    async for r in db.releases.find().sort("released_at", -1).limit(500):
        r["_id"] = str(r["_id"])
        releases.append(r)

    return {
        "total_challenges": len(challenges),
        "total_teams": len(teams),
        "challenges": challenges,
        "teams": teams,
        "allocations": allocations,
        "releases": releases,
    }


async def _fetch_report_data():
    db = get_db()

    challenges = []
    async for c in db.challenges.find().sort("created_at", -1):
        challenges.append({
            "challenge_code": c["challenge_code"],
            "challenge_name": c.get("challenge_name", c["challenge_code"]),
            "file_count": c.get("file_count", 0),
            "created_at": c.get("created_at"),
        })

    teams = []
    async for t in db.teams.find().sort("created_at", -1):
        teams.append({
            "team_code": t["team_code"],
            "team_name": t["team_name"],
            "team_count": t.get("team_count", len(t.get("members", []))),
            "members": t.get("members", []),
        })

    allocations = []
    async for a in db.allocations.find():
        ch = await db.challenges.find_one({"challenge_code": a.get("challenge_id")})
        allocations.append({
            "team_code": a.get("team_code", ""),
            "team_name": a.get("team_name", ""),
            "member_count": a.get("member_count", 0),
            "challenge_name": (ch.get("challenge_name") if ch else a.get("challenge_name", "")) or "",
            "challenge_code": a.get("challenge_id", ""),
            "updated_at": a.get("updated_at"),
        })

    releases = []
    async for r in db.releases.find().sort("released_at", -1).limit(500):
        ch = await db.challenges.find_one({"challenge_code": r.get("challenge_id")})
        releases.append({
            "team_name": r.get("team_name", ""),
            "challenge_name": (ch.get("challenge_name") if ch else r.get("challenge_name", "")) or "",
            "challenge_code": r.get("challenge_id", ""),
            "recipients": r.get("recipients", []),
            "status": r.get("status", ""),
            "released_at": r.get("released_at"),
        })

    return challenges, teams, allocations, releases


def _pdf_response(pdf_bytes, filename):
    from fastapi.responses import Response
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/reports/pdf/challenges")
async def get_challenges_report(admin=Depends(get_admin_user)):
    challenges, _, _, _ = await _fetch_report_data()
    return _pdf_response(challenge_report_pdf({"challenges": challenges}), "challenges_report.pdf")


@router.get("/reports/pdf/teams")
async def get_teams_report(admin=Depends(get_admin_user)):
    _, teams, _, _ = await _fetch_report_data()
    return _pdf_response(teams_report_pdf({"teams": teams}), "teams_report.pdf")


@router.get("/reports/pdf/allocations")
async def get_allocations_report(admin=Depends(get_admin_user)):
    _, _, allocations, _ = await _fetch_report_data()
    return _pdf_response(allocations_report_pdf({"allocations": allocations}), "allocated_challenges_report.pdf")


@router.get("/reports/pdf/status")
async def get_status_report(admin=Depends(get_admin_user)):
    db = get_db()
    total_challenges = await db.challenges.count_documents({})
    total_teams = await db.teams.count_documents({})
    total_releases = await db.releases.count_documents({})

    total_team_members = 0
    allocated_teams = 0
    async for t in db.teams.find():
        total_team_members += t.get("team_count", len(t.get("members", [])))
        alloc = await db.allocations.find_one({"team_code": t["team_code"]})
        if alloc and alloc.get("challenge_id"):
            allocated_teams += 1

    challenges, teams, allocations, releases = await _fetch_report_data()

    stats = {
        "total_challenges": total_challenges,
        "total_teams": total_teams,
        "total_team_members": total_team_members,
        "allocated_teams": allocated_teams,
        "unallocated_teams": total_teams - allocated_teams,
        "total_releases": total_releases,
    }
    data = {"stats": stats, "allocations": allocations, "releases": releases}
    return _pdf_response(status_report_pdf(data), "status_report.pdf")


# AUDIT LOGS

@router.get("/audit-logs")
async def get_audit_logs(admin=Depends(get_admin_user)):
    db = get_db()
    logs = []
    async for log in db.audit_logs.find().sort("timestamp", -1).limit(200):
        log["_id"] = str(log["_id"])
        logs.append(log)
    return {"logs": logs}
