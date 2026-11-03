from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app.core.auth import CurrentUserDep
from app.core.cookies import clear_refresh_cookie
from app.core.deps import PasswordsDep, RateLimiterDep, SessionDep, SettingsDep
from app.modules.consents.schemas import ConsentListOut, ConsentOut, ConsentUpdate, Purpose
from app.modules.users.schemas import DeleteAccountRequest, UserOut, UserUpdate
from app.modules.users.service import UserService

router = APIRouter(prefix="/users", tags=["users"])


def get_user_service(session: SessionDep, passwords: PasswordsDep) -> UserService:
    return UserService(session, passwords)


UserServiceDep = Annotated[UserService, Depends(get_user_service)]


@router.get("/me", operation_id="getMe", summary="Usuario autenticado")
async def get_me(user: CurrentUserDep, service: UserServiceDep) -> UserOut:
    return await service.get_me(user.id)


@router.patch("/me", operation_id="updateMe", summary="Actualizar perfil")
async def update_me(user: CurrentUserDep, data: UserUpdate, service: UserServiceDep) -> UserOut:
    return await service.update_me(user.id, data)


@router.delete(
    "/me",
    operation_id="deleteMe",
    summary="Eliminar la cuenta",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_me(
    data: DeleteAccountRequest,
    user: CurrentUserDep,
    service: UserServiceDep,
    limiter: RateLimiterDep,
    settings: SettingsDep,
) -> Response:
    # Antes de verificar la contraseña: limita los intentos de adivinarla con un token robado.
    await limiter.hit("delete_account:user", str(user.id), settings.rate_delete_account_user)
    await service.delete_me(user.id, data.password)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_refresh_cookie(response)
    return response


@router.get("/me/consents", operation_id="listMyConsents", summary="Consentimientos del usuario")
async def list_my_consents(user: CurrentUserDep, service: UserServiceDep) -> ConsentListOut:
    return await service.list_my_consents(user.id)


@router.put(
    "/me/consents/{purpose}",
    operation_id="setMyConsent",
    summary="Otorgar o retirar un consentimiento propio",
)
async def set_my_consent(
    purpose: Purpose, data: ConsentUpdate, user: CurrentUserDep, service: UserServiceDep
) -> ConsentOut:
    return await service.set_my_consent(user.id, purpose, data)
