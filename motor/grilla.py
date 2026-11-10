"""motor/grilla.py — Grilla 3D de almacenamiento con corredores de tránsito E·T·A.

Modelo de coordenadas (robots 1×2 con orientación fija). Cada borde con estación
sigue el patrón **E·T·A** (Entrega/conveyor → Tránsito → Almacenaje); el Sur, sin
estaciones, es sólo **T·A**:

  Márgenes por borde (celdas antes del almacenaje):
    M_OESTE = 2 (estación picking + carril tránsito)
    M_ESTE  = 2 (carril tránsito + estación picking)
    M_NORTE = 2 (conveyor de ingreso + carril tránsito)
    M_SUR   = 1 (sólo carril tránsito)

  Almacenaje (interior): x ∈ [M_OESTE .. M_OESTE+gx-1] = [2..gx+1]
                         y ∈ [M_NORTE .. M_NORTE+gy-1] = [2..gy+1]
  Superficie total transitable: (gx + M_OESTE + M_ESTE) × (gy + M_NORTE + M_SUR)
                                = (gx+4) × (gy+3)

  Estaciones de SALIDA (picking): Oeste x=0 (OESTE), Este x=gx+3 (ESTE), intercaladas
    en filas de almacenaje, `mitad(gy)` por lado.
  Conveyors de INGRESO (Norte): y=0, intercaladas en columnas de almacenaje,
    `mitad(gx)`. Entra carga en turno nocturno; los robots NORTE las recogen.

Una Caja exacta por celda (x, y, z), alineado con el contrato del bus.
"""
from __future__ import annotations

import random

from bus_persistencia.models.state import (
    Caja,
    Config,
    Estacion,
    EstacionRol,
    Orientacion,
    TipoEstacion,
)

# Márgenes por borde (ver docstring). E·T·A en O/E/N; T·A en S.
M_OESTE = 2
M_ESTE = 2
M_NORTE = 2
M_SUR = 1


def _mitad(n: int) -> int:
    """Mitad redondeando hacia arriba (no truncando): ceil(n/2)."""
    return (n + 1) // 2


