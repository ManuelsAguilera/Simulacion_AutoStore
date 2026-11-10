# Comparar varias configuraciones de ola

En **Configuración**, genera o recupera una instancia y busca **Comparar
configuraciones de ola**. Indica tamaños separados por coma, por ejemplo
`25,50,100`, y selecciona orden original, prioridad de urgencia o ambos. Puedes
incluir una ola con toda la lista. La vista previa muestra las combinaciones
antes de iniciar; admite hasta 24 configuraciones distintas.

El nombre es opcional. El lote utiliza el horizonte y la ventana sin progreso
configurados arriba. Cada combinación comienza con la misma lista, urgencias,
inventario, semilla y posición inicial de robots. En instancias v2 utiliza
preparación física; en v1 utiliza preparación sintética con costo cero.

Al pulsar **Iniciar comparación**, las configuraciones se ejecutan una tras otra
en segundo plano, con un estado independiente de la simulación individual.
Puedes cambiar de página o cerrar el navegador mientras trabaja el servicio.
Hay un lote activo por servicio; una segunda solicitud devuelve un conflicto
hasta que termine o se cancele el primero.

## Consultar y registrar resultados

El selector **Lotes guardados** está disponible tanto en Configuración como en
Historial. Al recargar, recupera el lote activo o el más reciente. La tabla
conserva por combinación:

- Tamaño y orden, estado y enlace al ID de corrida.
- Tiempo final para corridas completas; preparación y movimientos de cajas.
- Cumplimiento de urgentes, con pendientes incluidos en el denominador.

**Ver corrida** abre el resultado detallado del historial, con sus solicitudes y
medidas por categoría. **Exportar resumen JSON** descarga el lote y su protocolo,
incluida la instancia exacta usada. No es necesario introducir archivos de datos.

Un lote completo indica que terminaron todos sus intentos; cada corrida puede
haber terminado completa, por horizonte, por preparación inviable o por error.
Las incompletas conservan motivo y progreso, y no tienen tiempo de finalización
comparable. No se ocultan las alternativas que fallan.

**Cancelar lote** conserva las corridas terminadas, guarda la actual como
interrumpida y marca las pendientes como omitidas. Al cerrar o reiniciar el
servicio también se conservan los resultados; un lote interrumpido no se reanuda
automáticamente. Para repetirlo, crea otro lote: sus archivos tendrán IDs nuevos.

## Archivos y API

En `output/lotes/{id}/` se guardan `protocolo.json` (entrada de comparación) y
`lote.json` (estado y resultados). Cada intento conserva su entrada, eventos y
estado en `output/corridas/{corrida_id}/`, enlazado mediante `lote_id` y
`configuracion_id`. Los puntos de guardado de las corridas por lotes son cada
250 ticks y al terminar o interrumpir; el resumen del lote se actualiza mientras
avanza. Tras una caída abrupta, se recupera el último punto guardado.

- `POST /experiments/batches`: instancia, nombre opcional, configuraciones y límites.
- `GET /experiments/batches`: catálogo.
- `GET /experiments/batches/{id}`: progreso y resultados.
- `POST /experiments/batches/{id}/cancel`: cancelación conservando registros.
- `GET /experiments/batches/{id}/export`: descarga del resumen y protocolo.

## Verificación

Se añadieron 13 pruebas de lotes/API. El verificador completo pasó **115 pruebas**:

```powershell
& .\.venv\Scripts\python.exe scripts/verificar_preparacion_fisica.py
```

Cubren instancias idénticas entre configuraciones, historial recuperable,
cancelación sin bloquear el motor, recuperación tras reinicio, límites, errores
de entrada, exportación y aislamiento de la simulación individual. La compilación
de producción pasó en `output/interfaz_lotes_verificada`.

Se verificó además desde el navegador el lote
`514d1c3525784c38901708be15bc5e8a`: misma instancia de 500 solicitudes, tamaños 25
y 50 con ambos órdenes, cuatro corridas completas. La tabla se recuperó al
recargar y abrió el detalle de una corrida desde su fila. Las entradas y medidas
guardadas se contrastaron con sus trazas. Este control verifica la función;
no establece una configuración óptima general.
