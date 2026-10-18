"""Password hashing and login tokens, using only the Python standard library."""
import hashlib
import hmac
import secrets

PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    """Salted PBKDF2-SHA256. Stored as 'pbkdf2_sha256$iterations$salt$hash'."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    _algorithm, iterations, salt, expected = stored.split("$")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    return hmac.compare_digest(digest.hex(), expected)  # constant-time comparison


def new_token() -> str:
    """Random, unguessable bearer token returned to the client once at login."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """We store only this hash, so a leaked database does not leak usable tokens."""
    return hashlib.sha256(token.encode()).hexdigest()
