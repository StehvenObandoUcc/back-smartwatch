"""Proveedor del modelo de IA. DeepSeek (SDK `openai`) en producción; uno simulado sin red."""

import json
import re
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from openai import AsyncOpenAI

from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class Turn:
    role: str  # "user" | "assistant"
    content: str


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    arguments: str  # JSON tal como lo escribió el modelo: se valida antes de usarlo


@dataclass(frozen=True, slots=True)
class StreamEvent:
    text: str | None = None
    usage: Usage | None = None
    tool_call: ToolCall | None = None


class ProviderError(Exception):
    """El proveedor falló. `reason` es solo el tipo de error: nunca texto del usuario ni claves."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ChatProvider(Protocol):
    def stream(
        self,
        system: str,
        turns: list[Turn],
        *,
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]: ...

    async def aclose(self) -> None: ...


class DeepSeekProvider:
    """`deepseek-flash` por la API compatible con OpenAI (`base_url` de DeepSeek)."""

    def __init__(self, settings: Settings, http_client: Any = None) -> None:
        key = settings.deepseek_api_key
        if key is None:  # Settings ya lo impide; por si alguien lo construye a mano
            raise RuntimeError("Falta DEEPSEEK_API_KEY")
        self._model = settings.deepseek_model
        self._client = AsyncOpenAI(
            api_key=key.get_secret_value(),
            base_url=settings.deepseek_base_url,
            timeout=settings.chat_timeout_seconds,
            max_retries=0,  # un reintento oculto doblaría la espera del reloj
            http_client=http_client,
        )

    async def stream(
        self,
        system: str,
        turns: list[Turn],
        *,
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        messages: Any = [{"role": "system", "content": system}] + [
            {"role": t.role, "content": t.content} for t in turns
        ]
        extra: Any = {"tools": tools} if tools else {}
        # Las llamadas a herramientas llegan troceadas: nombre y argumentos por índice.
        calls: dict[int, list[str]] = {}
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.3,
                stream=True,
                stream_options={"include_usage": True},
                **extra,
            )
            async for chunk in response:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        yield StreamEvent(text=delta.content)
                    for call in delta.tool_calls or []:
                        parts = calls.setdefault(call.index, ["", ""])
                        if call.function:
                            parts[0] += call.function.name or ""
                            parts[1] += call.function.arguments or ""
                if chunk.usage is not None:
                    yield StreamEvent(
                        usage=Usage(chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
                    )
        except Exception as exc:
            # Cualquier fallo del SDK o de la red; solo el tipo, nunca su mensaje.
            raise ProviderError(type(exc).__name__) from None
        for name, arguments in calls.values():
            yield StreamEvent(tool_call=ToolCall(name, arguments))

    async def aclose(self) -> None:
        await self._client.close()


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


_FAKE_MEDICATION = re.compile(r"^- \[([0-9a-f-]{36})\] ([^,]+),", re.MULTILINE)
_FAKE_TIME = re.compile(r"\b([01][0-9]|2[0-3]):([0-5][0-9])\b")


def _fake_add_schedule(system: str, question: str) -> ToolCall | None:
    """«agrega las 14:00 a la metformina»: hora HH:MM y el nombre de un medicamento de la lista."""
    times = [f"{h}:{m}" for h, m in _FAKE_TIME.findall(question)]
    for medication_id, name in _FAKE_MEDICATION.findall(system):
        if times and _plain(name) in _plain(question):
            return ToolCall(
                "add_schedule", json.dumps({"medicationId": medication_id, "times": times})
            )
    return None


class FakeChatProvider:
    """Solo desarrollo y pruebas: responde sin red ni clave, repitiendo la pregunta. Si tiene la
    herramienta `add_schedule` y la pregunta trae una hora y uno de los medicamentos, la usa."""

    async def stream(
        self,
        system: str,
        turns: list[Turn],
        *,
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        question = turns[-1].content if turns else ""
        if any(tool["function"]["name"] == "add_schedule" for tool in tools or []):
            call = _fake_add_schedule(system, question)
            if call is not None:
                yield StreamEvent(tool_call=call)
                yield StreamEvent(usage=Usage(len(system) // 4, 1))
                return
        words = f"Respuesta simulada a: {question}".split(" ")
        for index, word in enumerate(words):
            yield StreamEvent(text=word + (" " if index < len(words) - 1 else ""))
        yield StreamEvent(usage=Usage(len(system) // 4, len(words)))

    async def aclose(self) -> None:
        return None


def build_provider(settings: Settings) -> ChatProvider:
    if settings.chat_provider == "deepseek":
        return DeepSeekProvider(settings)
    return FakeChatProvider()
