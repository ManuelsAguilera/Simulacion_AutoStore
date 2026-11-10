"""Liberación externa secuencial; un único reloj lógico desde la lista disponible."""
from copy import deepcopy
import math
import heapq


class PlanOlas:
    def __init__(self, sim, solicitudes, tamano=12, regla="original", preparacion_fija=0,
                 preparacion_por_pedido=0, adaptativa=False, minimo=1, maximo=32,
                 objetivo_ticks_por_pedido=15.0, modelo_preparacion="sintetica"):
        if type(tamano) is not int or tamano < 1:
            raise ValueError("El tamaño de ola debe ser un entero positivo")
        if regla not in {"original", "urgentes_primero"}:
            raise ValueError("Regla de formación desconocida")
        if modelo_preparacion == "legacy":
            modelo_preparacion = "sintetica"
        if modelo_preparacion not in {"sintetica", "fisica"}:
            raise ValueError("Modelo de preparación desconocido")
        if any(type(n) is not int or n < 0 for n in (preparacion_fija, preparacion_por_pedido)):
            raise ValueError("La preparación debe expresarse en ticks enteros no negativos")
        if modelo_preparacion == "fisica" and (preparacion_fija != 0 or preparacion_por_pedido != 0):
            raise ValueError("El modelo físico no admite costos sintéticos de preparación")
        if modelo_preparacion == "fisica" and adaptativa:
            raise ValueError("El tamaño adaptativo no está habilitado con preparación física")
        if (any(type(n) is not int or n < 1 for n in (minimo,maximo)) or minimo > maximo
                or not math.isfinite(objetivo_ticks_por_pedido) or objetivo_ticks_por_pedido <= 0
                or (adaptativa and not minimo <= tamano <= maximo)):
            raise ValueError("Parámetros adaptativos inválidos")
        self.adaptativa, self.minimo, self.maximo = adaptativa, minimo, maximo
        self.objetivo = objetivo_ticks_por_pedido
        self.sim = sim
        self.modelo_preparacion = modelo_preparacion
        self._preparador = None
        self.fallo: str | None = None
        self._cajas_objetivo: dict[str, str] = {}
        self._cajas_objetivo_usadas: set[str] = set()
        self.tamano, self.regla = tamano, regla
        self.fija, self.por_pedido = preparacion_fija, preparacion_por_pedido
        orden = sorted(solicitudes, key=lambda s: s.orden)
        if regla == "urgentes_primero":
            orden.sort(key=lambda s: self._prioridad(s.urgencia))
        self.grupos = [orden[i:i+tamano] for i in range(0, len(orden), tamano)]
        if not self.grupos:
            raise ValueError("La lista está vacía")
        self.tiempo = 0
        self.olas = []
        self.eventos = []
        self.pedidos = {s.pedido.id_pedido: {"id": s.pedido.id_pedido, "id_sku": s.pedido.id_sku, "urgencia": s.urgencia,
                         "plazo_tick": s.plazo_tick, "ola": None, "liberacion": None, "asignacion": None,
                         "entrega": None} for s in orden}
        self._iniciar_ola()

    @staticmethod
    def _prioridad(clase):
        return {"muy_baja": 4, "baja": 3, "media": 2, "alta": 1,
                "critica": 0, "normal": 1, "urgente": 0}.get(clase, 2)

    def _reservar_cajas_grupo(self, grupo):
        cajas_por_sku = {}
        for caja in self.sim._grilla.cajas:
            cajas_por_sku.setdefault(caja.id_sku, []).append(caja)
        for cajas in cajas_por_sku.values():
            cajas.sort(key=lambda c: (sum(1 for z in range(c.z+1,self.sim._grilla.gz)
                           if self.sim._grilla.ocupada(c.x,c.y,z)), c.x,c.y,c.z,c.id_caja))
        usadas = set(self._cajas_objetivo_usadas)
        reservas = {}
        for solicitud in grupo:
            disponibles = cajas_por_sku.get(solicitud.pedido.id_sku, [])
            caja = next((c for c in disponibles if c.id_caja not in usadas), None)
            if caja is None:
                self.fallo = f"Sin caja disponible para {solicitud.pedido.id_pedido} ({solicitud.pedido.id_sku})"
                return {}
            reservas[solicitud.pedido.id_pedido] = caja.id_caja
            usadas.add(caja.id_caja)
        self._cajas_objetivo_usadas.update(usadas)
        return reservas

    def _orden_fisico(self, grupo):
        """Ordena por urgencia entre cajas actualmente recuperables top-down."""
        por_columna = {}
        orden = []
        cajas = {c.id_caja:c for c in self.sim._grilla.cajas}
        for s in grupo:
            c = cajas.get(self._cajas_objetivo.get(s.pedido.id_pedido))
            if c is not None:
                por_columna.setdefault((c.x,c.y),[]).append((c.z,s))
        for objetivos in por_columna.values():
            objetivos.sort(key=lambda cs: -cs[0])
        heap = []
        indices = {col:0 for col in por_columna}
        def insertar(col):
            i=indices[col]
            if i >= len(por_columna[col]):
                return
            _z,s=por_columna[col][i]
            heapq.heappush(heap,((self._prioridad(s.urgencia) if self.regla=="urgentes_primero" else 0,
                                  s.orden,col),s))
        for col in por_columna:
            insertar(col)
        while heap:
            (_key,s)=heapq.heappop(heap)
            orden.append(s)
            caja=cajas[self._cajas_objetivo[s.pedido.id_pedido]]
            col=(caja.x,caja.y); indices[col]+=1; insertar(col)
        # Una reserva perdida se mantiene explícitamente al final; la preparación
        # fallará con el ID concreto, sin reasignar pedidos.
        orden.extend(s for s in grupo if s not in orden)
        return orden

    def _evento(self, tipo, **datos):
        evento = {"tipo": tipo, "tiempo": self.tiempo, **datos}
        self.eventos.append(evento)
        self.sim._eventos_pendientes.append(dict(evento))

    def _iniciar_ola(self):
        indice = len(self.olas)
        grupo = self.grupos[indice]
        costo = 0 if self.modelo_preparacion == "fisica" else self.fija + self.por_pedido * len(grupo)
        self._cajas_objetivo = self._reservar_cajas_grupo(grupo) if self.modelo_preparacion == "fisica" else {}
        self.olas.append({"numero": indice + 1, "pedidos": [s.pedido.id_pedido for s in grupo],
                          "preparacion_inicio": self.tiempo, "preparacion_ticks": costo,
                          "liberacion": None, "fin": None})
        self._evento("preparacion_ola", ola=indice+1,
                     duracion=None if self.modelo_preparacion == "fisica" else costo,
                     modelo=self.modelo_preparacion)
        if self.modelo_preparacion == "fisica":
            from motor.preparacion_fisica import PreparadorFisico
            self._preparador = PreparadorFisico(self.sim, grupo, self._cajas_objetivo, plan=self)
            if self._preparador.fallo:
                self.fallo = self._preparador.fallo
        elif costo == 0:
            self._liberar()

    @property
    def preparacion_fisica(self):
        return self._preparador.estado if self._preparador else {
            "fase": "inactiva", "caja": None, "robot": None,
            "origen": None, "destino": None, "pendientes": [], "motivo": self.fallo}

    def avanzar_preparacion(self):
        if self.modelo_preparacion != "fisica" or self.puede_procesar or self.fallo:
            return
        if self._preparador and self._preparador.avanzar_tick():
            if self._preparador.fallo:
                self.fallo = self._preparador.fallo

    def _liberar(self):
        ola = self.olas[-1]
        grupo = self.grupos[len(self.olas)-1]
        if self.modelo_preparacion == "fisica":
            ola["preparacion_ticks"] = self.tiempo - ola["preparacion_inicio"]
        if self.modelo_preparacion == "fisica":
            orden = self._orden_fisico(grupo)
            self.sim.pedidos_cola.extend(s.pedido for s in orden)
            if self.sim._despachador is not None:
                self.sim._despachador.cajas_objetivo_por_pedido = dict(self._cajas_objetivo)
                self.sim._despachador.preparacion_fisica_activa = True
                self.sim._despachador.plan_olas = self
        else:
            self.sim.pedidos_cola.extend(s.pedido for s in grupo)
        ola["liberacion"] = self.tiempo
        for s in grupo:
            self.pedidos[s.pedido.id_pedido]["ola"] = ola["numero"]
            self.pedidos[s.pedido.id_pedido]["liberacion"] = self.tiempo
        self._evento("liberacion_ola", ola=ola["numero"], pedidos=ola["pedidos"][:])

    @property
    def completa(self):
        return self.fallo is None and len(self.olas) == len(self.grupos) and self.olas[-1]["fin"] is not None

    @property
    def puede_procesar(self):
        return self.olas[-1]["liberacion"] is not None and not self.completa

    def cerrar_tick(self, eventos):
        self.tiempo += 1
        ola = self.olas[-1]
        if ola["liberacion"] is None:
            if self.modelo_preparacion == "fisica":
                if self._preparador and self._preparador.completa and not self.fallo:
                    self._liberar()
            elif self.tiempo - ola["preparacion_inicio"] >= ola["preparacion_ticks"]:
                self._liberar()
            return
        if self.modelo_preparacion == "fisica":
            for evento in eventos:
                if evento.get("tipo", "").startswith("preparacion_") or (
                        evento.get("tipo") == "excavacion" and evento.get("fase") == "preparacion"):
                    continue
                if evento.get("tipo") in {"caja_recuperada", "recuperacion_izado", "pedido_asignado",
                                           "pedido_completado", "movimiento", "bloqueo", "entrega"}:
                    if len(self.eventos) >= 200:
                        self.eventos.pop(0)
                    self.eventos.append({"tiempo": self.tiempo, **evento})
        for e in eventos:
            campo = {"pedido_asignado": "asignacion", "pedido_completado": "entrega"}.get(e["tipo"])
            if campo:
                pedido = self.pedidos[e["id_pedido"]]
                if pedido[campo] is None:
                    pedido[campo] = self.tiempo
                    self._evento("solicitud_asignada" if campo == "asignacion" else "solicitud_entregada",
                                 id_pedido=pedido["id"])
        if all(self.pedidos[p]["entrega"] is not None for p in ola["pedidos"]):
            ola["fin"] = self.tiempo
            self._evento("fin_ola", ola=ola["numero"])
            if len(self.olas) < len(self.grupos):
                if self.adaptativa:
                    observado = (ola["fin"]-ola["liberacion"])/len(ola["pedidos"])
                    siguiente = (max(self.minimo, len(ola["pedidos"])//2) if observado > self.objetivo
                                 else min(self.maximo, len(ola["pedidos"])*2))
                    restantes = [s for grupo in self.grupos[len(self.olas):] for s in grupo]
                    self.grupos = self.grupos[:len(self.olas)] + [restantes[i:i+siguiente]
                                   for i in range(0,len(restantes),siguiente)]
                    self._evento("ajuste_tamano", siguiente=siguiente, observado=observado,
                                 objetivo=self.objetivo)
                self._iniciar_ola()

    def resumen(self, compacto=False):
        urgentes = [p for p in self.pedidos.values() if p["urgencia"] in {"urgente", "critica", "alta"}]
        preparacion = sum(o["preparacion_ticks"] if o["liberacion"] is not None
                          else (self.tiempo-o["preparacion_inicio"] if self.modelo_preparacion == "fisica"
                                else min(o["preparacion_ticks"], self.tiempo - o["preparacion_inicio"]))
                          for o in self.olas)
        servicio = {}
        clases_servicio = ({"muy_baja","baja","media","alta","critica"}
                           if any(p["urgencia"] in {"muy_baja","baja","media","alta","critica"}
                                  for p in self.pedidos.values())
                           else {p["urgencia"] for p in self.pedidos.values()})
        for clase in sorted(clases_servicio):
            registros = [p for p in self.pedidos.values() if p["urgencia"] == clase]
            entregados = [p for p in registros if p["entrega"] is not None]
            con_plazo = [p for p in registros if p["plazo_tick"] is not None]
            cumplidos = [p for p in entregados if p["plazo_tick"] is not None
                         and p["entrega"] <= p["plazo_tick"]]
            servicio[clase] = {"total":len(registros),"entregados":len(entregados),
                               "a_tiempo":len(cumplidos),
                               "cumplimiento":(len(cumplidos)/len(con_plazo) if con_plazo else None),
                               "tiempo_medio_entrega":(sum(p["entrega"] for p in entregados)/len(entregados)
                                                        if entregados else None),
                               # Campos descriptivos adicionales mantienen el denominador visible.
                               "con_plazo":len(con_plazo),
                               "cumplimiento_plazo_pct":(len(cumplidos)/len(con_plazo)*100
                                                          if con_plazo else None)}
        resultado = {"tiempo": self.tiempo, "completa": self.completa,
                         "tiempo_finalizacion": self.tiempo if self.completa else None,
                         "modelo_preparacion": self.modelo_preparacion,
                         "preparacion_fisica": self.preparacion_fisica,
                         "parametros_preparacion_fisica": ({"ticks_por_celda_xy": 1,
                              "niveles_izados_por_tick": 1, "niveles_descendidos_por_tick": 1}
                              if self.modelo_preparacion == "fisica" else None),
                         "fallo": self.fallo,
                         "tamano": self.tamano, "regla": self.regla,
                         "preparacion_fija": self.fija, "preparacion_por_pedido": self.por_pedido,
                         "adaptativa": self.adaptativa, "minimo": self.minimo, "maximo": self.maximo,
                         "objetivo_ticks_por_pedido": self.objetivo,
                         "preparacion_total": preparacion,
                         "recuperacion_ticks": self.tiempo - preparacion,
                         "estado_ola": "completa" if self.completa else
                         ("preparando" if self.olas[-1]["liberacion"] is None else "liberada"),
                         "urgentes_total": len(urgentes),
                         "urgentes_a_tiempo": sum(p["entrega"] is not None and p["plazo_tick"] is not None and
                                                  p["entrega"] <= p["plazo_tick"] for p in urgentes),
                         "servicio_por_categoria": servicio,
                         "olas": self.olas, "pedidos": list(self.pedidos.values()),
                         "eventos": self.eventos[-200:] if compacto else self.eventos}
        return deepcopy(resultado)
