"""
ClawPulse Sync Server
====================
Encrypted data relay between the ClawPulse mobile app and any OpenClaw instance.

Privacy model: server stores only encrypted blobs. Plaintext never leaves the client.
All encryption/decryption happens on the edges (mobile app + OpenClaw).

GitHub: https://github.com/rodrigocava/clawpulse
"""

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import List

import aiosqlite
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# ── Config ────────────────────────────────────────────────────────────────────

DATABASE_PATH        = os.getenv("DATABASE_PATH", "sync.db")
DATA_TTL_HOURS       = int(os.getenv("DATA_TTL_HOURS", "48"))
MAX_PAYLOAD_BYTES    = int(os.getenv("MAX_PAYLOAD_BYTES", str(10 * 1024 * 1024)))  # 10 MB
MAX_TOKEN_QUOTA_BYTES = int(os.getenv("MAX_TOKEN_QUOTA_BYTES", str(5 * 1024 * 1024)))  # 5 MB total per token
CLIENT_SECRET        = os.getenv("CLIENT_SECRET", "")  # Empty = dev mode (no auth)

# ── Rate limiting ─────────────────────────────────────────────────────────────

def get_client_ip(request: Request) -> str:
    """Use CF-Connecting-IP when behind Cloudflare, fall back to remote address."""
    return request.headers.get("CF-Connecting-IP") or get_remote_address(request)

limiter = Limiter(key_func=get_client_ip)

# ── Auth ──────────────────────────────────────────────────────────────────────

async def verify_client_secret(x_clawpulse_secret: str = Header(default="")) -> None:
    """
    Validate the shared app secret sent by the ClawPulse mobile app.
    Set CLIENT_SECRET env var to enable. If unset, validation is skipped (dev mode).
    """
    if CLIENT_SECRET and x_clawpulse_secret != CLIENT_SECRET:
        raise HTTPException(status_code=401, detail="Invalid or missing client secret.")

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="ClawPulse",
    description="""
Encrypted data relay for the **ClawPulse** mobile app (iOS & Android).

## How it works

1. The ClawPulse app encrypts your phone context data client-side
2. Encrypted blobs are uploaded here — the server **never sees plaintext**
3. Your OpenClaw instance fetches all blobs and decrypts them locally
4. Analysis and insights happen entirely on your own infrastructure

## Privacy

The server stores only opaque encrypted blobs keyed by a SHA-256 hash of your token.
Even with full database access, no personal data is recoverable without your password.

## Data retention

Each payload expires automatically after **48 hours** by default. Clients can override per-upload
via the `X-TTL-Hours` header (clamped to 1–168h). Server-wide default is `DATA_TTL_HOURS`.
Multiple datapoints accumulate over time — e.g. hourly sync = up to 48 datapoints.
Expired rows are purged on every write, read, and via the `/cleanup` endpoint.

## Self-hosting

The server is open source. Run your own at: https://github.com/rodrigocava/clawpulse
""",
    version="2.1.0",
    contact={
        "name": "ClawPulse",
        "url": "https://github.com/rodrigocava/clawpulse",
    },
    license_info={"name": "MIT"},
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

# ── Models ────────────────────────────────────────────────────────────────────

class SyncUpload(BaseModel):
    token: str
    payload: str  # base64-encoded AES-256-GCM encrypted blob

    @field_validator("token")
    @classmethod
    def token_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Token must be at least 8 characters")
        return v

    @field_validator("payload")
    @classmethod
    def payload_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Payload cannot be empty")
        return v


class Datapoint(BaseModel):
    payload: str
    created_at: str
    expires_at: str


class SyncResponse(BaseModel):
    count: int
    datapoints: List[Datapoint]


class CountResponse(BaseModel):
    count: int
    oldest: str | None
    newest: str | None


class StatusResponse(BaseModel):
    status: str
    message: str


# ── DB helpers ────────────────────────────────────────────────────────────────

