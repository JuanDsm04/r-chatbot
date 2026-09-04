"""Prueba de conexion a los servidores MCP configurados, sin gastar creditos de la API.

Conecta a cada servidor habilitado, hace el initialize y el tools/list, e imprime
lo que encontro junto con el log de interacciones. No necesita clave de API, asi
que es la forma mas rapida de verificar un servidor nuevo.

Uso:
    python scripts/check_servers.py                 # usa config/mcp_servers.json
    python scripts/check_servers.py otro.json       # usa otro registro
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Permite ejecutarlo como script desde la raiz del repositorio.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import PROJECT_ROOT, ConfigError, load_server_registry  # noqa: E402
from src.interaction_logger import MCPInteractionLogger  # noqa: E402
from src.mcp_manager import MCPManager  # noqa: E402


async def main(registry: str) -> int:
    path = Path(registry)
    if not path.is_absolute():
        path = PROJECT_ROOT / path

    try:
        servers = load_server_registry(path)
    except ConfigError as exc:
        print(f"Error de configuracion: {exc}", file=sys.stderr)
        return 1

    if not servers:
        print("No hay servidores habilitados en el registro.")
        return 1

    print(f"Probando {len(servers)} servidor(es) de {path}\n")
    logger = MCPInteractionLogger(PROJECT_ROOT / "logs")

    async with MCPManager(servers, logger) as manager:
        print("Resultado de la conexion:")
        print(manager.describe_servers())
        print(f"\nHerramientas descubiertas ({len(manager.tools)}):")
        print(manager.describe_tools())
        print()
        print(logger.render(limit=None))
        failed = bool(manager.failures)

    return 2 if failed else 0


if __name__ == "__main__":
    registry_arg = sys.argv[1] if len(sys.argv) > 1 else "config/mcp_servers.json"
    raise SystemExit(asyncio.run(main(registry_arg)))
