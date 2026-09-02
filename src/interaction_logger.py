"""Registro de las interacciones con los servidores MCP.

Guarda cada intercambio JSON-RPC en logs/mcp_interactions.jsonl (una linea por
evento) y lo mantiene en memoria para el comando /log. Cada llamada genera dos
entradas con el mismo call_id: la solicitud y su respuesta o error.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from itertools import count
from pathlib import Path
from typing import Any, Iterator
import time

# En consola se recortan los payloads largos; el archivo siempre guarda todo.
_CONSOLE_PAYLOAD_LIMIT = 400


@dataclass
class LogEntry:
    """Una entrada del log: una solicitud, una respuesta, un error o una notificacion."""

    seq: int
    call_id: int
    timestamp: str
    direction: str
    server: str
    method: str
    payload: Any = None
    duration_ms: float | None = None

    def to_json(self) -> dict[str, Any]:
        """Devuelve la entrada como diccionario listo para json.dumps."""
        return asdict(self)


def _safe_json(value: Any) -> Any:
    """Convierte objetos del SDK en algo serializable a JSON."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _safe_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_json(v) for v in value]
    # Los tipos de mcp.types son modelos de Pydantic y exponen model_dump().
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return _safe_json(dump(mode="json", exclude_none=True))
        except Exception:
            pass
    return repr(value)


class MCPInteractionLogger:
    """Guarda y muestra el trafico del protocolo MCP.

    No depende del SDK: recibe parametros y resultados ya serializados, asi que
    se puede usar y probar sin un servidor conectado.
    """

    def __init__(self, log_dir: Path, echo: bool = False) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / "mcp_interactions.jsonl"
        self.echo = echo

        self._entries: list[LogEntry] = []
        self._seq = count(1)
        self._call_id = count(1)
        self._lock = threading.Lock()

    def _append(self, entry: LogEntry) -> LogEntry:
        """Guarda una entrada en memoria y en disco, y la imprime si echo esta activo."""
        with self._lock:
            self._entries.append(entry)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry.to_json(), ensure_ascii=False) + "\n")
        if self.echo:
            print(self.format_entry(entry))
        return entry

    def log_request(self, server: str, method: str, params: Any = None) -> int:
        """Registra una solicitud saliente y devuelve su call_id."""
        call_id = next(self._call_id)
        self._append(
            LogEntry(
                seq=next(self._seq),
                call_id=call_id,
                timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                direction="request",
                server=server,
                method=method,
                payload=_safe_json(params),
            )
        )
        return call_id

    def log_response(
        self, call_id: int, server: str, method: str, result: Any, duration_ms: float
    ) -> None:
        """Registra la respuesta exitosa de una solicitud previa."""
        self._append(
            LogEntry(
                seq=next(self._seq),
                call_id=call_id,
                timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                direction="response",
                server=server,
                method=method,
                payload=_safe_json(result),
                duration_ms=round(duration_ms, 2),
            )
        )

    def log_error(
        self, call_id: int, server: str, method: str, error: Any, duration_ms: float
    ) -> None:
        """Registra un error de transporte o de JSON-RPC de una solicitud previa."""
        self._append(
            LogEntry(
                seq=next(self._seq),
                call_id=call_id,
                timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                direction="error",
                server=server,
                method=method,
                payload=_safe_json(error),
                duration_ms=round(duration_ms, 2),
            )
        )

    def log_notification(self, server: str, method: str, payload: Any = None) -> None:
        """Registra una notificacion enviada por el servidor (no espera respuesta)."""
        self._append(
            LogEntry(
                seq=next(self._seq),
                call_id=0,  # las notificaciones no se asocian a una solicitud
                timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                direction="notification",
                server=server,
                method=method,
                payload=_safe_json(payload),
            )
        )

    @contextmanager
    def call(self, server: str, method: str, params: Any = None) -> Iterator["_Call"]:
        """Registra una solicitud y su resultado como un par.

        Se usa como 'with logger.call(...) as call:' y se le asigna call.result.
        Si el bloque lanza una excepcion, se registra como error y se relanza.
        """
        call_id = self.log_request(server, method, params)
        started = time.perf_counter()
        handle = _Call(call_id)
        try:
            yield handle
        except Exception as exc:
            elapsed = (time.perf_counter() - started) * 1000
            self.log_error(
                call_id,
                server,
                method,
                {"type": type(exc).__name__, "message": str(exc)},
                elapsed,
            )
            raise
        else:
            elapsed = (time.perf_counter() - started) * 1000
            self.log_response(call_id, server, method, handle.result, elapsed)

    @property
    def entries(self) -> list[LogEntry]:
        """Todas las entradas registradas en esta ejecucion, de la mas vieja a la mas nueva."""
        return list(self._entries)

    def filtered(self, server: str | None = None, limit: int | None = None) -> list[LogEntry]:
        """Devuelve las entradas, opcionalmente de un solo servidor y solo las ultimas N."""
        items = self._entries
        if server:
            items = [entry for entry in items if entry.server == server]
        if limit is not None and limit > 0:
            items = items[-limit:]
        return list(items)

    def summary(self) -> dict[str, dict[str, int]]:
        """Cuenta solicitudes, respuestas, errores y notificaciones por servidor."""
        stats: dict[str, dict[str, int]] = {}
        for entry in self._entries:
            bucket = stats.setdefault(
                entry.server,
                {"request": 0, "response": 0, "error": 0, "notification": 0},
            )
            bucket[entry.direction] = bucket.get(entry.direction, 0) + 1
        return stats

    @staticmethod
    def format_entry(entry: LogEntry) -> str:
        """Convierte una entrada en una linea legible para la consola."""
        arrow = {
            "request": "-->",
            "response": "<--",
            "error": "<!!",
            "notification": "<~~",
        }.get(entry.direction, "   ")

        payload = json.dumps(entry.payload, ensure_ascii=False, default=str)
        if len(payload) > _CONSOLE_PAYLOAD_LIMIT:
            payload = payload[:_CONSOLE_PAYLOAD_LIMIT] + f"... (+{len(payload)} chars)"

        timing = f" [{entry.duration_ms} ms]" if entry.duration_ms is not None else ""
        clock = entry.timestamp.split("T")[1].rstrip("Z+00:00")
        return (
            f"[MCP {entry.seq:04d}] {clock} {arrow} {entry.server}.{entry.method}"
            f"{timing}\n            {payload}"
        )

    def render(self, server: str | None = None, limit: int | None = 20) -> str:
        """Arma el texto que muestra el comando /log."""
        items = self.filtered(server=server, limit=limit)
        if not items:
            return "(aun no hay interacciones MCP registradas)"

        header = f"--- Log de interacciones MCP ({len(items)} de {len(self._entries)} entradas) ---"
        body = "\n".join(self.format_entry(entry) for entry in items)
        footer = f"--- log completo: {self.log_path} ---"
        return f"{header}\n{body}\n{footer}"


@dataclass
class _Call:
    """Objeto que devuelve el context manager call() para asignarle el resultado."""

    call_id: int
    result: Any = field(default=None)
