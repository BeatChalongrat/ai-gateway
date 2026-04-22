"""Integration tests run against a real Postgres + real uvicorn subprocess.

They don't use the in-memory SQLite + FastAPI-dep-override tricks from the
parent ``tests/conftest.py`` — a truly fresh database + alembic + live app
boot is the whole point. The parent conftest's autouse fixtures
(mock_resolved_api_key, stubbed ProviderDispatcher, etc.) are still active
for the httpx client that drives the subprocess app, but since we make the
subprocess talk to a *separate* DB, the in-memory one doesn't leak in.
"""
