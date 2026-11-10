"""api/loop_worker.py — loop de simulación en hilo separado para el bridge FastAPI.

`AutoStoreSimulator.avanzar_tick()` es síncrono y bloqueante (T-22), así que el
bridge lo corre en un `threading.Thread` controlado por `play()` / `pause()` /
`reset()`. Tras cada tick llama a `on_tick` (provisto por `api/server.py`) para
notificar a los websockets conectados.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from bus_persistencia.bus.state_bus import StateBus
from bus_persistencia.models.state import Caja, Config, ModoTurno, Pedido
from motor.simulador import AutoStoreSimulator
from motor.run import generar_ola_aleatoria, _asegurar_cajas_para_skus
from api.serializers import snapshot_to_m3_frame
from experimentos.resultados import RegistroCorrida

# ticks/seg declarados por M1 (1x, 2x, 5x) -> intervalo de sleep entre ticks
VELOCIDAD_INTERVALOS: dict[int, float] = {1: 1.0, 2: 0.5, 5: 0.2, 1000: 0.001}


class SimulationLoop:
    """Envuelve un `AutoStoreSimulator` y lo avanza en un hilo de fondo."""

    def __init__(
        self,
        bus: StateBus,
        on_tick: Callable[[], None],
        output_dir: str | Path = "output",
    ) -> None:
        self.bus = bus
        self._on_tick = on_tick
        self._output_dir = Path(output_dir)
        self._sim: AutoStoreSimulator | None = None
        self._config: Config | None = None
        self._seed: int | None = None
        self._modo: ModoTurno | None = None
        self._politica: str | None = None
        self._session_name: str = "default"
        self._pedidos_inicial: list[Pedido] = []
        self.instancia = None
        self.opciones_olas = None
        self.registro = None
        self.limite_ticks = 1000
        self.ventana_sin_entregas = None
        self._reposicion_inicial: list[Caja] = []
        self._running = threading.Event()
        self._thread: threading.Thread | None = None
        self.status = "IDLE"
        self.velocidad = 1
        # KPIs finales de las ejecuciones terminadas (para el reporte comparativo).
        # Cada entrada: (nombre_ejecucion, {KPI: valor}).
        self.finished_runs: list[tuple[str, dict[str, float]]] = []
        # Timeline completa para exportación M3 (Omniverse)
        self._m3_timeline: list[dict] = []
        self._ultima_notificacion = 0.0

    @property
    def simulador(self) -> AutoStoreSimulator | None:
        return self._sim

    def configurar(
        self,
        config: Config,
        seed: int | None = None,
        modo: ModoTurno | None = None,
        politica: str | None = None,
        pedidos_demandados: int | None = None,
        session_name: str | None = None,
        instancia=None,
        opciones_olas=None,
        limite_ticks=1000,
        ventana_sin_entregas=None,
    ) -> None:
        """(Re)inicializa la simulación con una nueva config. Detiene el loop actual."""
        self.pause()
        self._join()
        self._cerrar_registro()
        self.limite_ticks = limite_ticks
        self.ventana_sin_entregas = ventana_sin_entregas
        self.instancia = instancia
        self.opciones_olas = opciones_olas
        if instancia is not None and getattr(instancia, "version", 1) >= 2:
            self.opciones_olas = {"modelo_preparacion": "fisica", **(opciones_olas or {})}
        self._config = config
        self._seed = seed
        self._modo = modo
        self._politica = politica
        if session_name:
            self._session_name = session_name
        self._pedidos_inicial = list(self.bus.read_snapshot().pedidos.cola)
        if instancia is not None:
            instancia.validar()
            self._pedidos_inicial = [s.pedido for s in sorted(instancia.solicitudes, key=lambda s: s.orden)]
        # Sólo el turno DIURNO usa una ola de pedidos; el NOCTURNO repone desde las
        # conveyors (cola_reposicion), no genera pedidos de picking.
        es_nocturno = modo == ModoTurno.NOCTURNO
        if (not es_nocturno and not self._pedidos_inicial
                and pedidos_demandados and pedidos_demandados > 0):
            self._pedidos_inicial = generar_ola_aleatoria(
                config.grilla.x, config.grilla.y, seed or 42,
            )[:pedidos_demandados]
        self._reinicializar()

    def set_velocidad(self, velocidad: int) -> None:
        self.velocidad = velocidad if velocidad in VELOCIDAD_INTERVALOS else 1

    def set_politica(self, politica: str) -> None:
        """Cambia la política activa; se preserva si luego se hace `/control/reset`."""
        if self.registro is not None and politica != self._politica:
            raise ValueError("La política interna se mantiene fija en la corrida experimental")
        self._politica = politica
        self.bus.set_policy(politica)

    def set_cola_reposicion(self, cajas: list[Caja]) -> None:
        """Carga la cola de reposición (nocturno). Se guarda persistente para que
        sobreviva al recreado del simulador en `_reinicializar` (el upload del CSV
        ocurre antes de POST /config)."""
        self._reposicion_inicial = list(cajas)
        if self._sim is not None:
            self._sim.cola_reposicion = list(cajas)

    def play(self) -> None:
        if self.registro is not None and self.registro.terminal:
            raise RuntimeError("Corrida finalizada; reiniciar para crear otra corrida")
        if self._sim is None:
            raise RuntimeError("Simulación no inicializada — llamar POST /config primero.")
        if self._thread is not None and self._thread.is_alive():
            self._running.set()
            self.status = "RUNNING"
            return
        self._running.set()
        self.status = "RUNNING"
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def pause(self) -> None:
        was_running = self._running.is_set()
        self._running.clear()
        if was_running:
            self.status = "PAUSED"
            self._join()
            if self.registro is not None:
                self.registro.guardar("pausada")

    def _cerrar_registro(self):
        if self.registro is not None:
            self.registro.guardar("interrumpida", "Reinicio, nueva configuración o cierre del servicio")
            self.registro = None

    def cerrar(self):
        self.pause()
        self._join()
        self._cerrar_registro()

    def reset(self) -> None:
        self.pause()
        self._join()
        self._cerrar_registro()
        if self._config is not None:
            self._reinicializar()
        else:
            self.bus.reset()
            self._sim = None
            self.status = "IDLE"
            self._on_tick()

    def _reinicializar(self) -> None:
        """Lleva el bus a tick 0 con `_config` y vuelve a inicializar el simulador,
        reaplicando modo/política/pedidos capturados en `configurar()`."""
        assert self._config is not None
        self.bus.set_event_logging(self.instancia is None or getattr(self.instancia, "version", 1) < 2)
        self.bus.reset(config=self._config)
        # Dirigir la bitácora de sesión a output/sesion_<nombre>.csv (exportable).
        self.bus.set_session_output(self._output_dir, self._session_name)
        if self._modo is not None:
            self.bus.set_modo(self._modo)
        if self._politica is not None:
            self.bus.set_policy(self._politica)
        if self._pedidos_inicial:
            self.bus.set_pedidos_cola(self._pedidos_inicial)
        self._sim = AutoStoreSimulator(self.bus)
        self._sim.inicializar_desde_bus(
            seed=self._seed,
            inventario=list(self.instancia.inventario) if self.instancia else None,
            plan_olas={"solicitudes": self.instancia.solicitudes, **self.opciones_olas}
            if self.instancia is not None and self.opciones_olas is not None else None,
        )
        if self.instancia is None:
            _asegurar_cajas_para_skus(self._sim)
        elif self._sim.plan_olas is not None:
            self.registro = RegistroCorrida(self.instancia, self._sim, self._output_dir / "corridas",
                                            self.limite_ticks, self.ventana_sin_entregas)
        # Reaplicar la cola de reposición (nocturno) al simulador recreado.
        self._sim.cola_reposicion = list(self._reposicion_inicial)
        self._m3_timeline = []
        self.status = "IDLE"
        self._on_tick()

    def _join(self) -> None:
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                raise RuntimeError("El motor no se detuvo dentro de dos segundos")
        self._thread = None

    def _run(self) -> None:
        assert self._sim is not None
        while self._running.is_set():
            if self.registro is not None and self.registro.terminal:
                self.status = "FINISHED" if self.registro.estado == "completa" else "INCOMPLETE"
                self._running.clear()
                if self.registro.estado == "completa":
                    self._registrar_corrida_terminada()
                self._on_tick()
                break
            if self._sim.ha_terminado():
                self.status = "FINISHED"
                self._running.clear()
                self._registrar_corrida_terminada()
                self._on_tick()
                break
            try:
                if self.registro is not None:
                    self.registro.avanzar()
                else:
                    self._sim.avanzar_tick()
            except Exception:
                self.status = "ERROR"
                self._running.clear()
                self._on_tick()
                break
            grande = self.instancia is not None and getattr(self.instancia, "version", 1) >= 2
            if not grande:
                snap = self.bus.read_snapshot()
                self._m3_timeline.append(snapshot_to_m3_frame(snap, self._sim.last_tick_eventos))
            # Las listas largas guardan su traza física en RegistroCorrida; no
            # duplican snapshots completos de todo el almacén en RAM por tick.
            ahora = time.monotonic()
            if not grande or ahora - self._ultima_notificacion >= 0.1:
                self._on_tick()
                self._ultima_notificacion = ahora
            if self.registro is not None and self.registro.terminal:
                continue
            time.sleep(VELOCIDAD_INTERVALOS.get(self.velocidad, 1.0))

    def _registrar_corrida_terminada(self) -> None:
        """Guarda los KPIs finales de la ejecución para el reporte comparativo.
        Mantiene las últimas 5 corridas (el comparativo usa las 2 más recientes)."""
        kpis = self.bus.read_snapshot().kpis.as_dict()
        self.finished_runs.append((self._session_name, kpis))
        if len(self.finished_runs) > 5:
            self.finished_runs = self.finished_runs[-5:]

    def get_m3_export(self) -> dict:
        """Construye el JSON completo para M3 (Omniverse): config + timeline."""
        snap = self.bus.read_snapshot()
        config_data: dict = {}
        if snap.config is not None:
            from motor.grilla import Grilla
            g = Grilla(snap.config)
            config_data = {
                "grilla": {"x": snap.config.grilla.x, "y": snap.config.grilla.y, "z": snap.config.grilla.z},
                "robots": snap.config.robots,
                "ocupacion_inicial": snap.config.ocupacion_inicial,
                "gridTotal": {"x": g.ancho_total, "y": g.alto_total},
                "interior": dict(zip(("x0", "y0", "x1", "y1"), g.interior_bounds)),
                "estaciones": [
                    {"id": e.id, "x": e.x, "y": e.y, "orientacion": e.orientacion_requerida.value}
                    for e in g.estaciones
                ],
                "conveyorsNorte": [{"x": c.x, "y": c.y} for c in g.conveyors_norte],
            }
        return {"config": config_data, "timeline": self._m3_timeline}
