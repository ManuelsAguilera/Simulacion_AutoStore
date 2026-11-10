"""motor/despachador.py — Despachador central de M2 (T-12, T-15, T-16, T-17).

Máquina de estados por robot. Cada tick:
  1. Asigna tarea a robots INACTIVOS (aplica política activa).
  2. Avanza cada robot un paso en su fase actual.
  3. Resuelve colisiones XY: cesión de paso (T-17).

Fases de una tarea de picking:
  mover_a_objetivo → excavar → recuperar → mover_a_puerto → entregar

Acceso por ganchos (T-15): el robot puede acceder a cualquier z de una columna.
Excavación (T-16): las cajas encima del objetivo se mueven a columnas adyacentes
libres, una por tick, antes de recuperar la caja objetivo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bus_persistencia.models.state import (
    Caja,
    Estacion,
    Orientacion,
    Pedido,
    PoliticaPicking,
    Robot,
    RobotEstado,
    celdas_desde,
    celdas_robot,
    cuerpo_para_punta_en,
)
from motor.colmena import (
    COSTO_ROTACION_TICKS,
    RADIO_HANDOFF,
    ReservationTable,
    WaitForGraph,
    distancia_manhattan,
)
from motor.escorts import (
    HORIZONTE_REPLANIFICACION,
    Escort,
    EscortPlanner,
    StagnationDetector,
    _es_excavacion,
)
from motor.politicas import POLITICAS

if TYPE_CHECKING:
    from motor.grilla import Grilla
    from motor.kpis import Acumuladores

UMBRAL_RERUTA = 3  # ticks bloqueado consecutivos antes de recalcular ruta (BFS)

# ------------------------------------------------------------------
# Tarea interna — una por robot activo
# ------------------------------------------------------------------

@dataclass
class Tarea:
    pedido: Pedido
    caja_objetivo: Caja
    ruta_entrada: list[tuple[int, int]]   # pasos XY restantes hacia columna objetivo
    ruta_salida: list[tuple[int, int]]    # pasos XY restantes hacia puerto
    puerto: tuple[int, int]
    fase: str = field(default="mover_a_objetivo")
    tick_inicio: int = field(default=0)
    # fases: mover_a_objetivo | excavar | recuperar | mover_a_puerto | entregar
    # --- Extensiones M3 ---
    estacion: Estacion | None = field(default=None)  # estación de entrega (Feature 2)
    ticks_rotando: int = field(default=0)            # ticks ya girando (Feature 3)
    ticks_bloqueado_consecutivos: int = field(default=0)  # para reruta por bloqueo persistente
    # --- Coordinación de escorts en excavación (anti-livelock @95%) ---
    profundidad_inicial: int = field(default=-1)     # cajas sobre el objetivo al empezar a excavar (-1 = sin fijar)
    ultimo_progreso_medido: int = field(default=0)   # progreso neto medido en el último tick
    ticks_sin_progreso: int = field(default=0)       # alimenta el StagnationDetector
    escort_asignado: Escort | None = field(default=None)  # columna-escort de descanso asignada
    # --- Colaboración NORTE→E/O (Fase 5) ---
    # Un robot NORTE no entrega en estación: tras recuperar, ESPERA a que un robot
    # E/O ocioso se le acerque y reciba la caja. `receptor_handoff` es el E/O
    # reservado para recibir; `emisor_handoff` (en la tarea del receptor) es el
    # NORTE del que recibirá.
    receptor_handoff: int | None = field(default=None)
    emisor_handoff: int | None = field(default=None)
    niveles_izados: int = 0


# ------------------------------------------------------------------
# Despachador
# ------------------------------------------------------------------

class Despachador:
    """Cerebro central de M2: asigna y ejecuta rutas de robots por tick.

    El simulador instancia un único Despachador y lo llama en cada tick del
    turno diurno. Los robots no toman decisiones propias — el despachador
    asigna la ruta completa y ellos solo la ejecutan paso a paso.
    """

    def __init__(self, grilla: "Grilla") -> None:
        self.grilla = grilla
        self._tareas: dict[int, Tarea] = {}      # robot_id -> Tarea activa
        self._cajas_reservadas: set[tuple[int, int, int]] = set()  # celdas ya asignadas
        self._tick_actual: int = 0
        # El plan experimental puede fijar una caja por solicitud para conservar
        # identidad entre preparación física y recuperación.
        self.cajas_objetivo_por_pedido: dict[str, str] = {}
        self.preparacion_fisica_activa = False

        # --- Mente Colmena (Feature 4) ---
        # Estaciones Cinta/Carrusel declaradas en config; () = modo puerto clásico.
        self.estaciones: tuple[Estacion, ...] = tuple(
            getattr(grilla.config, "estaciones", ()) or ()
        )
        self._estaciones_por_pos: dict[tuple[int, int], Estacion] = {
            (e.x, e.y): e for e in self.estaciones
        }
        self._servidos: dict[str, int] = {}          # estacion_id -> servidos este tick
        self._espera_handoff: dict[int, int] = {}    # robot_id -> ticks esperando (aging)
        self.reservation_table = ReservationTable()
        self.wait_for_graph = WaitForGraph()

        # --- Coordinación de celdas libres en excavación (anti-livelock) ---
        self.escort_planner = EscortPlanner()
        self.stagnation_detector = StagnationDetector()
        # Columnas protegidas (objetivo) y reservadas (escort) del tick actual;
        # refrescadas antes del Paso 3 y leídas por _fase_excavar.
        self._cols_protegidas: set[tuple[int, int]] = set()
        self._cols_reservadas: set[tuple[int, int]] = set()

        # --- Colaboración NORTE→E/O (Fase 5) ---
        # E/O ociosos reservados como receptores de handoff (no toman pedidos).
        self._receptores_reservados: set[int] = set()
        # norte_id -> eo_id reservado para recibir su caja.
        self._handoff_pares: dict[int, int] = {}
        # norte_id -> ticks que lleva esperando que el receptor llegue (timeout).
        self._handoff_edad: dict[int, int] = {}

    @property
    def usa_estaciones(self) -> bool:
        """True si hay estaciones Cinta/Carrusel configuradas (activa Features 2/3/4)."""
        return bool(self.estaciones)

    def tick(
        self,
        robots: dict[int, Robot],
        pedidos_cola: list[Pedido],
        politica: str,
        acum: "Acumuladores",
    ) -> tuple[
        list[Robot],
        list[Caja],
        list[tuple[int, int, int]],
        list[Pedido],
        list[dict],
    ]:
        """Ejecuta un paso de simulación para todos los robots en turno diurno.

        Retorna:
            robots_actualizados  — robots que cambiaron este tick
            grilla_delta         — cajas agregadas/modificadas
            grilla_remove        — coordenadas de celdas vaciadas
            pedidos_completados  — pedidos entregados en puerto este tick
            eventos              — dicts con vocabulario del bus
        """
        self._tick_actual += 1

        grilla_delta: list[Caja] = []
        grilla_remove: list[tuple[int, int, int]] = []
        pedidos_completados: list[Pedido] = []
        eventos: list[dict] = []

        # Mente Colmena: reiniciar estado transitorio del tick (Feature 4).
        self._servidos = {e.id: 0 for e in self.estaciones}
        self.reservation_table.reset()
        self.wait_for_graph.reset()

        # Copia mutable del estado de robots para este tick
        robots_estado: dict[int, Robot] = dict(robots)

        # Acumula robots cuyo estado cambia este tick (delta para el bus).
        robots_modificados: list[Robot] = []

        # Paso 0: normalizar "zombies" — un robot sin tarea y sin carga que quedó
        # en un estado no-INACTIVO (p.ej. un receptor de handoff que hizo timeout
        # estando BLOQUEADO) debe volver a INACTIVO para poder recibir tareas o ser
        # reservado como receptor. Sin esto, queda inerte y provoca livelock.
        for rid, r in list(robots_estado.items()):
            if (rid not in self._tareas and rid not in self._receptores_reservados
                    and r.carga_id is None and r.estado != RobotEstado.INACTIVO):
                r2 = _cambiar_estado(r, RobotEstado.INACTIVO)
                robots_estado[rid] = r2
                robots_modificados.append(r2)

        # Paso 1: asignar tareas a robots INACTIVOS sin tarea
        politica_fn = POLITICAS[politica]
        pedidos_disponibles = [
            p for p in pedidos_cola
            if not any(t.pedido.id_pedido == p.id_pedido for t in self._tareas.values())
        ]
        for robot in robots_estado.values():
            if robot.estado == RobotEstado.INACTIVO and robot.id not in self._tareas:
                # Los E/O reservados como receptores de handoff no toman pedidos.
                if robot.id in self._receptores_reservados:
                    continue
                pedido = politica_fn(pedidos_disponibles, self.grilla, self.grilla.puertos)
                if pedido is None:
                    continue
                tarea = self._crear_tarea(robot, pedido)
                if tarea is None:
                    continue
                tarea.tick_inicio = self._tick_actual
                self._tareas[robot.id] = tarea
                eventos.append({"tipo": "pedido_asignado", "id_pedido": pedido.id_pedido,
                                "robot_id": robot.id})
                pedidos_disponibles.remove(pedido)

        # Paso 2: mapa de ocupación (ambas celdas de cada robot 1×2) para colisiones
        posiciones_actuales: dict[tuple[int, int], int] = _mapa_ocupacion(robots_estado)
        self.reservation_table.sembrar(posiciones_actuales)

        # Paso 2.4: Mente Colmena — handoff de orientación (Feature 4).
        # Un robot cargado y mal orientado en su estación cede la carga a un
        # vecino ocioso ya orientado, evitando el costo de rotación.
        if self.usa_estaciones:
            self._handoff_prepass(robots_estado, robots_modificados, eventos)

        # Paso 2.4b: Colaboración NORTE→E/O (Fase 5) — los robots NORTE cargados
        # ceden su caja a un robot E/O ocioso que la lleva a su estación.
        self._colaboracion_norte_prepass(robots_estado, robots_modificados, eventos)

        # Paso 2.5: Ceder paso — INACTIVOS se apartan si bloquean.
        #           Soporta cascada de 2 niveles: si el bloqueador directo
        #           no tiene celda libre, empuja a un vecino ocioso primero.
        for robot in list(robots_estado.values()):
            tarea = self._tareas.get(robot.id)
            if tarea is None or robot.estado != RobotEstado.BLOQUEADO:
                continue
            if tarea.fase in ("mover_a_objetivo", "ir_a_recibir") and tarea.ruta_entrada:
                siguiente = tarea.ruta_entrada[0]
            elif tarea.fase == "mover_a_puerto" and tarea.ruta_salida:
                siguiente = tarea.ruta_salida[0]
            else:
                continue
            # Cualquier celda del footprint destino puede estar ocupada por un
            # robot ocioso (ej. su punta sobre la celda-estación). Apartarlo todo.
            next_cells = celdas_desde(siguiente[0], siguiente[1], robot.orientacion)
            for celda in next_cells:
                ocupante_id = posiciones_actuales.get(celda)
                if ocupante_id is None or ocupante_id == robot.id:
                    continue
                ocupante = robots_estado.get(ocupante_id)
                if ocupante is None or ocupante.estado != RobotEstado.INACTIVO:
                    continue
                if ocupante_id in self._tareas:
                    continue
                _intentar_ceder_paso(
                    ocupante, robot.id, robots_estado, posiciones_actuales,
                    self._tareas, self.grilla, robots_modificados, eventos,
                    depth=2,
                )

        # Paso 2.6: Ruptura de deadlock por nudge footprint-aware.
        # Con robots 1×2 de orientación fija, dos robots tareados pueden bloquearse
        # mutuamente en una "calle" (ej. uno parado sobre la celda-cuerpo de entrega
        # del otro). El swap directo es inseguro con footprints, así que en su lugar
        # se EMPUJA al robot bloqueador hacia una celda libre adyacente (típicamente
        # el anillo) y se recalcula su ruta. Determinista: itera por id ascendente.
        robots_movidos_swap: set[int] = set()  # reservado (compat. Paso 3)
        nudged: set[int] = set()
        for robot in sorted(robots_estado.values(), key=lambda r: r.id):
            tarea = self._tareas.get(robot.id)
            if tarea is None or robot.estado != RobotEstado.BLOQUEADO:
                continue
            if tarea.fase == "mover_a_objetivo" and tarea.ruta_entrada:
                siguiente = tarea.ruta_entrada[0]
            elif tarea.fase == "mover_a_puerto" and tarea.ruta_salida:
                siguiente = tarea.ruta_salida[0]
            else:
                continue
            # Celdas del footprint que el robot necesita libres para avanzar.
            next_cells = celdas_desde(siguiente[0], siguiente[1], robot.orientacion)
            bloqueador_id = next(
                (posiciones_actuales[c] for c in next_cells
                 if posiciones_actuales.get(c) not in (None, robot.id)),
                None,
            )
            if bloqueador_id is None or bloqueador_id in nudged:
                continue
            b = robots_estado.get(bloqueador_id)
            if b is None:
                continue
            # Empujar al bloqueador (tareado u ocioso) fuera del footprint que el
            # robot necesita. Un bloqueador ocioso solo se aparta; uno tareado
            # además recalcula su ruta.
            btarea = self._tareas.get(bloqueador_id)
            evitar = set(celdas_robot(robot)) | set(next_cells)
            movido = _nudge_robot(
                b, btarea, robots_estado, posiciones_actuales,
                self.grilla, eventos, evitar, acum,
            )
            if movido is not None:
                robots_modificados.append(movido)
                nudged.add(b.id)

        # Paso 2.9: Coordinación de escorts en excavación (anti-livelock @95%).
        # Mide progreso neto y replanifica la asignación de columnas-escort por
        # horizonte rodante o ante estancamiento, antes de avanzar a los robots.
        tareas_excavacion = [t for t in self._tareas.values() if _es_excavacion(t)]
        if tareas_excavacion:
            self.stagnation_detector.actualizar(tareas_excavacion, self.grilla)
            necesita_replan = (
                self._tick_actual % HORIZONTE_REPLANIFICACION == 0
                or self.stagnation_detector.hay_estancamiento(tareas_excavacion)
                or any(t.escort_asignado is None for t in tareas_excavacion)
            )
            if necesita_replan:
                self.escort_planner.planificar(self._tareas, self.grilla, self._tick_actual)
        # Refrescar columnas protegidas/reservadas que leerá _fase_excavar.
        self._cols_protegidas = {
            (t.caja_objetivo.x, t.caja_objetivo.y)
            for t in self._tareas.values() if _es_excavacion(t)
        }
        self._cols_reservadas = {
            t.escort_asignado.columna
            for t in self._tareas.values() if t.escort_asignado is not None
        }

        # Paso 3: avanzar cada robot
        for robot in robots_estado.values():
            if robot.id in robots_movidos_swap:
                continue
            tarea = self._tareas.get(robot.id)
            if tarea is None:
                # Sin tarea: permanece INACTIVO sin cambios
                continue

            nuevo, g_d, g_r, completado, evs = self._avanzar(
                robot, tarea, posiciones_actuales, acum
            )

            if nuevo.id != robot.id or nuevo != robot:
                # Actualizar el footprint (2 celdas) en el mapa si se movió
                if (nuevo.x, nuevo.y) != (robot.x, robot.y):
                    _mover_en_mapa(posiciones_actuales, robot, nuevo)
                robots_estado[robot.id] = nuevo
                robots_modificados.append(nuevo)

            grilla_delta.extend(g_d)
            grilla_remove.extend(g_r)
            eventos.extend(evs)

            if completado is not None:
                pedidos_completados.append(completado)
                self._cajas_reservadas.discard(
                    (tarea.caja_objetivo.x, tarea.caja_objetivo.y, tarea.caja_objetivo.z)
                )
                del self._tareas[robot.id]

        return robots_modificados, grilla_delta, grilla_remove, pedidos_completados, eventos

    # ------------------------------------------------------------------
    # Creación de tareas
    # ------------------------------------------------------------------

    def _caja_disponible(self, id_sku: str, id_pedido: str | None = None) -> Caja | None:
        """Similar a grilla.primera_caja_accesible pero excluye celdas reservadas
        y columnas donde otro robot ya está trabajando (EXCAVANDO/RECUPERANDO)."""
        candidatas = self.grilla.buscar_por_sku(id_sku)
        objetivo_id = self.cajas_objetivo_por_pedido.get(id_pedido) if id_pedido else None
        if objetivo_id is not None:
            candidatas = [c for c in candidatas if c.id_caja == objetivo_id]
        candidatas = [c for c in candidatas if (c.x, c.y, c.z) not in self._cajas_reservadas]
        # Excluir columnas donde otro robot ya está excavando/recuperando
        # (físicamente presente en la columna). Evita bloqueo mutuo.
        columnas_ocupadas = {
            (t.caja_objetivo.x, t.caja_objetivo.y)
            for t in self._tareas.values()
            if t.fase in ("excavar", "recuperar")
        }
        candidatas = [c for c in candidatas if (c.x, c.y) not in columnas_ocupadas]
        if not candidatas:
            return None

        def costo_excavacion(c: Caja) -> int:
            gz = self.grilla.config.grilla.z
            return sum(1 for z in range(c.z + 1, gz) if self.grilla.ocupada(c.x, c.y, z))
        return min(candidatas, key=costo_excavacion)

    def _crear_tarea(self, robot: Robot, pedido: Pedido) -> Tarea | None:
        """Busca una caja no reservada y genera la ruta para un robot 1×2.

        Estacionamiento por punta: el robot lleva su CUERPO a la celda adyacente
        tal que su PUNTA quede sobre la columna objetivo (para excavar/recuperar)
        y, al entregar, sobre la celda-estación. La estación debe ser compatible
        con la orientación fija del robot. Retorna None si no hay caja/estación."""
        caja = self._caja_disponible(pedido.id_sku, pedido.id_pedido)
        if caja is None:
            return None

        # Cuerpo-objetivo de pick: la punta debe caer sobre la columna de la caja.
        cuerpo_pick = cuerpo_para_punta_en(caja.x, caja.y, robot.orientacion)
        ruta_entrada = _ruta_xy((robot.x, robot.y), cuerpo_pick)
        self._cajas_reservadas.add((caja.x, caja.y, caja.z))

        if robot.orientacion == Orientacion.NORTE:
            # NORTE no entrega en estación: tras recuperar, esperará el handoff a
            # un robot E/O que lleve la caja a su estación (Fase 5).
            return Tarea(
                pedido=pedido,
                caja_objetivo=caja,
                ruta_entrada=ruta_entrada,
                ruta_salida=[],
                puerto=cuerpo_pick,
                estacion=None,
            )

        # E/O: estación de entrega compatible con la orientación del robot.
        estacion = self.grilla.estacion_compatible_mas_cercana(
            caja.x, caja.y, robot.orientacion
        )
        if estacion is None:
            return None
        cuerpo_entrega = cuerpo_para_punta_en(estacion.x, estacion.y, robot.orientacion)
        ruta_salida = _ruta_xy(cuerpo_pick, cuerpo_entrega)

        return Tarea(
            pedido=pedido,
            caja_objetivo=caja,
            ruta_entrada=ruta_entrada,
            ruta_salida=ruta_salida,
            puerto=cuerpo_entrega,
            estacion=estacion,
        )

    def _estacion_mas_cercana(self, x: int, y: int) -> Estacion | None:
        """Estación con menor distancia Manhattan a la columna (x,y), o None si
        no hay estaciones configuradas (modo puerto clásico)."""
        if not self.estaciones:
            return None
        return min(self.estaciones, key=lambda e: distancia_manhattan((e.x, e.y), (x, y)))

    # ------------------------------------------------------------------
    # Avance de un robot un paso (máquina de estados)
    # ------------------------------------------------------------------

    def _avanzar(
        self,
        robot: Robot,
        tarea: Tarea,
        posiciones_actuales: dict[tuple[int, int], int],
        acum: "Acumuladores",
    ) -> tuple[Robot, list[Caja], list[tuple], Pedido | None, list[dict]]:
        """Avanza el robot un tick según la fase actual de su tarea."""
        if tarea.fase == "mover_a_objetivo":
            return self._fase_mover_a_objetivo(robot, tarea, posiciones_actuales, acum)
        elif tarea.fase == "excavar":
            return self._fase_excavar(robot, tarea, acum)
        elif tarea.fase == "recuperar":
            return self._fase_recuperar(robot, tarea, acum)
        elif tarea.fase == "recuperar_izado":
            return self._fase_recuperar_izado(robot, tarea)
        elif tarea.fase == "mover_a_puerto":
            return self._fase_mover_a_puerto(robot, tarea, posiciones_actuales, acum)
        elif tarea.fase == "ir_a_recibir":
            return self._fase_ir_a_recibir(robot, tarea, posiciones_actuales, acum)
        elif tarea.fase == "esperar_handoff":
            # NORTE cargado: espera quieto a que el receptor E/O llegue y reciba.
            return robot, [], [], None, []
        elif tarea.fase == "entregar":
            return self._fase_entregar(robot, tarea, acum)
        # Estado desconocido: no hacer nada
        return robot, [], [], None, []

    _AvanceResult = tuple[Robot, list[Caja], list[tuple[int, int, int]], Pedido | None, list[dict]]

    def _fase_mover_a_objetivo(
        self, robot: Robot, tarea: Tarea, posiciones_actuales: dict[tuple[int, int], int], acum: "Acumuladores",
    ) -> _AvanceResult:
        if not tarea.ruta_entrada:
            # Ya estamos en la columna objetivo
            tarea.fase = "excavar"
            return self._fase_excavar(robot, tarea, acum)

        siguiente = tarea.ruta_entrada[0]
        if not _footprint_libre(siguiente, robot.orientacion, robot.id, posiciones_actuales, self.grilla):
            tarea.ticks_bloqueado_consecutivos += 1
            if tarea.ticks_bloqueado_consecutivos >= UMBRAL_RERUTA:
                destino = cuerpo_para_punta_en(
                    tarea.caja_objetivo.x, tarea.caja_objetivo.y, robot.orientacion
                )
                nueva_ruta = _ruta_xy_evitando(
                    (robot.x, robot.y), destino, robot.orientacion,
                    robot.id, posiciones_actuales, self.grilla,
                )
                if nueva_ruta is not None and nueva_ruta != tarea.ruta_entrada:
                    tarea.ruta_entrada = list(nueva_ruta)
                    tarea.ticks_bloqueado_consecutivos = 0
                    paso = tarea.ruta_entrada[0] if tarea.ruta_entrada else None
                    if paso and _footprint_libre(paso, robot.orientacion, robot.id, posiciones_actuales, self.grilla):
                        tarea.ruta_entrada.pop(0)
                        acum.total_desplazamientos += 1
                        nuevo = Robot(id=robot.id, x=paso[0], y=paso[1], z=robot.z,
                                      estado=RobotEstado.DESPLAZANDOSE, carga_id=robot.carga_id,
                                      orientacion=robot.orientacion)
                        evs: list[dict] = [_ev_movimiento(nuevo), {
                            "tipo": "reruta", "robot_id": robot.id,
                            "motivo": "bloqueo_persistente", "fase": "mover_a_objetivo",
                        }]
                        return nuevo, [], [], None, evs
                # BFS failed or returned same route — cancel task so robot
                # can be reassigned on the next tick.
                self._cajas_reservadas.discard(
                    (tarea.caja_objetivo.x, tarea.caja_objetivo.y, tarea.caja_objetivo.z)
                )
                del self._tareas[robot.id]
                nuevo = _cambiar_estado(robot, RobotEstado.INACTIVO, carga_id=None)
                return nuevo, [], [], None, [{
                    "tipo": "tarea_cancelada", "robot_id": robot.id,
                    "motivo": "bloqueo_persistente_sin_reruta",
                    "id_pedido": tarea.pedido.id_pedido,
                }]

            nuevo = _cambiar_estado(robot, RobotEstado.BLOQUEADO)
            acum.ticks_bloqueados += 1
            return nuevo, [], [], None, [_ev_bloqueo(robot)]

        tarea.ticks_bloqueado_consecutivos = 0
        tarea.ruta_entrada.pop(0)
        acum.total_desplazamientos += 1
        nuevo = Robot(id=robot.id, x=siguiente[0], y=siguiente[1], z=robot.z,
                      estado=RobotEstado.DESPLAZANDOSE, carga_id=robot.carga_id,
                      orientacion=robot.orientacion)
        return nuevo, [], [], None, [_ev_movimiento(nuevo)]

    def _fase_excavar(self, robot: Robot, tarea: Tarea, acum: "Acumuladores") -> _AvanceResult:
        col = self.grilla.columna(tarea.caja_objetivo.x, tarea.caja_objetivo.y)
        z_obj = tarea.caja_objetivo.z
        encima = [c for c in col if c.z > z_obj]

        if not encima:
            tarea.fase = "recuperar"
            return self._fase_recuperar(robot, tarea, acum)

        if self.preparacion_fisica_activa:
            motivo = (f"Excavación residual inesperada para {tarea.pedido.id_pedido}: "
                      f"{len(encima)} cajas bloquean {tarea.caja_objetivo.id_caja}")
            plan = getattr(self, "plan_olas", None)
            if plan is not None:
                plan.fallo = motivo
            return robot, [], [], None, [{"tipo":"fallo_preparacion_fisica","motivo":motivo,
                                          "id_pedido":tarea.pedido.id_pedido}]

        # Coordinación de escorts (anti-livelock @95%): la columna de descanso la
        # asigna el EscortPlanner (ver motor/escorts.py). Sin escort asignado este
        # ciclo, el robot ESPERA — esto serializa las excavaciones que compiten por
        # las pocas celdas libres y rompe el ciclo degenerativo. La caja siempre se
        # deposita en una columna NO protegida (nunca se re-entierra otro objetivo).
        if tarea.escort_asignado is None:
            nuevo = _cambiar_estado(robot, RobotEstado.EXCAVANDO)
            return nuevo, [], [], None, [{
                "tipo": "espera_escort", "robot_id": robot.id,
                "id_pedido": tarea.pedido.id_pedido,
                "columna": [tarea.caja_objetivo.x, tarea.caja_objetivo.y],
            }]

        caja_mover = max(encima, key=lambda c: c.z)
        destino_cell = self.escort_planner.mover_escort_un_paso(
            caja_mover, tarea, self.grilla,
            self._cols_protegidas, self._cols_reservadas,
        )
        if destino_cell is None:
            # Sin celda segura disponible este tick: esperar (se replanificará).
            nuevo = _cambiar_estado(robot, RobotEstado.EXCAVANDO)
            return nuevo, [], [], None, [{"tipo": "espera_espacio_excavacion",
                                         "robot_id": robot.id, "id_pedido": tarea.pedido.id_pedido}]

        ax, ay, z_libre = destino_cell
        self.grilla.remover(caja_mover.x, caja_mover.y, caja_mover.z)
        caja_nueva = Caja(
            id_caja=caja_mover.id_caja,
            id_sku=caja_mover.id_sku,
            cantidad=caja_mover.cantidad,
            x=ax, y=ay, z=z_libre,
        )
        self.grilla.agregar(caja_nueva)
        acum.total_desplazamientos += 1
        g_d = [caja_nueva]
        g_r = [(caja_mover.x, caja_mover.y, caja_mover.z)]
        nuevo = _cambiar_estado(robot, RobotEstado.EXCAVANDO)
        ev = {"tipo": "excavacion", "robot_id": robot.id,
              "id_caja": caja_mover.id_caja, "id_pedido": tarea.pedido.id_pedido,
              "de": [caja_mover.x, caja_mover.y, caja_mover.z],
              "a": [ax, ay, z_libre]}
        return nuevo, g_d, g_r, None, [ev]

    def _fase_recuperar(self, robot: Robot, tarea: Tarea, acum: "Acumuladores") -> _AvanceResult:
        # Verificar que la caja objetivo sigue en su lugar
        caja = self.grilla.get(
            tarea.caja_objetivo.x, tarea.caja_objetivo.y, tarea.caja_objetivo.z
        )
        if caja is None:
            # La caja ya no está (caso edge): cancelar tarea y liberar robot
            self._cajas_reservadas.discard(
                (tarea.caja_objetivo.x, tarea.caja_objetivo.y, tarea.caja_objetivo.z)
            )
            del self._tareas[robot.id]
            nuevo = _cambiar_estado(robot, RobotEstado.INACTIVO, carga_id=None)
            return nuevo, [], [], None, []

        self.grilla.remover(caja.x, caja.y, caja.z)
        acum.cajas_recuperadas += 1
        acum.total_desplazamientos += 1

        if not self.preparacion_fisica_activa:
            if tarea.estacion is None:
                tarea.fase = "esperar_handoff"
            else:
                tarea.ruta_salida = _ruta_xy((robot.x, robot.y), tarea.puerto)
                tarea.fase = "mover_a_puerto"
            nuevo = Robot(id=robot.id, x=robot.x, y=robot.y, z=robot.z,
                          estado=RobotEstado.RECUPERANDO, carga_id=caja.id_caja,
                          orientacion=robot.orientacion)
            return nuevo, [], [(caja.x, caja.y, caja.z)], None, [{
                "tipo":"caja_recuperada","robot_id":robot.id,"id_caja":caja.id_caja,
                "id_sku":caja.id_sku,"x":caja.x,"y":caja.y,"z":caja.z}]

        tarea.niveles_izados = self.grilla.gz - caja.z
        tarea.fase = "recuperar_izado"
        nuevo = Robot(id=robot.id, x=robot.x, y=robot.y, z=robot.z,
                      estado=RobotEstado.RECUPERANDO, carga_id=caja.id_caja,
                      orientacion=robot.orientacion)
        g_r = [(caja.x, caja.y, caja.z)]
        ev = {"tipo":"caja_recuperada","robot_id":robot.id,"id_caja":caja.id_caja,
              "id_sku":caja.id_sku,"x":caja.x,"y":caja.y,"z":caja.z}
        return nuevo, [], g_r, None, [ev]

    def _fase_recuperar_izado(self, robot: Robot, tarea: Tarea) -> _AvanceResult:
        """Izado explícito por niveles antes de iniciar transporte al puerto."""
        if tarea.niveles_izados > 0:
            nivel = tarea.niveles_izados
            tarea.niveles_izados -= 1
            return robot, [], [], None, [{"tipo":"recuperacion_izado","fase":"recuperacion",
                                          "robot_id":robot.id,"id_caja":robot.carga_id,
                                          "nivel":nivel}]
        if tarea.estacion is None:
            tarea.fase = "esperar_handoff"
        else:
            tarea.ruta_salida = _ruta_xy((robot.x, robot.y), tarea.puerto)
            tarea.fase = "mover_a_puerto"
        return robot, [], [], None, []

    def _fase_mover_a_puerto(
        self, robot: Robot, tarea: Tarea, posiciones_actuales: dict[tuple[int, int], int], acum: "Acumuladores",
    ) -> _AvanceResult:
        if not tarea.ruta_salida:
            tarea.fase = "entregar"
            return self._fase_entregar(robot, tarea, acum)

        siguiente = tarea.ruta_salida[0]
        if not _footprint_libre(siguiente, robot.orientacion, robot.id, posiciones_actuales, self.grilla):
            tarea.ticks_bloqueado_consecutivos += 1
            if tarea.ticks_bloqueado_consecutivos >= UMBRAL_RERUTA:
                nueva_ruta = _ruta_xy_evitando(
                    (robot.x, robot.y), tarea.puerto, robot.orientacion,
                    robot.id, posiciones_actuales, self.grilla,
                )
                if nueva_ruta is not None and nueva_ruta != tarea.ruta_salida:
                    tarea.ruta_salida = list(nueva_ruta)
                    tarea.ticks_bloqueado_consecutivos = 0
                    paso = tarea.ruta_salida[0] if tarea.ruta_salida else None
                    if paso and _footprint_libre(paso, robot.orientacion, robot.id, posiciones_actuales, self.grilla):
                        tarea.ruta_salida.pop(0)
                        acum.total_desplazamientos += 1
                        nuevo = Robot(id=robot.id, x=paso[0], y=paso[1], z=robot.z,
                                      estado=RobotEstado.ENTREGANDO, carga_id=robot.carga_id,
                                      orientacion=robot.orientacion)
                        evs: list[dict] = [_ev_movimiento(nuevo), {
                            "tipo": "reruta", "robot_id": robot.id,
                            "motivo": "bloqueo_persistente", "fase": "mover_a_puerto",
                        }]
                        return nuevo, [], [], None, evs
                # BFS failed — robot carries a caja, can't cancel (would lose it).
                # Reset counter and keep waiting; the obstacle will eventually move.
                tarea.ticks_bloqueado_consecutivos = 0

            nuevo = _cambiar_estado(robot, RobotEstado.BLOQUEADO, carga_id=robot.carga_id)
            acum.ticks_bloqueados += 1
            return nuevo, [], [], None, [_ev_bloqueo(robot)]

        tarea.ticks_bloqueado_consecutivos = 0
        tarea.ruta_salida.pop(0)
        acum.total_desplazamientos += 1
        nuevo = Robot(id=robot.id, x=siguiente[0], y=siguiente[1], z=robot.z,
                      estado=RobotEstado.ENTREGANDO, carga_id=robot.carga_id,
                      orientacion=robot.orientacion)
        return nuevo, [], [], None, [_ev_movimiento(nuevo)]

    def _fase_ir_a_recibir(
        self, robot: Robot, tarea: Tarea, posiciones_actuales: dict[tuple[int, int], int], acum: "Acumuladores",
    ) -> _AvanceResult:
        """El receptor E/O se desplaza hacia el robot NORTE emisor. Cuando agota
        la ruta, espera adyacente a que el prepass de colaboración haga el transfer."""
        if not tarea.ruta_entrada:
            return robot, [], [], None, []  # adyacente: espera el transfer
        siguiente = tarea.ruta_entrada[0]
        if not _footprint_libre(siguiente, robot.orientacion, robot.id, posiciones_actuales, self.grilla):
            nuevo = _cambiar_estado(robot, RobotEstado.BLOQUEADO)
            acum.ticks_bloqueados += 1
            return nuevo, [], [], None, [_ev_bloqueo(robot)]
        tarea.ruta_entrada.pop(0)
        acum.total_desplazamientos += 1
        nuevo = Robot(id=robot.id, x=siguiente[0], y=siguiente[1], z=robot.z,
                      estado=RobotEstado.DESPLAZANDOSE, carga_id=None,
                      orientacion=robot.orientacion)
        return nuevo, [], [], None, [_ev_movimiento(nuevo)]

    def _fase_entregar(self, robot: Robot, tarea: Tarea, acum: "Acumuladores") -> _AvanceResult:
        est = tarea.estacion

        # --- Estación Cinta/Carrusel (Features 2 y 3) ---
        if est is not None:
            # 1) Capacidad por tick: Cinta=1, Carrusel=2. Si está saturada, el
            #    robot espera este tick sin liberar la celda (cuenta como bloqueo).
            if self._servidos.get(est.id, 0) >= est.capacidad_tick:
                acum.ticks_bloqueados += 1
                nuevo = _cambiar_estado(robot, RobotEstado.ENTREGANDO)
                return nuevo, [], [], None, [{
                    "tipo": "estacion_saturada", "robot_id": robot.id,
                    "estacion": est.id, "capacidad": est.capacidad_tick,
                }]

            # 2) Orientación: el robot NUNCA rota. La estación se eligió compatible
            #    con su orientación fija, así que aquí debe coincidir siempre. Si no
            #    coincide (caso defensivo), el robot no puede entregar: espera.
            if robot.orientacion != est.orientacion_requerida:
                acum.ticks_bloqueados += 1
                nuevo = _cambiar_estado(robot, RobotEstado.BLOQUEADO)
                return nuevo, [], [], None, [{
                    "tipo": "orientacion_incompatible", "robot_id": robot.id,
                    "estacion": est.id,
                    "orientacion_robot": robot.orientacion.value,
                    "orientacion_requerida": est.orientacion_requerida.value,
                }]

            # 3) Entrega efectiva: consume una unidad de capacidad de la estación.
            self._servidos[est.id] = self._servidos.get(est.id, 0) + 1
            self._espera_handoff.pop(robot.id, None)

        dt = self._tick_actual - tarea.tick_inicio
        acum.pedidos_completados += 1
        acum.suma_tiempos_ciclo += dt

        nuevo = Robot(id=robot.id, x=robot.x, y=robot.y, z=robot.z,
                      estado=RobotEstado.INACTIVO, carga_id=None,
                      orientacion=robot.orientacion)
        ev = {"tipo": "pedido_completado", "robot_id": robot.id,
              "id_pedido": tarea.pedido.id_pedido,
              "id_sku": tarea.pedido.id_sku,
              "ticks": dt,
              "estacion": est.id if est is not None else None}
        return nuevo, [], [], tarea.pedido, [ev]

    # ------------------------------------------------------------------
    # Mente Colmena — handoff de orientación (Feature 4)
    # ------------------------------------------------------------------

    def _handoff_prepass(
        self,
        robots_estado: dict[int, Robot],
        robots_modificados: list[Robot],
        eventos: list[dict],
    ) -> None:
        """Transfiere la carga de robots cargados pero mal orientados (parados en
        su estación) a un vecino ocioso ya orientado correctamente.

        Criterio TPTS: solo se acepta el handoff si el receptor puede entregar
        sin coste de rotación y su acercamiento a la estación no supera el coste
        de que el emisor rote (`<= COSTO_ROTACION_TICKS`). Determinista: itera
        robots por id ascendente y prioriza por aging (ticks_esperando_handoff).
        """
        # Robots candidatos a emitir handoff: cargados, en su estación, mal orientados.
        emisores = []
        for robot in robots_estado.values():
            tarea = self._tareas.get(robot.id)
            if tarea is None or tarea.estacion is None or robot.carga_id is None:
                continue
            est = tarea.estacion
            if (robot.x, robot.y) != (est.x, est.y):
                continue
            if robot.orientacion == est.orientacion_requerida:
                continue
            self._espera_handoff[robot.id] = self._espera_handoff.get(robot.id, 0) + 1
            emisores.append(robot)

        # Mayor aging primero; desempate por id para reproducibilidad.
        emisores.sort(key=lambda r: (-self._espera_handoff.get(r.id, 0), r.id))

        receptores_usados: set[int] = set()
        for emisor in emisores:
            tarea = self._tareas[emisor.id]
            est = tarea.estacion
            receptor = self._buscar_candidato_handoff(
                emisor, est, robots_estado, receptores_usados
            )
            if receptor is None:
                continue
            # TPTS: el receptor debe estar al menos tan cerca como el coste de rotar.
            dist = distancia_manhattan((receptor.x, receptor.y), (est.x, est.y))
            if dist > COSTO_ROTACION_TICKS:
                continue

            # Transferir tarea + carga al receptor; el emisor queda libre.
            receptores_usados.add(receptor.id)
            nueva_ruta = _ruta_xy((receptor.x, receptor.y), (est.x, est.y))
            tarea.ruta_salida = nueva_ruta
            tarea.fase = "mover_a_puerto" if nueva_ruta else "entregar"
            del self._tareas[emisor.id]
            self._tareas[receptor.id] = tarea
            self._espera_handoff.pop(emisor.id, None)

            receptor_upd = Robot(
                id=receptor.id, x=receptor.x, y=receptor.y, z=receptor.z,
                estado=RobotEstado.ENTREGANDO, carga_id=emisor.carga_id,
                orientacion=receptor.orientacion,
            )
            emisor_upd = Robot(
                id=emisor.id, x=emisor.x, y=emisor.y, z=emisor.z,
                estado=RobotEstado.INACTIVO, carga_id=None,
                orientacion=emisor.orientacion,
            )
            robots_estado[receptor.id] = receptor_upd
            robots_estado[emisor.id] = emisor_upd
            robots_modificados.append(receptor_upd)
            robots_modificados.append(emisor_upd)
            eventos.append({
                "tipo": "handoff", "de_robot": emisor.id, "a_robot": receptor.id,
                "id_caja": emisor.carga_id, "estacion": est.id,
            })

    # ------------------------------------------------------------------
    # Colaboración NORTE → E/O (Fase 5)
    # ------------------------------------------------------------------

    _UMBRAL_HANDOFF_TIMEOUT = 40  # ticks antes de soltar un receptor que no llega

    def _colaboracion_norte_prepass(
        self,
        robots_estado: dict[int, Robot],
        robots_modificados: list[Robot],
        eventos: list[dict],
    ) -> None:
        """Coordina el traspaso de cajas de robots NORTE (que no entregan en
        estación) a robots E/O ociosos que las llevan a su estación compatible.

        Por cada NORTE cargado y esperando: reserva el E/O ocioso más cercano y lo
        encamina hacia él; cuando el receptor llega adyacente, transfiere la caja.
        Determinista: itera emisores por id ascendente."""
        # Liberar reservas obsoletas (emisor que ya no espera o receptor inválido).
        for norte_id in list(self._handoff_pares):
            eo_id = self._handoff_pares[norte_id]
            ntarea = self._tareas.get(norte_id)
            eotarea = self._tareas.get(eo_id)
            valido = (
                ntarea is not None and ntarea.fase == "esperar_handoff"
                and eotarea is not None and eotarea.fase == "ir_a_recibir"
            )
            if not valido:
                self._liberar_reserva_handoff(norte_id)

        emisores = sorted(
            (r for r in robots_estado.values()
             if r.orientacion == Orientacion.NORTE and r.carga_id is not None
             and self._tareas.get(r.id) is not None
             and self._tareas[r.id].fase == "esperar_handoff"),
            key=lambda r: r.id,
        )
        for norte in emisores:
            eo_id = self._handoff_pares.get(norte.id)
            if eo_id is None:
                eo = self._reservar_receptor(norte, robots_estado, robots_modificados)
                if eo is None:
                    continue
                eo_id = eo.id
            else:
                # Timeout: si el receptor no llega, soltarlo y reintentar luego.
                self._handoff_edad[norte.id] = self._handoff_edad.get(norte.id, 0) + 1
                if self._handoff_edad[norte.id] > self._UMBRAL_HANDOFF_TIMEOUT:
                    self._liberar_reserva_handoff(norte.id)
                    continue

            eo = robots_estado.get(eo_id)
            if eo is None:
                continue
            if distancia_manhattan((eo.x, eo.y), (norte.x, norte.y)) <= 2:
                self._transferir_handoff(norte, eo, robots_estado, robots_modificados, eventos)
            else:
                # Aún no llega: si agotó su ruta (o el emisor se movió), recalcular
                # el encuentro hacia la posición actual del NORTE.
                eotarea = self._tareas.get(eo_id)
                if eotarea is not None and eotarea.fase == "ir_a_recibir" and not eotarea.ruta_entrada:
                    objetivo = self._celda_rendezvous(eo, norte)
                    if objetivo is not None and objetivo != (eo.x, eo.y):
                        eotarea.ruta_entrada = _ruta_xy((eo.x, eo.y), objetivo)

    def _reservar_receptor(
        self, norte: Robot, robots_estado: dict[int, Robot], robots_modificados: list[Robot],
    ) -> Robot | None:
        """Reserva el robot E/O ocioso más cercano para recibir la caja del NORTE,
        le crea la tarea `ir_a_recibir` y lo encamina hacia él."""
        ntarea = self._tareas[norte.id]
        candidatos = [
            r for r in robots_estado.values()
            if r.orientacion in (Orientacion.ESTE, Orientacion.OESTE)
            and r.estado == RobotEstado.INACTIVO
            and r.id not in self._tareas
            and r.id not in self._receptores_reservados
        ]
        if not candidatos:
            return None
        eo = min(candidatos, key=lambda r: distancia_manhattan((r.x, r.y), (norte.x, norte.y)))
        objetivo = self._celda_rendezvous(eo, norte)
        ruta = _ruta_xy((eo.x, eo.y), objetivo) if objetivo else []
        tarea_eo = Tarea(
            pedido=ntarea.pedido,
            caja_objetivo=ntarea.caja_objetivo,
            ruta_entrada=ruta,
            ruta_salida=[],
            puerto=(eo.x, eo.y),
            estacion=None,
            fase="ir_a_recibir",
            emisor_handoff=norte.id,
        )
        tarea_eo.tick_inicio = ntarea.tick_inicio
        self._tareas[eo.id] = tarea_eo
        self._handoff_pares[norte.id] = eo.id
        self._handoff_edad[norte.id] = 0
        self._receptores_reservados.add(eo.id)
        eo_upd = _cambiar_estado(eo, RobotEstado.DESPLAZANDOSE)
        robots_estado[eo.id] = eo_upd
        robots_modificados.append(eo_upd)
        return eo_upd

    def _celda_rendezvous(self, eo: Robot, norte: Robot) -> tuple[int, int] | None:
        """Celda-cuerpo adyacente al emisor donde el footprint del receptor cabe en
        la superficie; la más cercana al receptor."""
        validas = [
            c for c in self.grilla.celdas_adyacentes_superficie(norte.x, norte.y)
            if all(self.grilla.en_superficie(x, y)
                   for x, y in celdas_desde(c[0], c[1], eo.orientacion))
        ]
        if not validas:
            return None
        return min(validas, key=lambda c: distancia_manhattan(c, (eo.x, eo.y)))

    def _transferir_handoff(
        self, norte: Robot, eo: Robot,
        robots_estado: dict[int, Robot], robots_modificados: list[Robot], eventos: list[dict],
    ) -> None:
        """Transfiere la caja del NORTE al receptor E/O: el E/O queda cargado con
        una tarea de entrega a su estación compatible y el NORTE queda ocioso."""
        ntarea = self._tareas.get(norte.id)
        if ntarea is None:
            return
        caja = ntarea.caja_objetivo
        estacion = self.grilla.estacion_compatible_mas_cercana(caja.x, caja.y, eo.orientacion)
        if estacion is None:
            return
        cuerpo_entrega = cuerpo_para_punta_en(estacion.x, estacion.y, eo.orientacion)
        tarea_eo = Tarea(
            pedido=ntarea.pedido,
            caja_objetivo=caja,
            ruta_entrada=[],
            ruta_salida=_ruta_xy((eo.x, eo.y), cuerpo_entrega),
            puerto=cuerpo_entrega,
            estacion=estacion,
            fase="mover_a_puerto",
        )
        tarea_eo.tick_inicio = ntarea.tick_inicio
        self._tareas[eo.id] = tarea_eo
        eo_upd = Robot(id=eo.id, x=eo.x, y=eo.y, z=eo.z,
                       estado=RobotEstado.RECUPERANDO, carga_id=norte.carga_id,
                       orientacion=eo.orientacion)
        robots_estado[eo.id] = eo_upd
        robots_modificados.append(eo_upd)
        # NORTE suelta la caja y queda ocioso.
        del self._tareas[norte.id]
        self._cajas_reservadas.discard((caja.x, caja.y, caja.z))
        norte_upd = Robot(id=norte.id, x=norte.x, y=norte.y, z=norte.z,
                          estado=RobotEstado.INACTIVO, carga_id=None,
                          orientacion=norte.orientacion)
        robots_estado[norte.id] = norte_upd
        robots_modificados.append(norte_upd)
        self._liberar_reserva_handoff(norte.id)
        eventos.append({
            "tipo": "handoff", "de_robot": norte.id, "a_robot": eo.id,
            "id_caja": norte.carga_id, "estacion": estacion.id,
        })

    def _liberar_reserva_handoff(self, norte_id: int) -> None:
        eo_id = self._handoff_pares.pop(norte_id, None)
        self._handoff_edad.pop(norte_id, None)
        if eo_id is not None:
            self._receptores_reservados.discard(eo_id)
            # Si el receptor sigue solo "yendo a recibir" (no recibió), cancelar.
            tarea = self._tareas.get(eo_id)
            if tarea is not None and tarea.fase == "ir_a_recibir":
                del self._tareas[eo_id]

    def _buscar_candidato_handoff(
        self,
        emisor: Robot,
        est: Estacion,
        robots_estado: dict[int, Robot],
        excluidos: set[int],
    ) -> Robot | None:
        """Vecino (radio RADIO_HANDOFF) ocioso, sin carga y ya orientado a la
        orientación requerida por la estación. Elige el más cercano a la estación."""
        candidatos = [
            r for r in robots_estado.values()
            if r.id != emisor.id
            and r.id not in excluidos
            and r.carga_id is None
            and r.estado == RobotEstado.INACTIVO
            and r.id not in self._tareas
            and r.orientacion == est.orientacion_requerida
            and distancia_manhattan((r.x, r.y), (emisor.x, emisor.y)) <= RADIO_HANDOFF
        ]
        if not candidatos:
            return None
        return min(candidatos, key=lambda r: distancia_manhattan((r.x, r.y), (est.x, est.y)))


# ------------------------------------------------------------------
# Helpers puros
# ------------------------------------------------------------------

def _ruta_xy(origen: tuple[int, int], destino: tuple[int, int]) -> list[tuple[int, int]]:
    """Ruta L-shaped en XY: mueve X primero, luego Y."""
    x, y = origen
    dx, dy = destino
    pasos: list[tuple[int, int]] = []
    while x != dx:
        x += 1 if dx > x else -1
        pasos.append((x, y))
    while y != dy:
        y += 1 if dy > y else -1
        pasos.append((x, y))
    return pasos


def _ruta_xy_evitando(
    origen: tuple[int, int],
    destino: tuple[int, int],
    orientacion: Orientacion,
    robot_id: int,
    posiciones: dict[tuple[int, int], int],
    grilla: "Grilla",
) -> list[tuple[int, int]] | None:
    """BFS corta sobre posiciones del CUERPO que rodea robots ocupando celdas.

    Cada paso valida que el footprint 1×2 completo (cuerpo + punta) quepa en la
    superficie y no choque con otro robot. Retorna la lista de pasos del cuerpo
    (sin incluir el origen) o None si no hay ruta. Límite de profundidad 2× la
    distancia Manhattan."""
    from collections import deque

    if origen == destino:
        return []
    max_depth = 2 * (abs(destino[0] - origen[0]) + abs(destino[1] - origen[1]))
    queue: deque[tuple[tuple[int, int], list[tuple[int, int]]]] = deque()
    queue.append((origen, []))
    visitados: set[tuple[int, int]] = {origen}

    while queue:
        (cx, cy), camino = queue.popleft()
        if len(camino) >= max_depth:
            continue
        for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
            if (nx, ny) in visitados:
                continue
            if not _footprint_libre((nx, ny), orientacion, robot_id, posiciones, grilla):
                continue
            nuevo_camino = camino + [(nx, ny)]
            if (nx, ny) == destino:
                return nuevo_camino
            visitados.add((nx, ny))
            queue.append(((nx, ny), nuevo_camino))
    return None


def _intentar_ceder_paso(
    ocupante: Robot,
    solicitante_id: int,
    robots_estado: dict[int, Robot],
    posiciones_actuales: dict[tuple[int, int], int],
    tareas: dict[int, Tarea],
    grilla: "Grilla",
    robots_modificados: list[Robot],
    eventos: list[dict],
    depth: int = 1,
) -> bool:
    """Move an idle robot out of the way. If no free adjacent cell and depth > 1,
    recursively push an idle neighbor first (cascading yield)."""
    # Mueve el footprint 1×2 del robot ocioso a una celda-cuerpo adyacente donde
    # quepa entero (orientación fija). Primero busca un cuerpo libre directo.
    for ax, ay in grilla.celdas_adyacentes_superficie(ocupante.x, ocupante.y):
        if _footprint_libre((ax, ay), ocupante.orientacion, ocupante.id, posiciones_actuales, grilla):
            nuevo = Robot(id=ocupante.id, x=ax, y=ay, z=0,
                          estado=RobotEstado.INACTIVO, carga_id=None,
                          orientacion=ocupante.orientacion)
            _mover_en_mapa(posiciones_actuales, ocupante, nuevo)
            robots_estado[ocupante.id] = nuevo
            robots_modificados.append(nuevo)
            eventos.append(_ev_movimiento(nuevo))
            return True
    if depth <= 1:
        return False
    # Cascada: empuja a un vecino ocioso para liberar espacio y luego muévete.
    for ax, ay in grilla.celdas_adyacentes_superficie(ocupante.x, ocupante.y):
        vecino_id = posiciones_actuales.get((ax, ay))
        if vecino_id is None or vecino_id == solicitante_id or vecino_id == ocupante.id:
            continue
        vecino = robots_estado.get(vecino_id)
        if vecino is None or vecino.estado != RobotEstado.INACTIVO or vecino_id in tareas:
            continue
        if _intentar_ceder_paso(vecino, ocupante.id, robots_estado, posiciones_actuales,
                                tareas, grilla, robots_modificados, eventos, depth=depth - 1):
            if _footprint_libre((ax, ay), ocupante.orientacion, ocupante.id, posiciones_actuales, grilla):
                nuevo = Robot(id=ocupante.id, x=ax, y=ay, z=0,
                              estado=RobotEstado.INACTIVO, carga_id=None,
                              orientacion=ocupante.orientacion)
                _mover_en_mapa(posiciones_actuales, ocupante, nuevo)
                robots_estado[ocupante.id] = nuevo
                robots_modificados.append(nuevo)
                eventos.append(_ev_movimiento(nuevo))
                return True
    return False


def _mapa_ocupacion(robots: dict[int, Robot]) -> dict[tuple[int, int], int]:
    """Mapa celda → robot_id que incluye AMBAS celdas (cuerpo + punta) de cada
    robot 1×2. Base para la detección de colisiones por footprint."""
    mapa: dict[tuple[int, int], int] = {}
    for r in robots.values():
        for celda in celdas_robot(r):
            mapa[celda] = r.id
    return mapa


def _footprint_libre(
    body: tuple[int, int],
    orientacion: Orientacion,
    robot_id: int,
    posiciones: dict[tuple[int, int], int],
    grilla: "Grilla",
) -> bool:
    """True si un robot con la orientación dada puede colocar su cuerpo en `body`:
    ambas celdas del footprint están dentro de la superficie y libres (o son del
    propio robot)."""
    for (cx, cy) in celdas_desde(body[0], body[1], orientacion):
        if not grilla.en_superficie(cx, cy):
            return False
        ocupante = posiciones.get((cx, cy))
        if ocupante is not None and ocupante != robot_id:
            return False
    return True


def _mover_en_mapa(
    posiciones: dict[tuple[int, int], int], viejo: Robot, nuevo: Robot
) -> None:
    """Actualiza el mapa de ocupación al mover un robot: libera las celdas del
    footprint anterior (si eran suyas) y ocupa las del nuevo."""
    for celda in celdas_robot(viejo):
        if posiciones.get(celda) == viejo.id:
            del posiciones[celda]
    for celda in celdas_robot(nuevo):
        posiciones[celda] = nuevo.id


def _nudge_robot(
    robot: Robot,
    tarea: "Tarea | None",
    robots_estado: dict[int, Robot],
    posiciones: dict[tuple[int, int], int],
    grilla: "Grilla",
    eventos: list[dict],
    evitar: set[tuple[int, int]],
    acum: "Acumuladores",
) -> Robot | None:
    """Empuja un robot a una celda-cuerpo adyacente libre (sin entrar en `evitar`).
    Si está tareado, recalcula la ruta de su fase; si está ocioso (tarea None),
    solo se aparta. Rompe deadlocks frontales.

    Retorna el robot actualizado, o None si no hay celda libre donde apartarse."""
    ocioso = tarea is None
    estado = RobotEstado.INACTIVO if ocioso else RobotEstado.DESPLAZANDOSE
    for ax, ay in grilla.celdas_adyacentes_superficie(robot.x, robot.y):
        celdas_nuevas = celdas_desde(ax, ay, robot.orientacion)
        if any(c in evitar for c in celdas_nuevas):
            continue
        if not _footprint_libre((ax, ay), robot.orientacion, robot.id, posiciones, grilla):
            continue
        nuevo = Robot(id=robot.id, x=ax, y=ay, z=robot.z,
                      estado=estado,
                      carga_id=None if ocioso else robot.carga_id,
                      orientacion=robot.orientacion)
        _mover_en_mapa(posiciones, robot, nuevo)
        robots_estado[robot.id] = nuevo
        if not ocioso:
            acum.total_desplazamientos += 1
            # Recalcular la ruta de la fase desde la nueva posición del cuerpo.
            if tarea.fase == "mover_a_objetivo":
                destino = cuerpo_para_punta_en(
                    tarea.caja_objetivo.x, tarea.caja_objetivo.y, robot.orientacion
                )
                tarea.ruta_entrada = _ruta_xy((ax, ay), destino)
            elif tarea.fase == "mover_a_puerto":
                tarea.ruta_salida = _ruta_xy((ax, ay), tarea.puerto)
            tarea.ticks_bloqueado_consecutivos = 0
        eventos.append(_ev_movimiento(nuevo))
        return nuevo
    return None


def _cambiar_estado(
    robot: Robot,
    estado: RobotEstado,
    carga_id: str | None = ...,  # type: ignore[assignment]
    orientacion: Orientacion = ...,  # type: ignore[assignment]
) -> Robot:
    cid = robot.carga_id if carga_id is ... else carga_id
    ori = robot.orientacion if orientacion is ... else orientacion
    return Robot(id=robot.id, x=robot.x, y=robot.y, z=robot.z,
                 estado=estado, carga_id=cid, orientacion=ori)


def _ev_movimiento(robot: Robot) -> dict:
    return {"tipo": "movimiento", "robot_id": robot.id,
            "x": robot.x, "y": robot.y, "z": robot.z}


def _ev_bloqueo(robot: Robot) -> dict:
    return {"tipo": "bloqueo", "robot_id": robot.id,
            "x": robot.x, "y": robot.y, "z": robot.z}
