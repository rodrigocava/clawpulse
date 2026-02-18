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

import aiosqlite
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# ── Config ────────────────────────────────────────────────────────────────────

DATABASE_PATH = os.getenv("DATABASE_PATH", "sync.db")
DATA_TTL_HOURS = int(os.getenv("DATA_TTL_HOURS", "48"))
MAX_PAYLOAD_BYTES = int(os.getenv("MAX_PAYLOAD_BYTES", str(10 * 1024 * 1024)))  # 10MB

# ── App ───────────────────────────────────────────────────────────────────────

def get_client_ip(request: Request) -> str:
    """Use CF-Connecting-IP when behind Cloudflare, fall back to remote address."""
    return request.headers.get("CF-Connecting-IP") or get_remote_address(request)

limiter = Limiter(key_func=get_client_ip)

app = FastAPI(
    title="ClawPulse",
    description="""
Encrypted data relay for the **ClawPulse** mobile app (iOS & Android).

## How it works

1. The ClawPulse app encrypts your phone context data client-side
2. The encrypted blob is uploaded here (server never sees plaintext)
3. Your OpenClaw instance fetches the blob and decrypts it locally
4. Analysis and insights happen entirely on your own infrastructure

## Privacy

The server stores only opaque encrypted blobs keyed by a SHA-256 hash of your token.
Even with full database access, no personal data is recoverable without your password.

## Data retention

All payloads expire automatically after **48 hours**.

## Self-hosting

The server is open source. Run your own at: https://github.com/rodrigocava/clawpulse
""",
    version="1.0.0",
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
    payload: str  # base64-encoded encrypted blob

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


class SyncResponse(BaseModel):
    payload: str
    updated_at: str


class StatusResponse(BaseModel):
    status: str
    message: str


# ── DB helpers ────────────────────────────────────────────────────────────────


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DATABASE_PATH)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS sync_data (
            token_hash  TEXT PRIMARY KEY,
            payload     TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            expires_at  TEXT NOT NULL
        )
    """)
    await db.commit()
    return db


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def expiry_utc() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=DATA_TTL_HOURS)).isoformat()


async def purge_expired() -> None:
    db = await aiosqlite.connect(DATABASE_PATH)
    await db.execute("DELETE FROM sync_data WHERE expires_at < ?", (now_utc(),))
    await db.commit()
    await db.close()


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
)
@limiter.limit("10/minute")
async def upload_sync(request: Request, data: SyncUpload):
    """
    Upload an encrypted payload for later retrieval by OpenClaw.

    - **token**: Your secret token (only a SHA-256 hash is stored — never the raw value)
    - **payload**: Base64-encoded, client-side encrypted blob

    Uploading again with the same token **replaces** the previous payload.
    Data expires automatically after 48 hours.
    """
    if len(data.payload.encode()) > MAX_PAYLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Payload exceeds size limit")

    token_hash = hash_token(data.token)
    now = now_utc()
    exp = expiry_utc()

    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO sync_data (token_hash, payload, created_at, updated_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(token_hash) DO UPDATE SET
                payload    = excluded.payload,
                updated_at = excluded.updated_at,
                expires_at = excluded.expires_at
            """,
            (token_hash, data.payload, now, now, exp),
        )
        await db.commit()
    finally:
        await db.close()

    await purge_expired()
    return StatusResponse(status="ok", message=f"Stored. Expires in {DATA_TTL_HOURS}h.")


@app.get(
    "/sync/{token}",
    response_model=SyncResponse,
    summary="Fetch encrypted payload",
    tags=["Sync"],
)
@limiter.limit("30/minute")
async def fetch_sync(request: Request, token: str):
    """
    Retrieve the latest encrypted payload for a token.

    Returns the blob as-is — decryption happens on your OpenClaw instance.
    Returns **404** if no data exists or if it has expired.
    """
    token_hash = hash_token(token)

    db = await get_db()
    try:
        async with db.execute(
            "SELECT payload, updated_at FROM sync_data WHERE token_hash = ? AND expires_at > ?",
            (token_hash, now_utc()),
        ) as cursor:
            row = await cursor.fetchone()
    finally:
        await db.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail="No data found for this token (may have expired or never been uploaded)",
        )

    return SyncResponse(payload=row[0], updated_at=row[1])


@app.delete(
    "/sync/{token}",
    response_model=StatusResponse,
    summary="Delete payload",
    tags=["Sync"],
)
@limiter.limit("10/minute")
async def delete_sync(request: Request, token: str):
    """
    Delete the payload for a token.

    Call this after OpenClaw has successfully fetched and processed the data
    to keep your storage footprint minimal.
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
        raise HTTPException(status_code=404, detail="No data found for this token")

    return StatusResponse(status="ok", message="Payload deleted.")


@app.get(
    "/health",
    response_model=StatusResponse,
    summary="Health check",
    tags=["System"],
)
async def health_check():
    """Returns 200 OK if the server is running. Use for uptime monitoring."""
    return StatusResponse(status="ok", message="ClawPulse is running")
