from typing import Literal

from app.core.schemas import ApiModel


class HealthOut(ApiModel):
    status: Literal["ok"] = "ok"


class ReadinessChecks(ApiModel):
    database: Literal["ok"] = "ok"
    redis: Literal["ok"] = "ok"


class ReadinessOut(ApiModel):
    status: Literal["ok"] = "ok"
    checks: ReadinessChecks
