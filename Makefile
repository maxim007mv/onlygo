.PHONY: up down logs seed test lint demo-check
up:
	[ -f .env ] || cp .env.example .env
	docker compose up --build -d
	@echo "OnlyGo: http://localhost:8080  (demo: http://localhost:8080/?demo=1)"
down:
	docker compose down
logs:
	docker compose logs -f app
# Data is bundled in app/data (mock catalog + price aggregates); nothing to load. Kept for the standard interface.
seed:
	@echo "Seed data ships in app/data — no action needed."
test:
	DB_PATH=/tmp/onlygo-test.db PYTHONPATH=. python -m pytest -q
lint:
	ruff check . && ruff format --check .
demo-check:
	python scripts/ui_jitter_check.py --url http://localhost:8080/
