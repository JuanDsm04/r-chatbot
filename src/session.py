"""Contexto de la conversacion y ciclo de uso de herramientas.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .llm_client import LLMClient
from .mcp_manager import MCPManager

# Tope de rondas de herramientas por turno, para evitar un ciclo infinito.
MAX_TOOL_ROUNDS = 8

# A partir de esta cantidad de mensajes se recorta el historial por el inicio.
MAX_HISTORY_MESSAGES = 60


@dataclass
class ToolEvent:
    """Aviso de que una herramienta empezo o termino, para que la interfaz lo muestre."""

    tool: str
    arguments: dict[str, Any]
    result: str = ""
    finished: bool = False


class ChatSession:
    """Mantiene una conversacion y la ejecuta contra el LLM y las herramientas MCP."""

    def __init__(
        self,
        llm: LLMClient,
        mcp: MCPManager,
        on_tool_event: Callable[[ToolEvent], None] | None = None,
        max_tool_rounds: int = MAX_TOOL_ROUNDS,
    ) -> None:
        self._llm = llm
        self._mcp = mcp
        self._on_tool_event = on_tool_event
        self._max_tool_rounds = max_tool_rounds
        self.messages: list[dict[str, Any]] = []
        self.turns = 0

    def clear(self) -> None:
        """Borra la conversacion y empieza un contexto nuevo."""
        self.messages.clear()
        self.turns = 0

    def _trim(self) -> None:
        """Descarta los turnos mas viejos cuando el historial crece demasiado.

        El corte no puede separar un bloque tool_use de su tool_result, asi que se
        adelanta hasta caer en un mensaje normal del usuario.
        """
        if len(self.messages) <= MAX_HISTORY_MESSAGES:
            return

        cut = len(self.messages) - MAX_HISTORY_MESSAGES
        while cut < len(self.messages):
            message = self.messages[cut]
            # Un mensaje de usuario con contenido de texto siempre es un corte seguro.
            if message["role"] == "user" and isinstance(message["content"], str):
                break
            cut += 1
        self.messages = self.messages[cut:]

    async def ask(self, user_input: str) -> str:
        """Procesa un turno completo del usuario, incluyendo las llamadas a herramientas."""
        self.messages.append({"role": "user", "content": user_input})
        self.turns += 1

        tools = self._mcp.anthropic_tools()

        for _ in range(self._max_tool_rounds):
            response = await self._llm.complete(self.messages, tools)

            # Se guarda el turno del modelo tal cual (texto y bloques tool_use) para
            # que en la siguiente vuelta vea sus propias llamadas.
            self.messages.append(
                {
                    "role": "assistant",
                    "content": [b.model_dump(exclude_none=True) for b in response.content],
                }
            )

            if response.stop_reason != "tool_use":
                self._trim()
                return LLMClient.extract_text(response) or "(el modelo no devolvio texto)"

            tool_results = await self._run_tool_calls(response.content)
            self.messages.append({"role": "user", "content": tool_results})

        self._trim()
        return (
            f"Se alcanzo el limite de {self._max_tool_rounds} rondas de herramientas "
            "en un solo turno. Reformula la pregunta o dividela en pasos."
        )

    async def _run_tool_calls(self, content_blocks: list[Any]) -> list[dict[str, Any]]:
        """Ejecuta cada bloque tool_use y arma los resultados correspondientes.

        Cada tool_use necesita exactamente un tool_result con el mismo
        tool_use_id, o la siguiente peticion a la API es rechazada.
        """
        results: list[dict[str, Any]] = []

        for block in content_blocks:
            if getattr(block, "type", None) != "tool_use":
                continue

            arguments = block.input if isinstance(block.input, dict) else {}

            # Dos eventos distintos (en vez de uno modificado) para que quien los
            # guarde vea el antes y el despues correctamente.
            self._emit(ToolEvent(tool=block.name, arguments=arguments))
            output = await self._mcp.call_tool(block.name, arguments)
            self._emit(
                ToolEvent(
                    tool=block.name,
                    arguments=arguments,
                    result=output,
                    finished=True,
                )
            )

            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                }
            )

        return results

    def _emit(self, event: ToolEvent) -> None:
        """Manda un evento de herramienta al callback de la interfaz, si hay uno."""
        if self._on_tool_event is not None:
            self._on_tool_event(event)
