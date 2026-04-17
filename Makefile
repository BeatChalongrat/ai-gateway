.PHONY: backend admin test lint

backend:
	cd backend && uvicorn app.main:app --reload --port 8080

admin:
	cd admin-ui && npm run dev

test:
	cd backend && pytest

lint:
	cd backend && ruff check . && black --check .
