import os
import logging
import gridfs
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from app.database import get_db, is_db_available

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 512 * 1024


def _get_collection_name(challenge_code: str) -> str:
    return f"repo_{challenge_code}"


def _get_workspace_collection_name(team_code: str, challenge_code: str) -> str:
    safe_team = "".join(c if (c.isalnum() or c in "-_ ") else "_" for c in team_code).replace(" ", "_")
    safe_ch = "".join(c if (c.isalnum() or c in "-_ ") else "_" for c in challenge_code).replace(" ", "_")
    return f"ws_{safe_team}_{safe_ch}"


def _get_evaluator_collection_name(challenge_code: str) -> str:
    safe_ch = "".join(c if (c.isalnum() or c in "-_ ") else "_" for c in challenge_code).replace(" ", "_")
    return f"eval_{safe_ch}"


BINARY_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.webp',
    '.exe', '.dll', '.so', '.dylib', '.o', '.a', '.bin',
    '.zip', '.tar', '.gz', '.bz2', '.7z', '.rar',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.mp3', '.mp4', '.avi', '.mov', '.wav', '.ogg',
    '.pyc', '.pyo', '.class', '.jar', '.woff', '.woff2', '.ttf', '.otf',
}


async def save_files_to_db(challenge_code: str, disk_path: str, full_sync: bool = True) -> int:
    """Persist a challenge's files from disk into MongoDB GridFS.

    DB is the source of truth: after this call the repository can be restored
    even when the (Render) filesystem is wiped. Returns the number of files
    actually stored. Callers should treat ``0`` while the DB is available as a
    persistence failure.
    """
    if not is_db_available():
        logger.warning("DB not available; could not persist files for challenge %s", challenge_code)
        return 0
    db = get_db()
    bucket = AsyncIOMotorGridFSBucket(db, bucket_name=_get_collection_name(challenge_code))
    saved = 0
    failed = 0
    skip_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', 'env', '.env'}
    if not os.path.isdir(disk_path):
        logger.warning("Disk path %s missing; nothing to persist for %s", disk_path, challenge_code)
        return 0
    try:
        existing_ids = {}
        async for f in bucket.find():
            existing_ids[f.filename] = f._id

        seen = set()
        for root, dirs, files in os.walk(disk_path):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fname in files:
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, disk_path).replace("\\", "/")
                seen.add(rel_path)
                try:
                    file_size = os.path.getsize(full_path)
                    if file_size > MAX_FILE_SIZE:
                        continue
                    _, ext = os.path.splitext(fname.lower())
                    if ext in BINARY_EXTENSIONS:
                        continue
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    if rel_path in existing_ids:
                        await bucket.delete(existing_ids[rel_path])
                        existing_ids.pop(rel_path, None)
                    stream = bucket.open_upload_stream(rel_path, metadata={"challenge_code": challenge_code})
                    try:
                        await stream.write(content.encode("utf-8"))
                        await stream.close()
                    except Exception:
                        try:
                            await stream.abort()
                        except Exception:
                            pass
                        raise
                    saved += 1
                except Exception as e:
                    failed += 1
                    logger.warning("Failed to save %s to DB: %s", rel_path, e)

        if full_sync:
            # Remove GridFS files that no longer exist on disk (repo updated/cleaned)
            for rel_name, file_id in list(existing_ids.items()):
                if rel_name not in seen:
                    try:
                        await bucket.delete(file_id)
                    except Exception as e:
                        logger.warning("Failed to remove stale file %s: %s", rel_name, e)
        logger.info("Saved %d files for challenge %s to MongoDB", saved, challenge_code)
        if failed:
            logger.error("Failed to persist %d file(s) for challenge %s (saved %d)", failed, challenge_code, saved)
    except Exception as e:
        logger.error("Failed to save challenge %s to MongoDB: %s", challenge_code, e)
        return saved
    return saved


async def get_file_tree_from_db(challenge_code: str) -> list:
    if not is_db_available():
        return []
    db = get_db()
    bucket = AsyncIOMotorGridFSBucket(db, bucket_name=_get_collection_name(challenge_code))
    tree = {}
    try:
        async for file_doc in bucket.find():
            parts = file_doc.filename.split("/")
            current = tree
            for i, part in enumerate(parts[:-1]):
                if part not in current:
                    current[part] = {"_children": {}}
                current = current[part]["_children"]
            current[parts[-1]] = {"_file": True}
    except Exception as e:
        logger.error("Failed to get file tree from DB for %s: %s", challenge_code, e)
        return []

    def build_nodes(node, prefix=""):
        result = []
        for name in sorted(node.keys()):
            if name == "_children":
                continue
            entry = node[name]
            rel = (prefix + "/" + name) if prefix else name
            if "_children" in entry:
                children = build_nodes(entry["_children"], rel)
                if children:
                    result.append({"name": name, "path": rel, "type": "directory", "children": children})
            elif "_file" in entry:
                result.append({"name": name, "path": rel, "type": "file"})
        return result

    return build_nodes(tree)


