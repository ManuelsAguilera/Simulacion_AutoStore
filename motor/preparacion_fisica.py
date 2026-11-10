"""Preparación física secuencial de un grupo de pedidos.

Las cajas objetivo quedan reservadas por ID. Solo los bloqueadores que no son
objetivos del grupo se reubican, mediante una secuencia observable de acciones.
El motor usa ticks como unidad lógica; no se asigna una demora fija al grupo.
"""
from __future__ import annotations

from collections import deque

from bus_persistencia.models.state import (
    Caja, Robot, RobotEstado, celdas_desde, celdas_robot,
    cuerpo_para_punta_en,
)
from motor.despachador import _footprint_libre, _mapa_ocupacion, _mover_en_mapa


class PreparadorFisico:
    """Ejecuta tareas de despeje en el estado vivo del simulador."""

    def __init__(self, sim, grupo, pedidos_objetivo: dict[str, str], plan=None):
        self.sim = sim
        self.plan = plan
        self.grilla = sim._grilla
        self.grupo = list(grupo)
        self.pedidos_objetivo = dict(pedidos_objetivo)  # pedido_id -> caja_id
        self.ids_objetivo = set(pedidos_objetivo.values())
        self.fase = "planificando"
        self._tarea: dict | None = None
        self._ruta: list[tuple[int, int]] = []
        self._robot_id: int | None = None
        self._tick_inicio = 0
        self._fin = False
        self._fallo: str | None = None
        self._pendientes: list[str] = []
        self._by_id = {c.id_caja: c for c in self.grilla.cajas}
        self._vertical_restante = 0
        self._ocupacion_tick: dict[tuple[int, int], int] = {}
        self._elegir_robot()
        self._verificar_objetivos()

    def _elegir_robot(self) -> None:
        robots = sorted(self.sim.robots.values(), key=lambda r: r.id)
        if not robots:
            self._fallar("No hay robots disponibles para preparar el grupo")
            return
        # Se serializa el trabajo con un robot persistente. Los demás conservan
        # posición/estado y cuentan como obstáculos reales de tráfico.
        self._robot_id = robots[0].id

    def _verificar_objetivos(self) -> None:
        faltantes = [cid for cid in self.ids_objetivo if cid not in self._by_id]
        if faltantes:
            self._fallar(f"No se encontraron las cajas objetivo: {', '.join(sorted(faltantes))}")
            return
        for solicitud in self.grupo:
            caja_id = self.pedidos_objetivo.get(solicitud.pedido.id_pedido)
            caja = self._by_id.get(caja_id)
            if caja is None or caja.id_sku != solicitud.pedido.id_sku:
                self._fallar(f"La caja reservada para {solicitud.pedido.id_pedido} no coincide con su producto")
                return

    def _fallar(self, motivo: str) -> None:
        self._fallo = motivo
        self._fin = True
        self.fase = "fallida"
        self._emitir("preparacion_fallida", motivo=motivo)

    def _emitir(self, tipo: str, **datos) -> None:
        evento = {"tipo": tipo, "tick": self.sim.tick + 1, **datos}
        self.sim._eventos_pendientes.append(evento)
        if self.plan is not None:
            # La traza completa se conserva en el registro de corrida del bus;
            # el resumen incremental en memoria limita eventos recientes.
            if len(self.plan.eventos) >= 200:
                self.plan.eventos.pop(0)
            self.plan.eventos.append(dict(evento))

    def _caja_por_id(self, caja_id: str) -> Caja | None:
        return self._by_id.get(caja_id)

    def _bloqueador_pendiente(self) -> Caja | None:
        cajas = sorted(self.grilla.cajas, key=lambda c: (c.x, c.y, -c.z, c.id_caja))
        for solicitud in self.grupo:
            caja_id = self.pedidos_objetivo[solicitud.pedido.id_pedido]
            objetivo = self._caja_por_id(caja_id)
            if objetivo is None:
                self._fallar(f"La caja objetivo {caja_id} desapareció durante la preparación")
                return None
            superiores = [c for c in cajas if (c.x, c.y) == (objetivo.x, objetivo.y)
                          and c.z > objetivo.z]
            ajenas = [c for c in superiores if c.id_caja not in self.ids_objetivo]
            if ajenas:
                # Si hay un bloqueador ajeno debajo de un objetivo del grupo, se
                # aparta primero la caja superior conservando el ID. Los objetivos
                # apilados sin bloqueadores ajenos quedan como dependencias top-down.
                return max(superiores, key=lambda c: c.z)
        return None

    def _destino(self, caja: Caja) -> tuple[int, int, int] | None:
        columnas_protegidas = {(c.x, c.y) for cid in self.ids_objetivo
                               if (c := self._caja_por_id(cid)) is not None}
        candidatas = []
        x0, y0, x1, y1 = self.grilla.interior_bounds
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                if (x, y) in columnas_protegidas or (x, y) == (caja.x, caja.y):
                    continue
                col = self.grilla.columna(x, y)
                if len(col) >= self.grilla.gz:
                    continue
                # Una pila válida es contigua desde z=0.
                candidatas.append((len(col), abs(caja.x-x)+abs(caja.y-y), x, y))
        if not candidatas:
            return None
        _, _, x, y = min(candidatas)
        return x, y, len(self.grilla.columna(x, y))

    def _ruta_bfs(self, origen: tuple[int, int], destino: tuple[int, int], rid: int):
        robot = self.sim.robots[rid]
        posiciones = _mapa_ocupacion(self.sim.robots)
        cola = deque([(origen, [])])
        vistos = {origen}
        while cola:
            (x, y), ruta = cola.popleft()
            if (x, y) == destino:
                return ruta
            for nx, ny in ((x+1,y),(x-1,y),(x,y+1),(x,y-1)):
                if (nx, ny) in vistos:
                    continue
                if _footprint_libre((nx, ny), robot.orientacion, rid, posiciones, self.grilla):
                    vistos.add((nx, ny))
                    cola.append(((nx, ny), ruta+[(nx,ny)]))
        return None

    def _crear_tarea(self, caja: Caja) -> bool:
        destino = self._destino(caja)
        if destino is None:
            self._fallar(f"Sin espacio de reubicación utilizable para el bloqueador {caja.id_caja}")
            return False
        robot = self.sim.robots[self._robot_id]
        pickup = cuerpo_para_punta_en(caja.x, caja.y, robot.orientacion)
        ruta = self._ruta_bfs((robot.x, robot.y), pickup, robot.id)
        if ruta is None:
            self._fallar(f"No hay ruta de robot hasta el bloqueador {caja.id_caja}")
            return False
        self._tarea = {"caja_id": caja.id_caja, "origen": (caja.x,caja.y,caja.z),
                       "destino": destino, "pickup": pickup, "caja": caja}
        self._pendientes = self._calcular_pendientes()
        self._ruta = ruta
        self.fase = "aproximacion"
        self._tick_inicio = self.sim.tick
        self._emitir("preparacion_tarea_iniciada", caja=caja.id_caja,
                     origen=list(self._tarea["origen"]), destino=list(destino), robot=robot.id)
        return True

    @property
    def completa(self) -> bool:
        return self._fin

    @property
    def fallo(self) -> str | None:
        return self._fallo

    @property
    def estado(self) -> dict:
        return {"fase": self.fase,
                "caja": self._tarea["caja_id"] if self._tarea else None,
                "robot": self._robot_id,
                "origen": list(self._tarea["origen"]) if self._tarea else None,
                "destino": list(self._tarea["destino"]) if self._tarea else None,
                "pendientes": list(self._pendientes), "motivo": self._fallo}

    def avanzar_tick(self) -> bool:
        """Avanza una acción física; retorna True cuando hay fallo o preparación lista."""
        if self._fin:
            return True
        robot = self.sim.robots[self._robot_id]
        if self._tarea is None:
            bloqueador = self._bloqueador_pendiente()
            if self._fallo:
                return True
            if bloqueador is None:
                self.fase = "verificacion"
                self._verificar_final()
                return self._fin
            if not self._crear_tarea(bloqueador):
                return True
            robot = self.sim.robots[self._robot_id]

        tarea = self._tarea
        if self.fase == "aproximacion":
            if self._ruta:
                paso = self._ruta.pop(0)
                nuevo = Robot(robot.id, paso[0], paso[1], robot.z,
                              RobotEstado.DESPLAZANDOSE, robot.carga_id, robot.orientacion)
                posiciones = _mapa_ocupacion(self.sim.robots)
                if not _footprint_libre(paso, robot.orientacion, robot.id, posiciones, self.grilla):
                    self._ruta.insert(0, paso)
                    return False
                _mover_en_mapa(posiciones, robot, nuevo)
                self.sim.robots[robot.id] = nuevo
                self.sim._robots_delta.append(nuevo)
                if hasattr(self.sim, "_acum"):
                    self.sim._acum.total_desplazamientos += 1
                self._emitir("preparacion_movimiento", robot=robot.id, x=paso[0],y=paso[1],fase=self.fase)
                return False
            self.fase = "toma"
            return False
        if self.fase == "toma":
            caja = self._caja_por_id(tarea["caja_id"])
            if caja is None:
                self._fallar(f"El bloqueador {tarea['caja_id']} ya no está en el inventario")
                return True
            self.grilla.remover(caja.x,caja.y,caja.z)
            self._by_id.pop(caja.id_caja, None)
            self._vertical_restante = self.grilla.gz - caja.z
            cargado = Robot(robot.id,robot.x,robot.y,robot.z,RobotEstado.RECUPERANDO,
                            caja.id_caja,robot.orientacion)
            self.sim.robots[robot.id] = cargado
            self.sim._robots_delta.append(cargado)
            self.fase = "izado"
            self._emitir("preparacion_toma",robot=robot.id,caja=caja.id_caja)
            return False
        if self.fase == "izado":
            if self._vertical_restante > 0:
                self._vertical_restante -= 1
                self._emitir("preparacion_izado", robot=robot.id, caja=tarea["caja_id"],
                             nivel=self._vertical_restante + 1, carga=robot.carga_id)
                return False
            self.fase = "transporte"
            dx,dy=tarea["destino"][:2]
            destino_robot=cuerpo_para_punta_en(dx,dy,robot.orientacion)
            self._ruta = self._ruta_bfs((robot.x,robot.y), destino_robot, robot.id)
            if self._ruta is None:
                self._fallar(f"No hay ruta de transporte para la caja {tarea['caja_id']}")
                return True
            return False
        if self.fase == "transporte":
            if self._ruta:
                paso = self._ruta.pop(0)
                posiciones = _mapa_ocupacion(self.sim.robots)
                if not _footprint_libre(paso,robot.orientacion,robot.id,posiciones,self.grilla):
                    self._ruta.insert(0,paso)
                    self._emitir("preparacion_espera_trafico",robot=robot.id,caja=tarea["caja_id"])
                    return False
                nuevo = Robot(robot.id,paso[0],paso[1],robot.z,RobotEstado.DESPLAZANDOSE,
                              robot.carga_id,robot.orientacion)
                _mover_en_mapa(posiciones,robot,nuevo)
                self.sim.robots[robot.id] = nuevo
                self.sim._robots_delta.append(nuevo)
                if hasattr(self.sim, "_acum"):
                    self.sim._acum.total_desplazamientos += 1
                self._emitir("preparacion_movimiento",robot=robot.id,x=paso[0],y=paso[1],fase=self.fase)
                return False
            self.fase = "descenso"
            self._vertical_restante = self.grilla.gz - tarea["destino"][2]
            return False
        if self.fase == "descenso":
            if self._vertical_restante > 0:
                self._vertical_restante -= 1
                self._emitir("preparacion_descenso", robot=robot.id, caja=tarea["caja_id"],
                             nivel=self._vertical_restante + 1, carga=robot.carga_id)
                return False
            self.fase = "deposito"
            return False
        if self.fase == "deposito":
            x,y,z = tarea["destino"]
            fuente = tarea["caja"]
            depositada = Caja(fuente.id_caja,fuente.id_sku,fuente.cantidad,x,y,z)
            self.grilla.agregar(depositada)
            self._by_id[depositada.id_caja] = depositada
            libre = Robot(robot.id,robot.x,robot.y,robot.z,RobotEstado.INACTIVO,None,robot.orientacion)
            self.sim.robots[robot.id] = libre
            self.sim._robots_delta.append(libre)
            self._emitir("preparacion_deposito",robot=robot.id,caja=depositada.id_caja,
                         de=list(tarea["origen"]),a=list(tarea["destino"]))
            self._emitir("excavacion",fase="preparacion",robot_id=robot.id,
                         id_caja=depositada.id_caja,de=list(tarea["origen"]),
                         a=list(tarea["destino"]))
            self._tarea = None
            self.fase = "planificando"
            return False
        return False

    def _calcular_pendientes(self):
        pendientes = set()
        columnas = set()
        for solicitud in self.grupo:
            objetivo = self._caja_por_id(self.pedidos_objetivo[solicitud.pedido.id_pedido])
            if objetivo is None:
                continue
            columnas.add((objetivo.x,objetivo.y))
        for x,y in columnas:
            pila = self.grilla.columna(x,y)
            ids_obj = {self.pedidos_objetivo[s.pedido.id_pedido] for s in self.grupo
                       if (o:=self._caja_por_id(self.pedidos_objetivo[s.pedido.id_pedido])) is not None
                       and (o.x,o.y)==(x,y)}
            niveles_obj = [c.z for c in pila if c.id_caja in ids_obj]
            if niveles_obj:
                z_min = min(niveles_obj)
                pendientes.update(c.id_caja for c in pila if c.z > z_min)
        return sorted(pendientes)

    def _verificar_final(self) -> None:
        for solicitud in self.grupo:
            caja = self._caja_por_id(self.pedidos_objetivo[solicitud.pedido.id_pedido])
            if caja is None:
                self._fallar(f"No se conserva la caja objetivo de {solicitud.pedido.id_pedido}")
                return
            if any(c.x == caja.x and c.y == caja.y and c.z > caja.z and c.id_caja not in self.ids_objetivo
                   for c in self.grilla.cajas):
                self._fallar(f"El objetivo de {solicitud.pedido.id_pedido} conserva un bloqueador externo")
                return
        self._fin = True
        self.fase = "completa"
        self._emitir("preparacion_verificada", objetivos=len(self.ids_objetivo))
