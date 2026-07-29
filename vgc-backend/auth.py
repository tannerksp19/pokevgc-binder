"""Password hashing, JWT access tokens, and opaque refresh tokens.

Two-token scheme:
  * access  — short-lived HS256 JWT, verified without touching the database
  * refresh — long-lived opaque random string, stored hashed and revocable

That keeps logout meaningful (revoking the refresh token ends the session) while
access tokens stay stateless. The cost is a window: a stolen access token remains
valid until it expires, at most ACCESS_TTL_SECONDS.
"""

import base64
import hashlib
import os
import secrets
import sqlite3
import stat
from pathlib import Path
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException, status

from db import DB_PATH, connect, now_ms

ACCESS_TTL_SECONDS = 15 * 60                      # 15 minutes
REFRESH_TTL_MS = 30 * 24 * 60 * 60 * 1000         # 30 days
JWT_ALGORITHM = "HS256"

# Work factor. 12 is the current common default; raise it as hardware improves.
BCRYPT_COST = int(os.environ.get("VGC_BCRYPT_COST", "12"))


# ─────────────────────────── signing secret ───────────────────────────

def _load_secret() -> str:
    """Read VGC_SECRET, or persist a generated one beside the database.

    Persisting matters: a per-process random secret would invalidate every
    access token on restart. Set VGC_SECRET explicitly in production.
    """
    from_env = os.environ.get("VGC_SECRET")
    if from_env:
        # RFC 7518: HS256 keys shorter than the hash output are weak. Refuse early
        # rather than signing every token with a guessable secret.
        if len(from_env.encode("utf-8")) < 32:
            raise RuntimeError("VGC_SECRET must be at least 32 bytes")
        return from_env

    path = DB_PATH.parent / ".jwt_secret"
    if path.exists():
        return path.read_text().strip()

    generated = secrets.token_urlsafe(48)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generated)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600 — owner only
    return generated


_SECRET = _load_secret()


# ─────────────────────────── passwords ───────────────────────────

# bcrypt only looks at the first 72 bytes. Hashing to a fixed-length digest first
# means a long passphrase is fully accounted for instead of silently truncated.
# Base64 (not raw digest) because bcrypt also stops at the first NUL byte.
def _prehash(password: str) -> bytes:
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


_DUMMY_HASH = bcrypt.hashpw(_prehash("dummy-password-for-constant-time"), bcrypt.gensalt(BCRYPT_COST))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt(BCRYPT_COST)).decode("utf-8")


def verify_password(password: str, password_hash: Optional[str]) -> bool:
    """Check a password. Passing None still burns a hash comparison."""
    candidate = _prehash(password)
    if password_hash is None:
        bcrypt.checkpw(candidate, _DUMMY_HASH)
        return False
    try:
        return bcrypt.checkpw(candidate, password_hash.encode("utf-8"))
    except ValueError:
        return False


# ─────────────────────────── tokens ───────────────────────────

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def make_access_token(account_id: int) -> str:
    now = now_ms() // 1000
    return jwt.encode(
        {"sub": str(account_id), "type": "access", "iat": now, "exp": now + ACCESS_TTL_SECONDS},
        _SECRET,
        algorithm=JWT_ALGORITHM,
    )


def issue_refresh_token(conn: sqlite3.Connection, account_id: int) -> str:
    """Create a refresh token and return it — the only time it exists in plaintext."""
    token = secrets.token_urlsafe(32)
    created = now_ms()
    conn.execute(
        "INSERT INTO refresh_tokens (token_hash, account_id, created_at, expires_at) VALUES (?,?,?,?)",
        (_hash_token(token), account_id, created, created + REFRESH_TTL_MS),
    )
    return token


def consume_refresh_token(conn: sqlite3.Connection, token: str) -> int:
    """Validate a refresh token and return its account id, or raise 401."""
    row = conn.execute(
        "SELECT account_id, expires_at FROM refresh_tokens WHERE token_hash = ?",
        (_hash_token(token),),
    ).fetchone()

    if row is None:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    if row["expires_at"] < now_ms():
        conn.execute("DELETE FROM refresh_tokens WHERE token_hash = ?", (_hash_token(token),))
        raise HTTPException(status_code=401, detail="Refresh token expired")

    return row["account_id"]


def revoke_refresh_token(conn: sqlite3.Connection, token: str) -> int:
    cur = conn.execute("DELETE FROM refresh_tokens WHERE token_hash = ?", (_hash_token(token),))
    return cur.rowcount


# ─────────────────────────── request auth ───────────────────────────

_UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}


def current_account(authorization: Optional[str] = Header(default=None)) -> sqlite3.Row:
    """FastAPI dependency resolving the caller from a JWT access token, or 401."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token", headers=_UNAUTHORIZED)

    raw = authorization[7:].strip()
    try:
        claims = jwt.decode(raw, _SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Access token expired", headers=_UNAUTHORIZED)
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid access token", headers=_UNAUTHORIZED)

    # A refresh token must never be accepted as an access token.
    if claims.get("type") != "access":
        raise HTTPException(401, "Wrong token type", headers=_UNAUTHORIZED)

    with connect() as conn:
        account = conn.execute(
            "SELECT id, username, display_name, created_at FROM accounts WHERE id = ?",
            (claims.get("sub"),),
        ).fetchone()

    if account is None:  # account deleted while a token was still live
        raise HTTPException(401, "Account no longer exists", headers=_UNAUTHORIZED)

    return account


CurrentAccount = Depends(current_account)
