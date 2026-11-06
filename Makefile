.PHONY: dev-infra migrate test lint backend worker frontend demo-gazetteer

dev-infra:        ## start postgres + redis
	docker compose up -d postgres redis

migrate:
	cd backend && .venv/bin/alembic upgrade head

test:
	cd backend && .venv/bin/pytest -q
	cd frontend && npm run -s typecheck && npm run -s build

lint:
	cd backend && .venv/bin/ruff check app tests
	cd frontend && npm run -s lint

backend:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

worker:
	cd backend && .venv/bin/arq app.worker.WorkerSettings

frontend:
	cd frontend && npm run dev

demo-gazetteer:   ## tiny built-in gazetteer for development without GeoNames access
	cd backend && .venv/bin/python -m app.cli load-demo-gazetteer
