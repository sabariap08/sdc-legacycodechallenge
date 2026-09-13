import os
import logging
import gridfs
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from app.database import get_db, is_db_available

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 2 * 1024 * 1024

ZIP_BUCKET = "challenge_zips"


def _get_collection_name(challenge_code: str) -> str:
    safe = "".join(c if (c.isalnum() or c in "-_ ") else "_" for c in challenge_code).replace(" ", "_")
    return f"repo_{safe}"


BINARY_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.webp', '.svg',
    '.exe', '.dll', '.so', '.dylib', '.o', '.a', '.bin',
    '.zip', '.tar', '.gz', '.bz2', '.7z', '.rar',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.mp3', '.mp4', '.avi', '.mov', '.wav', '.ogg',
    '.pyc', '.pyo', '.class', '.jar', '.woff', '.woff2', '.ttf', '.otf',
}


def _is_binary_content(content: bytes, fname: str) -> bool:
    ext = os.path.splitext(fname)[1].lower()
    if ext in BINARY_EXTENSIONS:
        return True
    return b"\x00" in content


async def save_challenge_files_to_db(challenge_code: str, disk_path: str) -> int:
    """Persist a challenge's extracted files into MongoDB GridFS so they survive restarts and redeploys."""
    if not is_db_available():
        logger.warning("DB not available; could not persist files for challenge %s", challenge_code)
        return 0

    db = get_db()
    bucket = AsyncIOMotorGridFSBucket(db, bucket_name=_get_collection_name(challenge_code))
    saved = 0

    try:
        async for f in bucket.find():
            await bucket.delete(f._id)
    except Exception as e:
        logger.warning("Failed to clear existing files for %s: %s", challenge_code, e)

    if not os.path.isdir(disk_path):
        logger.warning("Disk path %s missing; nothing to persist for %s", disk_path, challenge_code)
        return 0

    skip_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', 'env', '.env', 'dist', 'build'}

    try:
        for root, dirs, files in os.walk(disk_path):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fname in files:
                full_path = os.path.join(root, fname)
                rel_path = os.path.relpath(full_path, disk_path).replace("\\", "/")
                try:
                    file_size = os.path.getsize(full_path)
                    if file_size > MAX_FILE_SIZE:
                        logger.info("Skipping large file %s (%d bytes) for challenge %s", rel_path, file_size, challenge_code)
                        continue
                    with open(full_path, 'rb') as f:
                        content = f.read()
                    stream = bucket.open_upload_stream(
                        rel_path,
                        metadata={
                            "challenge_code": challenge_code,
                            "binary": _is_binary_content(content, fname),
                        },
                    )
                    try:
                        await stream.write(content)
                        await stream.close()
                    except Exception:
                        try:
                            await stream.abort()
                        except Exception:
                            pass
                        raise
                    saved += 1
                except Exception as e:
                    logger.warning("Failed to save %s to DB: %s", rel_path, e)
        logger.info("Saved %d files for challenge %s to MongoDB", saved, challenge_code)
    except Exception as e:
        logger.error("Failed to save challenge %s to MongoDB: %s", challenge_code, e)
        return saved
    return saved


async def get_challenge_file_tree_from_db(challenge_code: str) -> list:
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
            current[parts[-1]] = {"_file": True, "_length": file_doc.length}
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
                result.append({"name": name, "path": rel, "type": "file", "size": entry.get("_length", 0)})
        return result

    return build_nodes(tree)


async def get_challenge_file_content_from_db(challenge_code: str, file_path: str):
    """Return dict with content/binary/size or None if not found."""
    if not is_db_available():
        return None
    db = get_db()
    bucket = AsyncIOMotorGridFSBucket(db, bucket_name=_get_collection_name(challenge_code))
    try:
        normalized = file_path.replace("\\", "/")
        grid_out = await bucket.open_download_stream_by_name(normalized)
        content = await grid_out.read()
        return {
            "content": content,
            "binary": _is_binary_content(content, os.path.basename(normalized)),
            "size": len(content),
        }
    except gridfs.errors.NoFile:
        return None
    except Exception as e:
        logger.error("Failed to get file %s from DB for %s: %s", file_path, challenge_code, e)
        return None


async def save_challenge_zip_to_db(challenge_code: str, original_filename: str, content: bytes):
    """Store the original challenge ZIP so it can be streamed back for download."""
    if not is_db_available():
        logger.warning("DB not available; could not persist ZIP for challenge %s", challenge_code)
        return False
    db = get_db()
    bucket = AsyncIOMotorGridFSBucket(db, bucket_name=ZIP_BUCKET)
    try:
        async for f in bucket.find({"filename": challenge_code}):
            await bucket.delete(f._id)
        stream = bucket.open_upload_stream(
            challenge_code,
            metadata={"challenge_code": challenge_code, "original_filename": original_filename},
        )
        await stream.write(content)
        await stream.close()
        return True
    except Exception as e:
        logger.error("Failed to persist ZIP for challenge %s: %s", challenge_code, e)
        return False


async def get_challenge_zip_from_db(challenge_code: str):
    """Return (original_filename, content_bytes) or None."""
    if not is_db_available():
        return None
    db = get_db()
    bucket = AsyncIOMotorGridFSBucket(db, bucket_name=ZIP_BUCKET)
    try:
        grid_out = await bucket.open_download_stream_by_name(challenge_code)
        content = await grid_out.read()
        return grid_out.metadata.get("original_filename", f"{challenge_code}.zip"), content
    except gridfs.errors.NoFile:
        return None
    except Exception as e:
        logger.error("Failed to read ZIP for challenge %s: %s", challenge_code, e)
        return None


async def delete_challenge_zip_from_db(challenge_code: str):
    if not is_db_available():
        return
    db = get_db()
    bucket = AsyncIOMotorGridFSBucket(db, bucket_name=ZIP_BUCKET)
    try:
        async for f in bucket.find({"filename": challenge_code}):
            await bucket.delete(f._id)
    except Exception as e:
        logger.warning("Failed to delete ZIP for challenge %s: %s", challenge_code, e)


async def delete_challenge_files_from_db(challenge_code: str):
    if not is_db_available():
        return
    db = get_db()
    bucket_name = _get_collection_name(challenge_code)
    try:
        await db[f"{bucket_name}.files"].drop()
        await db[f"{bucket_name}.chunks"].drop()
    except Exception as e:
        logger.warning("Failed to delete challenge %s from MongoDB: %s", challenge_code, e)
