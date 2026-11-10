# Informe de capacidades y estado de ejecución — Simulador AutoStore

> **Actualización del 10-nov-2026:** se corrigió la sustitución de cajas en
> `motor/run.py` sin alterar la ocupación; la CLI FIFO terminó una prueba de
> 30 ticks y pasó la prueba específica de inventario. Los resultados de la
> revisión del 30-oct-2026 que siguen documentan el estado previo a la
> corrección. La prueba de API aún no tiene un resultado concluyente porque
> quedó ejecutándose y se interrumpió.

## 1. Resumen ejecutivo

El simulador tiene un núcleo funcional: en un escenario controlado de baja ocupación, el motor completa pedidos de punta a punta, actualiza la grilla, mueve robots y calcula los 7 KPIs. Sin embargo, **hoy no se puede ejecutar desde la CLI ni desde la API** por un error de mutación sobre un objeto inmutable.

Resultados principales de la revisión:

| Hallazgo | Resultado |
|---|---|
| Motor básico | Funciona en prueba controlada con robots N/E/O |
| CLI `motor.run` | Bloqueada por `FrozenInstanceError` |
| Suite de pruebas | 101 pasaron, 41 fallaron |
| Fallos de la suite | 38 son pruebas desactualizadas; 3 son el bug del runner |
| ABC y afinidad | No implementados |
| BRP | Parcial, embebido en excavación; no es una política conmutable |
| Políticas disponibles | FIFO y prioridad por posición; plugin aleatorio |
| KPIs | Existen los 7, pero TPCP, TR y TBR requieren revisión |
| Ejecutor experimental | No existe como tal: no hay 6 políticas ni réplicas masivas |
| Limitación conocida | Livelock a ocupación alta (78–90 %) |

**Conclusión:** el simulador sirve como base para el seminario, pero antes de comparar políticas hay que reparar el runner, actualizar la suite y definir qué KPIs se van a considerar válidos.

## 2. Alcance y método

- Revisión estática del código y de los contratos en `docs/`.
- Ejecución de la suite completa con el intérprete del proyecto.
- Ejecución de la CLI con configuración real y con ola aleatoria.
- Prueba de humo controlada para separar el estado del motor del bug del runner.
- No se modificó la lógica del simulador para este informe.

| Dato | Valor |
|---|---|
| Repositorio | `C:\Users\manu\Documents\Simulacion_AutoStore` |
| Rama | `main` |
| Commit | `47e6c56` |
| Fecha de revisión | 2026-10-30 |
| Intérprete | `.venv\Scripts\python.exe` — Python 3.12.10 |
| pytest | 9.1.1 |
| Documento base | `docs/checklist_capacidades_simulador.md` |

El `python` del PATH es el de Inkscape y no tiene pytest. Todos los comandos de este informe usan el intérprete de `.venv`.

## 3. Resultado de las pruebas de ejecución

### 3.1 Suite completa

