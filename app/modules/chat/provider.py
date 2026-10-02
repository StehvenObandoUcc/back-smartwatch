"""Proveedor del modelo de IA. DeepSeek (SDK `openai`) en producción; uno simulado sin red."""

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
class StreamEvent:
    text: str | None = None
    usage: Usage | None = None


class ProviderError(Exception):
    """El proveedor falló. `reason` es solo el tipo de error: nunca texto del usuario ni claves."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ChatProvider(Protocol):
    def stream(
        self, system: str, turns: list[Turn], *, max_tokens: int
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
        self, system: str, turns: list[Turn], *, max_tokens: int
    ) -> AsyncIterator[StreamEvent]:
        messages: Any = [{"role": "system", "content": system}] + [
            {"role": t.role, "content": t.content} for t in turns
        ]
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.3,
                stream=True,
                stream_options={"include_usage": True},
            )
            async for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield StreamEvent(text=chunk.choices[0].delta.content)
                if chunk.usage is not None:
                    yield StreamEvent(
                        usage=Usage(chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
                    )
        except Exception as exc:
            # Cualquier fallo del SDK o de la red; solo el tipo, nunca su mensaje.
            raise ProviderError(type(exc).__name__) from None

    async def aclose(self) -> None:
        await self._client.close()


class FakeChatProvider:
    """Solo desarrollo y pruebas: responde sin red ni clave, repitiendo la pregunta."""

    async def stream(
        self, system: str, turns: list[Turn], *, max_tokens: int
    ) -> AsyncIterator[StreamEvent]:
        question = turns[-1].content if turns else ""
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
