"""
Carga de configuracion.

Lee las variables de entorno (clave de API, modelo, rutas) desde el archivo .env
y el registro de servidores MCP desde un archivo JSON.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Raiz del repositorio: dos niveles arriba de este archivo.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Detecta placeholders ${NOMBRE} dentro del registro de servidores.
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(RuntimeError):
    """Error de configuracion: falta un archivo o un campo obligatorio."""


@dataclass(frozen=True)
class ServerConfig:
    """Datos de conexion de un servidor MCP.

    El transporte 'stdio' lanza el servidor como subproceso y usa command/args;
    el transporte 'http' se conecta a un servidor remoto y usa url/headers.
    """

    name: str
    transport: str
    description: str = ""
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        """Verifica que la entrada tenga los campos que exige su transporte."""
        if self.transport == "stdio":
            if not self.command:
                raise ConfigError(f"servidor '{self.name}': el transporte stdio requiere 'command'")
        elif self.transport == "http":
            if not self.url:
                raise ConfigError(f"servidor '{self.name}': el transporte http requiere 'url'")
        else:
            raise ConfigError(
                f"servidor '{self.name}': transporte desconocido '{self.transport}' "
                "(se espera 'stdio' o 'http')"
            )


@dataclass(frozen=True)
class AppConfig:
    """Configuracion completa con la que arranca la aplicacion."""

    api_key: str
    model: str
    max_tokens: int
    log_dir: Path
    echo_mcp_log: bool
    servers: list[ServerConfig]


def _expand(value: Any, environment: dict[str, str]) -> Any:
    """Reemplaza los placeholders ${NOMBRE} en textos, listas y diccionarios.

    Los placeholders desconocidos se dejan intactos para que el error aparezca al
    conectar y no se conviertan en cadenas vacias silenciosamente.
    """
    if isinstance(value, str):
        return _PLACEHOLDER.sub(
            lambda m: environment.get(m.group(1), m.group(0)),
            value,
        )
    if isinstance(value, list):
        return [_expand(item, environment) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item, environment) for key, item in value.items()}
    return value


def load_server_registry(config_path: Path) -> list[ServerConfig]:
    """Lee el registro de servidores MCP y devuelve solo los habilitados."""
    if not config_path.exists():
        raise ConfigError(f"no se encontro el registro de servidores MCP: {config_path}")

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"el registro de servidores MCP no es JSON valido: {exc}") from exc

    # ${PROJECT_ROOT} siempre esta disponible, ademas del entorno real.
    environment = {**os.environ, "PROJECT_ROOT": str(PROJECT_ROOT)}

    servers: list[ServerConfig] = []
    for name, entry in (raw.get("servers") or {}).items():
        if not isinstance(entry, dict) or not entry.get("enabled", False):
            continue

        entry = _expand(entry, environment)
        server = ServerConfig(
            name=name,
            transport=entry.get("transport", "stdio"),
            description=entry.get("description", ""),
            command=entry.get("command"),
            args=list(entry.get("args", [])),
            env=dict(entry.get("env", {})),
            cwd=entry.get("cwd"),
            url=entry.get("url"),
            headers=dict(entry.get("headers", {})),
        )
        server.validate()
        servers.append(server)

    return servers


def load_config(config_path: str | None = None) -> AppConfig:
    """Arma la configuracion a partir del .env, el entorno y el registro de servidores."""
    load_dotenv(PROJECT_ROOT / ".env")

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise ConfigError(
            "ANTHROPIC_API_KEY no esta definida. Copia .env.example a .env y agrega tu clave."
        )

    registry = config_path or os.getenv("CHATBOT_MCP_CONFIG", "config/mcp_servers.json")
    registry_path = Path(registry)
    if not registry_path.is_absolute():
        registry_path = PROJECT_ROOT / registry_path

    log_dir = Path(os.getenv("CHATBOT_LOG_DIR", "logs"))
    if not log_dir.is_absolute():
        log_dir = PROJECT_ROOT / log_dir

    return AppConfig(
        api_key=api_key,
        model=os.getenv("CHATBOT_MODEL", "claude-sonnet-5"),
        max_tokens=int(os.getenv("CHATBOT_MAX_TOKENS", "2048")),
        log_dir=log_dir,
        echo_mcp_log=os.getenv("CHATBOT_ECHO_MCP_LOG", "0") in {"1", "true", "True"},
        servers=load_server_registry(registry_path),
    )
