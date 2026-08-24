import secrets
import string
from datetime import datetime


def generate_team_code() -> str:
    chars = string.ascii_uppercase + string.digits
    code = ''.join(secrets.choice(chars) for _ in range(4))
    return f"BUG-{code}"


def generate_challenge_code(index: int) -> str:
    return f"CH-{index:02d}"


def generate_bin_number(index: int) -> str:
    return f"BIN-{index:02d}"


def generate_event_code() -> str:
    chars = string.ascii_uppercase + string.digits
    code = ''.join(secrets.choice(chars) for _ in range(4))
    return f"EVT-{code}"


def sanitize_path(path: str) -> bool:
    forbidden = ["..", "~", "/etc", "/proc", "/sys", "\\", "\x00"]
    for f in forbidden:
        if f in path:
            return False
    return True


def compute_event_status(settings: dict) -> str:
    now = datetime.utcnow()
    start = settings.get("event_start_time") if settings else None
    end = settings.get("event_end_time") if settings else None
    if not start or not end:
        return settings.get("status", "DRAFT") if settings else "DRAFT"
    if isinstance(start, str):
        start = datetime.fromisoformat(start.replace("Z", "+00:00")).replace(tzinfo=None)
    if isinstance(end, str):
        end = datetime.fromisoformat(end.replace("Z", "+00:00")).replace(tzinfo=None)
    if now < start:
        return "UPCOMING"
    elif now < end:
        return "ONGOING"
    else:
        return "COMPLETED"
