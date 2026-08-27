from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form, status
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from app.database import get_db
from app.security import get_admin_user, verify_password, create_token, hash_password
from app.utils import generate_team_code, generate_bin_number, compute_event_status
from app.config import CHALLENGE_STORAGE_PATH
from app.storage import save_files_to_db, get_file_tree_from_db, get_file_content_from_db, delete_files_from_db
from datetime import datetime
import os
import shutil
import zipfile
import io
import subprocess
import secrets
import random
import re
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

DEFAULT_PARTICIPANT_PASSWORD = "participants@123"

# ─────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────

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


# ─────────────────────────────────────────────
# TEAMS
# ─────────────────────────────────────────────

class ParticipantCreate(BaseModel):
    name: str
    email: EmailStr
    roll_number: Optional[str] = ""
    college: Optional[str] = ""
    phone: Optional[str] = ""
    is_team_leader: bool = False


class TeamCreate(BaseModel):
    team_name: str
    participant_count: int
    participants: List[ParticipantCreate]


@router.post("/teams")
async def create_team(body: TeamCreate, admin=Depends(get_admin_user)):
    db = get_db()

    if not body.team_name or not body.team_name.strip():
        raise HTTPException(status_code=400, detail="Team name is required")
    if body.participant_count < 1 or body.participant_count > 4:
        raise HTTPException(status_code=400, detail="Participant count must be between 1 and 4")
    if len(body.participants) != body.participant_count:
        raise HTTPException(status_code=400, detail="Participant count mismatch")

    existing_team = await db.teams.find_one({
        "team_name": {"$regex": f"^{body.team_name.strip()}$", "$options": "i"}
    })
    if existing_team:
        raise HTTPException(status_code=400, detail="Team name already exists")

    emails = [p.email for p in body.participants]
    if len(set(emails)) != len(emails):
        raise HTTPException(status_code=400, detail="Duplicate emails within team not allowed")

    for email in emails:
        existing = await db.participants.find_one({"email": email})
        if existing:
            raise HTTPException(status_code=400, detail=f"Email {email} already registered")
        blocked = await db.blocked_users.find_one({"email": email})
        if blocked:
            raise HTTPException(status_code=400, detail=f"Email {email} is blocked")

    team_code = generate_team_code()
    attempts = 0
    while await db.teams.find_one({"team_code": team_code}) and attempts < 10:
        team_code = generate_team_code()
        attempts += 1

    used_bins = await db.teams.distinct("bin_number")
    all_bins = [generate_bin_number(i) for i in range(1, 41)]
    available_bins = [b for b in all_bins if b not in used_bins]
    if not available_bins:
        raise HTTPException(status_code=400, detail="No bins available")
    bin_number = secrets.choice(available_bins)

    team_doc = {
        "team_code": team_code,
        "team_name": body.team_name.strip(),
        "participant_count": body.participant_count,
        "bin_number": bin_number,
        "status": "REGISTERED",
        "created_at": datetime.utcnow()
    }
    await db.teams.insert_one(team_doc)

    default_hash = hash_password(DEFAULT_PARTICIPANT_PASSWORD)

    for i, p in enumerate(body.participants):
        participant_doc = {
            "team_code": team_code,
            "name": p.name.strip(),
            "email": p.email.strip(),
            "roll_number": p.roll_number or "",
            "college": p.college or "",
            "phone": p.phone or "",
            "is_team_leader": i == 0,
            "created_at": datetime.utcnow()
        }
        await db.participants.insert_one(participant_doc)

    auth_doc = {
        "team_code": team_code,
        "password_hash": default_hash,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    await db.team_auth.insert_one(auth_doc)

    await db.audit_logs.insert_one({
        "action": "team_created",
        "actor": admin.get("sub", "admin"),
        "details": f"Team {body.team_name} created with code {team_code}, bin {bin_number}",
        "timestamp": datetime.utcnow()
    })

    return {
        "message": "Registration Successful",
        "team_name": body.team_name,
        "team_code": team_code,
        "bin_number": bin_number,
        "participant_count": body.participant_count,
        "team_leader": body.participants[0].name,
        "default_password": DEFAULT_PARTICIPANT_PASSWORD
    }


@router.get("/teams")
async def list_teams(admin=Depends(get_admin_user)):
    db = get_db()
    teams = []
    async for team in db.teams.find().sort("created_at", -1):
        team["_id"] = str(team["_id"])
        blocked_count = await db.blocked_users.count_documents({"team_code": team["team_code"]})
        team["blocked_count"] = blocked_count
        team["is_blocked"] = team.get("status") == "BLOCKED" or blocked_count > 0
        teams.append(team)
    return {"teams": teams}


@router.get("/teams/{team_code}")
async def get_team(team_code: str, admin=Depends(get_admin_user)):
    db = get_db()
    team = await db.teams.find_one({"team_code": team_code})
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    team["_id"] = str(team["_id"])
    participants = []
    blocked_emails = set()
    async for bu in db.blocked_users.find({"team_code": team_code}):
        blocked_emails.add(bu["email"])
    async for p in db.participants.find({"team_code": team_code}):
        p["_id"] = str(p["_id"])
        p["is_blocked"] = p["email"] in blocked_emails
        participants.append(p)
    allocation = await db.allocations.find_one({"team_code": team_code})
    if allocation:
        allocation["_id"] = str(allocation["_id"])
    submission = await db.submissions.find_one({"team_code": team_code}, sort=[("submitted_at", -1)])
    if submission:
        submission["_id"] = str(submission["_id"])
    return {
        "team": team,
        "participants": participants,
        "allocation": allocation,
        "submission": submission
    }


@router.delete("/teams/{team_code}")
async def delete_team(team_code: str, admin=Depends(get_admin_user)):
    db = get_db()
    team = await db.teams.find_one({"team_code": team_code})
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    await db.teams.delete_one({"team_code": team_code})
    await db.participants.delete_many({"team_code": team_code})
    await db.team_auth.delete_one({"team_code": team_code})
    await db.allocations.delete_many({"team_code": team_code})
    await db.submissions.delete_many({"team_code": team_code})
    await db.blocked_users.delete_many({"team_code": team_code})

    await db.audit_logs.insert_one({
        "action": "team_deleted",
        "actor": admin.get("sub", "admin"),
        "details": f"Deleted team {team_code} ({team['team_name']})",
        "timestamp": datetime.utcnow()
    })

    return {"message": f"Team {team_code} deleted"}


@router.get("/participants")
async def list_participants(admin=Depends(get_admin_user)):
    db = get_db()
    participants = []
    async for p in db.participants.find():
        p["_id"] = str(p["_id"])
        participants.append(p)
    return {"participants": participants}


@router.post("/block")
async def block_entity(body: dict, admin=Depends(get_admin_user)):
    db = get_db()
    entity_type = body.get("type", "")
    target = body.get("target", "").strip()
    if entity_type not in ("team", "participant") or not target:
        raise HTTPException(status_code=400, detail="type must be 'team' or 'participant', target is required")

    if entity_type == "team":
        team = await db.teams.find_one({"team_code": target})
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")
        blocked = []
        async for p in db.participants.find({"team_code": target}):
            email = p["email"].lower().strip()
            if not await db.blocked_users.find_one({"email": email}):
                await db.blocked_users.insert_one({
                    "email": email, "team_code": target,
                    "blocked_by": admin.get("sub", "admin"),
                    "blocked_at": datetime.utcnow(), "reason": f"Team {target} blocked"
                })
                blocked.append(email)
        await db.teams.update_one({"team_code": target}, {"$set": {"status": "BLOCKED"}})
        await db.audit_logs.insert_one({
            "action": "team_blocked", "actor": admin.get("sub", "admin"),
            "details": f"Blocked team {target} ({len(blocked)} members)", "timestamp": datetime.utcnow()
        })
        return {"message": f"Team {target} blocked", "count": len(blocked)}
    else:
        participant = await db.participants.find_one({"email": target.lower()})
        if not participant:
            raise HTTPException(status_code=404, detail="Participant not found")
        if await db.blocked_users.find_one({"email": target.lower()}):
            raise HTTPException(status_code=400, detail="Already blocked")
        await db.blocked_users.insert_one({
            "email": target.lower(), "team_code": participant.get("team_code", ""),
            "blocked_by": admin.get("sub", "admin"),
            "blocked_at": datetime.utcnow(), "reason": "Individually blocked"
        })
        await db.audit_logs.insert_one({
            "action": "participant_blocked", "actor": admin.get("sub", "admin"),
            "details": f"Blocked participant {target}", "timestamp": datetime.utcnow()
        })
        return {"message": f"Participant {target} blocked"}


@router.post("/unblock")
async def unblock_entity(body: dict, admin=Depends(get_admin_user)):
    db = get_db()
    entity_type = body.get("type", "")
    target = body.get("target", "").strip()
    if entity_type not in ("team", "participant") or not target:
        raise HTTPException(status_code=400, detail="type must be 'team' or 'participant', target is required")

    if entity_type == "team":
        team = await db.teams.find_one({"team_code": target})
        if not team:
            raise HTTPException(status_code=404, detail="Team not found")
        async for p in db.participants.find({"team_code": target}):
            await db.blocked_users.delete_one({"email": p["email"].lower().strip()})
        await db.teams.update_one({"team_code": target}, {"$set": {"status": "REGISTERED"}})
        await db.audit_logs.insert_one({
            "action": "team_unblocked", "actor": admin.get("sub", "admin"),
            "details": f"Unblocked team {target}", "timestamp": datetime.utcnow()
        })
        return {"message": f"Team {target} unblocked"}
    else:
        result = await db.blocked_users.delete_one({"email": target.lower()})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Blocked record not found")
        await db.audit_logs.insert_one({
            "action": "participant_unblocked", "actor": admin.get("sub", "admin"),
            "details": f"Unblocked participant {target}", "timestamp": datetime.utcnow()
        })
        return {"message": f"Participant {target} unblocked"}


@router.get("/user-management")
async def user_management_list(admin=Depends(get_admin_user)):
    db = get_db()
    teams = []
    async for t in db.teams.find().sort("created_at", -1):
        t["_id"] = str(t["_id"])
        team_code = t["team_code"]
        blocked_count = await db.blocked_users.count_documents({"team_code": team_code})
        t["blocked_count"] = blocked_count
        t["is_blocked"] = t.get("status") == "BLOCKED" or blocked_count > 0
        teams.append(t)

    all_participants = []
    async for p in db.participants.find().sort("created_at", -1):
        p["_id"] = str(p["_id"])
        blocked = await db.blocked_users.find_one({"email": p["email"]})
        p["is_blocked"] = blocked is not None
        all_participants.append(p)

    blocked_list = []
    async for bu in db.blocked_users.find():
        bu["_id"] = str(bu["_id"])
        participant = await db.participants.find_one({"email": bu["email"]})
        bu["name"] = participant.get("name", "") if participant else ""
        team = await db.teams.find_one({"team_code": bu.get("team_code", "")})
        bu["team_name"] = team.get("team_name", "") if team else ""
        blocked_list.append(bu)

    return {"teams": teams, "participants": all_participants, "blocked_users": blocked_list}


# ─────────────────────────────────────────────
# CHALLENGES / REPOSITORIES
# ─────────────────────────────────────────────

DANGEROUS_EXTENSIONS = {
    '.exe', '.msi', '.dll', '.so', '.dylib', '.bin', '.cmd', '.com',
    '.scr', '.pif', '.vbs', '.vbe', '.jsf', '.jse', '.wsf', '.wsh',
    '.ps1', '.psm1', '.psd1', '.reg', '.inf',
    '.sh', '.bash', '.csh', '.ksh', '.zsh',
}


def _is_safe_path(member_path: str) -> bool:
    if ".." in member_path or member_path.startswith("/"):
        return False
    parts = member_path.replace("\\", "/").split("/")
    for part in parts:
        if part in ("", ".", ".."):
            return False
    return True


def _is_dangerous_file(filename: str) -> bool:
    _, ext = os.path.splitext(filename.lower())
    if ext in DANGEROUS_EXTENSIONS:
        return True
    basename = os.path.basename(filename).lower()
    dangerous_names = {'passwd', 'shadow', 'hosts', 'sudoers', '.ssh', 'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519'}
    if basename in dangerous_names:
        return True
    return False


def _get_challenge_storage_path(challenge_code: str) -> str:
    return os.path.join(os.path.abspath(CHALLENGE_STORAGE_PATH), challenge_code)


def _build_repo_file_tree(root_path: str, rel_path: str = "") -> list:
    tree = []
    full = os.path.join(root_path, rel_path)
    if not os.path.exists(full):
        return tree
    ignore_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', 'env', '.env'}
    for entry in sorted(os.listdir(full)):
        if entry in ignore_dirs:
            continue
        entry_rel = os.path.join(rel_path, entry) if rel_path else entry
        entry_full = os.path.join(root_path, entry_rel)
        if os.path.isdir(entry_full):
            tree.append({
                "name": entry,
                "path": entry_rel.replace("\\", "/"),
                "type": "directory",
                "children": _build_repo_file_tree(root_path, entry_rel)
            })
        else:
            tree.append({
                "name": entry,
                "path": entry_rel.replace("\\", "/"),
                "type": "file"
            })
    return tree


class ChallengeImport(BaseModel):
    challenge_code: str
    repository_url: str


class ImportRequest(BaseModel):
    challenges: List[ChallengeImport]


@router.post("/import-repositories")
async def import_repositories(body: ImportRequest, admin=Depends(get_admin_user)):
    db = get_db()
    results = []

    for ch in body.challenges:
        try:
            challenge_code = ch.challenge_code.strip()
            if not challenge_code:
                results.append({"challenge_code": challenge_code, "status": "FAILED", "error": "Empty challenge code"})
                continue

            if not ch.repository_url or not ch.repository_url.strip():
                results.append({"challenge_code": challenge_code, "status": "FAILED", "error": "Empty URL"})
                continue

            url = ch.repository_url.strip()
            if not (url.endswith(".git") or "github.com" in url or "gitlab.com" in url or "bitbucket.org" in url):
                results.append({"challenge_code": challenge_code, "status": "FAILED", "error": "Invalid repository URL. Use a GitHub/GitLab/Bitbucket URL."})
                continue

            storage_path = _get_challenge_storage_path(challenge_code)
            if os.path.exists(storage_path):
                shutil.rmtree(storage_path)
            os.makedirs(storage_path, exist_ok=True)

            commit_sha = "unknown"
            source = "link"

            if "github.com" in url:
                # Prefer GitHub codeload archive download (no git binary required, reliable on hosted platforms)
                try:
                    import httpx
                    normalized = url.rstrip("/")
                    if normalized.endswith(".git"):
                        normalized = normalized[:-4]
                    archive_url = normalized.rstrip("/") + "/archive/refs/heads/main.zip"
                    with httpx.Client(timeout=120, follow_redirects=True) as c:
                        r = c.get(archive_url)
                        if r.status_code != 200:
                            archive_url = normalized.rstrip("/") + "/archive/refs/heads/master.zip"
                            r = c.get(archive_url)
                        if r.status_code != 200:
                            raise HTTPException(status_code=400, detail=f"Could not fetch default branch: HTTP {r.status_code}")
                        content = r.content
                    with zipfile.ZipFile(io.BytesIO(content), 'r') as zf:
                        topdir = None
                        for info in zf.infolist():
                            if info.is_dir():
                                topdir = info.filename.rstrip("/")
                                break
                        if topdir:
                            for info in zf.infolist():
                                if info.is_dir():
                                    continue
                                if not info.filename.startswith(topdir + "/"):
                                    continue
                                rel = info.filename[len(topdir) + 1:]
                                if not rel:
                                    continue
                                target = os.path.join(storage_path, rel)
                                os.makedirs(os.path.dirname(target), exist_ok=True)
                                with open(target, "wb") as out:
                                    out.write(zf.read(info))
                        else:
                            zf.extractall(storage_path)
                    commit_sha = "github-archive"
                    source = "link"
                except Exception as e:
                    # Fall back to git clone
                    clone_ok = False
                    try:
                        result = subprocess.run(
                            ["git", "clone", "--depth", "1", url, storage_path],
                            capture_output=True, text=True, timeout=120
                        )
                        if result.returncode == 0:
                            clone_ok = True
                        else:
                            results.append({"challenge_code": challenge_code, "status": "FAILED", "error": f"Git clone failed: {result.stderr.strip()}"})
                            continue
                    except subprocess.TimeoutExpired:
                        results.append({"challenge_code": challenge_code, "status": "FAILED", "error": "Git clone timed out"})
                        continue
                    except Exception as e2:
                        results.append({"challenge_code": challenge_code, "status": "FAILED", "error": f"Git unavailable: {e2}"})
                        continue
                    if clone_ok:
                        sha_result = subprocess.run(
                            ["git", "-C", storage_path, "rev-parse", "HEAD"],
                            capture_output=True, text=True
                        )
                        commit_sha = sha_result.stdout.strip() if sha_result.returncode == 0 else "unknown"
            else:
                # Non-GitHub: use git clone
                try:
                    result = subprocess.run(
                        ["git", "clone", "--depth", "1", url, storage_path],
                        capture_output=True, text=True, timeout=120
                    )
                    if result.returncode != 0:
                        results.append({"challenge_code": challenge_code, "status": "FAILED", "error": result.stderr.strip()})
                        continue
                except subprocess.TimeoutExpired:
                    results.append({"challenge_code": challenge_code, "status": "FAILED", "error": "Clone timed out"})
                    continue

            existing = await db.challenges.find_one({"challenge_code": challenge_code})
            challenge_doc = {
                "challenge_code": challenge_code,
                "challenge_name": challenge_code,
                "repository_source": source,
                "repository_url": url,
                "language": "auto",
                "difficulty": "medium",
                "commit_sha": commit_sha,
                "setup_instructions": "",
                "testing_instructions": "",
                "imported_at": datetime.utcnow(),
                "status": "READY",
                "storage_path": storage_path
            }

            if existing:
                await db.challenges.update_one(
                    {"challenge_code": challenge_code},
                    {"$set": challenge_doc}
                )
            else:
                await db.challenges.insert_one(challenge_doc)

            await save_files_to_db(challenge_code, storage_path)

            results.append({"challenge_code": challenge_code, "status": "READY", "commit_sha": commit_sha})

        except subprocess.TimeoutExpired:
            results.append({"challenge_code": ch.challenge_code, "status": "FAILED", "error": "Clone timed out"})
        except Exception as e:
            results.append({"challenge_code": ch.challenge_code, "status": "FAILED", "error": str(e)})

    await db.audit_logs.insert_one({
        "action": "repository_import",
        "actor": admin.get("sub", "admin"),
        "details": f"Imported {len(results)} challenges via link",
        "timestamp": datetime.utcnow()
    })

    return {"results": results}


@router.post("/upload-challenge")
async def upload_challenge(
    challenge_name: str = Form(...),
    challenge_code: str = Form(...),
    file: UploadFile = File(...),
    admin=Depends(get_admin_user)
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

    if not file.filename.lower().endswith('.zip'):
        raise HTTPException(status_code=400, detail="Only ZIP files are supported")

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    if len(content) > 100 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 100MB)")

    storage_path = _get_challenge_storage_path(challenge_code)
    if os.path.exists(storage_path):
        shutil.rmtree(storage_path)
    os.makedirs(storage_path, exist_ok=True)

    try:
        with zipfile.ZipFile(io.BytesIO(content), 'r') as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if not _is_safe_path(info.filename):
                    raise HTTPException(status_code=400, detail=f"Path traversal detected: {info.filename}")
                if _is_dangerous_file(info.filename):
                    raise HTTPException(status_code=400, detail=f"Unsupported file type: {info.filename}")

            zf.extractall(storage_path)

    except zipfile.BadZipFile:
        if os.path.exists(storage_path):
            shutil.rmtree(storage_path)
        raise HTTPException(status_code=400, detail="Invalid or corrupted ZIP file")
    except HTTPException:
        if os.path.exists(storage_path):
            shutil.rmtree(storage_path)
        raise

    has_files = False
    for root, dirs, files in os.walk(storage_path):
        if files:
            has_files = True
            break
    if not has_files:
        shutil.rmtree(storage_path)
        raise HTTPException(status_code=400, detail="ZIP file is empty (no files found)")

    existing = await db.challenges.find_one({"challenge_code": challenge_code})
    challenge_doc = {
        "challenge_code": challenge_code,
        "challenge_name": challenge_name,
        "repository_source": "upload",
        "repository_url": "",
        "language": "auto",
        "difficulty": "medium",
        "commit_sha": "",
        "setup_instructions": "",
        "testing_instructions": "",
        "imported_at": datetime.utcnow(),
        "status": "READY",
        "storage_path": storage_path
    }

    if existing:
        await db.challenges.update_one(
            {"challenge_code": challenge_code},
            {"$set": challenge_doc}
        )
    else:
        await db.challenges.insert_one(challenge_doc)

    await save_files_to_db(challenge_code, storage_path)

    await db.audit_logs.insert_one({
        "action": "repository_upload",
        "actor": admin.get("sub", "admin"),
        "details": f"Uploaded challenge {challenge_code} ({challenge_name}) via file upload",
        "timestamp": datetime.utcnow()
    })

    return {
        "message": f"Challenge {challenge_code} uploaded successfully",
        "challenge_code": challenge_code,
        "status": "READY"
    }


@router.get("/challenges")
async def list_challenges(admin=Depends(get_admin_user)):
    db = get_db()
    challenges = []
    async for ch in db.challenges.find():
        ch["_id"] = str(ch["_id"])
        challenges.append(ch)
    return {"challenges": challenges}


@router.delete("/challenges/{challenge_code}")
async def delete_challenge(challenge_code: str, admin=Depends(get_admin_user)):
    db = get_db()
    challenge = await db.challenges.find_one({"challenge_code": challenge_code})
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")

    storage_path = challenge.get("storage_path", "")
    if storage_path and os.path.exists(storage_path):
        shutil.rmtree(storage_path)

    await delete_files_from_db(challenge_code)
    await db.challenges.delete_one({"challenge_code": challenge_code})

    await db.audit_logs.insert_one({
        "action": "repository_deleted",
        "actor": admin.get("sub", "admin"),
        "details": f"Deleted repository {challenge_code} ({challenge.get('challenge_name', '')})",
        "timestamp": datetime.utcnow()
    })

    return {"message": f"Repository {challenge_code} deleted successfully"}


@router.get("/challenges/{challenge_code}/file-tree")
async def get_repo_file_tree(challenge_code: str, admin=Depends(get_admin_user)):
    challenge = await get_db().challenges.find_one({"challenge_code": challenge_code})
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")

    storage_path = challenge.get("storage_path", "")
    if storage_path and os.path.exists(storage_path):
        tree = _build_repo_file_tree(storage_path)
        if tree:
            return {"tree": tree, "challenge_code": challenge_code, "challenge_name": challenge.get("challenge_name", challenge_code)}

    tree = await get_file_tree_from_db(challenge_code)
    if tree:
        return {"tree": tree, "challenge_code": challenge_code, "challenge_name": challenge.get("challenge_name", challenge_code)}

    if storage_path and not os.path.exists(storage_path):
        try:
            os.makedirs(storage_path, exist_ok=True)
            from app.storage import load_files_from_db
            loaded = await load_files_from_db(challenge_code, storage_path)
            if loaded > 0:
                tree = _build_repo_file_tree(storage_path)
                if tree:
                    return {"tree": tree, "challenge_code": challenge_code, "challenge_name": challenge.get("challenge_name", challenge_code)}
        except Exception as e:
            logger.error("Auto-recovery failed for %s: %s", challenge_code, e)

    raise HTTPException(status_code=404, detail="Repository files not found. Try re-importing the repository.")


@router.get("/challenges/{challenge_code}/file")
async def get_repo_file(challenge_code: str, path: str, admin=Depends(get_admin_user)):
    challenge = await get_db().challenges.find_one({"challenge_code": challenge_code})
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")

    storage_path = challenge.get("storage_path", "")
    if storage_path and os.path.exists(storage_path):
        if _is_safe_path(path):
            file_path = os.path.normpath(os.path.join(storage_path, path))
            if file_path.startswith(os.path.normpath(storage_path)) and os.path.exists(file_path) and os.path.isfile(file_path):
                max_size = 512 * 1024
                file_size = os.path.getsize(file_path)
                if file_size > max_size:
                    return {"content": f"# File too large to display ({file_size} bytes). Maximum preview size is 512 KB.", "path": path, "truncated": True}
                binary_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.svg', '.webp',
                                     '.exe', '.dll', '.so', '.dylib', '.bin', '.zip', '.tar', '.gz',
                                     '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
                                     '.mp3', '.mp4', '.avi', '.mov', '.wav', '.ogg'}
                _, ext = os.path.splitext(file_path.lower())
                if ext in binary_extensions:
                    return {"content": f"# Binary file cannot be previewed ({file_size} bytes)", "path": path, "binary": True}
                try:
                    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    return {"content": content, "path": path, "binary": False}
                except Exception:
                    pass

    if not _is_safe_path(path):
        raise HTTPException(status_code=400, detail="Invalid file path")

    content = await get_file_content_from_db(challenge_code, path)
    if content is not None:
        return {"content": content, "path": path, "binary": False}

    raise HTTPException(status_code=404, detail="File not found")


