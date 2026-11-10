# motor/simulador.py
# Orquestador central de M2 — AutoStoreSimulator.
#
# Rol: único punto de escritura al bus. Delega lógica en:
#   motor.grilla      — Grilla 3D, puertos, búsquedas por SKU
#   motor.politicas   — FIFO / PRIORIDAD_POSICION (funciones puras)
#   motor.modos       — procesar_diurno / procesar_nocturno
#   motor.despachador — Despachador (T-12/T-15/T-16/T-17 — Manuel)
#   motor.kpis        — calcular_kpis / Acumuladores (T-20 — Manuel)
#
# Contrato del bus: docs/bus_api.md, docs/integracion_grupo12.md
# Acuerdos de diseño: docs/acuerdos_diseno_m2.md

from __future__ import annotations

import warnings

from bus_persistencia.bus.state_bus import M2_WRITER_ID, StateBus
from bus_persistencia.models.state import (
    Caja,
    Config,
    KPISet,
    ModoTurno,
    Pedido,
    PoliticaPicking,
    Robot,
    RobotEstado,
    TickDelta,
    celdas_desde,
)
from motor.grilla import Grilla
from motor.kpis import Acumuladores
from motor.modos import DespachadorNocturno, procesar_nocturno

# Límites de rendimiento (T-23)
_LIMIT_CELDAS = 20 * 20 * 5
_LIMIT_ROBOTS = 10


