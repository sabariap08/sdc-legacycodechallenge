import os
import logging
import gridfs
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from app.database import get_db, is_db_available
from app.config import CHALLENGE_STORAGE_PATH

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 512 * 1024


def _get_collection_name(challenge_code: str) -> str:
    return f"repo_{challenge_code}"


async def save_files_to_db(challenge_code: str, disk_path: str):
    if not is_db_available():
        return
    db = get_db()
    bucket = AsyncIOMotorGridFSBucket(db, bucket_name=_get_collection_name(challenge_code))
    saved = 0
    skip_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', 'env', '.env'}
    try:
        existing_ids = {}
        async for f in bucket.find():
            existing_ids[f.filename] = f._id

        for root, dirs, files in os.walk(disk_path):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fname in files:
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, disk_path).replace("\\", "/")
                try:
                    file_size = os.path.getsize(full_path)
                    if file_size > MAX_FILE_SIZE:
                        continue
                    binary_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.exe', '.dll', '.so', '.zip', '.tar', '.gz', '.pdf'}
                    _, ext = os.path.splitext(fname.lower())
                    if ext in binary_extensions:
                        continue
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    if rel_path in existing_ids:
                        await bucket.delete(existing_ids[rel_path])
                    await bucket.upload_from_string(
                        rel_path,
                        content,
                        encoding="utf-8",
                        metadata={"challenge_code": challenge_code}
                    )
                    saved += 1
                except Exception as e:
                    logger.warning("Failed to save %s to DB: %s", rel_path, e)
        logger.info("Saved %d files for challenge %s to MongoDB", saved, challenge_code)
    except Exception as e:
        logger.error("Failed to save challenge %s to MongoDB: %s", challenge_code, e)


async def load_files_from_db(challenge_code: str, disk_path: str) -> int:
    if not is_db_available():
        return 0
    db = get_db()
    bucket = AsyncIOMotorGridFSBucket(db, bucket_name=_get_collection_name(challenge_code))
    loaded = 0
    try:
        os.makedirs(disk_path, exist_ok=True)
        async for file_doc in bucket.find():
            rel_path = file_doc.filename
            full_path = os.path.join(disk_path, rel_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            try:
                grid_out = await bucket.open_download_stream(file_doc._id)
                content = await grid_out.read()
                text = content.decode("utf-8", errors="replace")
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(text)
                loaded += 1
            except Exception as e:
                logger.warning("Failed to load %s from DB: %s", rel_path, e)
        logger.info("Loaded %d files for challenge %s from MongoDB", loaded, challenge_code)
    except gridfs.errors.NoFile:
        logger.info("No files found in MongoDB for challenge %s", challenge_code)
    except Exception as e:
        logger.error("Failed to load challenge %s from MongoDB: %s", challenge_code, e)
    return loaded


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

    def build_nodes(node):
        result = []
        for name in sorted(node.keys()):
            if name == "_children":
                continue
            entry = node[name]
            if "_children" in entry:
                children = build_nodes(entry["_children"])
                if children:
                    result.append({"name": name, "path": None, "type": "directory", "children": children})
            elif "_file" in entry:
                result.append({"name": name, "path": None, "type": "file"})
        return result

    return build_nodes(tree)


async def get_file_content_from_db(challenge_code: str, file_path: str) -> str:
    if not is_db_available():
        return None
    db = get_db()
    bucket = AsyncIOMotorGridFSBucket(db, bucket_name=_get_collection_name(challenge_code))
    try:
        normalized = file_path.replace("\\", "/")
        grid_out = await bucket.open_download_stream_by_filename(normalized)
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


async def recover_all_challenges():
    if not is_db_available():
        return
    db = get_db()
    recovered = 0
    try:
        async for ch in db.challenges.find():
            challenge_code = ch["challenge_code"]
            storage_path = ch.get("storage_path", "")
            if storage_path and not os.path.exists(storage_path):
                os.makedirs(storage_path, exist_ok=True)
                loaded = await load_files_from_db(challenge_code, storage_path)
                if loaded > 0:
                    recovered += 1
                    logger.info("Recovered %d files for challenge %s", loaded, challenge_code)
                elif ch.get("repository_source") == "link" and ch.get("repository_url"):
                    import subprocess
                    import shutil
                    try:
                        subprocess.run(
                            ["git", "clone", "--depth", "1", ch["repository_url"], storage_path],
                            capture_output=True, text=True, timeout=120
                        )
                        await save_files_to_db(challenge_code, storage_path)
                        recovered += 1
                        logger.info("Re-cloned and saved challenge %s", challenge_code)
                    except Exception as e:
                        logger.error("Failed to re-clone challenge %s: %s", challenge_code, e)
    except Exception as e:
        logger.error("Challenge recovery failed: %s", e)
    if recovered > 0:
        logger.info("Recovered %d challenges from MongoDB", recovered)
