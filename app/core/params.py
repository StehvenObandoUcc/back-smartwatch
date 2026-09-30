"""Parámetros de ruta con IDs.

Un ID mal formado se trata como "no existe" (404), no como 422: el contrato solo documenta 404
para esas rutas y, además, así no se distingue un ID inválido de uno ajeno.
"""

import uuid
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, Path

from app.core.errors import NotFoundError


def uuid_path(name: str, not_found: Callable[[], NotFoundError]) -> Any:
    def parse(value: Annotated[str, Path(alias=name)]) -> uuid.UUID:
        try:
            return uuid.UUID(value)
        except ValueError:
            raise not_found() from None

    return Depends(parse)
