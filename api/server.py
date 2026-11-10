"""api/server.py — bridge FastAPI entre el StateBus de M2 y el frontend M1 (T-45).

Contrato acordado con Alonso (M1):
  - FastAPI en :8000, Ionic dev server en :8100 (CORS habilitado para ese origen).
  - WebSocket `ws://localhost:8000/ws/state` empuja `{type: "tick", ...}` por tick.
  - `kpis` con claves en minúscula + `completados`, `capacidad`, `cajasPresentes`.
  - `POST /api/upload/{ola|reposicion}` valida CSV y retorna
    `{valid, errors: [{row, column, value, reason}]}`.

Ejecutar con: `uvicorn api.server:app --reload --port 8000`
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from pydantic import Field, model_validator
from experimentos.instancias import cargar, generar_demo, guardar
from experimentos import resultados as resultados_experimentos
from experimentos.resultados import listar as listar_corridas
from experimentos.lotes import GestorLotes

from bus_persistencia.bus.state_bus import StateBus
from bus_persistencia.models.state import Config, GrillaDimensions
from bus_persistencia.persistence.ola_loader import load_ola
from bus_persistencia.persistence.reposicion_loader import load_reposicion
from bus_persistencia.persistence.report_generator import generate_report
from bus_persistencia.persistence.validation import ValidationResult

from api.loop_worker import SimulationLoop
from api.serializers import MODO_FROM_M1, politica_from_m1, snapshot_to_payload

bus = StateBus()
_websockets: set[WebSocket] = set()
_main_loop: asyncio.AbstractEventLoop | None = None
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_gestores_lotes: dict[Path, GestorLotes] = {}
_lock_gestores_lotes = threading.Lock()


def _gestor_lotes():
    ruta = OUTPUT_DIR.resolve()
    with _lock_gestores_lotes:
        gestor = _gestores_lotes.get(ruta)
        if gestor is None or gestor.cerrado:
            gestor = GestorLotes(ruta)
            _gestores_lotes[ruta] = gestor
        return gestor


def _broadcast() -> None:
    """Notifica a los websockets conectados. Se llama desde el hilo de simulación."""
    if _main_loop is None or not _websockets:
        return
    compacto = loop.instancia is not None and getattr(loop.instancia, "version", 1) >= 2
    payload = snapshot_actual(compacto=compacto)
    for ws in list(_websockets):
        asyncio.run_coroutine_threadsafe(_send_safe(ws, payload), _main_loop)


async def _send_safe(ws: WebSocket, payload: dict[str, Any]) -> None:
    try:
        await ws.send_json(payload)
    except Exception:
        _websockets.discard(ws)


loop = SimulationLoop(bus, on_tick=_broadcast, output_dir=OUTPUT_DIR)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    import logging
    from motor.plugin_loader import load_plugins
    loaded = load_plugins()
    if loaded:
        logging.getLogger(__name__).info("Plugins cargados: %s", loaded)
    try:
        yield
    finally:
        loop.cerrar()
        for gestor in list(_gestores_lotes.values()):
            gestor.cerrar()
        _main_loop = None


app = FastAPI(title="AutoStore Simulator Bridge", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8100"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class GridConfigDTO(BaseModel):
    """Espejo de `GridConfigDTO` en `m1/src/app/core/models/grid-config.model.ts`."""

    x: int
    y: int
    z: int
    num_robots: int
    occupancy_pct: float
    mode: str
    policy: str
    session_name: str | None = None
    semilla: int | None = None
    pedidos_demandados: int | None = None
    # Robots 1×2 con orientación fija: conteos por orientación (configurable).
    robots_norte: int | None = None
    robots_este: int | None = None
    robots_oeste: int | None = None


class PolicyDTO(BaseModel):
    policy: str


class InstanciaDemoDTO(BaseModel):
    # Sin `version`/`cantidad` se conserva el contrato histórico de 12 pedidos.
    version: Literal[1, 2] | None = None
    cantidad: int | None = Field(default=None, ge=1, le=1000, strict=True)
    productos: int = Field(default=20, ge=1, le=500, strict=True)
    x: int | None = Field(default=None, ge=2, le=100, strict=True)
    y: int | None = Field(default=None, ge=2, le=100, strict=True)
    z: int = Field(default=4, ge=1, le=100, strict=True)
    ocupacion: float = Field(default=0.65, gt=0, le=1, allow_inf_nan=False)
    robots: int = Field(default=1, ge=1, le=100, strict=True)
    media_urgencia: float = Field(default=0, allow_inf_nan=False)
    desviacion_urgencia: float = Field(default=1, gt=0, allow_inf_nan=False)
    plazo_base: int | None = Field(default=None, ge=1, strict=True)
    perfil: Literal["uniforme", "concentrado"] = "uniforme"
    semilla: int = 42
    urgentes: int = Field(default=3, ge=0, le=12)
    plazo_tick: int = Field(default=100, ge=1)


class OlasDTO(BaseModel):
    tamano: int = Field(default=12, ge=1, strict=True)
    regla: Literal["original", "urgentes_primero"] = "original"
    preparacion_fija: int = Field(default=0, ge=0, strict=True)
    preparacion_por_pedido: int = Field(default=0, ge=0, strict=True)
    limite_ticks: int = Field(default=1000, ge=1, le=100000, strict=True)
    ventana_sin_entregas: int | None = Field(default=None, ge=1, strict=True)
    adaptativa: bool = False
    minimo: int = Field(default=1, ge=1, strict=True)
    maximo: int = Field(default=32, ge=1, strict=True)
    objetivo_ticks_por_pedido: float = Field(default=15, gt=0, allow_inf_nan=False)
    modelo_preparacion: Literal["sintetica", "fisica"] | None = None

    @model_validator(mode="after")
    def validar_limites(self):
        if self.minimo > self.maximo or (self.adaptativa and not self.minimo <= self.tamano <= self.maximo):
            raise ValueError("El tamaño inicial adaptativo debe estar entre mínimo y máximo")
        if self.modelo_preparacion == "fisica" and (
            self.adaptativa or self.preparacion_fija or self.preparacion_por_pedido
        ):
            raise ValueError("La preparación física no admite regla adaptativa ni costos sintéticos")
        return self


class ConfiguracionLoteDTO(BaseModel):
    model_config = {"extra": "forbid"}
    tamano: int = Field(ge=1, strict=True)
    regla: Literal["original", "urgentes_primero"] = "original"


class LoteDTO(BaseModel):
    model_config = {"extra": "forbid"}
    instancia_id: str = Field(pattern=r"^[0-9a-f]{20}$")
    nombre: str | None = Field(default=None, max_length=80)
    configuraciones: list[ConfiguracionLoteDTO] = Field(min_length=1, max_length=24)
    limite_ticks: int = Field(default=100000, ge=1, le=1000000, strict=True)
    ventana_sin_entregas: int | None = Field(default=None, ge=1, strict=True)


def _error_lote(exc):
    if isinstance(exc, FileNotFoundError):
        return HTTPException(404, "Comparación no encontrada")
    if isinstance(exc, RuntimeError):
        return HTTPException(409, str(exc))
    return HTTPException(422, str(exc))


@app.post("/experiments/batches", status_code=201)
def crear_lote(body: LoteDTO):
    instancia = _leer_instancia(body.instancia_id)
    try:
        return _gestor_lotes().crear(instancia, [c.model_dump() for c in body.configuraciones],
            nombre=body.nombre or "", limite_ticks=body.limite_ticks,
            ventana_sin_entregas=body.ventana_sin_entregas)
    except (ValueError, RuntimeError) as exc:
        raise _error_lote(exc) from exc


@app.get("/experiments/batches")
def listar_lotes():
    return {"lotes": _gestor_lotes().listar()}


@app.get("/experiments/batches/{identificador}")
def obtener_lote(identificador: str):
    try:
        return _gestor_lotes().leer(identificador)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise _error_lote(exc) from exc


@app.post("/experiments/batches/{identificador}/cancel")
def cancelar_lote(identificador: str):
    try:
        return _gestor_lotes().cancelar(identificador)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise _error_lote(exc) from exc


@app.get("/experiments/batches/{identificador}/export")
def exportar_lote(identificador: str):
    try:
        documento = _gestor_lotes().exportar(identificador)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise _error_lote(exc) from exc
    return Response(content=json.dumps(documento, ensure_ascii=False, indent=2),
                    media_type="application/json", headers={
                        "Content-Disposition": f'attachment; filename="comparacion_{identificador}.json"'})


def _leer_instancia(identificador: str):
    if len(identificador) != 20 or any(c not in "0123456789abcdef" for c in identificador):
        raise HTTPException(400, "Identificador de instancia inválido")
    try:
        paths = [OUTPUT_DIR / "instancias" / f"{identificador}.json"]
        paths += [p / "instancias" / f"{identificador}.json" for p in OUTPUT_DIR.glob("piloto_*")
                  if (p / "resumen.json").exists()]
        path = next((p for p in paths if p.exists()), paths[0])
        instancia = cargar(path)
        if instancia.id != identificador:
            raise ValueError("El contenido no coincide con el identificador")
        return instancia
    except FileNotFoundError as exc:
        raise HTTPException(404, "Instancia no encontrada") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/experiments/instances/generate")
def generar_instancia(body: InstanciaDemoDTO):
    if body.version == 2 or body.cantidad is not None:
        from experimentos.instancias import generar_instancia as generar_instancia_v2
        try:
            instancia = generar_instancia_v2(
                semilla=body.semilla,
                cantidad=body.cantidad or 500,
                productos=body.productos,
                x=body.x,
                y=body.y,
                z=body.z,
                ocupacion=body.ocupacion,
                robots=body.robots,
                media_urgencia=body.media_urgencia,
                desviacion_urgencia=body.desviacion_urgencia,
                plazo_base=body.plazo_base,
                perfil=body.perfil,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    else:
        instancia = generar_demo(body.semilla, body.urgentes, body.plazo_tick)
    guardar(instancia, OUTPUT_DIR / "instancias")
    return {"id": instancia.id, "instancia": instancia.to_dict()}


@app.get("/experiments/instances/{identificador}")
def obtener_instancia(identificador: str):
    instancia = _leer_instancia(identificador)
    return {"id": instancia.id, "instancia": instancia.to_dict()}


@app.get("/experiments/instances")
def obtener_instancias():
    instancias = {}
    carpetas = [OUTPUT_DIR / "instancias"] + [p / "instancias" for p in OUTPUT_DIR.glob("piloto_*")
                                              if (p / "resumen.json").exists()]
    for path in sorted(p for carpeta in carpetas for p in carpeta.glob("*.json")):
        instancia = cargar(path)
        clases: dict[str, int] = {}
        for solicitud in instancia.solicitudes:
            clases[solicitud.urgencia] = clases.get(solicitud.urgencia, 0) + 1
        instancias[instancia.id] = {"id": instancia.id, "semilla": instancia.semilla,
                           "total": len(instancia.solicitudes),
                           "urgentes": sum(s.urgencia in {"urgente", "alta", "critica"}
                                           for s in instancia.solicitudes),
                           "clases": clases, "version": instancia.to_dict().get("version", 1)}
    return {"instancias": list(instancias.values())}


@app.post("/experiments/instances/{identificador}/load")
def cargar_instancia(identificador: str, body: OlasDTO | None = None):
    instancia = _leer_instancia(identificador)
    v2 = instancia.to_dict().get("version") == 2
    opciones_dto = body or OlasDTO(modelo_preparacion="fisica" if v2 else "sintetica")
    opciones = opciones_dto.model_dump()
    if opciones["modelo_preparacion"] is None:
        opciones["modelo_preparacion"] = "fisica" if v2 else "sintetica"
    if v2 and opciones["modelo_preparacion"] != "fisica":
        raise HTTPException(422, "Las instancias versión 2 requieren preparación física")
    if opciones["modelo_preparacion"] == "fisica" and (
        opciones["adaptativa"] or opciones["preparacion_fija"] or opciones["preparacion_por_pedido"]
    ):
        raise HTTPException(422, "La preparación física no admite regla adaptativa ni costos sintéticos")
    if opciones["modelo_preparacion"] == "sintetica":
        opciones["modelo_preparacion"] = "legacy"
    limites = {k: opciones.pop(k) for k in ("limite_ticks", "ventana_sin_entregas")}
    loop.configurar(instancia.config, seed=instancia.semilla, politica="fifo",
                    session_name=f"instancia_{instancia.id}", instancia=instancia,
                    opciones_olas=opciones, **limites)
    return {"ok": True, "id": instancia.id, "corrida_id": loop.registro.id}


@app.get("/experiments/runs")
def obtener_corridas():
    carpetas = [OUTPUT_DIR / "corridas"] + [p / "corridas" for p in OUTPUT_DIR.glob("piloto_*")
                                           if (p / "resumen.json").exists()]
    corridas = [r for carpeta in carpetas for r in listar_corridas(carpeta)]
    return {"corridas": sorted(corridas,key=lambda r:(r["creada"],r["id"]),reverse=True)}


def _carpetas_corridas():
    return [OUTPUT_DIR / "corridas"] + [p / "corridas" for p in OUTPUT_DIR.glob("piloto_*")
                                        if (p / "resumen.json").exists()]


def _carpeta_corrida(identificador: str) -> Path:
    if len(identificador) != 32 or any(c not in "0123456789abcdef" for c in identificador):
        raise HTTPException(422, "Identificador de corrida inválido")
    for carpeta in _carpetas_corridas():
        if (carpeta / identificador / "estado.json").exists():
            return carpeta
    raise HTTPException(404, "Corrida no encontrada")


@app.get("/experiments/runs/{identificador}")
def obtener_corrida(identificador: str, compacto: bool = False):
    try:
        carpeta = _carpeta_corrida(identificador)
        if compacto:
            data = resultados_experimentos.leer_resumen(carpeta, identificador)
            data["resultado"].pop("pedidos", None)
            return data
        return resultados_experimentos.leer(carpeta, identificador)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Corrida no encontrada") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/experiments/runs/{identificador}/pedidos")
def obtener_pedidos_corrida(
    identificador: str,
    pagina: int = Query(default=1, ge=1),
    tamano: int = Query(default=50, ge=1, le=200),
    urgencia: str | None = None,
    estado: str | None = None,
    busqueda: str = "",
):
    try:
        carpeta = _carpeta_corrida(identificador)
        return resultados_experimentos.paginar_pedidos(
            carpeta, identificador, pagina=pagina, tamano=tamano,
            urgencia=urgencia, estado=estado, busqueda=busqueda,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, "Corrida no encontrada") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/experiments/runs/{identificador}/eventos")
def obtener_eventos_corrida(
    identificador: str,
    desde: int = Query(default=0, ge=0),
    limite: int = Query(default=100, ge=1, le=500),
    tipo: str | None = None,
    id_pedido: str | None = None,
    ola: int | None = Query(default=None, ge=1),
):
    try:
        carpeta = _carpeta_corrida(identificador)
        return resultados_experimentos.paginar_eventos(
            carpeta, identificador, desde=desde, limite=limite,
            tipo=tipo, id_pedido=id_pedido, ola=ola,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, "Corrida no encontrada") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class VelocidadDTO(BaseModel):
    velocidad: int


def _validation_to_dto(result: ValidationResult[Any]) -> dict[str, Any]:
    return {
        "valid": result.is_valid,
        "errors": [
            {"row": e.fila, "column": e.columna, "value": "", "reason": e.error}
            for e in result.errors
        ],
    }


def snapshot_actual(compacto: bool = False):
    payload = snapshot_to_payload(bus.read_snapshot(), loop.status, loop.velocidad)
    sim = loop.simulador
    if sim and sim.plan_olas:
        resumen = sim.plan_olas.resumen(compacto=compacto)
        if compacto:
            pedidos = resumen.pop("pedidos", [])
            resumen.pop("eventos", None)
            olas = resumen.get("olas", [])
            resumen["total_pedidos"] = resumen.get("total_pedidos", len(pedidos))
            resumen["total_olas"] = len(olas)
            resumen["olas"] = olas[-10:]
            # El detalle a escala se consulta con /pedidos y /eventos.
        payload["experimento"] = resumen
    else:
        payload["experimento"] = None
    payload["corrida"] = {"id": loop.registro.id, "estado": loop.registro.estado} if loop.registro else None
    return payload


@app.get("/snapshot")
def get_snapshot(compacto: bool = False) -> dict[str, Any]:
    return snapshot_actual(compacto=compacto)


@app.post("/config")
def post_config(cfg: GridConfigDTO) -> dict[str, Any]:
    config = Config(
        grilla=GrillaDimensions(x=cfg.x, y=cfg.y, z=cfg.z),
        robots=cfg.num_robots,
        ocupacion_inicial=cfg.occupancy_pct,
        robots_norte=cfg.robots_norte or 0,
        robots_este=cfg.robots_este or 0,
        robots_oeste=cfg.robots_oeste or 0,
    )
    modo = MODO_FROM_M1.get(cfg.mode.upper())
    politica = politica_from_m1(cfg.policy)
    loop.configurar(config, seed=cfg.semilla, modo=modo, politica=politica,
                    pedidos_demandados=cfg.pedidos_demandados,
                    session_name=cfg.session_name)
    return {"ok": True}


@app.post("/policy")
def post_policy(body: PolicyDTO) -> dict[str, Any]:
    politica = politica_from_m1(body.policy)
    if politica is None:
        raise HTTPException(status_code=400, detail=f"Política desconocida: {body.policy!r}")
    try:
        loop.set_politica(politica)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


@app.get("/policies")
def get_policies() -> dict[str, Any]:
    from motor.politicas import list_politicas
    return {"policies": list_politicas()}


@app.post("/control/play")
def control_play() -> dict[str, Any]:
    try:
        loop.play()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "status": loop.status}


@app.post("/control/pause")
def control_pause() -> dict[str, Any]:
    loop.pause()
    return {"ok": True, "status": loop.status}


@app.post("/control/reset")
def control_reset() -> dict[str, Any]:
    loop.reset()
    return {"ok": True, "status": loop.status}


@app.post("/control/speed")
def control_speed(body: VelocidadDTO) -> dict[str, Any]:
    loop.set_velocidad(body.velocidad)
    return {"ok": True, "velocidad": loop.velocidad}


@app.websocket("/ws/state")
async def ws_state(websocket: WebSocket) -> None:
    await websocket.accept()
    _websockets.add(websocket)
    try:
        compacto = loop.instancia is not None and getattr(loop.instancia, "version", 1) >= 2
        await websocket.send_json(snapshot_actual(compacto=compacto))
        while True:
            # Mantiene la conexión viva; M1 no necesita enviar nada por este canal.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _websockets.discard(websocket)


PLUGINS_DIR = Path(__file__).resolve().parents[1] / "plugins" / "politicas"


@app.post("/api/upload/policy")
async def upload_policy(file: UploadFile) -> dict[str, Any]:
    if not file.filename or not file.filename.endswith(".py"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un .py")
    contents = await file.read()
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    dest = PLUGINS_DIR / file.filename
    dest.write_bytes(contents)
    from motor.plugin_loader import validate_and_load_file
    name, error = validate_and_load_file(dest)
    if error:
        raise HTTPException(status_code=400, detail=error)
    return {"ok": True, "policy_name": name}


@app.post("/api/upload/ola")
async def upload_ola(file: UploadFile) -> dict[str, Any]:
    contents = await file.read()
    fd, tmp_str = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    tmp_path = Path(tmp_str)
    tmp_path.write_bytes(contents)
    try:
        result = load_ola(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    if result.is_valid:
        bus.set_pedidos_cola(result.data)
    return _validation_to_dto(result)


@app.post("/api/upload/reposicion")
async def upload_reposicion(file: UploadFile) -> dict[str, Any]:
    contents = await file.read()
    fd, tmp_str = tempfile.mkstemp(suffix=".csv")
    os.close(fd)
    tmp_path = Path(tmp_str)
    tmp_path.write_bytes(contents)
    try:
        result = load_reposicion(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    if result.is_valid:
        loop.set_cola_reposicion(result.data)
    return _validation_to_dto(result)


@app.post("/demo/load-ola")
def demo_load_ola(name: str) -> dict[str, Any]:
    """Carga una ola de demostración desde data/ola_{name}.csv al bus."""
    ola_path = DATA_DIR / f"ola_{name}.csv"
    if not ola_path.exists():
        raise HTTPException(status_code=404, detail=f"Demo file not found: ola_{name}.csv")
    result = load_ola(ola_path)
    if result.is_valid:
        bus.set_pedidos_cola(result.data)
    return _validation_to_dto(result)


@app.get("/report/comparativo")
def get_report_comparativo():
    """Genera y descarga reporte_comp.csv comparando las DOS últimas ejecuciones
    terminadas (KPI | Ejecución A | Ejecución B | Δ%)."""
    if len(loop.finished_runs) < 2:
        raise HTTPException(
            409,
            "Se requieren 2 ejecuciones terminadas para el reporte comparativo. "
            f"Hay {len(loop.finished_runs)}. Corre dos simulaciones completas.",
        )
    (nombre_a, kpis_a), (nombre_b, kpis_b) = loop.finished_runs[-2], loop.finished_runs[-1]
    # Desambiguar si ambas corridas tienen el mismo nombre de ejecución.
    if nombre_a == nombre_b:
        nombre_a, nombre_b = f"{nombre_a}_A", f"{nombre_b}_B"
    path = OUTPUT_DIR / "reporte_comp.csv"
    generate_report(nombre_a, nombre_b, path, kpis_a=kpis_a, kpis_b=kpis_b)
    return FileResponse(
        path,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=reporte_comp.csv"},
    )


@app.get("/report/sesion")
def get_report_sesion():
    csvs = sorted(OUTPUT_DIR.glob("sesion_*.csv"), reverse=True)
    if not csvs:
        raise HTTPException(404, "No hay sesión guardada aún")
    path = csvs[0]
    return FileResponse(
        path,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={path.name}"},
    )


@app.get("/report/m3")
def get_report_m3():
    """Exporta la timeline completa de la simulación como JSON para M3 (Omniverse)."""
    export = loop.get_m3_export()
    if not export["timeline"]:
        raise HTTPException(
            404,
            "No hay datos de simulación. Ejecute una simulación completa primero.",
        )
    import json
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "export_m3.json"
    path.write_text(json.dumps(export, ensure_ascii=False, indent=2), encoding="utf-8")
    return FileResponse(
        path,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=export_m3.json"},
    )


@app.get("/report/status")
def get_report_status():
    """Estado de las ejecuciones terminadas para el frontend."""
    return {
        "finished_runs": len(loop.finished_runs),
        "needed_for_comparativo": 2,
    }