# ─────────────────────────────────────────────
# ALLOCATION
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# EVENT CONTROL
# ─────────────────────────────────────────────

@router.get("/event/current")
async def get_current_event(admin=Depends(get_admin_user)):
    from app.events import get_current_event as _get_current, compute_current_status
    current = await _get_current()
    if not current:
        return {"event": None, "computed_status": "DRAFT"}
    current["_id"] = str(current["_id"])
    computed_status, _ = await compute_current_status()
    now = datetime.utcnow()
    start = current.get("event_start_time")
    end = current.get("event_end_time")
    remaining_seconds = None
    countdown_seconds = None
    if start:
        if isinstance(start, str):
            start = datetime.fromisoformat(str(start).replace("Z", "+00:00")).replace(tzinfo=None)
        if computed_status == "UPCOMING":
            countdown_seconds = max(0, int((start - now).total_seconds()))
        elif computed_status == "ONGOING" and end:
            if isinstance(end, str):
                end = datetime.fromisoformat(str(end).replace("Z", "+00:00")).replace(tzinfo=None)
            remaining_seconds = max(0, int((end - now).total_seconds()))
    return {
        "event": current,
        "computed_status": computed_status,
        "countdown_seconds": countdown_seconds,
        "remaining_seconds": remaining_seconds,
    }


