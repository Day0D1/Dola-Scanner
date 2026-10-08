"""Password hashing (PBKDF2-SHA256, stdlib) and admin bootstrap."""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets

from . import db
from .config import settings

log = logging.getLogger("bigsam.auth")
ITERATIONS = 240_000


def hash_password(pw: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, it, salt, digest = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(dk.hex(), digest)
    except ValueError:
        return False


def set_password(username: str, pw: str) -> None:
    if len(pw) < 8:
        raise ValueError("password must be at least 8 characters")
    h = hash_password(pw)
    if db.one("SELECT id FROM users WHERE username=?", (username,)):
        db.execute("UPDATE users SET password_hash=? WHERE username=?", (h, username))
    else:
        db.execute("INSERT INTO users(username,password_hash,created_at) VALUES(?,?,?)", (username, h, db.now()))


def authenticate(username: str, pw: str) -> dict | None:
    u = db.one("SELECT * FROM users WHERE username=?", (username,))
    return u if u and verify_password(pw, u["password_hash"]) else None


def ensure_admin() -> None:
    # A new ADMIN_PASSWORD (e.g. set in the hosting dashboard after first boot) is applied once;
    # a password changed later in Settings is kept until the env value changes again.
    pw = settings.admin_password
    if pw:
        fp = hashlib.sha256(pw.encode()).hexdigest()
        if db.kv_get("admin_env_pw") != fp:
            set_password(settings.admin_username, pw)
            db.kv_set("admin_env_pw", fp)
            return
    if db.one("SELECT id FROM users LIMIT 1"):
        return
    pw = settings.admin_password
    if not pw:
        pw = secrets.token_urlsafe(12)
        log.warning("ADMIN_PASSWORD not set - generated one-time password for '%s': %s",
                    settings.admin_username, pw)
    set_password(settings.admin_username, pw)


def secret_key() -> str:
    if settings.secret_key:
        return settings.secret_key
    key = db.kv_get("secret_key")
    if not key:
        key = secrets.token_hex(32)
        db.kv_set("secret_key", key)
    return key
