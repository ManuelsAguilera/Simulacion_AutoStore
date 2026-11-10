"""Generación, validación y persistencia versionada de instancias."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from pathlib import Path
import random
from statistics import NormalDist

from bus_persistencia.bus.state_bus import StateBus
from bus_persistencia.models.state import Caja, Config, GrillaDimensions, Pedido
from motor.grilla import Grilla
from motor.simulador import AutoStoreSimulator


URGENCIAS = ("muy_baja", "baja", "media", "alta", "critica")
CORTES_URGENCIA = (-1.5, -0.5, 0.5, 1.5)
_URGENCIA_A_PRIORIDAD = {nombre: i for i, nombre in enumerate(URGENCIAS)}
_URGENCIA_A_PRIORIDAD.update({"normal": 2, "urgente": 4})


def prioridad_urgencia(solicitud_o_clase) -> int:
    """Devuelve prioridad ascendente 0..4; acepta clase o ``Solicitud``.

    Los valores históricos ``normal`` y ``urgente`` se interpretan como media y
    crítica, respectivamente, para que consumidores existentes sigan funcionando.
    """
    clase = getattr(solicitud_o_clase, "urgencia", solicitud_o_clase)
    try:
        return _URGENCIA_A_PRIORIDAD[clase]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Clase de urgencia desconocida: {clase!r}") from exc


@dataclass(frozen=True)
class Solicitud:
    pedido: Pedido
    orden: int
    urgencia: str
    plazo_tick: int | None
    puntuacion: float | None = None


@dataclass(frozen=True)
class Instancia:
    semilla: int
    config: Config
    inventario: tuple[Caja, ...]
    solicitudes: tuple[Solicitud, ...]
    version: int = 1
    parametros: dict | None = None

    def validar(self) -> None:
        if self.version not in (1, 2):
            raise ValueError("Versión de instancia no soportada")
        dims = self.config.grilla
        if (any(type(n) is not int or n < 1 for n in (dims.x, dims.y, dims.z, self.config.robots))
                or any(type(n) is not int or n < 0 for n in
                       (self.config.robots_norte, self.config.robots_este, self.config.robots_oeste))
                or len(self.config.orientaciones_robots()) != self.config.robots):
            raise ValueError("Dimensiones o configuración de robots inválidas")
        if not self.solicitudes:
            raise ValueError("La lista de solicitudes está vacía")
        if self.version == 2 and not isinstance(self.parametros, dict):
            raise ValueError("Una instancia v2 debe conservar sus parámetros")
        g = Grilla(self.config)
        ids, posiciones = set(), set()
        for caja in self.inventario:
            pos = (caja.x, caja.y, caja.z)
            if (not isinstance(caja.id_caja, str) or not caja.id_caja or caja.id_caja in ids
                    or pos in posiciones or type(caja.cantidad) is not int or caja.cantidad != 1
                    or not g.es_interior(caja.x, caja.y) or type(caja.z) is not int or not 0 <= caja.z < g.gz):
                raise ValueError("Inventario inválido: ID/posición duplicada, cantidad o coordenadas")
            ids.add(caja.id_caja)
            posiciones.add(pos)
        # Toda columna debe ser una pila continua desde z=0.
        alturas: dict[tuple[int, int], set[int]] = {}
        for x, y, z in posiciones:
            alturas.setdefault((x, y), set()).add(z)
        if any(niveles != set(range(len(niveles))) for niveles in alturas.values()):
            raise ValueError("Inventario inválido: hay huecos debajo de cajas")
        pedidos, ordenes = set(), set()
        clases = {"urgente", "normal"} if self.version == 1 else set(URGENCIAS)
        for s in self.solicitudes:
            if (not isinstance(s.pedido.id_pedido, str) or not s.pedido.id_pedido
                    or s.pedido.id_pedido in pedidos or type(s.orden) is not int or s.orden in ordenes
                    or type(s.pedido.cantidad) is not int or s.pedido.cantidad != 1
                    or s.urgencia not in clases
                    or (s.urgencia == "urgente" and (type(s.plazo_tick) is not int or s.plazo_tick < 1))
                    or (s.plazo_tick is not None and (type(s.plazo_tick) is not int or s.plazo_tick < 1))
                    or (self.version == 2 and (not isinstance(s.puntuacion, (int, float))
                                               or not math.isfinite(s.puntuacion)))
                    or (s.puntuacion is not None and (not isinstance(s.puntuacion, (int, float))
                                                      or not math.isfinite(s.puntuacion)))):
                raise ValueError("Solicitud inválida: ID/orden duplicado, cantidad, urgencia, puntuación o plazo")
            pedidos.add(s.pedido.id_pedido)
            ordenes.add(s.orden)
        if ordenes != set(range(len(self.solicitudes))):
            raise ValueError("El orden debe ser consecutivo desde cero")
        stock = Counter(c.id_sku for c in self.inventario)
        demanda = Counter(s.pedido.id_sku for s in self.solicitudes)
        faltantes = {sku: n - stock[sku] for sku, n in demanda.items() if n > stock[sku]}
        if faltantes:
            raise ValueError(f"Sin inventario suficiente: {faltantes}")
        # El generador reserva una columna vacía completa para reubicación; la
        # validación genérica comprueba al menos capacidad libre en el almacén.
        if self.version == 2 and len(posiciones) >= dims.x * dims.y * dims.z:
            raise ValueError("Inventario v2 sin espacio libre para reubicación")

    def to_dict(self) -> dict:
        """Serializa v1 con el esquema original para conservar sus IDs."""
        doc = {"version": 1, "semilla": self.semilla, "config": asdict(self.config),
               "inventario": [asdict(c) for c in self.inventario],
               "solicitudes": [{"pedido": asdict(s.pedido), "orden": s.orden,
                                "urgencia": s.urgencia, "plazo_tick": s.plazo_tick}
                               for s in self.solicitudes]}
        if self.version == 2:
            doc["version"] = 2
            doc["parametros"] = self.parametros
            for solicitud_doc, solicitud in zip(doc["solicitudes"], self.solicitudes):
                solicitud_doc["puntuacion"] = solicitud.puntuacion
        return doc

    @classmethod
    def from_dict(cls, data: dict) -> Instancia:
        version = data.get("version")
        if version not in (1, 2):
            raise ValueError("Versión de instancia no soportada")
        try:
            cfg = dict(data["config"])
            cfg["grilla"] = GrillaDimensions(**cfg["grilla"])
            # Estas versiones utilizan estaciones derivadas de la geometría.
            if cfg.get("estaciones"):
                raise ValueError("Estaciones personalizadas no soportadas en esta versión")
            cfg["estaciones"] = ()
            solicitudes = tuple(
                Solicitud(Pedido(**s["pedido"]), s["orden"], s["urgencia"], s["plazo_tick"],
                          s.get("puntuacion")) for s in data["solicitudes"])
            obj = cls(data["semilla"], Config(**cfg),
                      tuple(Caja(**c) for c in data["inventario"]), solicitudes,
                      version=version, parametros=data.get("parametros"))
            obj.validar()
            return obj
        except (KeyError, TypeError) as exc:
            raise ValueError("Estructura de instancia inválida") from exc

    @property
    def id(self) -> str:
        raw = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:20]


def generar_demo(semilla: int = 42, urgentes: int = 3, plazo_tick: int = 100) -> Instancia:
    """12 cajas existentes y 12 recuperaciones unitarias; caso histórico v1."""
    if not 0 <= urgentes <= 12 or plazo_tick < 1:
        raise ValueError("Urgentes debe estar entre 0 y 12 y el plazo debe ser positivo")
    rng = random.Random(semilla)
    skus = [f"SKU-{i % 3 + 1}" for i in range(12)]
    rng.shuffle(skus)
    cajas = tuple(Caja(f"C{i+1:03}", sku, 1, 2 + i % 6, 2 + i // 6, 0)
                  for i, sku in enumerate(skus))
    seleccion = list(cajas)
    rng.shuffle(seleccion)
    indices = set(rng.sample(range(12), urgentes))
    solicitudes = tuple(Solicitud(Pedido(f"P{i+1:03}", c.id_sku, 1, "Destino_sintetico"),
                                 i, "urgente" if i in indices else "normal",
                                 plazo_tick if i in indices else None)
                        for i, c in enumerate(seleccion))
    obj = Instancia(semilla, Config(GrillaDimensions(6, 6, 2), 1, 100 * 12 / 72,
                                  robots_este=1), cajas, solicitudes)
    obj.validar()
    return obj


def _semilla_derivada(semilla: int, componente: str) -> int:
    bruto = f"autostore-instancia-v2:{semilla}:{componente}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(bruto).digest()[:8], "big")


def _probabilidades_urgencia(media: float, desviacion: float) -> dict[str, float]:
    normal = NormalDist(media, desviacion)
    limites = [normal.cdf(c) for c in CORTES_URGENCIA]
    valores = [limites[0], limites[1] - limites[0], limites[2] - limites[1],
               limites[3] - limites[2], 1.0 - limites[3]]
    return dict(zip(URGENCIAS, valores))


def generar_instancia(semilla: int = 42, cantidad: int = 500, productos: int = 20,
                      x: int | None = None, y: int | None = None, z: int = 4,
                      ocupacion: float = 0.65, robots: int = 1,
                      media_urgencia: float = 0, desviacion_urgencia: float = 1,
                      plazo_base: int | None = None, perfil: str = "uniforme") -> Instancia:
    """Genera una instancia v2 reproducible, con secuencias aleatorias separadas.

    ``x``/``y`` son dimensiones del área almacenable. Si se omiten, se dimensiona
    una grilla que aloja demanda, ocupación solicitada y una columna vacía para
    reubicación. ``perfil`` puede ser uniforme o concentrado.
    """
    if type(semilla) is not int:
        raise ValueError("La semilla debe ser un entero")
    if type(cantidad) is not int or not 1 <= cantidad <= 1000:
        raise ValueError("La cantidad debe ser un entero entre 1 y 1000")
    if type(productos) is not int or productos < 1:
        raise ValueError("La cantidad de productos debe ser un entero positivo")
    if type(z) is not int or z < 1:
        raise ValueError("La profundidad z debe ser un entero positivo")
    if x is not None and (type(x) is not int or x < 2):
        raise ValueError("La dimensión x debe ser un entero de al menos 2")
    if y is not None and (type(y) is not int or y < 2):
        raise ValueError("La dimensión y debe ser un entero de al menos 2")
    if not isinstance(ocupacion, (int, float)) or not math.isfinite(ocupacion) or not 0 < ocupacion < 1:
        raise ValueError("La ocupación debe ser una fracción mayor que 0 y menor que 1")
    if type(robots) is not int or robots < 1:
        raise ValueError("Debe configurarse al menos un robot")
    if (not isinstance(media_urgencia, (int, float)) or not isinstance(desviacion_urgencia, (int, float))
            or not math.isfinite(media_urgencia) or not math.isfinite(desviacion_urgencia)
            or desviacion_urgencia <= 0):
        raise ValueError("La media debe ser finita y la dispersión positiva y finita")
    if plazo_base is not None and (type(plazo_base) is not int or plazo_base < 1):
        raise ValueError("El plazo base debe ser un entero positivo o None")
    if perfil not in {"uniforme", "concentrado"}:
        raise ValueError("El perfil debe ser 'uniforme' o 'concentrado'")

    # Reservamos una columna completa vacía. El mínimo de capacidad también debe
    # satisfacer la ocupación elegida; dimensiones explícitas inviables se rechazan.
    celdas_necesarias = max(cantidad, math.ceil(cantidad / float(ocupacion)))
    columnas_necesarias = math.ceil(celdas_necesarias / z) + 1
    if x is None and y is None:
        x = max(2, math.ceil(math.sqrt(columnas_necesarias)))
        y = max(2, math.ceil(columnas_necesarias / x))
        while x * y < columnas_necesarias:
            y += 1
    elif x is None:
        x = max(2, math.ceil(columnas_necesarias / y))
    elif y is None:
        y = max(2, math.ceil(columnas_necesarias / x))
    columnas = x * y
    capacidad = columnas * z
    reserva = z  # una columna adyacente libre completa
    objetivo_cajas = max(cantidad, round(capacidad * float(ocupacion)))
    if objetivo_cajas > capacidad - reserva:
        raise ValueError("Dimensiones/ocupación no dejan una columna libre para reubicación")

    semillas = {nombre: _semilla_derivada(semilla, nombre)
                for nombre in ("inventario", "demanda", "urgencia")}
    rng_demanda = random.Random(semillas["demanda"])
    rng_inventario = random.Random(semillas["inventario"])
    rng_urgencia = random.Random(semillas["urgencia"])
    n_productos = min(productos, cantidad)
    skus = [f"SKU-{i+1:03}" for i in range(n_productos)]
    if perfil == "uniforme":
        lista_skus = [skus[i % n_productos] for i in range(cantidad)]
        rng_demanda.shuffle(lista_skus)
    else:
        dominante = max(1, round(cantidad * 0.7))
        lista_skus = [skus[0]] * dominante
        if n_productos > 1:
            lista_skus.extend(skus[1 + i % (n_productos - 1)]
                              for i in range(cantidad - dominante))
        else:
            lista_skus.extend([skus[0]] * (cantidad - dominante))
        rng_demanda.shuffle(lista_skus)
    demanda_por_sku = Counter(lista_skus)

    # Las cajas demandadas más las cajas distractoras ocupan el objetivo; se reparte
    # stock adicional sin alterar la lista de pedidos. No hay generación de stock
    # durante la simulación.
    conteos = dict(demanda_por_sku)
    for _ in range(objetivo_cajas - cantidad):
        conteos[rng_inventario.choice(skus)] += 1
    asignacion_skus = [sku for sku, n in conteos.items() for _ in range(n)]
    rng_inventario.shuffle(asignacion_skus)

    columnas_xy = [(2 + i % x, 2 + i // x) for i in range(columnas)]
    # Una columna vacía de reserva y selección aleatoria de las restantes.
    reserva_xy = columnas_xy.pop(rng_inventario.randrange(len(columnas_xy)))
    rng_inventario.shuffle(columnas_xy)
    alturas = {pos: 0 for pos in columnas_xy}
    for _ in range(objetivo_cajas):
        disponibles = [pos for pos, altura in alturas.items() if altura < z]
        pos = rng_inventario.choice(disponibles)
        alturas[pos] += 1
    posiciones = [(px, py, nivel) for (px, py), alto in alturas.items()
                  for nivel in range(alto)]
    rng_inventario.shuffle(posiciones)
    cajas = tuple(Caja(f"C{i+1:06}", sku, 1, *pos)
                  for i, (sku, pos) in enumerate(zip(asignacion_skus, posiciones)))

    plazo_base_efectivo = (plazo_base if plazo_base is not None else
                           cantidad * (2 * (x + y) + 2 * z + 4))
    probabilidades = _probabilidades_urgencia(float(media_urgencia), float(desviacion_urgencia))
    clases_observadas = Counter()
    solicitudes = []
    for i, sku in enumerate(lista_skus):
        puntuacion = rng_urgencia.gauss(media_urgencia, desviacion_urgencia)
        clase = URGENCIAS[sum(puntuacion >= corte for corte in CORTES_URGENCIA)]
        clases_observadas[clase] += 1
        plazo = None
        if plazo_base_efectivo is not None:
            # El mismo factor por clase se aplica a todos los pedidos; menor plazo
            # para mayor urgencia. El plazo es absoluto desde el inicio de la lista.
            plazo = max(1, round(plazo_base_efectivo * (1.4 - 0.2 * prioridad_urgencia(clase))))
        solicitudes.append(Solicitud(Pedido(f"P{i+1:06}", sku, 1, "Destino_sintetico"),
                                     i, clase, plazo, puntuacion))

    robot_este = 1
    restantes = robots - 1
    robot_norte = (restantes + 1) // 2
    robot_oeste = restantes // 2
    config = Config(GrillaDimensions(x, y, z), robots,
                    len(cajas) * 100 / capacidad, robots_este=robot_este,
                    robots_norte=robot_norte, robots_oeste=robot_oeste)
    parametros = {
        "cantidad": cantidad, "productos": n_productos, "perfil": perfil,
        "x": x, "y": y, "z": z, "ocupacion_objetivo": float(ocupacion),
        "ocupacion_observada": len(cajas) / capacidad, "robots": robots,
        "media_urgencia": float(media_urgencia), "desviacion_urgencia": float(desviacion_urgencia),
        "cortes_urgencia": list(CORTES_URGENCIA), "plazo_base_solicitado": plazo_base,
        "plazo_base": plazo_base_efectivo,
        "formula_plazo_base": "cantidad * (2 * (x + y) + 2 * z + 4)" if plazo_base is None else "explícito",
        "semillas": semillas, "columna_reserva": list(reserva_xy),
        "frecuencias_esperadas": {k: cantidad * v for k, v in probabilidades.items()},
        "frecuencias_observadas": {k: clases_observadas.get(k, 0) for k in URGENCIAS},
    }
    instancia = Instancia(semilla, config, cajas, tuple(solicitudes), version=2,
                          parametros=parametros)
    instancia.validar()
    return instancia


def guardar(instancia: Instancia, carpeta: Path) -> Path:
    instancia.validar()
    carpeta.mkdir(parents=True, exist_ok=True)
    path = carpeta / f"{instancia.id}.json"
    contenido = json.dumps(instancia.to_dict(), ensure_ascii=False, indent=2)
    try:
        with path.open("x", encoding="utf-8") as f:
            f.write(contenido + "\n")
    except FileExistsError:
        if cargar(path) != instancia:
            raise ValueError("La instancia guardada no coincide con su identificador")
    return path


def generar_escenario(semilla: int, perfil="uniforme", profundidad="superficial", ocupacion=None,
                      robots=1, plazo_tick=250) -> Instancia:
    """32 solicitudes y cuatro SKU; escenario histórico de pilotos v1."""
    if perfil not in {"uniforme", "concentrado"} or profundidad not in {"superficial", "mixta"}:
        raise ValueError("Perfil o profundidad desconocidos")
    rng = random.Random(semilla)
    skus = ([f"SKU-{i%4+1}" for i in range(32)] if perfil == "uniforme"
            else ["SKU-1"]*26 + ["SKU-2", "SKU-3", "SKU-4"]*2)
    rng.shuffle(skus)
    cajas = [Caja(f"C{i:03}", sku, 1, 2+i%8, 2+i//8, 0) for i, sku in enumerate(skus)]
    solicitadas = list(cajas)
    if profundidad == "mixta":
        cajas += [Caja(f"B{i:03}", "BLOQUEADOR", 1, c.x, c.y, 1)
                  for i, c in enumerate(solicitadas) if i%2 == 0]
    if ocupacion is not None:
        if not 0 < ocupacion <= 1:
            raise ValueError("Ocupación debe ser una fracción entre cero y uno")
        usadas = {(c.x,c.y,c.z) for c in cajas}
        objetivo = int(8*8*3*ocupacion)
        if objetivo < len(cajas):
            raise ValueError("La ocupación no permite alojar el inventario del escenario")
        for z0 in range(3):
            for y0 in range(2,10):
                for x0 in range(2,10):
                    if len(cajas) < objetivo and (x0,y0,z0) not in usadas:
                        cajas.append(Caja(f"F{len(cajas):03}", "BLOQUEADOR", 1, x0,y0,z0))
    rng.shuffle(solicitadas)
    urgentes = set(rng.sample(range(32),8))
    solicitudes = tuple(Solicitud(Pedido(f"P{i+1:03}", c.id_sku, 1, "Destino_sintetico"), i,
                        "urgente" if i in urgentes else "normal", plazo_tick if i in urgentes else None)
                        for i,c in enumerate(solicitadas))
    config = Config(GrillaDimensions(8,8,3), robots, len(cajas)*100/192,
                    robots_este=1 if robots==1 else 0)
    obj = Instancia(semilla,config,tuple(cajas),solicitudes)
    obj.validar()
    return obj


def cargar(path: Path) -> Instancia:
    return Instancia.from_dict(json.loads(path.read_text(encoding="utf-8")))


def inicializar(instancia: Instancia, bus: StateBus | None = None,
                opciones_olas: dict | None = None) -> AutoStoreSimulator:
    instancia.validar()
    bus = bus or StateBus()
    bus.set_event_logging(instancia.version < 2)
    bus.reset(config=instancia.config)
    bus.set_pedidos_cola([s.pedido for s in sorted(instancia.solicitudes, key=lambda s: s.orden)])
    bus.set_policy("fifo")
    sim = AutoStoreSimulator(bus)
    opciones = dict(opciones_olas or {})
    if instancia.version == 2:
        opciones.setdefault("modelo_preparacion", "fisica")
    crear_plan = opciones_olas is not None or instancia.version == 2
    sim.inicializar_desde_bus(seed=instancia.semilla, inventario=list(instancia.inventario),
                             plan_olas={"solicitudes": instancia.solicitudes, **opciones}
                             if crear_plan else None)
    return sim
