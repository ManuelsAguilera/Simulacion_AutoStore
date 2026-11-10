# Tercera etapa: medidas físicas e historial recuperable

Cada ejecución experimental creada por la API o por `simular` tiene un ID
único. En `output/corridas/{id}/` se conservan tres archivos:

- `entrada.json`: instancia completa, semilla, parámetros de olas, política
  interna, horizonte, ventana sin entregas y versión del formato. Incluye el
  commit base cuando está disponible y una huella de las fuentes Python: el
  commit por sí solo no representa los cambios locales.
- `eventos.jsonl`: eventos de preparación, liberación, asignación, entrega,
  movimiento y espera con el mismo tiempo lógico de la corrida.
- `estado.json`: último resumen, tiempos por pedido y ola, indicadores físicos,
  estado final o en curso y último inventario/estado de robots.

El resumen se actualiza reemplazando su archivo temporal, y cada corrida usa una
carpeta distinta. Repetir la misma instancia conserva ambas corridas. Consultar
un resultado devuelve el último resumen y los eventos completos hasta su tiempo.
Esto permite reabrir datos después de reiniciar la aplicación; no implementa
continuación automática de una corrida tras una caída del proceso.

## Uso

```powershell
& .\.venv\Scripts\python.exe -m experimentos simular --archivo data/experimentos/7ab48968e77a8c692e40.json --tamano 4 --regla urgentes_primero --preparacion-fija 2 --preparacion-por-pedido 1 --ticks 500
& .\.venv\Scripts\python.exe -m experimentos historial
& .\.venv\Scripts\python.exe -m experimentos resultado --corrida-id ID_DEVUELTO_POR_SIMULAR
& .\.venv\Scripts\python.exe scripts/verificar_etapa3.py
```

`simular` devuelve código 0 para completa, 2 para una parada incompleta por
horizonte o ventana y 1 para un error de ejecución registrado. `--resultados`
permite cambiar la carpeta. La instancia sin stock se rechaza antes de crear
la corrida y de ejecutar el motor.

La API agrega `GET /experiments/runs` y `GET /experiments/runs/{id}`. Cargar una
instancia devuelve `corrida_id`; `/snapshot` incluye el ID y el estado de la
corrida activa. Se pueden configurar `limite_ticks` (1000 por defecto) y
`ventana_sin_entregas` (desactivada por defecto) en el cuerpo de carga de olas.
El inicio, la pausa y el reinicio usan los controles habituales. Una corrida
finalizada exige reiniciar para volver a ejecutarla con otro ID. El cambio de
política interna durante una corrida experimental se rechaza para conservar
la configuración registrada.

Los estados son `lista`, `ejecutando`, `pausada`, `completa`,
`horizonte_agotado`, `sin_entregas`, `error` e `interrumpida`. Reiniciar, cambiar
de instancia o cerrar normalmente el servicio conserva la corrida anterior
como interrumpida si no había terminado. Una pausa conserva un registro en curso.
Una parada incompleta no tiene tiempo de finalización. La ventana sin entregas
cuenta únicamente ticks de recuperación; no cuenta preparación. Ese estado
es una señal diagnóstica y no identifica automáticamente un atasco.

## Medición física y tiempos

Un evento `excavacion` se emite cuando se reubica una caja bloqueadora en el
modelo. Incluye ID de caja/pedido, robot y posiciones de origen y destino.
Se cuentan por separado movimientos, pares únicos (tick, robot) con movimiento
de excavación, ticks con al menos una excavación y pares de espera por escolta
o espacio. Dos robots que excavan en un mismo tick suman dos robot-ticks y un
solo tick transcurrido con excavación. El estado EXCAVANDO de un robot no basta
para contabilizar trabajo activo: también puede representar espera.

La duración de una reubicación sigue siendo la del motor por pasos, no una
medición calibrada de segundos de un robot real. Los contadores no añaden
demoras al reloj ni convierten la suma de trabajo paralelo en tiempo total.

Los tiempos por solicitud se reconstruyen de liberaciones, primeras asignaciones
y entregas. Se conservan espera previa a liberación, espera hasta asignación,
tiempo desde la primera asignación hasta entrega y tiempo total desde el inicio.
Si una tarea se cancela y vuelve a asignarse, el tiempo desde la primera
asignación incluye esa demora. Los urgentes pendientes siguen en el denominador
del cumplimiento; si no hay urgentes, la proporción se guarda como nula.

## Controles

- Caja accesible: una entrega en 11 ticks, cero movimientos bloqueadores.
- Misma recuperación con un bloqueador: una entrega en 12 ticks, una reubicación
  cuyo ID y posiciones se contrastaron con el inventario antes y después.
- Esperas forzadas por escolta y por espacio: no incrementan trabajo activo.
- Registros completos, horizonte agotado, ventana sin entregas, error e
  interrupción: se conservan y recuperan; las incompletas no reciben tiempo final.
- Dos corridas de la misma instancia no sobrescriben sus resultados. El historial
  se consulta mediante un nuevo controlador y los tiempos guardados se reconstruyen
  desde los eventos.

La interfaz gráfica para consultar ese historial corresponde a la cuarta etapa.
La auditoría anterior valida estos contadores en casos controlados, no todas las
dinámicas del motor a alta ocupación ni las definiciones de todos los indicadores
heredados.