async def get_file_content_from_db(challenge_code: str, file_path: str) -> str:
    if not is_db_available():
        return None
    db = get_db()
    bucket = AsyncIOMotorGridFSBucket(db, bucket_name=_get_collection_name(challenge_code))
    try:
        normalized = file_path.replace("\\", "/")
        grid_out = await bucket.open_download_stream_by_name(normalized)
        content = await grid_out.read()
        return content.decode("utf-8", errors="replace")
    except gridfs.errors.NoFile:
        return None
    except Exception as e:
        logger.error("Failed to get file %s from DB for %s: %s", file_path, challenge_code, e)
        return None


async def delete_files_from_db(challenge_code: str):
    if not is_db_available():
        return
    db = get_db()
    bucket_name = _get_collection_name(challenge_code)
    try:
        await db[f"{bucket_name}.files"].drop()
        await db[f"{bucket_name}.chunks"].drop()
        logger.info("Deleted GridFS collections for challenge %s", challenge_code)
    except Exception as e:
        logger.warning("Failed to delete challenge %s from MongoDB: %s", challenge_code, e)


# ─────────────────────────────────────────────
# WORKSPACES (DB-first: no files written to team_workspaces/)
# ─────────────────────────────────────────────

def _workspace_bucket(db, team_code: str, challenge_code: str):
    return AsyncIOMotorGridFSBucket(db, bucket_name=_get_workspace_collection_name(team_code, challenge_code))


async def exists_workspace(team_code: str, challenge_code: str) -> bool:
    """True if the team already has workspace files in Mongo (file count > 0)."""
    if not is_db_available():
        return False
    try:
        bucket = _workspace_bucket(get_db(), team_code, challenge_code)
        async for _ in bucket.find():
            return True
        return False
    except Exception as e:
        logger.error("exists_workspace failed for %s/%s: %s", team_code, challenge_code, e)
        return False


async def copy_challenge_to_workspace(team_code: str, challenge_code: str) -> int:
    """Seed a team's workspace by copying the challenge repo files (DB-only)."""
    if not is_db_available():
        return 0
    db = get_db()
    src = AsyncIOMotorGridFSBucket(db, bucket_name=_get_collection_name(challenge_code))
    dst = _workspace_bucket(db, team_code, challenge_code)
    copied = 0
    try:
        async for f in src.find():
            try:
                grid_out = await src.open_download_stream(f._id)
                content = await grid_out.read()
                stream = dst.open_upload_stream(f.filename, metadata={"team_code": team_code, "challenge_code": challenge_code})
                try:
                    await stream.write(content)
                    await stream.close()
                except Exception:
                    try:
                        await stream.abort()
                    except Exception:
                        pass
                    raise
                copied += 1
            except Exception as e:
                logger.warning("Failed to copy %s to workspace for %s/%s: %s", f.filename, team_code, challenge_code, e)
        logger.info("Copied %d challenge files into workspace %s/%s", copied, team_code, challenge_code)
    except Exception as e:
        logger.error("copy_challenge_to_workspace failed for %s/%s: %s", team_code, challenge_code, e)
    return copied


async def save_workspace_files_to_db(team_code: str, challenge_code: str, disk_path: str) -> int:
    """Persist a hydrated temp workspace back into Mongo (most clients never call this)."""
    if not is_db_available() or not os.path.isdir(disk_path):
        return 0
    db = get_db()
    bucket = _workspace_bucket(db, team_code, challenge_code)
    saved = 0
    try:
        for root, dirs, files in os.walk(disk_path):
            dirs[:] = [d for d in dirs if d not in {'.git', '__pycache__', 'node_modules', '.venv', 'venv', 'env', '.env', 'dist', 'build'}]
            for fname in files:
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, disk_path).replace("\\", "/")
                try:
                    if os.path.getsize(full_path) > MAX_FILE_SIZE:
                        continue
                    _, ext = os.path.splitext(fname.lower())
                    if ext in BINARY_EXTENSIONS:
                        continue
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    stream = bucket.open_upload_stream(rel_path, metadata={"team_code": team_code, "challenge_code": challenge_code})
                    try:
                        await stream.write(content.encode("utf-8"))
                        await stream.close()
                    except Exception:
                        try:
                            await stream.abort()
                        except Exception:
                            pass
                        raise
                    saved += 1
                except Exception as e:
                    logger.warning("Failed to save workspace file %s: %s", rel_path, e)
    except Exception as e:
        logger.error("save_workspace_files_to_db failed for %s/%s: %s", team_code, challenge_code, e)
    return saved


