# Segunda etapa: olas sucesivas y preparación

El historial recuperable y los indicadores físicos se implementaron después en
[la tercera etapa](experimentos_etapa3.md). Los límites de este documento describen
el cierre de la segunda etapa.

El planificador externo divide la lista conocida en grupos de tamaño fijo.
Se puede conservar el orden original o colocar urgentes primero, manteniendo
el orden original entre pedidos de la misma clase. El motor conserva robots,
grilla e inventario durante toda la sesión; FIFO sigue siendo la regla interna.

La preparación es secuencial: empieza en el tiempo cero para la primera ola
y al completar todas las entregas de la anterior para las siguientes. Su costo
sintético es `preparacion_fija + preparacion_por_pedido × tamaño efectivo`.
Se libera el grupo al terminar ese costo y el motor puede asignarlo en el
siguiente tick. Con costo cero, se libera inmediatamente. Durante preparación
avanza el reloj, con los robots detenidos. Este supuesto no modela preparación
anticipada ni mide tiempo real de cómputo al ordenar una lista.

## Repetir una corrida

```powershell
& .\.venv\Scripts\python.exe -m experimentos simular --archivo data/experimentos/7ab48968e77a8c692e40.json --tamano 4 --regla urgentes_primero --preparacion-fija 2 --preparacion-por-pedido 1
& .\.venv\Scripts\python.exe scripts/verificar_etapa2.py
```

La salida incluye ola de cada solicitud, tiempos de liberación, asignación y
entrega, inicio y fin de las olas y urgentes a tiempo. El tiempo lógico empieza
en cero después de inicializar el motor: el snapshot inicial del bus tiene tick 1.
Por eso `experimento.tiempo` excluye el paso de inicialización. Los plazos se
interpretan contra ese tiempo lógico; entrega exactamente en el plazo cumple.
Si se agota `--ticks`, el proceso devuelve código 2 y tiempo de finalización
nulo. La detección automática de estancamiento y el historial de corridas aún
requieren la siguiente etapa.

## API

La ruta de carga de la etapa 1 ahora admite opciones en el cuerpo:

```json
{"tamano": 4, "regla": "urgentes_primero", "preparacion_fija": 2, "preparacion_por_pedido": 1}
```

Ruta: `POST /experiments/instances/{id}/load`. Sin cuerpo se usa una sola ola
de hasta 12 pedidos, orden original y preparación cero. `/snapshot` y las
notificaciones de estado agregan `experimento` con el estado de preparación,
los tiempos y los pedidos. El reinicio conserva las opciones y reconstruye la
misma instancia. Las pantallas actuales todavía no presentan estos nuevos campos.

## Evidencia de funcionamiento

Pasaron 36 pruebas acotadas a olas, instancia, inventario y API. Se verificó que
`B=N` con costo cero conserva las posiciones, entregas e indicadores del motor
original tick a tick. Se probaron grupos de tamaño 1, 4, 12 y un último grupo
incompleto de tamaño 2; ninguna solicitud se asigna antes de su liberación.

Demostración con semilla 42, 12 pedidos, tres urgentes con plazo 100,
preparación fija 2 y costo por solicitud 1:

| Regla | Tamaño | Olas | Tiempo total | Preparación | Urgentes a tiempo | Entregados |
|---|---:|---:|---:|---:|---:|---:|
| Original | 1 | 12 | 184 | 36 | 1/3 | 12/12 |
| Original | 4 | 3 | 166 | 18 | 1/3 | 12/12 |
| Original | 12 | 1 | 162 | 14 | 1/3 | 12/12 |
| Urgentes primero | 1 | 12 | 184 | 36 | 3/3 | 12/12 |
| Urgentes primero | 4 | 3 | 166 | 18 | 3/3 | 12/12 |
| Urgentes primero | 12 | 1 | 162 | 14 | 3/3 | 12/12 |

Todos los tiempos están en ticks. Es un control de funcionamiento con una sola
instancia superficial y un robot; no estima un tamaño óptimo ni un nivel real
de servicio. En este caso el tiempo del motor es 148 ticks; la diferencia entre
tamaños procede del costo sintético de preparación. La traza local del control
está en `output/verificacion_etapa2.json`, que no sustituye un historial de
resultados persistente de la aplicación. Los indicadores físicos de excavación
y los resúmenes estadísticos se validarán en las próximas etapas.
