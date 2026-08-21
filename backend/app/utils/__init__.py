import secrets
import string


def generate_team_code() -> str:
    chars = string.ascii_uppercase + string.digits
    code = ''.join(secrets.choice(chars) for _ in range(4))
    return f"BUG-{code}"


def generate_challenge_code(index: int) -> str:
    return f"CH-{index:02d}"


def generate_bin_number(index: int) -> str:
    return f"BIN-{index:02d}"


def sanitize_path(path: str) -> bool:
    forbidden = ["..", "~", "/etc", "/proc", "/sys", "\\", "\x00"]
    for f in forbidden:
        if f in path:
            return False
    return True
