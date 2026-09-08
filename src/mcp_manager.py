"""Gestor de clientes MCP: un cliente por cada servidor configurado.

El chatbot es el anfitrion y no habla directamente con los servidores: crea un
cliente por servidor, cada uno con su propia conexion. Este modulo abre los
transportes (stdio para servidores locales, Streamable HTTP para remotos), hace
el handshake, descubre las herramientas de cada servidor y enruta las llamadas
al cliente correcto. Cada intercambio se manda al logger.

Si un servidor falla al arrancar, los demas siguen funcionando.
"""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession, StdioServerParameters, stdio_client

try:
    from mcp.client.streamable_http import streamablehttp_client
except ImportError as exc:
    raise ImportError(
    ) from exc

from mcp.types import (
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    Implementation,
    TextContent,
)

from .config import ServerConfig
from .interaction_logger import MCPInteractionLogger

# Separador entre el nombre del servidor y el de la herramienta.
NAMESPACE_SEP = "__"

# Identidad que este anfitrion reporta a cada servidor durante el initialize.
CLIENT_INFO = Implementation(name="chatbot-redes", version="0.1.0")

@dataclass(frozen=True)
class ManagedTool:
    """Una herramienta descubierta en un servidor, lista para ofrecersela al LLM."""

    server: str
    name: str
    qualified_name: str
    description: str
    input_schema: dict[str, Any]

    def to_anthropic(self) -> dict[str, Any]:
        """Convierte la herramienta al formato que espera la API de Anthropic."""
        return {
            "name": self.qualified_name,
            "description": f"[{self.server}] {self.description}".strip(),
            "input_schema": self.input_schema or {"type": "object", "properties": {}},
        }


class MCPConnection:
    """Sesion activa y herramientas descubiertas de un servidor."""

    def __init__(self, config: ServerConfig, session: ClientSession) -> None:
        self.config = config
        self.session = session
        self.tools: list[ManagedTool] = []
        self.server_info: Any = None


