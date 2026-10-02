from typing import Literal

from pydantic import EmailStr, Field

from app.core.schemas import ApiModel, DisplayName, Timezone
from app.modules.users.schemas import Locale, Role, UserOut


class RegisterRequest(ApiModel):
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=10, max_length=128)
    display_name: DisplayName
    role: Role
    timezone: Timezone
    locale: Locale = "es"


class LoginRequest(ApiModel):
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=1, max_length=128)


class TokenBody(ApiModel):
    token: str = Field(min_length=16, max_length=512)


class ForgotPasswordRequest(ApiModel):
    email: EmailStr = Field(max_length=254)


class ResetPasswordRequest(ApiModel):
    token: str = Field(min_length=16, max_length=512)
    new_password: str = Field(min_length=10, max_length=128)


class AccessTokenOut(ApiModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105 (no es un secreto)
    expires_in: int


class TokenPairOut(AccessTokenOut):
    refresh_token: str


class AuthSessionOut(ApiModel):
    user: UserOut
    tokens: AccessTokenOut
