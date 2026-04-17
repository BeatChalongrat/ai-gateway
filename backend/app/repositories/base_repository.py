"""Generic base repository.

Carves out a thin boundary between services (business logic) and
SQLAlchemy session access. Subclasses bind ``model_class`` to a
specific ORM type and inherit simple by-id / add / delete primitives.

Session lifecycle is owned by the caller — repositories accept an
existing ``Session`` in ``__init__`` and never open / close one
themselves. This matches the existing codebase convention where
sessions come from ``Depends(get_db_session)`` at the route layer.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from sqlalchemy.orm import Session

from app.core.db import Base

T = TypeVar("T", bound=Base)


class BaseRepository(Generic[T]):
    """Per-session repository over a single ORM model.

    Subclasses must set a ``model_class`` attribute pointing at the
    ORM class they manage — the generic ``T`` alone isn't enough at
    runtime because Python's generics are erased.
    """

    model_class: type[T]

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, id: str) -> T | None:
        """Return the row with the given primary key, or None."""
        return self.session.get(self.model_class, id)

    def add(self, obj: T) -> T:
        """Insert *obj* and flush so its primary key is populated."""
        self.session.add(obj)
        self.session.flush()
        return obj

    def delete_obj(self, obj: T) -> None:
        """Hard-delete *obj* and flush so subsequent queries see it gone."""
        self.session.delete(obj)
        self.session.flush()
