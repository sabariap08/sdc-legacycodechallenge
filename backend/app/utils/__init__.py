import secrets
import string
from datetime import datetime


def generate_team_code() -> str:
    chars = string.ascii_uppercase + string.digits
    code = ''.join(secrets.choice(chars) for _ in range(4))
    return f"BUG-{code}"


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


def _to_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def compute_event_status(settings: dict) -> str:
    """Derive the authoritative, database-driven event status.

    Rules (source of truth = persisted event document):
      - CANCELLED is sticky and always wins (never auto-reverts).
      - If start/end are missing -> UPCOMING (scheduled but not begun).
      - now < start  -> UPCOMING
      - now < end    -> ONGOING
      - else         -> COMPLETED
    """
    if not settings:
        return "UPCOMING"

    if settings.get("status") == "CANCELLED":
        return "CANCELLED"

    start = _to_datetime(settings.get("event_start_time"))
    end = _to_datetime(settings.get("event_end_time"))

    now = datetime.utcnow()

    if start is None and end is None:
        return settings.get("status", "UPCOMING") if settings.get("status") in ("UPCOMING", "ONGOING", "COMPLETED") else "UPCOMING"

    if start is None:
        return "UPCOMING"

    if now < start:
        return "UPCOMING"
    elif end is None or now < end:
        return "ONGOING"
    else:
        return "COMPLETED"
