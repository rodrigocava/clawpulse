.PHONY: run dev stop logs test update help

## Start the server (production, detached)
run:
	docker compose up -d --build
	@echo "✅ ClawPulse running on http://localhost:$${PORT:-6413}"
	@echo "📖 API docs: http://localhost:$${PORT:-6413}/docs"

## Start for local development (auto-reload)
dev:
	pip install -r requirements.txt
	uvicorn main:app --reload --port $${PORT:-6413}

## Stop the server
stop:
	docker compose down

## Tail server logs
logs:
	docker compose logs -f

## Check server health
test:
	@curl -sf http://localhost:$${PORT:-6413}/health && echo "✅ ClawPulse is healthy" || echo "❌ ClawPulse is not responding"

## Pull latest and restart
update:
	git pull
	docker compose up -d --build
	@echo "✅ Updated and restarted"

## Show this help
help:
	@grep -E '^##' Makefile | sed 's/## //'
