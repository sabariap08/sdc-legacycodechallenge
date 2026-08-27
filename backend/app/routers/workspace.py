import os
import time
import shutil
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.database import get_db
from app.security import get_participant_user
from app.config import CHALLENGE_STORAGE_PATH, TEAM_WORKSPACE_PATH
from app.utils import sanitize_path
from app.events import get_current_event, compute_event_status
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workspace", tags=["workspace"])

BINARY_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.svg',
    '.exe', '.dll', '.so', '.dylib', '.o', '.a',
    '.zip', '.tar', '.gz', '.bz2', '.7z', '.rar',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx',
    '.mp3', '.mp4', '.avi', '.mov', '.wav',
    '.pyc', '.pyo', '.class', '.jar',
}

SKIP_DIRS = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', 'env', '.idea', '.vscode', 'dist', 'build'}

def _get_workspace_path(team_code: str, challenge_code: str) -> str:
    return os.path.join(TEAM_WORKSPACE_PATH, team_code, challenge_code)

async def _init_workspace_async(team_code: str, challenge_code: str) -> str:
    workspace = _get_workspace_path(team_code, challenge_code)
    if not os.path.exists(workspace):
        source = os.path.join(CHALLENGE_STORAGE_PATH, challenge_code)
        if os.path.exists(source):
            shutil.copytree(source, workspace, dirs_exist_ok=True)
        else:
            os.makedirs(workspace, exist_ok=True)
            try:
                from app.storage import load_files_from_db
                await load_files_from_db(challenge_code, source)
                if os.path.exists(source) and os.listdir(source):
                    shutil.copytree(source, workspace, dirs_exist_ok=True)
            except Exception as e:
                logger.warning("Could not recover challenge files from DB for %s: %s", challenge_code, e)
    return workspace

def _is_binary_file(file_path: str) -> bool:
    try:
        with open(file_path, 'rb') as f:
            chunk = f.read(8192)
            if b'\x00' in chunk:
                return True
    except Exception:
        return True
    return False

def _get_file_size(file_path: str) -> int:
    try:
        return os.path.getsize(file_path)
    except Exception:
        return 0

def _build_file_tree(root_path: str, rel_path: str = "") -> list:
    tree = []
    full = os.path.join(root_path, rel_path)
    if not os.path.exists(full):
        return tree
    try:
        entries = sorted(os.listdir(full))
    except PermissionError:
        return tree
    for entry in entries:
        if entry in SKIP_DIRS:
            continue
        entry_rel = os.path.join(rel_path, entry) if rel_path else entry
        entry_full = os.path.join(root_path, entry_rel)
        if os.path.isdir(entry_full):
            children = _build_file_tree(root_path, entry_rel)
            if children:
                tree.append({
                    "name": entry,
                    "path": entry_rel.replace("\\", "/"),
                    "type": "directory",
                    "children": children,
                })
        else:
            ext = os.path.splitext(entry)[1].lower()
            size = _get_file_size(entry_full)
            is_binary = ext in BINARY_EXTENSIONS or _is_binary_file(entry_full)
            tree.append({
                "name": entry,
                "path": entry_rel.replace("\\", "/"),
                "type": "file",
                "size": size,
                "binary": is_binary,
                "editable": not is_binary and size < 1024 * 1024,
            })
    return tree

def _is_file_editable(workspace: str, file_path: str) -> bool:
    full = os.path.normpath(os.path.join(workspace, file_path))
    if not full.startswith(os.path.normpath(workspace)):
        return False
    if not os.path.isfile(full):
        return False
    ext = os.path.splitext(file_path)[1].lower()
    if ext in BINARY_EXTENSIONS:
        return False
    size = _get_file_size(full)
    if size > 1024 * 1024:
        return False
    if _is_binary_file(full):
        return False
    return True

@router.get("/tree")
async def get_file_tree(user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")
    alloc = await db.allocations.find_one({"team_code": team_code})
    if not alloc or not alloc.get("released"):
        raise HTTPException(status_code=403, detail="Challenge not yet released")

    challenge_code = alloc["challenge_code"]
    workspace = await _init_workspace_async(team_code, challenge_code)
    tree = _build_file_tree(workspace)

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

    is_binary = _is_binary_file(file_path)
    ext = os.path.splitext(path)[1].lower()
    size = _get_file_size(file_path)
    editable = not is_binary and size < 1024 * 1024

    if is_binary:
        return {
            "content": "",
            "path": path,
            "binary": True,
            "editable": False,
            "size": size,
            "extension": ext,
            "message": "Binary file - cannot display or edit",
        }

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading file: {str(e)}")

    mtime = 0
    try:
        mtime = os.path.getmtime(file_path)
    except Exception:
        pass

    return {
        "content": content,
        "path": path,
        "binary": False,
        "editable": editable,
        "size": size,
        "extension": ext,
        "modified": mtime,
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

    if not _is_file_editable(workspace, body.path):
        raise HTTPException(status_code=403, detail="File is not editable")

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

class BulkSaveRequest(BaseModel):
    files: list

@router.post("/files/save")
async def bulk_save_files(body: BulkSaveRequest, user=Depends(get_participant_user)):
    db = get_db()
    team_code = user.get("sub")

    cmps = compute_event_status(await get_current_event())
    if cmps == "COMPLETED":
        raise HTTPException(status_code=403, detail="Event has ended. No more edits allowed.")

    alloc = await db.allocations.find_one({"team_code": team_code})
    if not alloc or not alloc.get("released"):
        raise HTTPException(status_code=403, detail="Challenge not yet released")

    challenge_code = alloc["challenge_code"]
    workspace = _get_workspace_path(team_code, challenge_code)
    saved = []

    for item in body.files:
        path = item.get("path", "")
        content = item.get("content", "")
        if not sanitize_path(path):
            continue
        file_path = os.path.normpath(os.path.join(workspace, path))
        if not file_path.startswith(os.path.normpath(workspace)):
            continue
        if not _is_file_editable(workspace, path):
            continue
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            saved.append(path)
        except Exception:
            pass

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
    alloc = await db.allocations.find_one({"team_code": team_code})
    if not alloc or not alloc.get("released"):
        raise HTTPException(status_code=403, detail="Challenge not yet released")

    challenge_code = alloc["challenge_code"]
    team = await db.teams.find_one({"team_code": team_code})
    ch = await db.challenges.find_one({"challenge_code": challenge_code})

    event = await get_current_event()
    event_start = event.get("event_start_time") if event else None
    event_end = event.get("event_end_time") if event else None

    has_eval, eval_files = _has_evaluator(challenge_code)

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

def _has_evaluator(challenge_code: str):
    from app.config import EVALUATOR_PATH
    evaluator_dir = os.path.join(EVALUATOR_PATH, challenge_code)
    if not os.path.exists(evaluator_dir):
        return False, []
    test_files = sorted(
        f for f in os.listdir(evaluator_dir)
        if f.startswith("test_") and f.endswith(".py")
    )
    return bool(test_files), test_files
