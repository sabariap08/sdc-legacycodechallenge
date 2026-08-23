import os
import shutil
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from app.database import get_db
from app.security import get_participant_user
from app.config import CHALLENGE_STORAGE_PATH, TEAM_WORKSPACE_PATH
from app.utils import sanitize_path, compute_event_status
from datetime import datetime

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


def _get_workspace_path(team_code: str, challenge_code: str) -> str:
    return os.path.join(TEAM_WORKSPACE_PATH, team_code, challenge_code)


def _init_workspace(team_code: str, challenge_code: str) -> str:
    workspace = _get_workspace_path(team_code, challenge_code)
    if not os.path.exists(workspace):
        source = os.path.join(CHALLENGE_STORAGE_PATH, challenge_code)
        if os.path.exists(source):
            shutil.copytree(source, workspace, dirs_exist_ok=True)
        else:
            os.makedirs(workspace, exist_ok=True)
    return workspace


def _build_file_tree(root_path: str, rel_path: str = "") -> list:
    tree = []
    full = os.path.join(root_path, rel_path)
    if not os.path.exists(full):
        return tree
    for entry in sorted(os.listdir(full)):
        entry_rel = os.path.join(rel_path, entry) if rel_path else entry
        entry_full = os.path.join(root_path, entry_rel)
        if os.path.isdir(entry_full):
            tree.append({
                "name": entry,
                "path": entry_rel.replace("\\", "/"),
                "type": "directory",
                "children": _build_file_tree(root_path, entry_rel)
            })
        else:
            tree.append({
                "name": entry,
                "path": entry_rel.replace("\\", "/"),
                "type": "file"
            })
    return tree


@router.get("/tree")
async def get_file_tree(user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")
    alloc = await db.allocations.find_one({"team_code": team_code})
    if not alloc or not alloc.get("released"):
        raise HTTPException(status_code=403, detail="Challenge not yet released")

    challenge_code = alloc["challenge_code"]
    workspace = _init_workspace(team_code, challenge_code)
    tree = _build_file_tree(workspace)
    return {"tree": tree, "challenge_code": challenge_code}


@router.get("/file")
async def get_file(path: str, user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")
    alloc = await db.allocations.find_one({"team_code": team_code})
    if not alloc or not alloc.get("released"):
        raise HTTPException(status_code=403, detail="Challenge not yet released")

    challenge_code = alloc["challenge_code"]

    if not sanitize_path(path):
        raise HTTPException(status_code=400, detail="Invalid file path")

    workspace = _get_workspace_path(team_code, challenge_code)
    file_path = os.path.normpath(os.path.join(workspace, path))

    if not file_path.startswith(os.path.normpath(workspace)):
        raise HTTPException(status_code=403, detail="Path traversal detected")

    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading file: {str(e)}")

    return {"content": content, "path": path}


class SaveFileRequest(BaseModel):
    path: str
    content: str


@router.post("/file/save")
async def save_file(body: SaveFileRequest, user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")

    settings = await db.event_settings.find_one({})
    computed = compute_event_status(settings)
    if computed == "COMPLETED":
        raise HTTPException(status_code=403, detail="Event has ended. No more edits allowed.")

    alloc = await db.allocations.find_one({"team_code": team_code})
    if not alloc or not alloc.get("released"):
        raise HTTPException(status_code=403, detail="Challenge not yet released")

    challenge_code = alloc["challenge_code"]

    if not sanitize_path(body.path):
        raise HTTPException(status_code=400, detail="Invalid file path")

    workspace = _get_workspace_path(team_code, challenge_code)
    file_path = os.path.normpath(os.path.join(workspace, body.path))

    if not file_path.startswith(os.path.normpath(workspace)):
        raise HTTPException(status_code=403, detail="Path traversal detected")

    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(body.content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving file: {str(e)}")

    await db.audit_logs.insert_one({
        "action": "file_saved",
        "actor": team_code,
        "details": f"Saved {body.path}",
        "timestamp": datetime.utcnow()
    })

    return {"message": "File saved successfully", "path": body.path}


@router.get("/code-details")
async def get_code_details(user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")
    alloc = await db.allocations.find_one({"team_code": team_code})
    if not alloc or not alloc.get("released"):
        raise HTTPException(status_code=403, detail="Challenge not yet released")

    challenge_code = alloc["challenge_code"]
    team = await db.teams.find_one({"team_code": team_code})

    ch = await db.challenges.find_one({"challenge_code": challenge_code})

    event_settings = await db.event_settings.find_one({})
    event_start = event_settings.get("event_start_time") if event_settings else None
    event_end = event_settings.get("event_end_time") if event_settings else None

    return {
        "team_code": team_code,
        "team_name": team.get("team_name", "") if team else "",
        "challenge_code": challenge_code,
        "challenge_name": ch.get("challenge_name", ch.get("name", ch.get("title", challenge_code))) if ch else challenge_code,
        "language": ch.get("language", "") if ch else "",
        "difficulty": ch.get("difficulty", "") if ch else "",
        "bin_number": team.get("bin_number", "") if team else "",
        "event_start": event_start.isoformat() if event_start else None,
        "event_end": event_end.isoformat() if event_end else None,
    }