class MCPManager:
    """Conecta a todos los servidores configurados y agrupa sus herramientas."""

    def __init__(self, servers: list[ServerConfig], logger: MCPInteractionLogger) -> None:
        self._servers = servers
        self._logger = logger
        self._connections: dict[str, MCPConnection] = {}
        self._stacks: dict[str, AsyncExitStack] = {}
        self.failures: dict[str, str] = {}

    async def __aenter__(self) -> "MCPManager":
        await self.connect_all()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()

    async def connect_all(self) -> None:
        """Conecta a cada servidor y guarda el motivo de los que fallen."""
        for config in self._servers:
            try:
                await self._connect(config)
            except Exception as exc:
                self.failures[config.name] = f"{type(exc).__name__}: {exc}"
                # Cierra lo que el intento fallido alcanzo a abrir.
                stack = self._stacks.pop(config.name, None)
                if stack is not None:
                    await self._close_stack(stack)

    async def _connect(self, config: ServerConfig) -> None:
        """Abre el transporte de un servidor, hace el initialize y descubre sus herramientas."""
        stack = AsyncExitStack()
        self._stacks[config.name] = stack

        if config.transport == "stdio":
            params = StdioServerParameters(
                command=config.command,
                args=config.args,
                env=config.env or None,
                cwd=config.cwd,
            )
            # El stderr del servidor se reenvia al nuestro para ver sus errores.
            read_stream, write_stream = await stack.enter_async_context(
                stdio_client(params, errlog=sys.stderr)
            )
        else:  # "http": Streamable HTTP, para servidores remotos
            read_stream, write_stream, _get_session_id = await stack.enter_async_context(
                streamablehttp_client(
                    url=config.url,
                    headers=config.headers or None,
                    timeout=config.timeout,
                )
            )

        session = await stack.enter_async_context(
            ClientSession(
                read_stream,
                write_stream,
                client_info=CLIENT_INFO,
                logging_callback=self._make_logging_callback(config.name),
            )
        )

        # initialize: negociacion de version y de capacidades.
        with self._logger.call(
            config.name,
            "initialize",
            {"clientInfo": CLIENT_INFO.model_dump(), "transport": config.transport},
        ) as call:
            init_result = await session.initialize()
            call.result = init_result

        connection = MCPConnection(config, session)
        connection.server_info = init_result.serverInfo
        self._connections[config.name] = connection

        await self._discover_tools(connection)

    async def _discover_tools(self, connection: MCPConnection) -> None:
        """Pide tools/list al servidor y guarda el resultado."""
        name = connection.config.name
        with self._logger.call(name, "tools/list", {}) as call:
            result = await connection.session.list_tools()
            call.result = result

        connection.tools = [
            ManagedTool(
                server=name,
                name=tool.name,
                qualified_name=f"{name}{NAMESPACE_SEP}{tool.name}",
                description=tool.description or "",
                input_schema=tool.inputSchema or {"type": "object", "properties": {}},
            )
            for tool in result.tools
        ]

    def _make_logging_callback(self, server_name: str):
        """Crea el callback que registra las notificaciones que envia un servidor."""

        async def _callback(params: Any) -> None:
            self._logger.log_notification(server_name, "notifications/message", params)

        return _callback

    async def aclose(self) -> None:
        """Cierra todas las sesiones y termina los subprocesos.

        Se cierran en orden inverso al de creacion, que es como anyio espera que
        se desarmen los transportes anidados.
        """
        for name in reversed(list(self._stacks)):
            await self._close_stack(self._stacks.pop(name))
        self._connections.clear()

    @staticmethod
    async def _close_stack(stack: AsyncExitStack) -> None:
        """Cierra el stack de un servidor sin dejar que un error corte el resto.

        Se ignora tambien CancelledError: los transportes usan cancel scopes de
        anyio y desarmar uno despues de matar el subproceso puede lanzarlo aunque
        no haya nada que atender.
        """
        try:
            await stack.aclose()
        except (asyncio.CancelledError, Exception):
            pass

    @property
    def connected_servers(self) -> list[str]:
        """Nombres de los servidores cuyo handshake fue exitoso."""
        return list(self._connections)

    @property
    def tools(self) -> list[ManagedTool]:
        """Todas las herramientas descubiertas en todos los servidores conectados."""
        return [tool for conn in self._connections.values() for tool in conn.tools]

    def anthropic_tools(self) -> list[dict[str, Any]]:
        """Todas las herramientas con el formato del parametro 'tools' de la API."""
        return [tool.to_anthropic() for tool in self.tools]

    def find(self, qualified_name: str) -> ManagedTool | None:
        """Busca una herramienta por su nombre completo (servidor__herramienta)."""
        for tool in self.tools:
            if tool.qualified_name == qualified_name:
                return tool
        return None

    def describe_servers(self) -> str:
        """Arma el resumen que muestra el comando /servers."""
        lines: list[str] = []
        for name, conn in self._connections.items():
            info = conn.server_info
            version = f" v{info.version}" if info is not None else ""
            lines.append(
                f"  [ok]   {name} ({conn.config.transport}){version} "
                f"- {len(conn.tools)} tools - {conn.config.description}"
            )
        for name, error in self.failures.items():
            lines.append(f"  [fail] {name} - {error}")
        return "\n".join(lines) if lines else "  (no hay servidores MCP conectados)"

    def describe_tools(self) -> str:
        """Arma el catalogo que muestra el comando /tools."""
        if not self.tools:
            return "  (no hay herramientas disponibles)"
        lines: list[str] = []
        for server in self._connections:
            server_tools = [t for t in self.tools if t.server == server]
            lines.append(f"  {server} ({len(server_tools)}):")
            for tool in server_tools:
                summary = (tool.description or "").split("\n")[0][:100]
                lines.append(f"    - {tool.qualified_name}: {summary}")
        return "\n".join(lines)

    async def call_tool(self, qualified_name: str, arguments: dict[str, Any]) -> str:
        """Ejecuta una herramienta en el servidor que la ofrece y devuelve su texto.

        Los errores se devuelven como texto en lugar de lanzarse, para que el
        modelo pueda leerlos y corregir la llamada.
        """
        tool = self.find(qualified_name)
        if tool is None:
            return f"Error: herramienta desconocida '{qualified_name}'."

        connection = self._connections[tool.server]
        try:
            with self._logger.call(
                tool.server,
                "tools/call",
                {"name": tool.name, "arguments": arguments},
            ) as call:
                result = await connection.session.call_tool(tool.name, arguments)
                call.result = result
        except Exception as exc:
            return f"Error al llamar '{qualified_name}': {type(exc).__name__}: {exc}"

        return self._render_result(result)

    @staticmethod
    def _render_result(result: CallToolResult) -> str:
        """Convierte el resultado de una herramienta MCP en texto plano para el LLM."""
        parts: list[str] = []
        for block in result.content:
            if isinstance(block, TextContent):
                parts.append(block.text)
            elif isinstance(block, ImageContent):
                parts.append(f"[contenido de imagen: {block.mimeType}]")
            elif isinstance(block, EmbeddedResource):
                parts.append(f"[recurso embebido: {getattr(block.resource, 'uri', '?')}]")
            else:
                parts.append(repr(block))

        # Si no hubo bloques de texto pero el servidor devolvio datos estructurados.
        # Muchos servidores devuelven un resumen en texto y los datos reales en
        # structuredContent. 
        if result.structuredContent is not None:
            parts.append(json.dumps(result.structuredContent, ensure_ascii=False, indent=2))

        text = "\n".join(parts).strip() or "(la herramienta no devolvio contenido)"
        return f"La herramienta reporto un error: {text}" if result.isError else text
