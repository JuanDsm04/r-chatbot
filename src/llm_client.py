"""Cliente de la API de Anthropic.

Envia una conversacion junto con el catalogo de herramientas MCP y devuelve la
respuesta del modelo. El historial y el ciclo de uso de herramientas viven en
session.py; aqui solo se arma y se manda la peticion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from anthropic import APIError, AsyncAnthropic
from anthropic.types import Message

# Prompt de sistema que se manda en cada peticion.
DEFAULT_SYSTEM_PROMPT = """Eres el asistente de un chatbot de consola que funciona como anfitrion \
MCP (Model Context Protocol).

Indicaciones:
- Algunas de tus herramientas vienen de servidores MCP externos. Sus nombres tienen \
el formato `<servidor>__<herramienta>`; el prefijo indica donde se ejecuta.
- Prefiere llamar a una herramienta antes que adivinar cuando la pregunta trate de \
archivos, repositorios, bases de datos o cualquier otro estado externo. Nunca \
inventes el resultado de una herramienta.
- Si una llamada falla, lee el error, explicalo brevemente y reintenta con los \
argumentos corregidos cuando tenga sentido.
- Responde en el mismo idioma en que te escriba el usuario.
- Se concreto y breve: esto es una terminal, no una pagina web."""


class LLMError(RuntimeError):
    """Error devuelto por la API de Anthropic."""


@dataclass
class UsageStats:
    """Acumulado de tokens consumidos durante la sesion."""

    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0
    per_request: list[tuple[int, int]] = field(default_factory=list)

    def add(self, message: Message) -> None:
        """Suma el consumo reportado por una respuesta de la API."""
        usage = getattr(message, "usage", None)
        if usage is None:
            return
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
        self.requests += 1
        self.per_request.append((usage.input_tokens or 0, usage.output_tokens or 0))

    def __str__(self) -> str:
        return (
            f"{self.requests} llamadas a la API | "
            f"{self.input_tokens} tokens de entrada | {self.output_tokens} tokens de salida"
        )


class LLMClient:
    """Envuelve la API de mensajes de Anthropic."""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int = 2048,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.system_prompt = system_prompt
        self.usage = UsageStats()

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> Message:
        """Pide una respuesta al modelo.

        Devuelve el Message tal cual para que quien llama pueda revisar el
        stop_reason y los bloques de contenido. Si no hay herramientas se omite
        el parametro, porque la API rechaza una lista vacia.
        """
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": self.system_prompt,
            "messages": messages,
        }
        if tools:
            request["tools"] = tools

        try:
            response = await self._client.messages.create(**request)
        except APIError as exc:
            raise LLMError(f"error de la API de Anthropic: {exc}") from exc

        self.usage.add(response)
        return response

    async def aclose(self) -> None:
        """Cierra las conexiones HTTP del cliente."""
        await self._client.close()

    @staticmethod
    def extract_text(message: Message) -> str:
        """Junta los bloques de texto de una respuesta, ignorando los de tool_use."""
        return "\n".join(
            block.text for block in message.content if getattr(block, "type", None) == "text"
        ).strip()