async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DATABASE_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("""
        CREATE TABLE IF NOT EXISTS sync_data (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            token_hash  TEXT NOT NULL,
            payload     TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            expires_at  TEXT NOT NULL
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_token_hash ON sync_data(token_hash)")
    await db.commit()
    return db


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def expiry_utc(ttl_hours: int = DATA_TTL_HOURS) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()


TTL_MIN_HOURS = 1
TTL_MAX_HOURS = 168  # 7 days


def parse_ttl_header(value: str | None) -> int:
    """Parse X-TTL-Hours header. Clamps to [1, 168]. Falls back to DATA_TTL_HOURS on invalid input."""
    if value is None:
        return DATA_TTL_HOURS
    try:
        return max(TTL_MIN_HOURS, min(int(value), TTL_MAX_HOURS))
    except (ValueError, TypeError):
        return DATA_TTL_HOURS


async def purge_expired_for_token(db: aiosqlite.Connection, token_hash: str) -> None:
    """Remove expired rows for a specific token."""
    await db.execute(
        "DELETE FROM sync_data WHERE token_hash = ? AND expires_at < ?",
        (token_hash, now_utc()),
    )
    await db.commit()


async def purge_all_expired(db: aiosqlite.Connection) -> int:
    """Remove all expired rows across all tokens. Returns number of rows deleted."""
    cursor = await db.execute(
        "DELETE FROM sync_data WHERE expires_at < ?", (now_utc(),)
    )
    await db.commit()
    return cursor.rowcount


async def check_quota(db: aiosqlite.Connection, token_hash: str, new_payload: str) -> None:
    """Enforce per-token total storage quota across all datapoints."""
    new_size = len(new_payload.encode())
    if new_size > MAX_PAYLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Payload exceeds single upload size limit.")

    async with db.execute(
        "SELECT COALESCE(SUM(LENGTH(payload)), 0) FROM sync_data WHERE token_hash = ? AND expires_at > ?",
        (token_hash, now_utc()),
    ) as cursor:
        row = await cursor.fetchone()
        current_total = row[0] if row else 0

    if current_total + new_size > MAX_TOKEN_QUOTA_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Token storage quota exceeded ({MAX_TOKEN_QUOTA_BYTES // 1024 // 1024}MB total). "
                   "Old datapoints will free up space as they expire.",
        )


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    db = await get_db()
    await db.close()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post(
    "/sync",
    response_model=StatusResponse,
    summary="Upload encrypted payload",
    tags=["Sync"],
    dependencies=[Depends(verify_client_secret)],
)
@limiter.limit("10/minute")
async def upload_sync(request: Request, data: SyncUpload, x_ttl_hours: str | None = Header(default=None)):
    """
    Upload an encrypted payload. Each upload creates a **new datapoint** — previous
    uploads are not replaced.

    - **token**: Your UUID (only its SHA-256 hash is stored)
    - **payload**: Base64-encoded AES-256-GCM encrypted blob
    - **X-TTL-Hours** *(optional header)*: How long this datapoint should live, in hours.
      Clamped to `[1, 168]` (1h–7 days). Defaults to `DATA_TTL_HOURS` (48h) if omitted or invalid.
    """
    ttl_hours = parse_ttl_header(x_ttl_hours)
    token_hash = hash_token(data.token)
    db = await get_db()
    try:
        await purge_expired_for_token(db, token_hash)
        await check_quota(db, token_hash, data.payload)
        await db.execute(
            "INSERT INTO sync_data (token_hash, payload, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token_hash, data.payload, now_utc(), expiry_utc(ttl_hours)),
        )
        await db.commit()
    finally:
        await db.close()

    return StatusResponse(status="ok", message=f"Stored. Expires in {ttl_hours}h.")


@app.get(
    "/sync/{token}",
    response_model=SyncResponse,
    summary="Fetch all datapoints",
    tags=["Sync"],
    dependencies=[Depends(verify_client_secret)],
)
@limiter.limit("30/minute")
async def fetch_sync(request: Request, token: str):
    """
    Retrieve all non-expired datapoints for a token, ordered oldest → newest.

    Returns an array of encrypted blobs — decryption happens on your OpenClaw instance.
    Returns **404** if no data exists or everything has expired.
    """
    token_hash = hash_token(token)
    db = await get_db()
    try:
        await purge_expired_for_token(db, token_hash)
        async with db.execute(
            "SELECT payload, created_at, expires_at FROM sync_data "
            "WHERE token_hash = ? AND expires_at > ? ORDER BY created_at ASC",
            (token_hash, now_utc()),
        ) as cursor:
            rows = await cursor.fetchall()
    finally:
        await db.close()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="No data found for this token (may have expired or never been uploaded).",
        )

    return SyncResponse(
        count=len(rows),
        datapoints=[
            Datapoint(payload=row[0], created_at=row[1], expires_at=row[2])
            for row in rows
        ],
    )


@app.get(
    "/sync/{token}/count",
    response_model=CountResponse,
    summary="Count datapoints for a token",
    tags=["Sync"],
    dependencies=[Depends(verify_client_secret)],
)
@limiter.limit("30/minute")
async def count_sync(request: Request, token: str):
    """
    Returns the number of non-expired datapoints stored for a token,
    plus the oldest and newest timestamps. Useful for the app dashboard.
    No payload data is returned.
    """
    token_hash = hash_token(token)
    db = await get_db()
    try:
        await purge_expired_for_token(db, token_hash)
        async with db.execute(
            "SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM sync_data "
            "WHERE token_hash = ? AND expires_at > ?",
            (token_hash, now_utc()),
        ) as cursor:
            row = await cursor.fetchone()
    finally:
        await db.close()

    count, oldest, newest = row if row else (0, None, None)
    return CountResponse(count=count, oldest=oldest, newest=newest)


@app.delete(
    "/sync/{token}",
    response_model=StatusResponse,
    summary="Delete all datapoints for a token",
    tags=["Sync"],
    dependencies=[Depends(verify_client_secret)],
)
@limiter.limit("10/minute")
async def delete_sync(request: Request, token: str):
    """
    Delete **all** datapoints for a token (the app's Nuke button).
    """
    token_hash = hash_token(token)
    db = await get_db()
    try:
        result = await db.execute(
            "DELETE FROM sync_data WHERE token_hash = ?", (token_hash,)
        )
        await db.commit()
        deleted = result.rowcount
    finally:
        await db.close()

    if deleted == 0:
        raise HTTPException(status_code=404, detail="No data found for this token.")

    return StatusResponse(status="ok", message=f"Deleted {deleted} datapoint(s).")


@app.get(
    "/cleanup",
    response_model=StatusResponse,
    summary="Purge all expired datapoints",
    tags=["System"],
)
async def cleanup():
    """
    Purge all expired datapoints across all tokens.
    Safe to call via cron — returns number of rows deleted.
    No auth required (deletes only expired data, exposes nothing).
    """
    db = await get_db()
    try:
        deleted = await purge_all_expired(db)
    finally:
        await db.close()

    return StatusResponse(status="ok", message=f"Purged {deleted} expired datapoint(s).")


@app.get(
    "/health",
    response_model=StatusResponse,
    summary="Health check",
    tags=["System"],
)
async def health_check():
    """Returns 200 OK if the server is running. Use for uptime monitoring."""
    return StatusResponse(status="ok", message="ClawPulse is running")