@router.get("/event/status")
async def get_event_status(admin=Depends(get_admin_user)):
    return await get_current_event(admin)


class EventStart(BaseModel):
    event_start_time: str
    event_end_time: str
    event_duration_minutes: int = 300
    event_name: Optional[str] = "Legacy Code Rescue"


def _parse_event_times(start_str: str, end_str: str):
    try:
        start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00")).replace(tzinfo=None)
        end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")
    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="End time must be after start time")
    return start_dt, end_dt


@router.post("/event/start")
async def start_event(body: EventStart, admin=Depends(get_admin_user)):
    db = get_db()
    try:
        start_dt = datetime.fromisoformat(body.event_start_time.replace("Z", "+00:00")).replace(tzinfo=None)
        end_dt = datetime.fromisoformat(body.event_end_time.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")
    if end_dt <= start_dt:
        raise HTTPException(status_code=400, detail="End time must be after start time")
    if body.event_duration_minutes < 5 or body.event_duration_minutes > 1440:
        raise HTTPException(status_code=400, detail="Duration must be between 5 and 1440 minutes")

    from app.events import get_current_event as _get_current, create_event
    existing = await _get_current()

    if existing:
        old_status = compute_event_status(existing)
        if old_status in ("ONGOING", "UPCOMING", "CANCELLED"):
            raise HTTPException(
                status_code=400,
                detail="Cannot start a new event while one is active, upcoming, or cancelled. Archive the current event first (Start a New Event)."
            )
        await db.events.update_one(
            {"event_id": existing["event_id"]},
            {"$set": {
                "event_start_time": start_dt,
                "event_end_time": end_dt,
                "event_duration_minutes": body.event_duration_minutes,
                "event_name": (body.event_name or "Legacy Code Rescue").strip(),
                "status": "UPCOMING",
                "updated_at": datetime.utcnow(),
                "created_by": admin.get("sub", "admin"),
            }}
        )
        event = await _get_current()
        event_code = event["event_code"]
    else:
        event = await create_event(event_name=body.event_name or "Legacy Code Rescue",
                                   duration_minutes=body.event_duration_minutes)
        await db.events.update_one(
            {"event_id": event["event_id"]},
            {"$set": {
                "event_start_time": start_dt,
                "event_end_time": end_dt,
                "status": "UPCOMING",
                "created_by": admin.get("sub", "admin"),
                "updated_at": datetime.utcnow(),
            }}
        )
        event = await _get_current()
        event_code = event["event_code"]

    await db.audit_logs.insert_one({
        "action": "event_started",
        "actor": admin.get("sub", "admin"),
        "details": f"Event {event_code} scheduled. Start: {start_dt.isoformat()}, End: {end_dt.isoformat()}",
        "timestamp": datetime.utcnow()
    })

    return {"message": "Event scheduled", "event_code": event_code, "event_start_time": start_dt.isoformat(), "event_end_time": end_dt.isoformat()}


@router.post("/event/new")
async def create_new_event(body: dict, admin=Depends(get_admin_user)):
    """Start a New Event: archive the current one (clearing its per-event data),
    then create a fresh event with a brand-new unique code."""
    from app.events import get_current_event as _get_current, archive_current_event, create_event
    db = get_db()

    current = await _get_current()
    if current and compute_event_status(current) in ("ONGOING", "UPCOMING"):
        reason = body.get("reason") or "New event started by admin"
        await archive_current_event(reason)

    event_name = (body.get("event_name") or "Legacy Code Rescue").strip()
    duration = body.get("event_duration_minutes") or 300

    start_str = body.get("event_start_time")
    end_str = body.get("event_end_time")

    new_event = await create_event(event_name=event_name, duration_minutes=duration)

    if start_str and end_str:
        try:
            start_dt, end_dt = _parse_event_times(start_str, end_str)
            await db.events.update_one(
                {"event_id": new_event["event_id"]},
                {"$set": {"event_start_time": start_dt, "event_end_time": end_dt, "status": "UPCOMING"}}
            )
        except HTTPException:
            await db.events.update_one({"event_id": new_event["event_id"]}, {"$set": {"status": "UPCOMING"}})

    await db.audit_logs.insert_one({
        "action": "event_created",
        "actor": admin.get("sub", "admin"),
        "details": f"New event created with code {new_event['event_code']}",
        "timestamp": datetime.utcnow()
    })

    return {"message": "New event created", "event_code": new_event["event_code"], "event_id": new_event["event_id"]}


class EventDelete(BaseModel):
    confirm: bool = False
    reason: Optional[str] = "Event deleted"


@router.post("/event/delete")
async def delete_current_event(body: EventDelete, admin=Depends(get_admin_user)):
    """Delete the current event: archive it to history and mark it deleted."""
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Must confirm deletion")
    from app.events import get_current_event as _get_current, archive_current_event
    db = get_db()
    current = await _get_current()
    if not current:
        raise HTTPException(status_code=404, detail="No active event to delete")
    archived = await archive_current_event(body.reason or "Event deleted by admin")
    # Clean up event-scoped data so the system is fresh
    event_id = current.get("event_id")
    await db.allocations.delete_many({"event_id": event_id})
    await db.submissions.delete_many({"event_id": event_id})
    await db.audit_logs.insert_one({
        "action": "event_deleted",
        "actor": admin.get("sub", "admin"),
        "details": f"Event {current.get('event_code')} deleted. Reason: {body.reason}",
        "timestamp": datetime.utcnow()
    })
    return {"message": "Event deleted", "event_code": current.get("event_code")}


@router.post("/event/cancel")
async def cancel_current_event(body: dict, admin=Depends(get_admin_user)):
    """Cancel the current event (status -> CANCELLED) without deleting it."""
    from app.events import get_current_event as _get_current, cancel_current_event
    db = get_db()
    current = await _get_current()
    if not current:
        raise HTTPException(status_code=404, detail="No active event to cancel")
    reason = body.get("reason") or "Cancelled by admin"
    await cancel_current_event(reason)
    await db.audit_logs.insert_one({
        "action": "event_cancelled",
        "actor": admin.get("sub", "admin"),
        "details": f"Event {current.get('event_code')} cancelled. Reason: {reason}",
        "timestamp": datetime.utcnow()
    })
    return {"message": "Event cancelled", "event_code": current.get("event_code")}


class EventUpdate(BaseModel):
    event_start_time: Optional[str] = None
    event_end_time: Optional[str] = None
    event_duration_minutes: Optional[int] = None
    event_name: Optional[str] = None
    leaderboard_enabled: Optional[bool] = None


@router.put("/event/current")
async def update_current_event(body: EventUpdate, admin=Depends(get_admin_user)):
    from app.events import get_current_event as _get_current
    db = get_db()
    event = await _get_current()
    if not event:
        raise HTTPException(status_code=404, detail="No event configured")

    computed = compute_event_status(event)
    if computed == "COMPLETED":
        raise HTTPException(status_code=400, detail="Cannot modify a completed event. Start a new event instead.")
    if computed == "CANCELLED":
        raise HTTPException(status_code=400, detail="Cannot modify a cancelled event. Start a new event instead.")

    update = {}
    if body.event_start_time is not None:
        try:
            update["event_start_time"] = datetime.fromisoformat(body.event_start_time.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start time format")
    if body.event_end_time is not None:
        try:
            update["event_end_time"] = datetime.fromisoformat(body.event_end_time.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid end time format")
    if body.event_duration_minutes is not None:
        if body.event_duration_minutes < 5 or body.event_duration_minutes > 1440:
            raise HTTPException(status_code=400, detail="Duration must be between 5 and 1440 minutes")
        update["event_duration_minutes"] = body.event_duration_minutes
    if body.event_name is not None:
        if body.event_name.strip():
            update["event_name"] = body.event_name.strip()
    if body.leaderboard_enabled is not None:
        update["leaderboard_enabled"] = body.leaderboard_enabled

    if not update:
        raise HTTPException(status_code=400, detail="No settings provided")

    if "event_start_time" in update or "event_end_time" in update:
        existing_start = event.get("event_start_time")
        existing_end = event.get("event_end_time")
        if isinstance(existing_start, str):
            existing_start = datetime.fromisoformat(existing_start.replace("Z", "+00:00")).replace(tzinfo=None)
        if isinstance(existing_end, str):
            existing_end = datetime.fromisoformat(existing_end.replace("Z", "+00:00")).replace(tzinfo=None)
        s = update.get("event_start_time", existing_start)
        e = update.get("event_end_time", existing_end)
        if s and e:
            if e <= s:
                raise HTTPException(status_code=400, detail="End time must be after start time")

    update["updated_at"] = datetime.utcnow()
    await db.events.update_one({"event_id": event["event_id"]}, {"$set": update})

    await db.audit_logs.insert_one({
        "action": "event_settings_updated",
        "actor": admin.get("sub", "admin"),
        "details": f"Updated: {list(update.keys())}",
        "timestamp": datetime.utcnow()
    })

    return {"message": "Event configuration updated"}


@router.get("/event/history")
async def get_event_history(admin=Depends(get_admin_user)):
    db = get_db()
    history = []
    async for h in db.event_history.find().sort("archived_at", -1).limit(100):
        h["_id"] = str(h["_id"])
        history.append(h)
    from app.events import get_current_event as _get_current
    current = await _get_current()
    return {"history": history, "current_event": current}


@router.get("/event/countdown")
async def get_event_countdown(admin=Depends(get_admin_user)):
    from app.events import get_current_event as _get_current, compute_event_status
    db = get_db()
    event = await _get_current()
    if not event:
        return {"server_time": datetime.utcnow().isoformat(), "status": "DRAFT"}
    computed = compute_event_status(event)
    return {
        "server_time": datetime.utcnow().isoformat(),
        "status": computed,
        "event_start_time": event.get("event_start_time"),
        "event_end_time": event.get("event_end_time"),
    }


@router.post("/allocation/generate")
async def generate_allocation(admin=Depends(get_admin_user)):
    from app.events import get_current_event as _get_current
    db = get_db()

    event = await _get_current()
    event_id = event["event_id"] if event else None

    if event and compute_event_status(event) == "ONGOING":
        raise HTTPException(
            status_code=403,
            detail="Challenge allocation is locked because the event is live."
        )

    teams = []
    async for team in db.teams.find():
        if team.get("status") != "BLOCKED":
            teams.append(team)

    if not teams:
        raise HTTPException(status_code=400, detail="No eligible teams registered")

    challenges = []
    async for ch in db.challenges.find({"status": "READY"}):
        challenges.append(ch)

    if not challenges:
        raise HTTPException(status_code=400, detail="No challenges imported")

    await db.allocations.delete_many({"event_id": event_id} if event_id else {})

    # Balanced randomized allocation: round-robin over shuffled challenge codes
    # so that no challenge is unfairly over/under-loaded.
    random.shuffle(teams)
    challenge_codes = [ch["challenge_code"] for ch in challenges]
    random.shuffle(challenge_codes)
    counts = {cc: 0 for cc in challenge_codes}
    num_challenges = len(challenge_codes)

    allocations = []
    for i, team in enumerate(teams):
        # pick the challenge with the fewest allocations so far (balanced)
        cc = min(challenge_codes, key=lambda c: counts[c])
        counts[cc] += 1
        alloc_doc = {
            "team_code": team["team_code"],
            "challenge_code": cc,
            "released": True,
            "event_id": event_id,
            "allocated_at": datetime.utcnow()
        }
        await db.allocations.insert_one(alloc_doc)
        allocations.append(alloc_doc)

    if event_id:
        await db.events.update_one(
            {"event_id": event_id},
            {"$set": {"allocation_state": "RELEASED", "updated_at": datetime.utcnow()}}
        )

    await db.audit_logs.insert_one({
        "action": "allocation_generated",
        "actor": admin.get("sub", "admin"),
        "details": f"Generated and released balanced allocations for {len(teams)} teams across {len(challenges)} challenges",
        "timestamp": datetime.utcnow()
    })

    for a in allocations:
        a["_id"] = str(a.get("_id", ""))

    return {
        "message": "Allocations generated and released successfully",
        "allocations": allocations,
        "count": len(allocations)
    }


@router.post("/allocation/release")
async def release_allocation(admin=Depends(get_admin_user)):
    from app.events import get_current_event as _get_current
    db = get_db()

    event = await _get_current()
    event_id = event["event_id"] if event else None

    if event and compute_event_status(event) == "ONGOING":
        raise HTTPException(status_code=403, detail="Cannot release allocations during an ongoing event.")

    result = await db.allocations.update_many(
        {"event_id": event_id} if event_id else {},
        {"$set": {"released": True}}
    )
    if event_id:
        await db.events.update_one(
            {"event_id": event_id},
            {"$set": {"allocation_state": "RELEASED", "updated_at": datetime.utcnow()}}
        )

    await db.audit_logs.insert_one({
        "action": "allocation_released",
        "actor": admin.get("sub", "admin"),
        "details": f"Released {result.modified_count} allocations",
        "timestamp": datetime.utcnow()
    })

    return {"message": "Allocations released", "count": result.modified_count}


@router.post("/allocation/reset")
async def reset_allocation(admin=Depends(get_admin_user)):
    from app.events import get_current_event as _get_current
    db = get_db()

    event = await _get_current()
    event_id = event["event_id"] if event else None

    if event and compute_event_status(event) == "ONGOING":
        raise HTTPException(status_code=403, detail="Cannot reset allocations during an ongoing event.")

    await db.allocations.delete_many({"event_id": event_id} if event_id else {})
    if event_id:
        await db.events.update_one(
            {"event_id": event_id},
            {"$set": {"allocation_state": "PENDING", "updated_at": datetime.utcnow()}}
        )

    await db.audit_logs.insert_one({
        "action": "allocation_reset",
        "actor": admin.get("sub", "admin"),
        "details": "All allocations have been reset",
        "timestamp": datetime.utcnow()
    })

    return {"message": "Allocations reset successfully"}


@router.get("/allocation")
async def get_allocations(admin=Depends(get_admin_user)):
    from app.events import get_current_event as _get_current
    db = get_db()
    event = await _get_current()
    event_id = event["event_id"] if event else None

    allocations = []
    query = {"event_id": event_id} if event_id else {}
    async for alloc in db.allocations.find(query):
        alloc["_id"] = str(alloc["_id"])
        allocations.append(alloc)

    alloc_state = (event.get("allocation_state", "PENDING") if event else "PENDING")
    computed = compute_event_status(event) if event else "DRAFT"

    return {
        "allocations": allocations,
        "allocation_state": alloc_state,
        "event_status": computed,
        "event_code": event.get("event_code") if event else None,
        "count": len(allocations)
    }


# ─────────────────────────────────────────────
# ANNOUNCEMENTS
# ─────────────────────────────────────────────

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


# ─────────────────────────────────────────────
# LEADERBOARD
# ─────────────────────────────────────────────

@router.get("/leaderboard")
async def get_leaderboard(admin=Depends(get_admin_user)):
    from app.events import get_current_event as _get_current
    db = get_db()
    event = await _get_current()
    event_id = event["event_id"] if event else None
    query = {"status": "evaluated"}
    if event_id:
        query["event_id"] = event_id
    entries = []
    async for sub in db.submissions.find(query).sort("score", -1):
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


# ─────────────────────────────────────────────
# AUDIT LOGS
# ─────────────────────────────────────────────

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


# ─────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────

@router.get("/dashboard")
async def get_dashboard(admin=Depends(get_admin_user)):
    from app.events import get_current_event as _get_current, compute_event_status
    db = get_db()

    total_teams = await db.teams.count_documents({})
    total_participants = await db.participants.count_documents({})
    total_challenges = await db.challenges.count_documents({})
    ready_challenges = await db.challenges.count_documents({"status": "READY"})

    event = await _get_current()
    event_id = event["event_id"] if event else None
    event_status = compute_event_status(event) if event else "DRAFT"
    alloc_query = {"event_id": event_id} if event_id else {}

    challenge_distribution = []
    async for ch in db.challenges.find():
        alloc_count = await db.allocations.count_documents({"challenge_code": ch["challenge_code"], **alloc_query})
        challenge_distribution.append({
            "challenge_code": ch["challenge_code"],
            "challenge_name": ch.get("challenge_name", ch.get("name", ch.get("title", ch["challenge_code"]))),
            "status": ch.get("status", "UNKNOWN"),
            "allocated_teams": alloc_count
        })

    allocated_teams = await db.allocations.count_documents(alloc_query)
    released_allocations = await db.allocations.count_documents({**alloc_query, "released": True})

    sub_query = {"event_id": event_id} if event_id else {}
    total_submissions = await db.submissions.count_documents(sub_query)
    evaluated_submissions = await db.submissions.count_documents({**sub_query, "status": "evaluated"})

    alloc_state = (event.get("allocation_state", "PENDING") if event else "PENDING")

    return {
        "total_teams": total_teams,
        "total_participants": total_participants,
        "imported_challenges": total_challenges,
        "ready_challenges": ready_challenges,
        "registered_teams": total_teams,
        "event_status": event_status,
        "event_code": event.get("event_code") if event else None,
        "event_name": event.get("event_name") if event else None,
        "challenge_distribution": challenge_distribution,
        "total_submissions": total_submissions,
        "evaluated_submissions": evaluated_submissions,
        "allocated_teams": allocated_teams,
        "released_allocations": released_allocations,
        "allocation_state": alloc_state,
    }