async def load_workspace_files_from_db(team_code: str, challenge_code: str, disk_path: str) -> int:
    """Hydrate a workspace from Mongo into a temp dir for execution/submission only."""
    if not is_db_available():
        return 0
    db = get_db()
    bucket = _workspace_bucket(db, team_code, challenge_code)
    loaded = 0
    try:
        os.makedirs(disk_path, exist_ok=True)
        async for file_doc in bucket.find():
            rel_path = file_doc.filename
            full_path = os.path.join(disk_path, rel_path)
            os.makedirs(os.path.dirname(full_path) or disk_path, exist_ok=True)
            try:
                grid_out = await bucket.open_download_stream(file_doc._id)
                content = await grid_out.read()
                with open(full_path, "wb") as f:
                    f.write(content)
                loaded += 1
            except Exception as e:
                logger.warning("Failed to load workspace file %s: %s", rel_path, e)
    except Exception as e:
        logger.error("load_workspace_files_from_db failed for %s/%s: %s", team_code, challenge_code, e)
    return loaded


async def get_workspace_tree_from_db(team_code: str, challenge_code: str) -> list:
    if not is_db_available():
        return []
    db = get_db()
    bucket = _workspace_bucket(db, team_code, challenge_code)
    tree = {}
    try:
        async for file_doc in bucket.find():
            parts = file_doc.filename.split("/")
            current = tree
            for i, part in enumerate(parts[:-1]):
                if part not in current:
                    current[part] = {"_children": {}}
                current = current[part]["_children"]
            current[parts[-1]] = {"_file": True, "_length": file_doc.length}
    except Exception as e:
        logger.error("get_workspace_tree_from_db failed for %s/%s: %s", team_code, challenge_code, e)
        return []

    def _is_binary(name: str, length: int) -> bool:
        ext = os.path.splitext(name)[1].lower()
        if ext in BINARY_EXTENSIONS:
            return True
        if not length:
            return False
        return False

    def build_nodes(node, prefix=""):
        result = []
        for name in sorted(node.keys()):
            if name == "_children":
                continue
            entry = node[name]
            rel = (prefix + "/" + name) if prefix else name
            if "_children" in entry:
                children = build_nodes(entry["_children"], rel)
                if children:
                    result.append({"name": name, "path": rel, "type": "directory", "children": children})
            elif "_file" in entry:
                size = entry.get("_length", 0)
                binary = _is_binary(name, size)
                result.append({
                    "name": name,
                    "path": rel,
                    "type": "file",
                    "size": size,
                    "binary": binary,
                    "editable": not binary and size < 1024 * 1024,
                })
        return result

    return build_nodes(tree)


async def get_workspace_file_from_db(team_code: str, challenge_code: str, file_path: str):
    if not is_db_available():
        return None
    db = get_db()
    bucket = _workspace_bucket(db, team_code, challenge_code)
    try:
        normalized = file_path.replace("\\", "/")
        grid_out = await bucket.open_download_stream_by_name(normalized)
        content = await grid_out.read()
        return {
            "content": content.decode("utf-8", errors="replace"),
            "size": len(content),
            "binary": b"\x00" in content,
            "modified": grid_out.upload_date.isoformat() if grid_out.upload_date else None,
        }
    except gridfs.errors.NoFile:
        return None
    except Exception as e:
        logger.error("get_workspace_file_from_db failed %s/%s %s: %s", team_code, challenge_code, file_path, e)
        return None


async def save_workspace_file_to_db(team_code: str, challenge_code: str, file_path: str, content: str) -> bool:
    if not is_db_available():
        return False
    db = get_db()
    bucket = _workspace_bucket(db, team_code, challenge_code)
    try:
        normalized = file_path.replace("\\", "/")
        try:
            existing = await bucket.open_download_stream_by_name(normalized)
            await bucket.delete(existing._id)
        except gridfs.errors.NoFile:
            pass
        stream = bucket.open_upload_stream(normalized, metadata={"team_code": team_code, "challenge_code": challenge_code})
        try:
            await stream.write(content.encode("utf-8"))
            await stream.close()
        except Exception:
            try:
                await stream.abort()
            except Exception:
                pass
            raise
        return True
    except Exception as e:
        logger.error("save_workspace_file_to_db failed %s/%s %s: %s", team_code, challenge_code, file_path, e)
        return False


