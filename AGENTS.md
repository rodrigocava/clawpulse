# AGENTS.md — Instructions for AI Agents

This file tells you everything you need to deploy and operate ClawPulse autonomously.

## What is ClawPulse?

ClawPulse is a privacy-first encrypted data relay server. Mobile apps post encrypted health/context blobs to it; OpenClaw instances fetch and decrypt them locally. **The server never sees plaintext.**

## Requirements

- Docker + Docker Compose (v2+)
- Port 6413 available (or set `PORT` in `.env`)
- At least 100MB disk space

## Deploy in 3 steps (Option A — clone + build)

```bash
git clone https://github.com/rodrigocava/clawpulse.git
cd clawpulse
make run
```

## Deploy in 2 steps (Option B — pre-built image, no clone needed)

```bash
curl -O https://raw.githubusercontent.com/rodrigocava/clawpulse/main/docker-compose.ghcr.yml
docker compose -f docker-compose.ghcr.yml up -d
```

That's it. The server is running.

## Verify it's working

```bash
make test
# Expected: ✅ ClawPulse is healthy

curl http://localhost:6413/health
# Expected: {"status":"ok","message":"ClawPulse is running"}
```

## Configuration (optional)

```bash
cp .env.example .env
# Edit .env as needed — all values have sensible defaults
```

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `6413` | Port to expose |
| `DATA_DIR` | `./data` | Host path for SQLite database |
| `DATA_TTL_HOURS` | `48` | Hours before payloads auto-expire |
| `MAX_PAYLOAD_BYTES` | `10485760` | Max upload size (10MB) |

### cava.industries example `.env`

```env
PORT=6413
DATA_DIR=/srv/dev-disk-by-uuid-649a14d8-043c-4d63-9959-64d1bf74963b/docker_data/Config/clawpulse
DATA_TTL_HOURS=48
```

## API reference

Full interactive docs at: `http://localhost:6413/docs`

### Quick reference

```bash
BASE="http://localhost:6413"
TOKEN="your-secret-token"

# Upload encrypted payload
curl -X POST "$BASE/sync" \
  -H "Content-Type: application/json" \
  -d "{\"token\": \"$TOKEN\", \"payload\": \"<base64-encrypted-blob>\"}"

# Fetch payload
curl "$BASE/sync/$TOKEN"

# Delete payload (after processing)
curl -X DELETE "$BASE/sync/$TOKEN"
```

### Response shapes

```json
// POST /sync → 200
{"status": "ok", "message": "Stored. Expires in 48h."}

// GET /sync/{token} → 200
{"payload": "<base64-encrypted-blob>", "updated_at": "2026-02-18T10:00:00+00:00"}

// Any → 404 (token not found or expired)
{"detail": "No data found for this token (may have expired or never been uploaded)"}
```

## Update

```bash
make update
# Pulls latest from git and restarts — zero downtime build
```

## Logs

```bash
make logs
```

## Stop

```bash
make stop
```

## Data location

SQLite database is stored at `./data/sync.db` (relative to the repo root).
Back it up if needed — though data is ephemeral by design (48h TTL).

## Security notes

- Tokens are stored as SHA-256 hashes — raw tokens are never persisted
- Rate limited: 10 writes/min, 30 reads/min per IP
- All encryption happens client-side — server is a dumb relay
- For production: put behind a reverse proxy (Nginx, Caddy, Cloudflare Tunnel)
