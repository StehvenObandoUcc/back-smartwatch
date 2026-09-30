import re
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    """Base de los esquemas públicos: camelCase en JSON, snake_case en Python."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)


# Nombres IANA: letras, dígitos y _+-, separados por "/". Filtra antes de tocar el sistema de
# archivos (ZoneInfo lee ficheros y con caracteres raros lanza OSError en Windows).
_IANA_NAME = re.compile(r"[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+)*")


def _iana_timezone(value: str) -> str:
    if not _IANA_NAME.fullmatch(value):
        raise ValueError("Zona horaria IANA desconocida")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise ValueError("Zona horaria IANA desconocida") from None
    return value


Timezone = Annotated[str, Field(min_length=1, max_length=64), AfterValidator(_iana_timezone)]
DisplayName = Annotated[str, Field(min_length=1, max_length=80)]


def require_partial_update[M: ApiModel](model: M) -> M:
    """PATCH: al menos un campo y ninguno a null (el contrato no los admite)."""
    if not model.model_fields_set:
        raise ValueError("Indica al menos un campo")
    if any(getattr(model, name) is None for name in model.model_fields_set):
        raise ValueError("Los campos no admiten null")
    return model