async def delete_workspace_from_db(team_code: str, challenge_code: str):
    if not is_db_available():
        return
    db = get_db()
    bucket_name = _get_workspace_collection_name(team_code, challenge_code)
    try:
        await db[f"{bucket_name}.files"].drop()
        await db[f"{bucket_name}.chunks"].drop()
        logger.info("Deleted workspace GridFS for %s/%s", team_code, challenge_code)
    except Exception as e:
        logger.warning("Failed to delete workspace %s/%s from MongoDB: %s", team_code, challenge_code, e)


async def delete_evaluator_from_db(challenge_code: str):
    if not is_db_available():
        return
    db = get_db()
    bucket_name = _get_evaluator_collection_name(challenge_code)
    try:
        await db[f"{bucket_name}.files"].drop()
        await db[f"{bucket_name}.chunks"].drop()
        logger.info("Deleted evaluator GridFS for %s", challenge_code)
    except Exception as e:
        logger.warning("Failed to delete evaluator for %s from MongoDB: %s", challenge_code, e)


# ─────────────────────────────────────────────
# EVALUATORS (DB-first: tests stored in GridFS, hydrated to temp for runs)
# ─────────────────────────────────────────────

def _evaluator_bucket(db, challenge_code: str):
    return AsyncIOMotorGridFSBucket(db, bucket_name=_get_evaluator_collection_name(challenge_code))


async def save_evaluator_to_db(challenge_code: str, disk_path: str) -> int:
    if not is_db_available() or not os.path.isdir(disk_path):
        return 0
    db = get_db()
    bucket = _evaluator_bucket(db, challenge_code)
    saved = 0
    try:
        async for f in bucket.find():
            await bucket.delete(f._id)
        for fname in sorted(os.listdir(disk_path)):
            if not (fname.startswith("test_") and fname.endswith(".py")):
                continue
            full_path = os.path.join(disk_path, fname)
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                stream = bucket.open_upload_stream(fname, metadata={"challenge_code": challenge_code})
                try:
                    await stream.write(content.encode("utf-8"))
                    await stream.close()
                except Exception:
                    try:
                        await stream.abort()
                    except Exception:
                        pass
                    raise
                saved += 1
            except Exception as e:
                logger.warning("Failed to save evaluator %s/%s: %s", challenge_code, fname, e)
        logger.info("Saved %d evaluator test files for %s", saved, challenge_code)
    except Exception as e:
        logger.error("save_evaluator_to_db failed for %s: %s", challenge_code, e)
    return saved


async def load_evaluator_from_db(challenge_code: str, disk_path: str) -> int:
    if not is_db_available():
        return 0
    db = get_db()
    bucket = _evaluator_bucket(db, challenge_code)
    loaded = 0
    try:
        os.makedirs(disk_path, exist_ok=True)
        async for file_doc in bucket.find():
            fname = file_doc.filename
            full_path = os.path.join(disk_path, fname)
            try:
                grid_out = await bucket.open_download_stream(file_doc._id)
                content = await grid_out.read()
                with open(full_path, "wb") as f:
                    f.write(content)
                loaded += 1
            except Exception as e:
                logger.warning("Failed to load evaluator file %s: %s", fname, e)
    except Exception as e:
        logger.error("load_evaluator_from_db failed for %s: %s", challenge_code, e)
    return loaded


async def has_evaluator(challenge_code: str):
    """Return (has_evaluator, sorted list of test filenames) sourced from DB."""
    if not is_db_available():
        return False, []
    db = get_db()
    bucket = _evaluator_bucket(db, challenge_code)
    test_files = []
    try:
        async for file_doc in bucket.find():
            fname = file_doc.filename
            if fname.startswith("test_") and fname.endswith(".py"):
                test_files.append(fname)
    except Exception as e:
        logger.error("has_evaluator failed for %s: %s", challenge_code, e)
        return False, []
    test_files.sort()
    return bool(test_files), test_files


async def sync_evaluators_from_disk():
    """Seed evaluator test files from the app-shipped evaluator/ dir into GridFS.

    Evaluator tests are committed with the deployment (static challenge specs).
    The DB must be the only runtime storage, so at startup we push every
    evaluator/{code}/*.py found on disk into their eval_ bucket.
    """
    from app.config import EVALUATOR_PATH
    if not is_db_available():
        return 0
    total = 0
    try:
        if not os.path.isdir(EVALUATOR_PATH):
            return 0
        for name in sorted(os.listdir(EVALUATOR_PATH)):
            challenge_dir = os.path.join(EVALUATOR_PATH, name)
            if not os.path.isdir(challenge_dir):
                continue
            saved = await save_evaluator_to_db(name, challenge_dir)
            total += saved
        if total:
            logger.info("Synced %d evaluator test files into MongoDB", total)
    except Exception as e:
        logger.error("sync_evaluators_from_disk failed: %s", e)
    return total
