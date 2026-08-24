from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional, List
from app.database import get_db
from app.security import get_admin_user
from app.config import CHALLENGE_STORAGE_PATH
from app.storage import save_files_to_db, get_file_tree_from_db, get_file_content_from_db, delete_files_from_db
from datetime import datetime
import os
import shutil
import zipfile
import io
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["challenges"])

ALLOWED_EXTENSIONS = {
    '.py', '.js', '.ts', '.jsx', '.tsx', '.html', '.css', '.scss', '.less',
    '.java', '.c', '.cpp', '.h', '.hpp', '.cs', '.go', '.rs', '.rb',
    '.php', '.swift', '.kt', '.scala', '.r', '.R', '.m', '.sql',
    '.sh', '.bash', '.zsh', '.fish', '.ps1', '.bat', '.cmd',
    '.json', '.yaml', '.yml', '.toml', '.xml', '.ini', '.cfg', '.conf',
    '.md', '.txt', '.rst', '.csv', '.tsv',
    '.gitignore', '.dockerignore', '.editorconfig', '.env', '.env.example',
    'Makefile', 'Dockerfile', 'docker-compose.yml', 'docker-compose.yaml',
    'requirements.txt', 'setup.py', 'setup.cfg', 'pyproject.toml',
    'package.json', 'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml',
    'Cargo.toml', 'Cargo.lock', 'go.mod', 'go.sum',
    'Gemfile', 'Gemfile.lock', 'Podfile', 'Podfile.lock',
    'build.gradle', 'pom.xml', 'settings.gradle',
    'CMakeLists.txt', 'Makefile',
}

DANGEROUS_EXTENSIONS = {
    '.exe', '.msi', '.dll', '.so', '.dylib', '.bin', '.cmd', '.com',
    '.scr', '.pif', '.vbs', '.vbe', '.jsf', '.jse', '.wsf', '.wsh',
    '.ps1', '.psm1', '.psd1', '.reg', '.inf', '.reg',
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
                results.append({"challenge_code": challenge_code, "status": "FAILED", "error": "Invalid repository URL"})
                continue

            storage_path = _get_challenge_storage_path(challenge_code)
            if os.path.exists(storage_path):
                shutil.rmtree(storage_path)

            os.makedirs(storage_path, exist_ok=True)

            result = subprocess.run(
                ["git", "clone", "--depth", "1", url, storage_path],
                capture_output=True, text=True, timeout=120
            )

            if result.returncode != 0:
                results.append({"challenge_code": challenge_code, "status": "FAILED", "error": result.stderr.strip()})
                continue

            sha_result = subprocess.run(
                ["git", "-C", storage_path, "rev-parse", "HEAD"],
                capture_output=True, text=True
            )
            commit_sha = sha_result.stdout.strip() if sha_result.returncode == 0 else "unknown"

            existing = await db.challenges.find_one({"challenge_code": challenge_code})
            challenge_doc = {
                "challenge_code": challenge_code,
                "challenge_name": challenge_code,
                "repository_source": "link",
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



