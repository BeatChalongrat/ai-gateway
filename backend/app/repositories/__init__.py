"""Repository layer — per-session SQLAlchemy query wrappers.

Keeps DB access narrow and testable so service-layer code can talk
business logic (encryption, round-robin, validation, etc.) without
writing inline SELECT statements.

Only one repository is implemented today (``ProviderKeyRepository``)
as a pilot for the pattern. Additional repositories should follow the
same shape: inherit from ``BaseRepository``, bind a single ORM model,
own every query touching that model's table.
"""

from app.repositories.base_repository import BaseRepository
from app.repositories.provider_key_repository import ProviderKeyRepository

__all__ = ["BaseRepository", "ProviderKeyRepository"]