Comando ejecutado:

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/ bus_persistencia/tests/ -q --tb=line
```

Resultado:

```text
41 failed, 101 passed, 2 warnings in 5.13s
```

Los 41 fallos se explican así:

| Grupo | Cantidad | Causa | ¿Es regresión del motor? |
|---|---:|---|---|
| Bug del runner | 3 | `FrozenInstanceError` al intentar modificar `Caja` | Sí, es un bug real |
| Coordenadas, márgenes y estaciones antiguas | 28 | Usan cajas en `(0,0)`, `(1,0)`, `(1,1)`, etc.; ahora son celdas de tránsito, o esperan puertos en la posición vieja | No: prueba obsoleta |
| Orientación NORTE sin E/O | 5 | Asumen que un robot NORTE entrega solo, sin handoff | No: diseño 1×2 cambió |
| Rotación eliminada | 1 | Espera `RobotEstado.ROTANDO`; el diseño actual usa orientación fija | No: prueba obsoleta |
| Anillo/capacidad | 3 | Asumen que el anillo es opcional y que el interior empieza en `(0,0)` | No: la grilla E·T·A cambió |
| Fixture de configuración | 1 | Espera grilla 10×10; `data/config.json` ahora es 15×10 | No: dato de prueba desactualizado |

Total: **3 fallos reales del runner + 38 fallos por pruebas desactualizadas**.

Los 3 fallos reales son:

```text
tests/test_api_bridge.py::test_config_play_pause_avanza_ticks
tests/test_api_bridge.py::test_websocket_recibe_tick
tests/test_api_bridge.py::test_control_reset_vuelve_al_tick_inicial
```

Error:

```text
dataclasses.FrozenInstanceError: cannot assign to field 'id_sku'
```

### 3.2 CLI

Comando FIFO:

```powershell
& .\.venv\Scripts\python.exe -m motor.run --policy fifo --ticks 30 --quiet --no-clear --output $env:TEMP\autostore_cli_fifo
```

Comando prioridad con ola aleatoria:

```powershell
& .\.venv\Scripts\python.exe -m motor.run --policy prioridad_posicion --ticks 30 --random --seed 42 --quiet --no-clear --output $env:TEMP\autostore_cli_prioridad
```

Ambos terminan con código de salida 1 y el mismo error:

```text
File "motor\run.py", line 186, in ejecutar_sesion
    _asegurar_cajas_para_skus(sim)
File "motor\run.py", line 106, in _asegurar_cajas_para_skus
    caja.id_sku = sku