class Grilla:
    """Grilla 3D de almacenamiento con acceso O(1) por coordenada (x, y, z).

    El almacenamiento ocupa el interior desplazado por los márgenes; el resto de la
    superficie es tránsito y aloja estaciones de salida (E/O) y conveyors de ingreso
    (Norte)."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._celdas: dict[tuple[int, int, int], Caja] = {}
        self._delta: list[Caja] = []
        self._remove: list[tuple[int, int, int]] = []
        # Estaciones de SALIDA (picking, E/O) y conveyors de INGRESO (Norte).
        self._estaciones: tuple[Estacion, ...] = self._calcular_estaciones()
        self._conveyors_norte: tuple[Estacion, ...] = self._calcular_conveyors_norte()
        self._estaciones_por_pos: dict[tuple[int, int], Estacion] = {
            (e.x, e.y): e for e in (*self._estaciones, *self._conveyors_norte)
        }
        # `puertos`: posiciones de entrega (celdas-estación de salida). Se mantiene el
        # nombre por compatibilidad con políticas/despachador.
        self._puertos: list[tuple[int, int]] = sorted(
            (e.x, e.y) for e in self._estaciones
        )

    # ------------------------------------------------------------------
    # Dimensiones y geometría
    # ------------------------------------------------------------------

    @property
    def gx(self) -> int:
        """Ancho del área almacenable (interior)."""
        return self.config.grilla.x

    @property
    def gy(self) -> int:
        """Fondo del área almacenable (interior)."""
        return self.config.grilla.y

    @property
    def gz(self) -> int:
        return self.config.grilla.z

    @property
    def ancho_total(self) -> int:
        """Ancho de la superficie transitable, incluyendo márgenes: gx + M_OESTE + M_ESTE."""
        return self.gx + M_OESTE + M_ESTE

    @property
    def alto_total(self) -> int:
        """Alto de la superficie transitable, incluyendo márgenes: gy + M_NORTE + M_SUR."""
        return self.gy + M_NORTE + M_SUR

    @property
    def interior_bounds(self) -> tuple[int, int, int, int]:
        """Límites del almacenaje (x0, y0, x1, y1) inclusivos."""
        return (M_OESTE, M_NORTE, M_OESTE + self.gx - 1, M_NORTE + self.gy - 1)

    def es_interior(self, x: int, y: int) -> bool:
        """True si (x,y) es una columna almacenable (interior, no tránsito)."""
        x0, y0, x1, y1 = self.interior_bounds
        return x0 <= x <= x1 and y0 <= y <= y1

    def en_superficie(self, x: int, y: int) -> bool:
        """True si (x,y) está dentro de la superficie transitable total."""
        return 0 <= x < self.ancho_total and 0 <= y < self.alto_total

    def es_transito(self, x: int, y: int) -> bool:
        """True si (x,y) es celda de tránsito (en superficie y no interior)."""
        return self.en_superficie(x, y) and not self.es_interior(x, y)

    # ------------------------------------------------------------------
    # Inicialización
    # ------------------------------------------------------------------

    def inicializar_aleatoria(self, seed: int | None = None) -> None:
        """Puebla el interior aleatoriamente hasta `config.ocupacion_inicial`.
        El tránsito nunca recibe cajas. Usa `seed` para reproducibilidad."""
        rng = random.Random(seed)
        cap = self.capacidad_almacenable
        ocupacion = self.config.ocupacion_inicial
        n_cajas = int(cap * ocupacion / 100 if ocupacion > 1 else cap * ocupacion)

        gz = self.gz
        x0, y0, x1, y1 = self.interior_bounds
        todas_celdas = [
            (x, y, z)
            for x in range(x0, x1 + 1)
            for y in range(y0, y1 + 1)
            for z in range(gz)
        ]
        n_cajas = min(n_cajas, len(todas_celdas))
        rng.shuffle(todas_celdas)

        skus = [f"SKU{i:03d}" for i in range(1, 11)]
        for i, (x, y, z) in enumerate(todas_celdas[:n_cajas]):
            caja = Caja(
                id_caja=f"C{i:05d}",
                id_sku=rng.choice(skus),
                cantidad=rng.randint(1, 10),
                x=x, y=y, z=z,
            )
            self._celdas[(x, y, z)] = caja
        self._delta = list(self._celdas.values())
        self._remove = []

    # ------------------------------------------------------------------
    # Operaciones CRUD — O(1) por celda
    # ------------------------------------------------------------------

    def agregar(self, caja: Caja) -> None:
        """Coloca una caja en su celda (x,y,z) del interior.

        Lanza ValueError si la celda no es interior (tránsito o fuera de la zona
        almacenable): el tránsito es exclusivo para robots."""
        if not self.es_interior(caja.x, caja.y):
            raise ValueError(
                f"Celda ({caja.x},{caja.y}) no es interior almacenable "
                f"(tránsito o fuera de grilla): no admite cajas."
            )
        key = (caja.x, caja.y, caja.z)
        self._celdas[key] = caja
        self._delta.append(caja)

    def remover(self, x: int, y: int, z: int) -> Caja | None:
        """Vacía la celda (x,y,z). Retorna la caja que había, o None."""
        key = (x, y, z)
        caja = self._celdas.pop(key, None)
        if caja is not None:
            self._remove.append(key)
        return caja

    def get(self, x: int, y: int, z: int) -> Caja | None:
        """Retorna la caja en la celda, o None si está vacía."""
        return self._celdas.get((x, y, z))

    def ocupada(self, x: int, y: int, z: int) -> bool:
        return (x, y, z) in self._celdas

    # ------------------------------------------------------------------
    # Consultas de columna (para excavación y reposición)
    # ------------------------------------------------------------------

    def columna(self, x: int, y: int) -> list[Caja]:
        """Cajas de la columna (x,y) ordenadas de menor a mayor z."""
        gz = self.gz
        return [self._celdas[(x, y, z)] for z in range(gz) if (x, y, z) in self._celdas]

    def celdas_libres_en_columna(self, x: int, y: int) -> list[int]:
        """Niveles z disponibles (sin caja) en la columna interior (x,y).
        Una columna de tránsito nunca tiene niveles almacenables."""
        if not self.es_interior(x, y):
            return []
        gz = self.gz
        return [z for z in range(gz) if (x, y, z) not in self._celdas]

    def columnas_adyacentes(self, x: int, y: int) -> list[tuple[int, int]]:
        """Columnas interiores vecinas (ortogonales) de (x,y), para reubicar cajas."""
        candidatas = [(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)]
        return [(cx, cy) for cx, cy in candidatas if self.es_interior(cx, cy)]

    def celdas_adyacentes_superficie(self, x: int, y: int) -> list[tuple[int, int]]:
        """Celdas vecinas (ortogonales) dentro de la superficie transitable total.
        Para el desplazamiento de robots (no para cajas)."""
        candidatas = [(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)]
        return [(cx, cy) for cx, cy in candidatas if self.en_superficie(cx, cy)]

    # ------------------------------------------------------------------
    # Búsqueda por SKU (T-10) — para el despachador
    # ------------------------------------------------------------------

    def buscar_por_sku(self, id_sku: str) -> list[Caja]:
        """Retorna todas las cajas con el SKU dado, ordenadas por z ascendente."""
        resultados = [c for c in self._celdas.values() if c.id_sku == id_sku]
        return sorted(resultados, key=lambda c: c.z)

    def primera_caja_accesible(self, id_sku: str) -> Caja | None:
        """La caja de ese SKU con menor costo de excavación en su columna."""
        candidatas = self.buscar_por_sku(id_sku)
        if not candidatas:
            return None

        def costo_excavacion(c: Caja) -> int:
            gz = self.gz
            return sum(1 for z in range(c.z + 1, gz) if (c.x, c.y, z) in self._celdas)
        return min(candidatas, key=costo_excavacion)

    # ------------------------------------------------------------------
    # Estaciones de salida (picking, E/O) y conveyors de ingreso (Norte)
    # ------------------------------------------------------------------

    def _calcular_estaciones(self) -> tuple[Estacion, ...]:
        """Estaciones de SALIDA (picking): Oeste en x=0 (OESTE) y Este en
        x=ancho_total-1 (ESTE), intercaladas en filas de almacenaje, `mitad(gy)`
        por lado. Si la config trae estaciones explícitas, se respetan."""
        if self.config.estaciones:
            return tuple(self.config.estaciones)
        x_oeste = 0
        x_este = self.ancho_total - 1
        y0, y1 = M_NORTE, M_NORTE + self.gy - 1
        filas = list(range(y0, y1 + 1, 2))  # intercaladas → mitad(gy)
        estaciones: list[Estacion] = []
        for y in filas:
            estaciones.append(Estacion(
                id=f"EST-O-{y}", x=x_oeste, y=y,
                tipo=TipoEstacion.CINTA,
                orientacion_requerida=Orientacion.OESTE,
                rol=EstacionRol.ENTREGA,
            ))
            estaciones.append(Estacion(
                id=f"EST-E-{y}", x=x_este, y=y,
                tipo=TipoEstacion.CINTA,
                orientacion_requerida=Orientacion.ESTE,
                rol=EstacionRol.ENTREGA,
            ))
        return tuple(estaciones)

    def _calcular_conveyors_norte(self) -> tuple[Estacion, ...]:
        """Conveyors de INGRESO en el Norte (y=0), intercaladas en columnas de
        almacenaje, `mitad(gx)`. Orientación NORTE, rol INGRESO."""
        x0, x1 = M_OESTE, M_OESTE + self.gx - 1
        cols = list(range(x0, x1 + 1, 2))  # intercaladas → mitad(gx)
        return tuple(
            Estacion(
                id=f"CONV-N-{x}", x=x, y=0,
                tipo=TipoEstacion.CINTA,
                orientacion_requerida=Orientacion.NORTE,
                rol=EstacionRol.INGRESO,
            )
            for x in cols
        )

    @property
    def estaciones(self) -> tuple[Estacion, ...]:
        """Estaciones de SALIDA (picking, E/O) — las usadas en turno diurno."""
        return self._estaciones

    @property
    def conveyors_norte(self) -> tuple[Estacion, ...]:
        """Conveyors de INGRESO del Norte — usadas en turno nocturno."""
        return self._conveyors_norte

    def estacion_en(self, x: int, y: int) -> Estacion | None:
        return self._estaciones_por_pos.get((x, y))

    def estaciones_compatibles(self, orientacion: Orientacion) -> list[Estacion]:
        """Estaciones de SALIDA que un robot con la orientación dada puede usar para
        entregar (su punta cae sobre la celda-estación). Los robots NORTE no tienen
        estación de salida compatible (deben colaborar vía handoff)."""
        return [e for e in self._estaciones if e.orientacion_requerida == orientacion]

    def estacion_compatible_mas_cercana(
        self, x: int, y: int, orientacion: Orientacion
    ) -> Estacion | None:
        """Estación de salida compatible más cercana (Manhattan) a (x,y)."""
        compatibles = self.estaciones_compatibles(orientacion)
        if not compatibles:
            return None
        return min(compatibles, key=lambda e: abs(e.x - x) + abs(e.y - y))

    # ------------------------------------------------------------------
    # Tránsito y puertos (compatibilidad)
    # ------------------------------------------------------------------

    @property
    def anillo(self) -> list[tuple[int, int]]:
        """Todas las celdas de tránsito (superficie no interior), ordenadas.
        Base para el spawn de robots y para el render del corredor."""
        return sorted(
            (x, y)
            for y in range(self.alto_total)
            for x in range(self.ancho_total)
            if not self.es_interior(x, y)
        )

    @property
    def puertos(self) -> list[tuple[int, int]]:
        """Posiciones de entrega (celdas-estación de salida E/O)."""
        return self._puertos

    def puerto_mas_cercano(self, x: int, y: int) -> tuple[int, int]:
        """Estación de salida con menor distancia Manhattan a la columna (x, y)."""
        return min(self._puertos, key=lambda p: abs(p[0] - x) + abs(p[1] - y))

    # ------------------------------------------------------------------
    # Capacidad e IOG
    # ------------------------------------------------------------------

    @property
    def capacidad_almacenable(self) -> int:
        """Capacidad real para cajas: solo el interior (gx·gy·gz)."""
        return self.gx * self.gy * self.gz

    def iog(self) -> float:
        """Índice de Ocupación de Grilla: cajas / capacidad_almacenable * 100."""
        cap = self.capacidad_almacenable
        return len(self._celdas) / cap * 100 if cap > 0 else 0.0

    @property
    def total_cajas(self) -> int:
        return len(self._celdas)

    @property
    def cajas(self) -> list[Caja]:
        """Copia de las cajas almacenadas; no incluye cargas transportadas."""
        return list(self._celdas.values())

    # ------------------------------------------------------------------
    # Delta para el TickDelta del bus
    # ------------------------------------------------------------------

    def flush_delta(self) -> tuple[list[Caja], list[tuple[int, int, int]]]:
        """Retorna (grilla_delta, grilla_remove) acumulados y limpia los buffers.
        Llamar una vez por tick, justo antes de armar el TickDelta."""
        delta, remove = self._delta, self._remove
        self._delta = []
        self._remove = []
        return delta or [], remove or []
