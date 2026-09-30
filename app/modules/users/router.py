from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.auth import CurrentUserDep
from app.core.deps import SessionDep
from app.modules.consents.schemas import ConsentListOut, ConsentOut, ConsentUpdate, Purpose
from app.modules.users.schemas import UserOut, UserUpdate
from app.modules.users.service import UserService

router = APIRouter(prefix="/users", tags=["users"])


def get_user_service(session: SessionDep) -> UserService:
    return UserService(session)


UserServiceDep = Annotated[UserService, Depends(get_user_service)]


@router.get("/me", operation_id="getMe", summary="Usuario autenticado")
async def get_me(user: CurrentUserDep, service: UserServiceDep) -> UserOut:
    return await service.get_me(user.id)


@router.patch("/me", operation_id="updateMe", summary="Actualizar perfil")
async def update_me(user: CurrentUserDep, data: UserUpdate, service: UserServiceDep) -> UserOut:
    return await service.update_me(user.id, data)


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
