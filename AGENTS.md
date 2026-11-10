# AGENTS.md — AutoStore Simulator

> 10-nov-2026: comparación de configuraciones desde Configuración/Historial.
> `experimentos/lotes.py` y `/experiments/batches` ejecutan hasta 24 combinaciones
> tamaño/orden desde la misma instancia y un bus independiente por corrida.
> Un lote activo, cancelación conservando registros, recuperación y exportación
> JSON; archivos en `output/lotes/`, intentos en `output/corridas/` con contexto.
> Guía `docs/comparar_olas.md`. Verificador ampliado: 115 pruebas pasaron;
> producción compila. Navegador: lote `514d1c3525784c38901708be15bc5e8a`, cuatro
> configuraciones completas de 500 solicitudes, tabla recuperada y enlaces a
> corridas verificados. No comparte ni reinicia la simulación individual.
> Instancias v1 usan preparación sintética cero en esta comparación; v2 física.
> Cambios locales sin publicar.

> 10-nov-2026: ampliación v2 implementada con tres agentes Luna e integración del
> agente principal. Listas hasta 1000, cinco urgencias muestreadas de normal,
> inventario sin huecos y preparación física secuencial dentro del almacén.
> Guía `docs/preparacion_fisica_v2.md`; verificador
> `scripts/verificar_preparacion_fisica.py`: 102 pruebas específicas verificadas
> (incluye conservación, API y bus); compilación de producción pasó. Piloto
> `output/piloto_fisico_2026-11-10_v2/`: 72/72 completas, 3.808.594 eventos auditados.
> Navegador: 1000/1000 en 37458 ticks, 904 de preparación, 40 reubicaciones;
> resultado recuperado desde historial. Preparación serializada con un robot;
> tiempos sin calibración operacional. V1 conserva su preparación sintética.
> No duplicar el logger por sesión ni la película completa por tick en v2.
> Cambios locales sin commit/push.

> 10-nov-2026: cierre local de las etapas 1–5. Interfaz de configuración, seguimiento
> y reportes experimentales; catálogo de pilotos y regla adaptativa. 53 pruebas
> específicas pasaron y producción compila. Guías `docs/experimentos_etapa4.md` y
> `docs/experimentos_etapa5.md`. Piloto `output/piloto_2026-11-10_v3/`: 126 corridas,
> 125 completas y una por horizonte; informe reproducible con `python -m
> experimentos.analizar`. No implica validación operacional. Cambios sin publicar.

> 10-nov-2026: tercera etapa local, `experimentos/resultados.py`. Corridas con
> ID único en `output/corridas/`, entrada/eventos/estado y consulta por CLI/API.
> Auditoría controlada de excavación y esperas; guía `docs/experimentos_etapa3.md`.
> Verificar con `scripts/verificar_etapa3.py` (límite 60 segundos). Falta la
> interfaz para mostrar instancias, olas y este historial. Sin commit ni push.

> 10-nov-2026: segunda etapa local en la misma rama: `experimentos/olas.py`
> implementa olas sucesivas, preparación secuencial en ticks y urgentes primero.
> API `/snapshot` incluye `experimento`; guía en `docs/experimentos_etapa2.md`.
> Verificación: `scripts/verificar_etapa2.py`, 36 pruebas pasaron. Falta interfaz
> experimental e historial recuperable de resultados. Cambios sin publicar.

> 10-nov-2026: primera etapa experimental implementada localmente en
> `codex/experimentos-etapa-1`. Ver `docs/experimentos_etapa1.md` y
> `experimentos/instancias.py`: inventario explícito, 12 solicitudes sintéticas
> identificadas y urgencias externas al modelo compartido Pedido. Verificación
> acotada: `.venv/Scripts/python.exe scripts/verificar_etapa1.py` (15 pruebas).
> No hay todavía olas sucesivas ni selección de instancias en la interfaz.

Compact reference for AI coding agents. See `CLAUDE.md` for full project context and `docs/` for contracts.

## Entrypoints

| Purpose | Command |
|---|---|
| Standalone simulation | `python -m motor.run --policy fifo --ticks 100` |
| P09 demo (both policies, report) | `python -m motor.run --compare --ticks 100` |
| All 88 tests | `python -m pytest tests/ bus_persistencia/tests/ -v` |
| Motor tests only | `python -m pytest tests/ -v` |
| Bus tests only | `python -m pytest bus_persistencia/tests/ -v` |
| FastAPI bridge (backend) | `uvicorn api.server:app --reload --port 8000` |
| Frontend (M1 Angular/Ionic) | `cd m1/ && npm start` |