dataclasses.FrozenInstanceError: cannot assign to field 'id_sku'
```

La API también queda afectada porque `api/loop_worker.py:148` llama a la misma función.

### 3.3 Prueba de humo controlada

Para comprobar si el motor funciona sin el bug del runner, se ejecutó un escenario mínimo:

- Grilla 5×5×2, ocupación 0 %, 3 robots.
- Cajas colocadas en el interior: `(2,2)` y `(3,2)`.
- Pedidos con SKU-A y SKU-B.
- Sin pasar por `_asegurar_cajas_para_skus`.

Resultados:

| Caso | Orientaciones | Pedidos | Tick final | Completados | TSP | TPCP |
|---|---|---|---:|---:|---:|---:|
| 1 pedido | N, E, O | 1 | 9 | 1 | 100 % | 7.0 |
| 2 pedidos | N, E, O | 2 | 9 | 2 | 100 % | 7.0 |
| Solo NORTE | N, N, N | 1 | 81 | 0 | 0 % | 0.0 |

Interpretación:

- Con robots E/O disponibles, el motor completa pedidos y termina correctamente.
- Con solo robots NORTE, el pedido no se completa: el NORTE recupera la caja, pero depende de un E/O para el handoff. Esto confirma la limitación conocida, no un fallo del ciclo básico.
- La prueba es un control positivo mínimo; no valida alta ocupación ni el 100 % de una ola grande.

## 4. Checklist de capacidades

| Bloque | Estado | Qué existe | Brecha principal |
|---|---|---|---|
| A. Modelo y estado | Verificado con matices | Grilla 3D, columnas apiladas, celdas ocupadas/libres, extracción/descenso, pedidos/SKU, semilla reproducible, ocupación y flota configurables | El estado de cajas movidas depende de BRP parcial; no existe afinidad entre SKU |
| B. Asignación ABC | No existe | Solo hay conteo puntual de demanda en `run.py` para asegurar cajas | No hay clasificación A/B/C, umbrales ni asignación inicial por frecuencia |
| C. Reubicación de bloqueadores | Parcial | `EscortPlanner` mueve bloqueadores, evita columnas objetivo y detecta estancamiento | No coordina un robot transportador dedicado; no resuelve todos los casos sin celda libre; livelock a ocupación alta |
| D. Políticas e integración | Parcial | FIFO y prioridad por posición con interfaz común; plugin aleatorio; BRP embebido en excavación | ABC, BRP e integrada no son políticas separadas; no se pueden comparar de forma aislada; no hay reposicionamiento con horizonte rodante |
| E. Demanda, ocupación y flota | Parcial | Ola CSV y ola sintética con semilla; ocupación configurable; hasta 10 robots; turno diurno y nocturno | No hay perfiles fijos sesgados ni demanda intra-corrida; falta validar nocturno y benchmark 4/6/8 robots |
| F. Indicadores | Parcial | Los 7 KPIs y exportación por tick | TPCP se mide desde la asignación, no desde la llegada del pedido; TR no reinicia el numerador al cambiar de turno; TBR requiere definición y validación |
| G. Ejecutor experimental | No existe | `--compare` ejecuta FIFO y prioridad y genera reporte | No ejecuta 6 políticas, no hace réplicas, no corre 36×10 configuraciones, no reporta incompletas de forma sistemática |
| H. Viabilidad | Ver tabla siguiente | — | — |

## 5. Tabla de viabilidad y esfuerzo

Estimaciones gruesas, sujetas a que primero se estabilicen el runner y la suite.

| Función | ¿Existe? | Cambio necesario | Esfuerzo | Recomendación |
|---|---|---|---|---|
| Asignación ABC por frecuencia | No | Módulo de frecuencia, umbrales y asignación inicial | Medio | Incluir si el alcance lo permite |
| Afinidad entre SKU | No | Matriz de co-ocurrencia y agrupación | Alto | Trabajo futuro |
| BRP de celda libre cercana | Parcial | Completar selección de celda y casos sin opción | Medio | Incluir |
| Coordinación local para BRP | Parcial | Coordinar extractor y transportador; evitar ciclos | Alto | Incluir acotado o futuro |
| Comparación ABC, BRP y ABC+BRP | No | Políticas separadas y ejecutor común | Medio | Depende de las dos anteriores |
| Perfiles fijos de demanda | No | Generadores uniforme/sesgado con semilla | Bajo | Incluir |
| Demanda variable en la corrida | No | Cambio de demanda por tick y registro | Medio | Trabajo futuro |
| Ocupaciones 50/70/90 % | Parcial | Ya es configurable; falta verificar ocupación efectiva | Bajo | Incluir |
| Flotas 4/6/8 robots | Parcial | Benchmark de tiempo y estabilidad | Bajo | Incluir |
| Turno diurno / nocturno | Parcial | Diurno funciona; validar ingreso nocturno | Medio | Incluir diurno; nocturno validar |
| KPIs TPCP, TR y TBR | Parcial | Corregir definiciones y agregar pruebas | Medio | Incluir antes de comparar |
| Réplicas y análisis estadístico | No | Runner batch, CSV agregado e intervalos | Medio | Incluir |
| Reposicionamiento dinámico / horizonte rodante | No | Planificador y estado adicional | Alto | Trabajo futuro |

## 6. Hallazgos críticos

### 6.1 P0 — El runner no puede ejecutar

`motor/run.py:106-107` intenta modificar una `Caja`, pero `Caja` es `frozen=True` en `bus_persistencia/models/state.py:135`.

Corrección recomendada: no mutar la caja; crear una nueva con `dataclasses.replace(caja, id_sku=sku, cantidad=1)` y reemplazarla en la grilla y en el delta. Luego agregar una prueba que ejecute `ejecutar_sesion` con una ola que requiera reemplazo de SKU.

También debe revisarse `api/loop_worker.py:148`, que usa la misma función.

### 6.2 P1 — La suite no es una base de regresión confiable

La grilla cambió a un modelo E·T·A: el interior empieza en `(2,2)` y el perímetro es tránsito. Muchas pruebas siguen colocando cajas en `(0,0)` y esperan que sean válidas.

Además, el diseño 1×2 eliminó la rotación: los robots tienen orientación fija y los NORTE dependen de un E/O para entregar. Varias pruebas siguen asumiendo un robot NORTE que entrega solo o que rota.

Recomendaciones:

1. Reemplazar coordenadas hardcodeadas por `grilla.interior_bounds` o un helper de test.
2. Crear fixtures con robots N/E/O explícitos.
3. Eliminar o reescribir las pruebas de rotación, porque la rotación ya no es parte del diseño.
4. Actualizar el fixture de configuración a 15×10×5 o leer el archivo real.

Mientras no se haga esto, un resultado rojo de pytest no distingue entre una regresión real y una prueba vieja.

### 6.3 P1 — Livelock a ocupación alta

Está documentado en `docs/limitaciones_conocidas_robots_1x2.md`: a 78–90 % de ocupación la simulación puede quedar en 75–95 % de la ola y no completar el 100 %.

Causas:

- Menos estaciones de salida por el diseño E/O intercalado.
- Inanición de handoff de los robots NORTE.
- Coordinación greedy por robot.

La solución recomendada en ese documento es planificación cooperativa con reservas espacio-temporales, no más heurísticas locales. Para las demos se puede mitigar con más estaciones, menos robots, menor ocupación o sin robots NORTE en diurno.

### 6.4 P1 — KPIs que no miden lo que la checklist pide

- **TPCP:** se acumula desde `tick_inicio`, que se fija al asignar la tarea. La checklist pide desde la llegada del pedido.
- **TR:** se divide por los ticks del turno actual, pero `cajas_recuperadas` no se reinicia al cambiar de modo. El numerador y el denominador no quedan alineados.
- **TBR:** existe la fórmula, pero la definición de bloqueo/interferencia necesita una prueba explícita y un criterio de aceptación.

### 6.5 P2 — ABC, afinidad y BRP

- No hay clasificación ABC ni umbrales.
- No hay afinidad entre SKU.
- BRP existe solo como parte de la excavación (`EscortPlanner`), no como política seleccionable.
- No se puede ejecutar “ABC solo”, “BRP solo” y “ABC + BRP” con el mismo estado inicial.

### 6.6 P2 — Falta el ejecutor experimental

`python -m motor.run --compare` ejecuta dos políticas y genera un reporte comparativo. No ejecuta seis políticas, no hace réplicas por semilla, no agrupa resultados para intervalos de confianza y no tiene un criterio explícito para marcar una corrida como incompleta.

## 7. Plan recomendado

| Prioridad | Acción | Resultado esperado |
|---|---|---|
| 1 | Corregir `_asegurar_cajas_para_skus` y agregar prueba del runner | CLI y API vuelven a ejecutar |
| 2 | Actualizar la suite a la grilla E·T·A y a las orientaciones N/E/O | Suite verde como línea base |
| 3 | Corregir TPCP, TR y TBR; documentar fórmulas | Comparaciones defendibles |
| 4 | Crear runner de escenarios, semillas y réplicas | Evidencia experimental reproducible |
| 5 | Implementar ABC por frecuencia y perfiles de demanda | Primera comparación de asignación |
| 6 | Convertir BRP en política o módulo conmutable | Comparación ABC/BRP/ABC+BRP |
| 7 | Evaluar reposicionamiento dinámico y horizonte rodante | Trabajo futuro, no bloquea la demo |

## 8. Anexo — comandos reproducibles

```powershell
# Suite completa
& .\.venv\Scripts\python.exe -m pytest tests/ bus_persistencia/tests/ -q --tb=line

# CLI FIFO (hoy falla por FrozenInstanceError)
& .\.venv\Scripts\python.exe -m motor.run --policy fifo --ticks 30 --quiet --no-clear

# CLI prioridad con ola aleatoria (hoy falla por FrozenInstanceError)
& .\.venv\Scripts\python.exe -m motor.run --policy prioridad_posicion --ticks 30 --random --seed 42 --quiet --no-clear

# Demo P09 (depende de la corrección del runner)
& .\.venv\Scripts\python.exe -m motor.run --compare --ticks 100
```

Archivos de referencia:

- `docs/checklist_capacidades_simulador.md`
- `motor/run.py`
- `bus_persistencia/models/state.py`
- `api/loop_worker.py`
- `motor/grilla.py`
- `motor/despachador.py`
- `motor/escorts.py`
- `motor/kpis.py`
- `tests/`
- `docs/limitaciones_conocidas_robots_1x2.md`
