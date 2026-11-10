"""Comparaciones persistentes de configuraciones sobre una misma instancia."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
from uuid import uuid4

from bus_persistencia.bus.state_bus import StateBus
from .instancias import Instancia, inicializar
from .resultados import RegistroCorrida, _json, version_codigo


class _SoloRegistroCorrida:
    def buffer_event(self, *args): pass
    def flush_tick_async(self, *args): pass


def _ahora():
    return datetime.now(timezone.utc).isoformat()


def _resumen_resultado(resultado):
    campos = ("estado", "motivo", "entregados", "total", "tiempo", "tiempo_finalizacion",
              "preparacion_total", "movimientos_preparacion", "movimientos_bloqueadores",
              "cumplimiento_urgentes", "servicio_por_categoria")
    return {k: resultado.get(k) for k in campos}


class GestorLotes:
    """Un lote activo por servicio, con un bus nuevo para cada configuración.

    La simulación individual no se comparte. El manifiesto permite recuperar los
    resultados después de un reinicio; una ejecución interrumpida no se reanuda
    automáticamente ni recibe un tiempo de finalización.
    """
    TERMINALES = {"completa", "cancelada", "interrumpida", "error"}

    def __init__(self, output_dir):
        self.output = Path(output_dir)
        self.carpeta = self.output / "lotes"
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._hilo = None
        self._activo = None
        self._documento = None
        self.cerrado = False
        self._recuperar()

    @staticmethod
    def _identificador(identificador):
        if (not isinstance(identificador, str) or len(identificador) != 32
                or any(c not in "0123456789abcdef" for c in identificador)):
            raise ValueError("Identificador de comparación inválido")
        return identificador

    def _ruta(self, identificador):
        return self.carpeta / self._identificador(identificador) / "lote.json"

    def _recuperar(self):
        for path in self.carpeta.glob("*/lote.json"):
            documento = json.loads(path.read_text(encoding="utf-8"))
            if documento["estado"] in self.TERMINALES:
                continue
            motivo = "El servicio se reinició antes de terminar la comparación"
            for fila in documento["configuraciones"]:
                if fila["estado"] == "ejecutando":
                    fila["estado"] = "interrumpida"
                    corrida = fila.get("corrida_id")
                    if corrida:
                        estado_path = self.output / "corridas" / corrida / "estado.json"
                        if estado_path.exists():
                            estado = json.loads(estado_path.read_text(encoding="utf-8"))
                            if estado["estado"] not in RegistroCorrida.TERMINALES:
                                estado.update(estado="interrumpida", motivo=motivo, tiempo_finalizacion=None)
                                _json(estado_path, estado)
                            fila["estado"] = estado["estado"]
                            fila["resultado"] = _resumen_resultado(estado)
                elif fila["estado"] == "pendiente":
                    fila["estado"] = "omitida"
            documento.update(estado="interrumpida", motivo=motivo, actual=None, actualizada=_ahora())
            documento["finalizadas"] = self._finalizadas(documento)
            _json(path, documento)

    @staticmethod
    def _finalizadas(documento):
        return sum(f["estado"] in RegistroCorrida.TERMINALES for f in documento["configuraciones"])

    def crear(self, instancia, configuraciones, nombre="", limite_ticks=100000, ventana_sin_entregas=None):
        instancia.validar()
        if not isinstance(configuraciones, list) or not 1 <= len(configuraciones) <= 24:
            raise ValueError("Selecciona entre una y 24 configuraciones")
        if type(limite_ticks) is not int or not 1 <= limite_ticks <= 1000000:
            raise ValueError("Horizonte fuera de 1–1000000 ticks")
        if ventana_sin_entregas is not None and (type(ventana_sin_entregas) is not int or ventana_sin_entregas < 1):
            raise ValueError("Ventana sin progreso inválida")
        if not isinstance(nombre, str) or len(nombre.strip()) > 80:
            raise ValueError("El nombre debe tener hasta 80 caracteres")
        normalizadas, vistas = [], set()
        for configuracion in configuraciones:
            if not isinstance(configuracion, dict) or set(configuracion) - {"tamano", "regla"}:
                raise ValueError("Configuración de ola desconocida")
            tamano, regla = configuracion.get("tamano"), configuracion.get("regla", "original")
            if type(tamano) is not int or not 1 <= tamano <= len(instancia.solicitudes):
                raise ValueError("El tamaño debe estar entre uno y la longitud de la lista")
            if regla not in {"original", "urgentes_primero"}:
                raise ValueError("Orden de formación desconocido")
            if (tamano, regla) in vistas:
                raise ValueError("Hay configuraciones repetidas")
            vistas.add((tamano, regla))
            normalizadas.append({"tamano": tamano, "regla": regla})
        # Copia independiente también de parámetros mutables de la instancia.
        instancia = Instancia.from_dict(json.loads(json.dumps(instancia.to_dict())))
        with self._lock:
            if self.cerrado:
                raise RuntimeError("El gestor de comparaciones está cerrado")
            if self._activo is not None:
                raise RuntimeError("Ya hay una comparación en ejecución; espera o cancélala")
            identificador = uuid4().hex
            directorio = self.carpeta / identificador
            directorio.mkdir(parents=True, exist_ok=False)
            protocolo = {"version": 1, "id": identificador, "instancia": instancia.to_dict(),
                "instancia_id": instancia.id, "configuraciones": normalizadas,
                "limite_ticks": limite_ticks, "ventana_sin_entregas": ventana_sin_entregas,
                "politica_interna": "fifo", "modelo_preparacion": "fisica" if instancia.version >= 2 else "sintetica",
                "codigo": version_codigo(), "intervalo_guardado": 250}
            _json(directorio / "protocolo.json", protocolo)
            documento = {"version": 1, "id": identificador, "nombre": nombre.strip() or "Comparación de olas",
                "instancia_id": instancia.id, "semilla": instancia.semilla, "total_pedidos": len(instancia.solicitudes),
                "estado": "lista", "motivo": None, "creada": _ahora(), "actualizada": _ahora(),
                "total_configuraciones": len(normalizadas), "finalizadas": 0, "actual": None,
                "configuraciones": [{"id": f"C{i+1:02}", **c, "estado": "pendiente",
                                      "corrida_id": None, "resultado": None} for i,c in enumerate(normalizadas)]}
            self._documento, self._activo = documento, identificador
            self._stop.clear()
            self._persistir()
            respuesta = deepcopy(documento)
            self._hilo = threading.Thread(target=self._ejecutar, args=(instancia, protocolo), daemon=True)
            self._hilo.start()
            return respuesta

    def _persistir(self):
        self._documento["actualizada"] = _ahora()
        self._documento["finalizadas"] = self._finalizadas(self._documento)
        _json(self._ruta(self._documento["id"]), self._documento)

    def leer(self, identificador):
        path = self._ruta(identificador)
        with self._lock:
            if self._activo == identificador:
                return deepcopy(self._documento)
            return json.loads(path.read_text(encoding="utf-8"))

    def listar(self):
        campos = ("id", "nombre", "instancia_id", "semilla", "total_pedidos", "estado", "creada",
                  "total_configuraciones", "finalizadas")
        with self._lock:
            documentos = [self.leer(p.parent.name) for p in self.carpeta.glob("*/lote.json")]
        return sorted(({k: d[k] for k in campos} for d in documentos), key=lambda d: (d["creada"],d["id"]), reverse=True)

    def cancelar(self, identificador):
        with self._lock:
            documento = self.leer(identificador)
            if documento["estado"] in self.TERMINALES:
                return documento
            if self._activo == identificador:
                self._stop.set()
                self._documento["estado"] = "cancelando"
                self._persistir()
                return deepcopy(self._documento)
            raise RuntimeError("La comparación no pertenece a este proceso")

    def exportar(self, identificador):
        with self._lock:
            return {"lote": self.leer(identificador), "protocolo": json.loads(
                (self._ruta(identificador).parent / "protocolo.json").read_text(encoding="utf-8"))}

    def cerrar(self):
        with self._lock:
            self.cerrado = True
            self._stop.set()
            hilo = self._hilo
        if hilo is not None:
            hilo.join(timeout=10)
            if hilo.is_alive():
                raise RuntimeError("La comparación no se detuvo dentro de diez segundos")

    def _actualizar_fila(self, indice, estado, corrida_id=None, resultado=None):
        with self._lock:
            fila = self._documento["configuraciones"][indice]
            fila["estado"] = estado
            if corrida_id is not None:
                fila["corrida_id"] = corrida_id
            if resultado is not None:
                fila["resultado"] = resultado
            self._persistir()

    def _ejecutar(self, instancia, protocolo):
        registro = None
        try:
            with self._lock:
                if not self._stop.is_set():
                    self._documento["estado"] = "ejecutando"
                self._persistir()
            for indice, configuracion in enumerate(protocolo["configuraciones"]):
                if self._stop.is_set():
                    break
                with self._lock:
                    self._documento["actual"] = indice
                self._actualizar_fila(indice, "ejecutando")
                registro = None
                try:
                    sim = inicializar(instancia, bus=StateBus(_SoloRegistroCorrida()),
                        opciones_olas={**configuracion, "modelo_preparacion": protocolo["modelo_preparacion"]})
                    registro = RegistroCorrida(instancia, sim, self.output / "corridas",
                        limite_ticks=protocolo["limite_ticks"], ventana_sin_entregas=protocolo["ventana_sin_entregas"],
                        intervalo_guardado=protocolo["intervalo_guardado"],
                        contexto={"lote_id": protocolo["id"], "configuracion_id": f"C{indice+1:02}"})
                    self._actualizar_fila(indice, "ejecutando", registro.id)
                    ultimo = time.monotonic()
                    while not registro.terminal and not self._stop.is_set():
                        registro.avanzar()
                        if time.monotonic() - ultimo >= 0.5:
                            actual = json.loads((registro.directorio / "estado.json").read_text(encoding="utf-8"))
                            self._actualizar_fila(indice, "ejecutando", registro.id, _resumen_resultado(actual))
                            ultimo = time.monotonic()
                    if not registro.terminal:
                        registro.guardar("interrumpida", "Cierre del servicio" if self.cerrado else "Comparación cancelada")
                    resultado = json.loads((registro.directorio / "estado.json").read_text(encoding="utf-8"))
                    self._actualizar_fila(indice, resultado["estado"], registro.id, _resumen_resultado(resultado))
                except Exception as exc:
                    motivo = f"{type(exc).__name__}: {exc}"
                    if registro is not None:
                        registro.guardar("error", motivo)
                        resultado = json.loads((registro.directorio / "estado.json").read_text(encoding="utf-8"))
                        self._actualizar_fila(indice, resultado["estado"], registro.id, _resumen_resultado(resultado))
                    else:
                        self._actualizar_fila(indice, "error", resultado={"estado": "error", "motivo": motivo,
                            "tiempo_finalizacion": None, "entregados": 0, "total": len(instancia.solicitudes)})
        except Exception as exc:
            with self._lock:
                self._documento.update(estado="error", motivo=f"{type(exc).__name__}: {exc}")
        finally:
            with self._lock:
                if self._stop.is_set():
                    for fila in self._documento["configuraciones"]:
                        if fila["estado"] == "pendiente":
                            fila["estado"] = "omitida"
                    self._documento["estado"] = "interrumpida" if self.cerrado else "cancelada"
                elif self._documento["estado"] != "error":
                    self._documento["estado"] = "completa"
                self._documento["actual"] = None
                self._persistir()
                self._activo = None
