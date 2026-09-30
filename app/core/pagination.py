"""Paginación por cursor (keyset sobre `created_at`, `id`)."""

import base64
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends, Query
from fastapi.exceptions import RequestValidationError
from sqlalchemy import ColumnElement, and_, or_
from sqlalchemy.orm import InstrumentedAttribute

Cursor = tuple[datetime, uuid.UUID]


@dataclass(frozen=True, slots=True)
class Page:
    cursor: Cursor | None
    limit: int


def encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> Cursor:
    padded = cursor + "=" * (-len(cursor) % 4)
    created_at, row_id = base64.urlsafe_b64decode(padded).decode().split("|")
    parsed = datetime.fromisoformat(created_at)
    if parsed.tzinfo is None:
        raise ValueError("cursor sin zona horaria")
    return parsed, uuid.UUID(row_id)


def _page(
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page:
    if cursor is None:
        return Page(cursor=None, limit=limit)
    try:
        return Page(cursor=_decode_cursor(cursor), limit=limit)
    except (ValueError, UnicodeDecodeError):
        raise RequestValidationError(
            [{"loc": ("query", "cursor"), "msg": "Cursor no válido", "type": "value_error"}]
        ) from None


PageDep = Annotated[Page, Depends(_page)]


def after_cursor(
    created_at: InstrumentedAttribute[datetime],
    row_id: InstrumentedAttribute[uuid.UUID],
    cursor: Cursor | None,
) -> ColumnElement[bool] | None:
    """Condición para las filas posteriores al cursor en orden (created_at, id)."""
    if cursor is None:
        return None
    at, last_id = cursor
    return or_(created_at > at, and_(created_at == at, row_id > last_id))


def next_cursor[T](
    rows: list[T], limit: int, key: Callable[[T], Cursor]
) -> tuple[list[T], str | None]:
    """Recibe `limit + 1` filas: devuelve la página y el cursor de la siguiente (o None)."""
    if len(rows) <= limit:
        return rows, None
    page = rows[:limit]
    created_at, row_id = key(page[-1])
    return page, encode_cursor(created_at, row_id)
