"""Punto de entrada: el REPL del anfitrion MCP.

Se ejecuta con `python -m src.main`. Lee una linea, decide si es un comando o un
mensaje para el modelo, e imprime la respuesta. Las llamadas a herramientas se
muestran mientras ocurren para que se vea la interaccion con los servidores MCP.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .config import AppConfig, ConfigError, load_config
from .interaction_logger import MCPInteractionLogger
from .llm_client import LLMClient, LLMError
from .mcp_manager import MCPManager
from .session import ChatSession, ToolEvent

BANNER = r"""
==========================================================
                     CHATBOT REDES
==========================================================
"""

HELP_TEXT = """
Comandos disponibles:
  /help              Muestra esta ayuda.
  /servers           Lista los servidores MCP conectados y los que fallaron.
  /tools             Lista todas las herramientas descubiertas (tools/list).
  /log [n|all] [srv] Muestra el log de interacciones MCP (por defecto, 20 entradas).
  /stats             Resumen de mensajes JSON-RPC por servidor.
  /usage             Tokens consumidos en la sesion.
  /clear             Borra el contexto de la conversacion.
  /exit, /quit       Cierra el chatbot.

Cualquier otra linea se envia al modelo, que decidira si usa herramientas MCP.
"""

# Limites para recortar el eco de las llamadas a herramientas en consola.
_ARGS_PREVIEW_LIMIT = 160
_RESULT_PREVIEW_LIMIT = 300


def _preview(text: str, limit: int) -> str:
    """Recorta un texto para mostrarlo en una sola linea."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + f"... (+{len(flat) - limit} chars)"


def _on_tool_event(event: ToolEvent) -> None:
    """Imprime la actividad de las herramientas conforme ocurre."""
    if not event.finished:
        args = _preview(json.dumps(event.arguments, ensure_ascii=False), _ARGS_PREVIEW_LIMIT)
        print(f"  [tool] -> {event.tool}({args})")
    else:
        print(f"  [tool] <- {_preview(event.result, _RESULT_PREVIEW_LIMIT)}")


async def _read_line(prompt: str) -> str:
    """Lee una linea sin bloquear el event loop."""
    return await asyncio.to_thread(input, prompt)


def _handle_log_command(logger: MCPInteractionLogger, argv: list[str]) -> str:
    """Interpreta y ejecuta el comando /log [n|all] [servidor]."""
    limit: int | None = 20
    server: str | None = None

    if argv:
        head = argv[0]
        if head.lower() == "all":
            limit = None
        elif head.isdigit():
            limit = int(head)
        else:
            server = head
    if len(argv) > 1:
        server = argv[1]

    return logger.render(server=server, limit=limit)


async def _dispatch_command(
    line: str,
    manager: MCPManager,
    logger: MCPInteractionLogger,
    session: ChatSession,
    llm: LLMClient,
) -> bool:
    """Ejecuta un comando y devuelve False si hay que cerrar el chatbot."""
    parts = line.split()
    command, argv = parts[0].lower(), parts[1:]

    if command in {"/exit", "/quit"}:
        return False
    if command == "/help":
        print(HELP_TEXT)
    elif command == "/servers":
        print("\nServidores MCP:")
        print(manager.describe_servers())
    elif command == "/tools":
        print(f"\nHerramientas descubiertas ({len(manager.tools)}):")
        print(manager.describe_tools())
    elif command == "/log":
        print(_handle_log_command(logger, argv))
    elif command == "/stats":
        summary = logger.summary()
        if not summary:
            print("  (sin interacciones MCP registradas)")
        for server, counts in summary.items():
            print(
                f"  {server}: {counts['request']} requests, {counts['response']} responses, "
                f"{counts['error']} errors, {counts['notification']} notifications"
            )
    elif command == "/usage":
        print(f"  {llm.usage}  (modelo: {llm.model})")
    elif command == "/clear":
        session.clear()
        print("  Contexto borrado. La conversacion empieza de cero.")
    else:
        print(f"  Comando desconocido: {command}. Usa /help.")

    return True


async def _repl(config: AppConfig) -> int:
    """Conecta los clientes MCP y corre el ciclo interactivo hasta que el usuario salga."""
    logger = MCPInteractionLogger(config.log_dir, echo=config.echo_mcp_log)
    llm = LLMClient(
        api_key=config.api_key,
        model=config.model,
        max_tokens=config.max_tokens,
    )

    print(BANNER)
    print(f"Modelo: {config.model}")
    print(f"Conectando a {len(config.servers)} servidor(es) MCP...")

    async with MCPManager(config.servers, logger) as manager:
        print(manager.describe_servers())
        print(f"\nHerramientas disponibles: {len(manager.tools)}. Escribe /help para los comandos.\n")

        session = ChatSession(llm, manager, on_tool_event=_on_tool_event)

        while True:
            try:
                line = (await _read_line("tu> ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not line:
                continue
            if line.startswith("/"):
                if not await _dispatch_command(line, manager, logger, session, llm):
                    break
                continue

            try:
                answer = await session.ask(line)
            except LLMError as exc:
                print(f"  [error] {exc}\n")
                continue
            except KeyboardInterrupt:
                print("\n  [cancelado]\n")
                continue

            print(f"\nbot> {answer}\n")

        await llm.aclose()

    print(f"Log completo de MCP: {logger.log_path}")
    print("Hasta luego.")
    return 0


def run() -> int:
    """Lee los argumentos, carga la configuracion y arranca el REPL."""
    parser = argparse.ArgumentParser(
        prog="chatbot-redes",
        description="Chatbot de consola que funciona como anfitrion MCP (CC3067 Redes).",
    )
    parser.add_argument(
        "--config",
        help="Ruta del registro de servidores MCP (por defecto: config/mcp_servers.json).",
    )
    parser.add_argument("--model", help="Sobrescribe el modelo definido en el entorno.")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Error de configuracion: {exc}", file=sys.stderr)
        return 1

    if args.model:
        config = AppConfig(**{**config.__dict__, "model": args.model})

    try:
        return asyncio.run(_repl(config))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(run())
