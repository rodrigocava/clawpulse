# ClawPulse 🦞🔗

> Connect your phone's context to your OpenClaw AI agent — privately.

ClawPulse is an **encrypted data relay** between the ClawPulse mobile app (iOS & Android) and any OpenClaw instance. Your health, activity, and context data is encrypted before it ever leaves your device. The server stores only opaque blobs it cannot read.

---

## Quick start

```bash
git clone https://github.com/rodrigocava/clawpulse.git
cd clawpulse
make run
```

Server starts on **http://localhost:6413**  
API docs at **http://localhost:6413/docs**

> **AI agent?** Read [AGENTS.md](./AGENTS.md) for concise deploy + operate instructions.

---

## How it works

```
Mobile App  →  encrypt data locally  →  POST /sync  →  opaque blob stored on server
OpenClaw    →  GET /sync/{token}     →  decrypt locally  →  analyze + act
```

The server is **dumb by design** — it stores only encrypted blobs it cannot read.  
Even with full database access, no personal data is recoverable without your password.

---

## API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/sync` | Upload encrypted payload |
| `GET` | `/sync/{token}` | Fetch latest payload |
| `DELETE` | `/sync/{token}` | Delete payload after processing |
| `GET` | `/health` | Health check |
| `GET` | `/docs` | Interactive Swagger UI |
| `GET` | `/redoc` | ReDoc API reference |

---

## Configuration

```bash
cp .env.example .env
```

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `6413` | Port to expose |
| `DATA_TTL_HOURS` | `48` | Hours before payloads auto-expire |
| `MAX_PAYLOAD_BYTES` | `10485760` | Max upload size (10MB) |

---

## Make commands

```bash
make run      # Start (production, Docker)
make dev      # Start (local, auto-reload)
make stop     # Stop
make logs     # Tail logs
make test     # Health check
make update   # Pull latest + restart
```

---

## Deploy on a server

Two options — pick whichever fits your setup:

### Option A — Clone & compose (includes build step)

```bash
git clone https://github.com/rodrigocava/clawpulse.git
cd clawpulse
make run        # builds image locally + starts
make test       # verify it's healthy
```

Update later:
```bash
make update     # git pull + rebuild + restart
```

### Option B — Standalone (no clone, pre-built image from GHCR)

```bash
# Download just the compose file
curl -O https://raw.githubusercontent.com/rodrigocava/clawpulse/main/docker-compose.ghcr.yml

# Start (pulls ghcr.io/rodrigocava/clawpulse:latest automatically)
docker compose -f docker-compose.ghcr.yml up -d

# Verify
curl http://localhost:6413/health
```

Update later:
```bash
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
```

> The GHCR image is built and pushed automatically on every commit to `main` via GitHub Actions. Free for public repos.

Then point a reverse proxy or Cloudflare Tunnel at port 6413.

---

## Project components

| Component | Status | Description |
|-----------|--------|-------------|
| **ClawPulse Server** | ✅ Ready | This repo — self-hostable relay |
| **ClawPulse iOS** | 🔜 Soon | Swift/SwiftUI + HealthKit |
| **ClawPulse Android** | 📅 Roadmap | — |
| **ClawPulse Skill** | 🔜 Soon | OpenClaw integration |

---

## Data the app shares (V1)

- Sleep analysis (stages + duration)
- Heart Rate Variability (HRV)
- Resting heart rate
- Steps + active energy
- Activity type (walking, running, driving…)
- Focus mode + battery level

Schema is **versioned and evolvable** — new fields and modules are additive and backward compatible.

---

## Payload schema

```json
{
  "v": 1,
  "sent_at": "2026-02-18T10:00:00Z",
  "device": { "platform": "ios", "app_version": "1.0.0" },
  "modules": {
    "health": {
      "sleep": [{ "ts_start": "...", "ts_end": "...", "stage": "deep|rem|core|awake" }],
      "hrv": [{ "ts": "...", "value": 42, "unit": "ms" }],
      "heart_rate": [{ "ts": "...", "value": 68, "unit": "bpm" }],
      "steps": { "date": "2026-02-17", "count": 8432 },
      "active_energy": { "date": "2026-02-17", "value": 420, "unit": "kcal" }
    },
    "context": {
      "battery": 0.82,
      "focus_mode": "sleep",
      "activity_type": "stationary"
    }
  }
}
```

---

## Self-host vs hosted

| | Self-host | Hosted (cava.industries) |
|--|-----------|--------------------------|
| Cost | Free | ~$12-20/year |
| Setup | ~5 min with Docker | Zero config |
| Privacy | Your server, your rules | Encrypted blobs only — we can't read your data |
| Control | Full | Standard |

---

## License

MIT — fork it, self-host it, build on it.
