import os
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.database import get_db, is_db_available
from app.security import get_participant_user
from app.utils import sanitize_path
from app.events import get_current_event, compute_event_status
from app.storage import (
    exists_workspace,
    copy_challenge_to_workspace,
    get_workspace_tree_from_db,
    get_workspace_file_from_db,
    save_workspace_file_to_db,
    has_evaluator,
)
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


async def _get_current_allocation(db, team_code: str):
    event = await get_current_event()
    if not event:
        return None
    return await db.allocations.find_one({"team_code": team_code, "event_id": event["event_id"]})


BINARY_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.svg',
    '.exe', '.dll', '.so', '.dylib', '.o', '.a',
    '.zip', '.tar', '.gz', '.bz2', '.7z', '.rar',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx',
    '.mp3', '.mp4', '.avi', '.mov', '.wav',
    '.pyc', '.pyo', '.class', '.jar',
}


async def _ensure_workspace(team_code: str, challenge_code: str):
    """DB-first: if the team has no workspace files yet, seed from the challenge repo."""
    if not is_db_available():
        return False
    has_files = await exists_workspace(team_code, challenge_code)
    if not has_files:
        await copy_challenge_to_workspace(team_code, challenge_code)
        has_files = await exists_workspace(team_code, challenge_code)
    return has_files


def _is_binary_path(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    return ext in BINARY_EXTENSIONS


@router.get("/tree")
async def get_file_tree(user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")
    alloc = await _get_current_allocation(db, team_code)
    if not alloc or not alloc.get("released"):
        raise HTTPException(status_code=403, detail="Challenge not yet released")

    challenge_code = alloc["challenge_code"]
    await _ensure_workspace(team_code, challenge_code)
    tree = await get_workspace_tree_from_db(team_code, challenge_code)

    total_files = 0
    total_dirs = 0
    def count_nodes(nodes):
        nonlocal total_files, total_dirs
        for n in nodes:
            if n["type"] == "directory":
                total_dirs += 1
                if "children" in n:
                    count_nodes(n["children"])
            else:
                total_files += 1
    count_nodes(tree)

    return {
        "tree": tree,
        "challenge_code": challenge_code,
        "stats": {"files": total_files, "directories": total_dirs},
    }


@router.get("/file")
async def get_file(path: str, user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")
    alloc = await _get_current_allocation(db, team_code)
    if not alloc or not alloc.get("released"):
        raise HTTPException(status_code=403, detail="Challenge not yet released")

    challenge_code = alloc["challenge_code"]
    if not sanitize_path(path):
        raise HTTPException(status_code=400, detail="Invalid file path")

    if _is_binary_path(path):
        raise HTTPException(status_code=400, detail="Binary file - cannot display or edit")

    await _ensure_workspace(team_code, challenge_code)
    file_data = await get_workspace_file_from_db(team_code, challenge_code, path)
    if file_data is None:
        raise HTTPException(status_code=404, detail="File not found")

    is_binary = file_data.get("binary", False)
    size = file_data.get("size", 0)
    editable = not is_binary and size < 1024 * 1024

    if is_binary:
        return {
            "content": "",
            "path": path,
            "binary": True,
            "editable": False,
            "size": size,
            "extension": os.path.splitext(path)[1].lower(),
            "message": "Binary file - cannot display or edit",
        }

    return {
        "content": file_data["content"],
        "path": path,
        "binary": False,
        "editable": editable,
        "size": size,
        "extension": os.path.splitext(path)[1].lower(),
        "modified": file_data.get("modified") or 0,
    }


class SaveFileRequest(BaseModel):
    path: str
    content: str


@router.post("/file/save")
async def save_file(body: SaveFileRequest, user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")

    cmps = compute_event_status(await get_current_event())
    if cmps == "COMPLETED":
        raise HTTPException(status_code=403, detail="Event has ended. No more edits allowed.")

    alloc = await _get_current_allocation(db, team_code)
    if not alloc or not alloc.get("released"):
        raise HTTPException(status_code=403, detail="Challenge not yet released")

    challenge_code = alloc["challenge_code"]
    if not sanitize_path(body.path):
        raise HTTPException(status_code=400, detail="Invalid file path")

    if _is_binary_path(body.path):
        raise HTTPException(status_code=403, detail="File is not editable")

    if len(body.content.encode("utf-8")) > 1024 * 1024:
        raise HTTPException(status_code=403, detail="File is too large to save")

    await _ensure_workspace(team_code, challenge_code)
    ok = await save_workspace_file_to_db(team_code, challenge_code, body.path, body.content)
    if not ok:
        raise HTTPException(status_code=500, detail="Error saving file")
    if not await exists_workspace(team_code, challenge_code):
        raise HTTPException(status_code=500, detail="Error saving file")

    await db.audit_logs.insert_one({
        "action": "file_saved",
        "actor": team_code,
        "details": f"Saved {body.path}",
        "timestamp": datetime.utcnow()
    })

    return {"message": "File saved successfully", "path": body.path}


class BulkSaveRequest(BaseModel):
    files: list


@router.post("/files/save")
async def bulk_save_files(body: BulkSaveRequest, user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")

    cmps = compute_event_status(await get_current_event())
    if cmps == "COMPLETED":
        raise HTTPException(status_code=403, detail="Event has ended. No more edits allowed.")

    alloc = await _get_current_allocation(db, team_code)
    if not alloc or not alloc.get("released"):
        raise HTTPException(status_code=403, detail="Challenge not yet released")

    challenge_code = alloc["challenge_code"]
    await _ensure_workspace(team_code, challenge_code)
    saved = []

    for item in body.files:
        path = item.get("path", "")
        content = item.get("content", "")
        if not sanitize_path(path):
            continue
        if _is_binary_path(path):
            continue
        if len(content.encode("utf-8")) > 1024 * 1024:
            continue
        if await save_workspace_file_to_db(team_code, challenge_code, path, content):
            saved.append(path)

    if saved:
        await db.audit_logs.insert_one({
            "action": "bulk_file_save",
            "actor": team_code,
            "details": f"Saved {len(saved)} files",
            "timestamp": datetime.utcnow()
        })

    return {"message": f"Saved {len(saved)} files", "saved": saved}


@router.get("/code-details")
async def get_code_details(user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")
    alloc = await _get_current_allocation(db, team_code)
    if not alloc or not alloc.get("released"):
        raise HTTPException(status_code=403, detail="Challenge not yet released")

    challenge_code = alloc["challenge_code"]
    team = await db.teams.find_one({"team_code": team_code})
    ch = await db.challenges.find_one({"challenge_code": challenge_code})

    event = await get_current_event()
    event_start = event.get("event_start_time") if event else None
    event_end = event.get("event_end_time") if event else None

    has_eval, eval_files = await has_evaluator(challenge_code)

    return {
        "team_code": team_code,
        "team_name": team.get("team_name", "") if team else "",
        "challenge_code": challenge_code,
        "challenge_name": ch.get("challenge_name", ch.get("name", ch.get("title", challenge_code))) if ch else challenge_code,
        "challenge_description": ch.get("description", "") if ch else "",
        "language": ch.get("language", "") if ch else "",
        "difficulty": ch.get("difficulty", "") if ch else "",
        "bin_number": team.get("bin_number", "") if team else "",
        "event_start": event_start.isoformat() if event_start else None,
        "event_end": event_end.isoformat() if event_end else None,
        "has_evaluator": has_eval,
        "evaluator_count": len(eval_files),
    }