import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.core.auth import CurrentDeviceDep, CurrentUserDep
from app.core.deps import RateLimiterDep, SessionDep, SettingsDep
from app.core.params import uuid_path
from app.modules.chat.schemas import ChatRequest, WatchChatReply
from app.modules.chat.service import ChatService
from app.modules.patients.service import patient_not_found

router = APIRouter(tags=["chat"])

PatientIdPath = Annotated[uuid.UUID, uuid_path("patientId", patient_not_found)]


def get_chat_service(
    request: Request, session: SessionDep, settings: SettingsDep, limiter: RateLimiterDep
) -> ChatService:
    return ChatService(
        session,
        request.app.state.session_factory,
        settings,
        request.app.state.chat_provider,
        limiter,
    )


ServiceDep = Annotated[ChatService, Depends(get_chat_service)]


@router.post(
    "/patients/{patientId}/chat/messages",
    operation_id="sendChatMessage",
    summary="Preguntar al asistente sobre el plan de un paciente (web, SSE)",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "Flujo SSE."}},
)
async def send_chat_message(
    patient_id: PatientIdPath, data: ChatRequest, user: CurrentUserDep, service: ServiceDep
) -> StreamingResponse:
    events = await service.start_web(user, patient_id, data)
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/devices/me/chat/messages",
    operation_id="sendMyChatMessage",
    summary="Preguntar al asistente desde el reloj (respuesta corta)",
)
async def send_my_chat_message(
    data: ChatRequest, device: CurrentDeviceDep, service: ServiceDep
) -> WatchChatReply:
    return await service.reply_watch(device, data)
