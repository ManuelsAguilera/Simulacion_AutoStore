"""Corridas identificadas, trazas recuperables y medidas reconstruidas de eventos."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4


def _json(path, data):
    temporal = path.with_suffix(".tmp")
    temporal.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for intento in range(8):
        try:
            temporal.replace(path)
            return
        except PermissionError:
            # Windows puede retener brevemente un archivo en una carpeta compartida.
            if intento == 7:
                raise
            time.sleep(min(0.01 * 2**intento, 0.2))


def version_codigo():
    raiz = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for carpeta in ("motor", "experimentos", "api", "bus_persistencia"):
        for p in sorted((raiz / carpeta).rglob("*.py")):
            digest.update(p.relative_to(raiz).as_posix().encode())
            digest.update(p.read_bytes())
    try:
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=raiz, capture_output=True,
                              text=True, timeout=2, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        base = None
    return {"commit_base": base, "sha256_fuentes": digest.hexdigest()}


class MedidasIncrementales:
    """Consume cada evento una vez; no vuelve a recorrer el historial por tick."""

    def __init__(self, instancia):
        self.pedidos = {s.pedido.id_pedido: {"id": s.pedido.id_pedido, "id_sku": s.pedido.id_sku, "urgencia": s.urgencia,
               "plazo_tick": s.plazo_tick, "liberacion": None, "asignacion": None, "entrega": None}
               for s in instancia.solicitudes}
        self.movimientos = 0
        self.preparatorios = 0
        self.pares_excavacion = set()
        self.ticks_excavacion = set()
        self.esperas_escort = set()
        self.esperas_espacio = set()
        self.categorias = (["muy_baja", "baja", "media", "alta", "critica"]
                           if getattr(instancia, "version", 1) >= 2 else
                           list(dict.fromkeys(p["urgencia"] for p in self.pedidos.values())))

    def consumir(self, eventos):
        for e in eventos:
            t = e["tiempo"]
            if e["tipo"] == "liberacion_ola":
                for identificador in e["pedidos"]:
                    self.pedidos[identificador]["liberacion"] = t
            campo = {"pedido_asignado": "asignacion", "pedido_completado": "entrega"}.get(e["tipo"])
            if campo:
                p = self.pedidos[e["id_pedido"]]
                if p[campo] is None:
                    p[campo] = t
            if e["tipo"] == "excavacion":
                self.movimientos += 1
                self.preparatorios += int(e.get("fase") == "preparacion" or e.get("preparacion") is True)
                self.pares_excavacion.add((t, e["robot_id"]))
                self.ticks_excavacion.add(t)
            if e["tipo"] == "espera_escort":
                self.esperas_escort.add((t, e["robot_id"]))
            if e["tipo"] == "espera_espacio_excavacion":
                self.esperas_espacio.add((t, e["robot_id"]))

    def resumen(self):
        pedidos = {k: dict(v) for k, v in self.pedidos.items()}
        for p in pedidos.values():
            p["espera_previa_liberacion"] = p["liberacion"]
            p["espera_asignacion"] = (p["asignacion"] - p["liberacion"]
                if p["asignacion"] is not None and p["liberacion"] is not None else None)
            p["tiempo_desde_asignacion"] = (p["entrega"] - p["asignacion"]
                if p["entrega"] is not None and p["asignacion"] is not None else None)
            p["tiempo_total"] = p["entrega"]
        urgentes = [p for p in pedidos.values() if p["urgencia"] in {"urgente", "alta", "critica"}]
        a_tiempo = sum(p["entrega"] is not None and p["plazo_tick"] is not None
                      and p["entrega"] <= p["plazo_tick"] for p in urgentes)
        categorias = {}
        for categoria in self.categorias:
            grupo = [p for p in pedidos.values() if p["urgencia"] == categoria]
            entregas = [p["entrega"] for p in grupo if p["entrega"] is not None]
            cumplidas = sum(p["entrega"] is not None and p["plazo_tick"] is not None
                           and p["entrega"] <= p["plazo_tick"] for p in grupo)
            categorias[categoria] = {"total": len(grupo), "entregados": len(entregas),
                "a_tiempo": cumplidas, "cumplimiento": cumplidas / len(grupo)
                if grupo and any(p["plazo_tick"] is not None for p in grupo) else None,
                "tiempo_medio_entrega": sum(entregas) / len(entregas) if entregas else None}
        return {"pedidos": list(pedidos.values()),
            "entregados": sum(p["entrega"] is not None for p in pedidos.values()),
            "total": len(pedidos), "urgentes_total": len(urgentes), "urgentes_a_tiempo": a_tiempo,
            "cumplimiento_urgentes": a_tiempo / len(urgentes) if urgentes else None,
            "servicio_por_categoria": categorias,
            "movimientos_bloqueadores": self.movimientos,
            "movimientos_preparacion": self.preparatorios,
            "movimientos_recuperacion": self.movimientos - self.preparatorios,
            "robot_ticks_excavacion": len(self.pares_excavacion),
            "ticks_con_excavacion": len(self.ticks_excavacion),
            "robot_ticks_espera_escort": len(self.esperas_escort),
            "robot_ticks_espera_espacio": len(self.esperas_espacio)}


def reconstruir(instancia, eventos):
    medidas = MedidasIncrementales(instancia)
    medidas.consumir(eventos)
    return medidas.resumen()


def leer(carpeta: Path, id: str, incluir_eventos=True):
    if len(id) != 32 or any(c not in "0123456789abcdef" for c in id):
        raise ValueError("Identificador de corrida inválido")
    directorio = carpeta / id
    resultado = json.loads((directorio / "estado.json").read_text(encoding="utf-8"))
    eventos = []
    if incluir_eventos:
        with (directorio / "eventos.jsonl").open(encoding="utf-8") as f:
            eventos = [json.loads(l) for l in f if l.endswith("\n")]
    return {"entrada": json.loads((directorio / "entrada.json").read_text(encoding="utf-8")),
            "resultado": resultado,
            "eventos": [e for e in eventos if e["tiempo"] <= resultado["tiempo"]]}


def leer_resumen(carpeta: Path, id: str):
    return leer(carpeta, id, incluir_eventos=False)


def paginar_pedidos(carpeta: Path, id: str, pagina=1, tamano=50, urgencia=None, estado=None, busqueda=""):
    if pagina < 1 or not 1 <= tamano <= 200:
        raise ValueError("Página inválida o tamaño fuera de 1–200")
    resultado = leer_resumen(carpeta, id)["resultado"]
    grupos = {pedido: ola["numero"] for ola in resultado.get("olas", []) for pedido in ola["pedidos"]}
    pedidos = [{**p, "ola": grupos.get(p["id"])} for p in resultado["pedidos"]]
    if urgencia:
        pedidos = [p for p in pedidos if p["urgencia"] == urgencia]
    if estado:
        if estado not in {"pendiente", "entregado", "asignado", "sin_liberar"}:
            raise ValueError("Estado de pedido desconocido")
        pedidos = [p for p in pedidos if (
            (estado == "entregado" and p["entrega"] is not None) or
            (estado == "pendiente" and p["entrega"] is None) or
            (estado == "asignado" and p["asignacion"] is not None and p["entrega"] is None) or
            (estado == "sin_liberar" and p["liberacion"] is None))]
    if busqueda:
        termino = busqueda.casefold()
        pedidos = [p for p in pedidos if termino in p["id"].casefold() or termino in p["id_sku"].casefold()]
    inicio = (pagina - 1) * tamano
    return {"items": pedidos[inicio:inicio+tamano], "pagina": pagina, "tamano": tamano, "total": len(pedidos)}


def paginar_eventos(carpeta: Path, id: str, desde=0, limite=100, tipo=None, id_pedido=None, ola=None):
    if desde < 0 or not 1 <= limite <= 1000:
        raise ValueError("Cursor o límite inválido")
    checkpoint = leer_resumen(carpeta, id)["resultado"]["tiempo"]
    items, siguiente, hay_mas = [], desde, False
    # El cursor es una posición de bytes: permite continuar sin releer la traza.
    with (carpeta / id / "eventos.jsonl").open("rb") as f:
        f.seek(0, 2)
        if desde > f.tell():
            raise ValueError("Cursor fuera de la traza")
        if desde:
            f.seek(desde - 1)
            if f.read(1) != b"\n":
                raise ValueError("El cursor debe ser el devuelto por la página anterior")
        f.seek(desde)
        while linea := f.readline():
            if not linea.endswith(b"\n"):
                break
            evento = json.loads(linea)
            if evento["tiempo"] > checkpoint:
                break
            coincide = ((tipo is None or evento["tipo"] == tipo)
                        and (id_pedido is None or evento.get("id_pedido") == id_pedido
                             or id_pedido in evento.get("pedidos", []))
                        and (ola is None or evento.get("ola") == ola))
            if coincide:
                if len(items) == limite:
                    hay_mas = True
                    break
                items.append(evento)
            siguiente = f.tell()
    return {"items": items, "desde": desde, "siguiente": siguiente, "hay_mas": hay_mas}


def listar(carpeta: Path):
    salida = []
    for p in carpeta.glob("*/estado.json"):
        r = json.loads(p.read_text(encoding="utf-8"))
        salida.append({**{k: r[k] for k in ("id", "instancia_id", "creada", "estado", "tiempo", "entregados", "total")},
                       "version": r.get("version", 1),
                       "modelo_preparacion": r.get("modelo_preparacion", "sintetica"),
                       **{k: r[k] for k in ("lote_id", "configuracion_id") if k in r}})
    return sorted(salida, key=lambda r: (r["creada"], r["id"]), reverse=True)


class RegistroCorrida:
    TERMINALES = {"completa", "horizonte_agotado", "sin_entregas", "error", "interrumpida", "inviable_preparacion"}

    def __init__(self, instancia, sim, carpeta: Path, limite_ticks=1000, ventana_sin_entregas=None,
                 intervalo_guardado=None, contexto=None):
        if type(limite_ticks) is not int or limite_ticks < 1:
            raise ValueError("Horizonte inválido")
        if ventana_sin_entregas is not None and (type(ventana_sin_entregas) is not int or ventana_sin_entregas < 1):
            raise ValueError("Ventana sin entregas inválida")
        if sim.plan_olas is None:
            raise ValueError("La corrida requiere un plan de olas")
        if intervalo_guardado is not None and (type(intervalo_guardado) is not int or intervalo_guardado < 1):
            raise ValueError("Intervalo de guardado inválido")
        self.instancia, self.sim = instancia, sim
        self.contexto = {k: v for k, v in (contexto or {}).items() if k in {"lote_id", "configuracion_id"}}
        self.id = uuid4().hex
        self.directorio = carpeta / self.id
        self.directorio.mkdir(parents=True, exist_ok=False)
        self.creada = datetime.now(timezone.utc).isoformat()
        self.limite, self.ventana = limite_ticks, ventana_sin_entregas
        self.sin_entregas = 0
        self.estado = "lista"
        self.eventos = []
        self._buffer = []
        self._medidas = MedidasIncrementales(instancia)
        self.es_v2 = getattr(instancia, "version", 1) >= 2
        self.intervalo_guardado = intervalo_guardado or (50 if self.es_v2 else 1)
        self._ultimo_guardado = -1
        plan = sim.plan_olas.resumen(compacto=True)
        self.entrada = {"version": 2 if self.es_v2 else 1, "id": self.id, "creada": self.creada,
                        "instancia_id": instancia.id, "instancia": instancia.to_dict(),
                        "codigo": version_codigo(), "politica_interna": sim.bus.read_snapshot().politica,
                        "opciones": {k: plan[k] for k in ("tamano", "regla", "preparacion_fija", "preparacion_por_pedido",
                            "adaptativa", "minimo", "maximo", "objetivo_ticks_por_pedido")},
                        "limite_ticks": limite_ticks, "ventana_sin_entregas": ventana_sin_entregas}
        self.entrada["modelo_preparacion"] = plan.get("modelo_preparacion", "sintetica")
        self.entrada["parametros_movimiento"] = plan.get("parametros_preparacion_fisica")
        self.entrada["intervalo_guardado"] = self.intervalo_guardado
        self.entrada["opciones"]["modelo_preparacion"] = self.entrada["modelo_preparacion"]
        self.entrada.update(self.contexto)
        _json(self.directorio / "entrada.json", self.entrada)
        self._registrar_eventos()
        self.guardar()

    @property
    def terminal(self):
        return self.estado in self.TERMINALES

    def _registrar_eventos(self):
        t = self.sim.plan_olas.tiempo
        eventos = [{**e, "tiempo": t} for e in self.sim.last_tick_eventos]
        self._buffer.extend(eventos)
        self._medidas.consumir(eventos)
        if not self.es_v2:
            self.eventos.extend(eventos)

    def _volcar_eventos(self):
        with (self.directorio / "eventos.jsonl").open("a", encoding="utf-8") as f:
            for e in self._buffer:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        self._buffer.clear()

    def guardar(self, estado=None, motivo=None):
        if self.terminal:
            return
        if estado is not None:
            self.estado = estado
        plan = self.sim.plan_olas.resumen(compacto=True)
        metricas = self._medidas.resumen()
        if plan["completa"]:
            self.estado = "completa"
            if metricas["entregados"] != metricas["total"]:
                self.estado, motivo = "error", "El registro de entregas no reconcilia con el motor"
        snap = self.sim.bus.read_snapshot()
        resultado = {"version": 2 if self.es_v2 else 1, "id": self.id, "instancia_id": self.instancia.id,
                     "creada": self.creada, "estado": self.estado, "motivo": motivo,
                     "tiempo": plan["tiempo"], "tiempo_finalizacion": plan["tiempo_finalizacion"]
                     if self.estado == "completa" else None,
                     "preparacion_total": plan["preparacion_total"], "olas": plan["olas"],
                     "modelo_preparacion": plan.get("modelo_preparacion", "sintetica"),
                     "parametros_movimiento": plan.get("parametros_preparacion_fisica"),
                     "preparacion_fisica": plan.get("preparacion_fisica"),
                     **metricas, "ultimo_estado": {"tick_bus": snap.tick,
                     "robots": [asdict(r) for r in snap.robots],
                     "inventario": [asdict(c) for c in snap.grilla]}}
        resultado.update(self.contexto)
        self._volcar_eventos()
        _json(self.directorio / "estado.json", resultado)
        self._ultimo_guardado = plan["tiempo"]

    def avanzar(self):
        if self.terminal:
            return
        if self.sim.plan_olas.completa:
            self.guardar("completa")
            return
        procesando = self.sim.plan_olas.puede_procesar
        preparador = getattr(self.sim.plan_olas, "_preparador", None)
        marca_previa = (getattr(preparador, "fase", None),
                        getattr(preparador, "_vertical_restante", None),
                        len(getattr(preparador, "_ruta", [])))
        entregados = len(self.sim.pedidos_completados)
        try:
            self.sim.avanzar_tick()
            self._registrar_eventos()
        except Exception as exc:
            self.guardar("error", f"{type(exc).__name__}: {exc}")
            raise
        sin_progreso_previo = self.sin_entregas
        self.sin_entregas = (self.sin_entregas + 1 if procesando and
                             len(self.sim.pedidos_completados) == entregados else 0)
        if self.es_v2:
            acciones = {"movimiento", "preparacion_movimiento", "preparacion_toma", "preparacion_izado",
                        "preparacion_descenso", "preparacion_deposito", "caja_recuperada", "recuperacion_izado",
                        "pedido_completado", "liberacion_ola", "preparacion_verificada", "handoff"}
            preparador = getattr(self.sim.plan_olas, "_preparador", None)
            marca_actual = (getattr(preparador, "fase", None),
                            getattr(preparador, "_vertical_restante", None),
                            len(getattr(preparador, "_ruta", [])))
            progreso = (marca_actual != marca_previa or
                        any(e["tipo"] in acciones for e in self.sim.last_tick_eventos))
            self.sin_entregas = 0 if progreso else sin_progreso_previo + 1
        estado = "ejecutando"
        motivo = None
        if self.sim.plan_olas.completa:
            estado = "completa"
        elif getattr(self.sim.plan_olas, "fallo", None):
            estado, motivo = "inviable_preparacion", self.sim.plan_olas.fallo
        elif self.sim.plan_olas.tiempo >= self.limite:
            estado, motivo = "horizonte_agotado", "Se alcanzó el límite de ticks configurado"
        elif self.ventana is not None and self.sin_entregas >= self.ventana:
            estado, motivo = "sin_entregas", ("Ventana sin progreso físico; revisar causa" if self.es_v2
                                               else "Ventana sin entregas durante recuperación; revisar causa")
        self.estado = estado if estado not in self.TERMINALES else self.estado
        if estado in self.TERMINALES or self.sim.plan_olas.tiempo - self._ultimo_guardado >= self.intervalo_guardado:
            self.guardar(estado, motivo)