class AutoStoreSimulator:
    def __init__(self, bus: StateBus) -> None:
        """Guarda la referencia al bus. NO carga config ni pedidos aquí —
        M1 los configura vía `bus.set_config` / `bus.set_pedidos_cola`
        ANTES de llamar a `inicializar_desde_bus()`."""
        self.bus = bus

        # Módulos de motor — se crean en inicializar_desde_bus()
        self._grilla: Grilla | None = None
        self._despachador = None   # motor.despachador.Despachador (Manuel)
        self._desp_nocturno: DespachadorNocturno | None = None  # ingreso nocturno

        # Estado interno
        self.robots: dict[int, Robot] = {}
        self.pedidos_cola: list[Pedido] = []
        self.pedidos_completados: list[Pedido] = []
        self.cola_reposicion: list[Caja] = []

        self.modo: ModoTurno = ModoTurno.DIURNO
        self.tick: int = 0
        self.kpis: KPISet = KPISet()
        self._acum: Acumuladores = Acumuladores()

        # Condición de término por ola (Feature 5): tamaño del lote de pedidos
        # cargado al inicio. La sesión se detiene al completar el 100%.
        self.total_ola: int = 0
        self.plan_olas = None
        self._ola_completada_emitida: bool = False
        self._politica_olas = None

        # Buffers de delta — se vacían en _construir_delta()
        self._grilla_delta: list[Caja] = []
        self._grilla_remove: list[tuple[int, int, int]] = []
        self._robots_delta: list[Robot] = []
        self._pedidos_completados_add: list[Pedido] = []
        self._eventos_pendientes: list[dict] = []
        self._modo_pendiente: ModoTurno | None = None

        # Eventos del último tick (para exportación M3)
        self._last_tick_eventos: list[dict] = []

    # ------------------------------------------------------------------
    # Inicialización (T-09, T-11, T-23)
    # ------------------------------------------------------------------

    @property
    def last_tick_eventos(self) -> list[dict]:
        return self._last_tick_eventos

    def inicializar_desde_bus(self, seed: int | None = None, inventario: list[Caja] | None = None,
                             plan_olas: dict | None = None) -> None:
        """Lee la config del bus, construye la grilla y los robots.
        Emite una advertencia de rendimiento (T-23) si los parámetros
        exceden los límites de referencia (no aborta, solo avisa).

        Args:
            seed: semilla para la inicialización aleatoria de la grilla
                  (permite reproducibilidad — ver T-08).
        """
        snap = self.bus.read_snapshot()
        config: Config = snap.config
        if config is None:
            raise RuntimeError("M1 debe llamar bus.set_config() antes de inicializar.")

        # T-23 — advertencia de rendimiento
        capacidad = config.grilla.x * config.grilla.y * config.grilla.z
        if capacidad > _LIMIT_CELDAS or config.robots > _LIMIT_ROBOTS:
            warnings.warn(
                f"Parámetros fuera de rango de referencia: "
                f"grilla={config.grilla.x}×{config.grilla.y}×{config.grilla.z} "
                f"({capacidad} celdas, límite {_LIMIT_CELDAS}), "
                f"robots={config.robots} (límite {_LIMIT_ROBOTS}). "
                "El rendimiento puede verse afectado.",
                RuntimeWarning,
                stacklevel=2,
            )

        # Construir grilla
        self._grilla = Grilla(config)
        if inventario is None:
            self._grilla.inicializar_aleatoria(seed=seed)
        else:
            posiciones = set()
            ids = set()
            for caja in inventario:
                posicion = (caja.x, caja.y, caja.z)
                if (posicion in posiciones or caja.id_caja in ids
                        or not self._grilla.es_interior(caja.x, caja.y)
                        or not 0 <= caja.z < self._grilla.gz or caja.cantidad < 1):
                    raise ValueError("Inventario inicial inválido: caja duplicada o fuera de grilla")
                posiciones.add(posicion)
                ids.add(caja.id_caja)
                self._grilla.agregar(caja)

        # Construir robots 1×2 con orientación fija en el anillo de tránsito.
        # Cada robot ocupa cuerpo (ancla) + punta (derivada de su orientación);
        # se colocan sin solapar footprints.
        orientaciones = config.orientaciones_robots()
        ocupadas: set[tuple[int, int]] = set()
        candidatos = self._grilla.anillo  # celdas-ancla candidatas (orden estable)
        for i, ori in enumerate(orientaciones):
            colocado = False
            for (cx, cy) in candidatos:
                celdas = celdas_desde(cx, cy, ori)
                if any(not self._grilla.en_superficie(x, y) for x, y in celdas):
                    continue
                if any((x, y) in ocupadas for x, y in celdas):
                    continue
                self.robots[i] = Robot(
                    id=i, x=cx, y=cy, z=0,
                    estado=RobotEstado.INACTIVO,
                    carga_id=None,
                    orientacion=ori,
                )
                ocupadas.update(celdas)
                colocado = True
                break
            if not colocado:
                # Fallback defensivo: ubicar en la primera celda libre del anillo.
                cx, cy = candidatos[i % len(candidatos)]
                self.robots[i] = Robot(
                    id=i, x=cx, y=cy, z=0,
                    estado=RobotEstado.INACTIVO,
                    carga_id=None,
                    orientacion=ori,
                )

        # Leer pedidos y cola de reposición que M1 ya cargó en el bus
        self.pedidos_cola = list(snap.pedidos.cola)
        self._acum.pedidos_demandados = len(self.pedidos_cola)
        self.total_ola = len(self.pedidos_cola)  # tamaño de la ola (Feature 5)
        self.modo = snap.modo

        # Construir despachador (T-12 — Manuel)
        try:
            from motor.despachador import Despachador
            self._despachador = Despachador(self._grilla)
        except (ImportError, NotImplementedError):
            self._despachador = None  # turno diurno quedará en NotImplementedError

        if plan_olas is not None:
            from experimentos.olas import PlanOlas
            self.pedidos_cola = []
            self.plan_olas = PlanOlas(self, **plan_olas)
            self._politica_olas = snap.politica
            self.total_ola = len(self.plan_olas.pedidos)
            self._acum.pedidos_demandados = self.total_ola

        # Emitir snapshot inicial: grilla + robots al bus
        grilla_delta, grilla_remove = self._grilla.flush_delta()
        self._grilla_delta.extend(grilla_delta)
        self._grilla_remove.extend(grilla_remove)
        self._robots_delta.extend(self.robots.values())
        delta = self._construir_delta()
        self.tick = self.bus.write_tick_delta(M2_WRITER_ID, delta)

    # ------------------------------------------------------------------
    # Ciclo de simulación
    # ------------------------------------------------------------------

    def avanzar_tick(self) -> None:
        """Ejecuta un paso completo de simulación y escribe el TickDelta."""
        if self.plan_olas is not None and (self.plan_olas.completa or self.plan_olas.fallo):
            return
        # El modelo físico experimental conserva la política registrada al inicio;
        # evita copiar toda la grilla desde el bus dos veces en cada tick largo.
        if self.plan_olas is not None and self.plan_olas.modelo_preparacion == "fisica":
            politica_activa = self._politica_olas or "fifo"
        else:
            politica_activa = self.bus.read_snapshot().politica
        self._acum.ticks_totales += 1
        self._acum.ticks_turno_actual += 1

        if self.plan_olas is not None and not self.plan_olas.puede_procesar:
            if self.plan_olas.modelo_preparacion == "fisica":
                self.plan_olas.avanzar_preparacion()
        elif self.modo == ModoTurno.DIURNO:
            self._procesar_turno_diurno(politica_activa)
        else:
            self._procesar_turno_nocturno()

        self._resolver_colisiones()
        self._actualizar_kpis()

        if self.plan_olas is not None:
            self.plan_olas.cerrar_tick(list(self._eventos_pendientes))

        # Condición de término por ola (Feature 5): emitir señal una sola vez,
        # DESPUÉS de aplicar las entregas del tick (para no cortar un tick antes
        # de la última entrega real).
        if self.ola_completa() and not self._ola_completada_emitida:
            self._ola_completada_emitida = True
            self._eventos_pendientes.append({
                "tipo": "ola_completada",
                "tick": self.tick + 1,
                "pedidos": len(self.pedidos_completados),
                "total_ola": self.total_ola,
            })

        delta = self._construir_delta()
        self.tick = self.bus.write_tick_delta(M2_WRITER_ID, delta)

    def cambiar_modo(self, nuevo_modo: ModoTurno) -> None:
        """Marca transición de turno para el próximo TickDelta."""
        self._modo_pendiente = nuevo_modo
        self.modo = nuevo_modo
        self._acum.ticks_turno_actual = 0

    def ola_completa(self) -> bool:
        """True cuando el 100% de los pedidos de la ola han sido completados.

        Condición de término principal (Feature 5): se evalúa contra el tamaño
        del lote (`total_ola`) capturado al inicializar, no contra la cola, para
        que un pedido insatisfacible (sin caja del SKU) no quede colgado — la ola
        se considera completa solo si TODOS sus pedidos llegaron a un puerto."""
        return self.total_ola > 0 and len(self.pedidos_completados) >= self.total_ola

    def ha_terminado(self) -> bool:
        """Sesión terminada.

        - Nocturno (ingreso): cuando no quedan cajas por reponer en la cola, ni
          tareas de ingreso activas, y todos los robots están inactivos.
        - Diurno (picking): cuando se completa el 100% de la ola, o como fallback
          cuando no quedan pedidos pendientes ni robots activos (ola insatisfacible).
        """
        if self.plan_olas is not None:
            return self.plan_olas.completa
        todos_inactivos = all(
            r.estado == RobotEstado.INACTIVO for r in self.robots.values()
        )
        if self.modo == ModoTurno.NOCTURNO:
            tareas_ingreso = (
                self._desp_nocturno._tareas if self._desp_nocturno is not None else {}
            )
            return (
                len(self.cola_reposicion) == 0
                and not tareas_ingreso
                and todos_inactivos
            )
        if self.ola_completa():
            return True
        return len(self.pedidos_cola) == 0 and todos_inactivos

    # ------------------------------------------------------------------
    # Procesamiento por turno
    # ------------------------------------------------------------------

    def _procesar_turno_diurno(self, politica: str) -> None:
        """Delega en motor.despachador (Manuel — T-12, T-15, T-16, T-17).

        Convenciones de delta (acuerdos con Martín):
        - robots_delta: SOLO robots que cambiaron este tick (merge por id)
        - grilla_delta/remove: SOLO celdas que cambiaron (merge por celda)
        - Al soltar la carga: incluir Robot con carga_id=None explícitamente
        """
        if self._despachador is None:
            raise NotImplementedError(
                "Despachador no disponible. Manuel debe implementar motor/despachador.py."
            )

        robots_upd, g_delta, g_remove, completados, eventos = (
            self._despachador.tick(self.robots, self.pedidos_cola, politica, self._acum)
        )

        # Actualizar espejo interno
        for robot in robots_upd:
            self.robots[robot.id] = robot
        for pedido in completados:
            if pedido in self.pedidos_cola:
                self.pedidos_cola.remove(pedido)
            self.pedidos_completados.append(pedido)

        # Acumular en buffers de delta
        self._robots_delta.extend(robots_upd)
        self._grilla_delta.extend(g_delta)
        self._grilla_remove.extend(g_remove)
        self._pedidos_completados_add.extend(completados)
        self._eventos_pendientes.extend(eventos)

    def _procesar_turno_nocturno(self) -> None:
        """Ingreso nocturno por conveyors del Norte: los robots NORTE recogen cajas
        de las conveyors y las almacenan (motor.modos.DespachadorNocturno)."""
        if self._grilla is None:
            raise RuntimeError("Llamar inicializar_desde_bus() primero.")

        if self._desp_nocturno is None:
            self._desp_nocturno = DespachadorNocturno()

        # El dispatcher consume de `cola_reposicion` directamente (pop por asignación).
        robots_upd, g_delta, g_remove, eventos = self._desp_nocturno.tick(
            self._grilla,
            self.robots,
            self.cola_reposicion,
            self._acum,
        )

        for robot in robots_upd:
            self.robots[robot.id] = robot

        self._robots_delta.extend(robots_upd)
        self._grilla_delta.extend(g_delta)
        self._grilla_remove.extend(g_remove)
        self._eventos_pendientes.extend(eventos)

    def _resolver_colisiones(self) -> None:
        """Cesión de paso: el despachador ya maneja los bloqueos y emite eventos.
        Este método queda como stub para preservar el ciclo de tick."""
        for robot in self.robots.values():
            if robot.estado == RobotEstado.BLOQUEADO:
                pass  # evento ya emitido por el despachador

    # ------------------------------------------------------------------
    # KPIs — delega en motor.kpis (Manuel — T-20)
    # ------------------------------------------------------------------

    def _actualizar_kpis(self) -> None:
        """Recalcula KPISet y lo acumula en buffers para el TickDelta.
        Implementación real en motor/kpis.py (Manuel)."""
        try:
            from motor.kpis import calcular_kpis
            if self._grilla is not None:
                config = self._grilla.config
                self.kpis = calcular_kpis(self._acum, self._grilla, config)
        except NotImplementedError:
            pass  # kpis.py todavía en stub — no bloquea el resto del tick

        self._eventos_pendientes.append({
            "tipo": "kpi_update",
            "tick": self.tick,
            "kpis": self.kpis.as_dict(),
        })

    # ------------------------------------------------------------------
    # Comunicación con el bus — único punto de escritura
    # ------------------------------------------------------------------

    def _construir_delta(self) -> TickDelta:
        """Empaqueta los buffers en un TickDelta y los vacía."""
        # Sincronizar grilla_delta desde la grilla si hay cambios pendientes
        if self._grilla is not None:
            g_delta, g_remove = self._grilla.flush_delta()
            self._grilla_delta.extend(g_delta)
            self._grilla_remove.extend(g_remove)

        self._last_tick_eventos = list(self._eventos_pendientes)

        delta = TickDelta(
            grilla_delta=self._grilla_delta or None,
            grilla_remove=self._grilla_remove or None,
            robots_delta=self._robots_delta or None,
            pedidos_cola=list(self.pedidos_cola) if self.pedidos_cola is not None else None,
            pedidos_completados_add=self._pedidos_completados_add or None,
            kpis=self.kpis,
            modo=self._modo_pendiente,
            eventos=list(self._eventos_pendientes),
        )

        self._grilla_delta = []
        self._grilla_remove = []
        self._robots_delta = []
        self._pedidos_completados_add = []
        self._eventos_pendientes = []
        self._modo_pendiente = None

        return delta
