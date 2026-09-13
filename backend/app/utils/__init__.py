import secrets
import string


def generate_team_code() -> str:
    chars = string.ascii_uppercase + string.digits
    code = ''.join(secrets.choice(chars) for _ in range(4))
    return f"BUG-{code}"
