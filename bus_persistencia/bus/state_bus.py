"""Bus de Estado Central con patrón single-writer / multiple-reader (T-01, T-02)."""

from __future__ import annotations

import threading
import time
from typing import Any

from bus_persistencia.models.state import (
    Caja,
    Config,
    KPISet,
    ModoTurno,
    Pedido,
    PedidosState,
    PoliticaPicking,
    Robot,
    StateSnapshot,
    TickDelta,
    deep_copy_snapshot,
)
from bus_persistencia.persistence.session_logger import SessionLogger

M2_WRITER_ID = "M2"


class WriterNotAuthorizedError(PermissionError):
    """Solo M2 puede escribir en el Bus."""


UnauthorizedWriterError = WriterNotAuthorizedError


class StateBus:
    """
    Fuente única de verdad compartida entre M1, M2 y M3.

    - M2 es el único escritor (`write_tick_delta`).
    - M1/M3 leen snapshots inmutables (`read_snapshot`).
    - Escrituras protegidas con `threading.Lock()`.
    """

    def __init__(self, session_logger: SessionLogger | None = None) -> None:
        self._lock = threading.Lock()
        self._session_logger = session_logger or SessionLogger(
            output_path="_noop/session.csv", defer_io=True
        )
        self._tick = 0
        self._modo = ModoTurno.DIURNO
        self._politica: str = "fifo"
        self._grilla: list[Caja] = []
        self._robots: list[Robot] = []
        self._pedidos_cola: list[Pedido] = []
        self._pedidos_completados: list[Pedido] = []
        self._kpis = KPISet()
        self._config: Config | None = None
        self._paused = False
        self._snapshot = self._build_snapshot()
        self._write_latencies_ms: list[float] = []

    def set_session_output(self, output_dir, session_name: str) -> None:
        """Dirige la bitácora de sesión a `output_dir/sesion_<session_name>.csv`.
        Permite que `GET /report/sesion` encuentre el archivo (por defecto el bus
        escribe a `_noop/`, no exportable). Resetea el buffer/header para la corrida."""
        with self._lock:
            self._session_logger.reset()
            self._session_logger.configure(output_dir, session_name)

    def set_config(self, config: Config) -> None:
        with self._lock:
            self._config = config
            self._refresh_snapshot()

    def set_modo(self, modo: ModoTurno) -> None:
        with self._lock:
            self._modo = modo
            self._refresh_snapshot()

    def set_policy(self, politica: str) -> None:
        with self._lock:
            self._politica = politica
            self._refresh_snapshot()

    def set_pedidos_cola(self, pedidos: list[Pedido]) -> None:
        with self._lock:
            self._pedidos_cola = list(pedidos)
            self._refresh_snapshot()

    def get_metadata(self) -> dict[str, Any]:
        snap = self.read_snapshot()
        return {
            "tick": snap.tick,
            "modo": snap.modo.value,
            "politica": snap.politica,
            "kpis": snap.kpis.as_dict(),
            "paused": snap.paused,
        }

    def read_snapshot(self) -> StateSnapshot:
        """Lectura no bloqueante: retorna copia profunda del último snapshot."""
        return deep_copy_snapshot(self._snapshot)

    def set_event_logging(self, enabled: bool) -> None:
        """Evita duplicar trazas cuando RegistroCorrida es el registro principal.

        No cambia la escritura del estado ni la autorización del escritor.
        El registro histórico por sesión sigue habilitado por defecto.
        """
        if type(enabled) is not bool:
            raise ValueError("enabled debe ser booleano")
        with self._lock:
            self._event_logging = enabled

    def write_tick_delta(self, writer_id: str, delta: TickDelta) -> int:
        """M2: aplica delta, incrementa tick, registra eventos y flush diferido."""
        if writer_id != M2_WRITER_ID:
            raise WriterNotAuthorizedError(
                f"Solo {M2_WRITER_ID} puede escribir; recibido: {writer_id!r}"
            )

        start = time.perf_counter()

        with self._lock:
            self._tick += 1
            self._apply_delta(delta)
            self._refresh_snapshot()

            registrar = getattr(self, "_event_logging", True)
            if registrar:
                for evento in delta.eventos:
                    self._session_logger.buffer_event(self._tick, evento)

        if registrar:
            self._session_logger.flush_tick_async(self._tick)

        elapsed_ms = (time.perf_counter() - start) * 1000
        self._write_latencies_ms.append(elapsed_ms)
        return self._tick

    def reset(self, config: Config | None = None) -> None:
        with self._lock:
            self._tick = 0
            self._modo = ModoTurno.DIURNO
            self._politica = "fifo"
            self._grilla = []
            self._robots = []
            self._pedidos_cola = []
            self._pedidos_completados = []
            self._kpis = KPISet()
            self._config = config
            self._paused = False
            self._refresh_snapshot()
            self._write_latencies_ms.clear()

    def p99_write_latency_ms(self) -> float:
        if not self._write_latencies_ms:
            return 0.0
        sorted_vals = sorted(self._write_latencies_ms)
        idx = int(len(sorted_vals) * 0.99) - 1
        idx = max(0, min(idx, len(sorted_vals) - 1))
        return sorted_vals[idx]

    def _apply_delta(self, delta: TickDelta) -> None:
        if delta.modo is not None:
            self._modo = delta.modo

        if delta.grilla_delta is not None:
            index = {(c.x, c.y, c.z): i for i, c in enumerate(self._grilla)}
            for caja in delta.grilla_delta:
                key = (caja.x, caja.y, caja.z)
                if key in index:
                    self._grilla[index[key]] = caja
                else:
                    self._grilla.append(caja)

        if delta.grilla_remove:
            remove_set = set(delta.grilla_remove)
            self._grilla = [
                c for c in self._grilla if (c.x, c.y, c.z) not in remove_set
            ]

        if delta.robots_delta is not None:
            index = {robot.id: i for i, robot in enumerate(self._robots)}
            for robot in delta.robots_delta:
                if robot.id in index:
                    self._robots[index[robot.id]] = robot
                else:
                    self._robots.append(robot)

        if delta.pedidos_cola is not None:
            self._pedidos_cola = list(delta.pedidos_cola)

        if delta.pedidos_completados_add:
            self._pedidos_completados.extend(delta.pedidos_completados_add)

        if delta.kpis is not None:
            self._kpis = delta.kpis

    def _build_snapshot(self) -> StateSnapshot:
        return StateSnapshot(
            tick=self._tick,
            modo=self._modo,
            politica=self._politica,
            grilla=tuple(self._grilla),
            robots=tuple(self._robots),
            pedidos=PedidosState(
                cola=tuple(self._pedidos_cola),
                completados=tuple(self._pedidos_completados),
            ),
            kpis=self._kpis,
            config=self._config,
            paused=self._paused,
        )

    def _refresh_snapshot(self) -> None:
        self._snapshot = self._build_snapshot()
