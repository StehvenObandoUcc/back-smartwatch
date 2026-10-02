from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request, Response, status

from app.core.auth import CurrentUserDep
from app.core.deps import (
    AccessTokensDep,
    ClientIpDep,
    PasswordsDep,
    RateLimiterDep,
    SessionDep,
    SettingsDep,
)
from app.core.errors import UnauthorizedError, instance_of, problem_from
from app.modules.auth.account import AccountService
from app.modules.auth.schemas import (
    AccessTokenOut,
    AuthSessionOut,
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenBody,
)
from app.modules.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "__Secure-refresh-token"
REFRESH_COOKIE_PATH = "/auth"

# Sin max_length: una cookie anómala es una sesión inválida (401), no un 422.
RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE)]


def get_auth_service(
    session: SessionDep,
    passwords: PasswordsDep,
    access_tokens: AccessTokensDep,
    settings: SettingsDep,
) -> AuthService:
    return AuthService(session, passwords, access_tokens, settings)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def get_account_service(
    session: SessionDep, passwords: PasswordsDep, settings: SettingsDep
) -> AccountService:
    return AccountService(session, passwords, settings)


AccountServiceDep = Annotated[AccountService, Depends(get_account_service)]


def _set_refresh_cookie(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=max_age,
        path=REFRESH_COOKIE_PATH,
        secure=True,
        httponly=True,
        samesite="strict",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, secure=True, httponly=True, samesite="strict"
    )


@router.post(
    "/register",
    operation_id="register",
    summary="Crear cuenta",
    status_code=status.HTTP_201_CREATED,
)
async def register(
    data: RegisterRequest,
    response: Response,
    service: AuthServiceDep,
    limiter: RateLimiterDep,
    ip: ClientIpDep,
    settings: SettingsDep,
) -> AuthSessionOut:
    await limiter.hit("register:ip", ip, settings.rate_register_ip)
    result = await service.register(data)
    _set_refresh_cookie(response, result.refresh_token, settings.user_refresh_ttl_days * 86400)
    return result.body


@router.post("/login", operation_id="login", summary="Iniciar sesión")
async def login(
    data: LoginRequest,
    response: Response,
    service: AuthServiceDep,
    limiter: RateLimiterDep,
    ip: ClientIpDep,
    settings: SettingsDep,
) -> AuthSessionOut:
    await limiter.hit("login:ip", ip, settings.rate_login_ip)
    await limiter.hit("login:email", data.email.lower(), settings.rate_login_email)
    result = await service.login(data)
    _set_refresh_cookie(response, result.refresh_token, settings.user_refresh_ttl_days * 86400)
    return result.body


@router.post(
    "/refresh",
    operation_id="refreshTokens",
    summary="Renovar la sesión web",
    response_model=AccessTokenOut,
)
async def refresh(
    request: Request,
    response: Response,
    service: AuthServiceDep,
    settings: SettingsDep,
    refresh_token: RefreshCookie = None,
) -> AccessTokenOut | Response:
    try:
        result = await service.refresh(refresh_token)
    except UnauthorizedError as exc:
        # Sin sesión válida: además del 401, se borra la cookie.
        problem = problem_from(exc, instance_of(request))
        _clear_refresh_cookie(problem)
        return problem
    _set_refresh_cookie(response, result.refresh_token, settings.user_refresh_ttl_days * 86400)
    return result.body


@router.post(
    "/logout",
    operation_id="logout",
    summary="Cerrar sesión web",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def logout(service: AuthServiceDep, refresh_token: RefreshCookie = None) -> Response:
    await service.logout(refresh_token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(response)
    return response


@router.post(
    "/verify-email",
    operation_id="verifyEmail",
    summary="Verificar el correo con el token recibido",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def verify_email(
    data: TokenBody,
    service: AccountServiceDep,
    limiter: RateLimiterDep,
    ip: ClientIpDep,
    settings: SettingsDep,
) -> Response:
    await limiter.hit("verify-email:ip", ip, settings.rate_verify_email_ip)
    await service.verify_email(data.token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/resend-verification",
    operation_id="resendVerification",
    summary="Reenviar el correo de verificación",
    status_code=status.HTTP_202_ACCEPTED,
)
async def resend_verification(
    user: CurrentUserDep,
    service: AccountServiceDep,
    limiter: RateLimiterDep,
    settings: SettingsDep,
) -> Response:
    await limiter.hit(
        "resend-verification:user", str(user.id), settings.rate_resend_verification_user
    )
    queued = await service.resend_verification(user.id)
    code = status.HTTP_202_ACCEPTED if queued else status.HTTP_204_NO_CONTENT
    return Response(status_code=code)


@router.post(
    "/forgot-password",
    operation_id="forgotPassword",
    summary="Pedir el correo de recuperación de contraseña",
    status_code=status.HTTP_202_ACCEPTED,
)
async def forgot_password(
    data: ForgotPasswordRequest,
    service: AccountServiceDep,
    limiter: RateLimiterDep,
    ip: ClientIpDep,
    settings: SettingsDep,
) -> Response:
    await limiter.hit("forgot-password:ip", ip, settings.rate_forgot_password_ip)
    await limiter.hit(
        "forgot-password:email", data.email.lower(), settings.rate_forgot_password_email
    )
    await service.forgot_password(data.email)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/reset-password",
    operation_id="resetPassword",
    summary="Fijar una contraseña nueva con el token de recuperación",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def reset_password(
    data: ResetPasswordRequest,
    service: AccountServiceDep,
    limiter: RateLimiterDep,
    ip: ClientIpDep,
    settings: SettingsDep,
) -> Response:
    await limiter.hit("reset-password:ip", ip, settings.rate_reset_password_ip)
    await service.reset_password(data.token, data.new_password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