## Architecture rules

- **Single writer**: Only `M2_WRITER_ID = "M2"` can call `bus.write_tick_delta()`. M1/M3 only read via `bus.read_snapshot()`. `WriterNotAuthorizedError` is enforced in code.
- **Delta writes**: M2 writes `TickDelta` (only changed fields per tick). Bus applies merge-by-id for robots, merge-by-cell for grilla.
- **Policy**: Set by M1 via `bus.set_policy()`. M2 **reads only** — never writes policy.
- **Config + pedidos**: M1 writes them to bus (`set_config`, `set_pedidos_cola`). M2 calls `inicializar_desde_bus()` to consume them.
- **M2 runs standalone**: no GPU, no Omniverse, no UI required. M3 failure does not block M2.

## Project layout (key dirs)

```
motor/          # M2 engine — pure Python, no GPU
  simulador.py  # AutoStoreSimulator (orchestrator)
  despachador.py# Central dispatcher (all robot intelligence)
  grilla.py     # 3D grid, O(1) by (x,y,z)
  politicas.py  # FIFO / PRIORIDAD_POSICION
  kpis.py       # 7 KPIs per tick
  modos.py      # Day/night shift processing
  run.py        # Standalone CLI runner
  colmena.py    # Mente Colmena (traffic coordination)
  escorts.py    # Excavation coordination (anti-livelock)
api/            # FastAPI bridge (:8000)
  server.py     # Routes, WebSocket /ws/state
  loop_worker.py# SimulationLoop — runs M2 in daemon thread
bus_persistencia/ # Bus + Persistence (single package)
  bus/state_bus.py  # StateBus (threading.Lock)
  models/state.py   # TickDelta, StateSnapshot, enums, dataclasses
  persistence/      # Loaders, SessionLogger, MetadataStore, report generator
tests/              # 57 motor tests
bus_persistencia/tests/  # 31 bus/persistence tests
m1/                 # Angular/Ionic frontend (:8100)
data/               # config.json, ola.csv, reposicion.csv
output/             # Runtime output (gitignored) — sesion_*.csv, metadata, reporte_comp.csv
```

## Critical models (from `bus_persistencia/models/state.py`)

- `ModoTurno.DIURNO="diurno"`, `ModoTurno.NOCTURNO="nocturno"`
- `PoliticaPicking.FIFO="fifo"`, `PoliticaPicking.PRIORIDAD_POSICION="prioridad_posicion"`
- `RobotEstado`: inactive, desplazandose, excavando, recuperando, bloqueado, entregando, reponiendo, rotando, necesita_handoff, en_transito_anillo
- `Orientacion`: NORTE="N", ESTE="E", OESTE="O" (no SUR)
- 7 KPIs: TSP, TPCP, MTRP, IOG, TR, TI, TBR

## Conventions

- **Commits**: `WIP:` prefix for work-in-progress; module prefix for ready code (`motor:`, `bus:`, `ui:`, `m3:`)
- **Test naming**: `pytest` only (no unittest). Fixtures in `bus_persistencia/tests/fixtures/`.
- **pytest.ini** sets `pythonpath = .`, `testpaths = bus_persistencia/tests`
- **`conftest.py`** in bus_persistencia/tests/ inserts root into sys.path (motor tests rely on running from root, not conftest)

## Dependencies

```
pytest>=8.0.0, fastapi>=0.110.0, uvicorn[standard]>=0.29.0, httpx>=0.27.0, python-multipart>=0.0.9
```

Not required: Omniverse, GPU, `rich` (optional for dashboard).

## Testing quirks

- `python -m` is required for `motor.run` (uses relative imports)
- Tests call `load_config` / `load_ola` from `bus_persistencia.persistence`
- Integration demo: `python -m bus_persistencia.integration` (mock M1/M2/M3)
- P09 demo test: `python -m pytest tests/test_p09_demo.py -v`
- `output/` is gitignored; created at runtime if missing

## What is OUT OF SCOPE

- No sorter/classifier to loading docks
- No external optimization algorithm injection
- No real-time communication with physical AutoStore
- No intelligent night-shift reordering
- No more than 2 picking policies in this version
