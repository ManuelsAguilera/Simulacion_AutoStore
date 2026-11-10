# Instancias v2 y preparación física de olas

La versión nueva genera solicitudes de recuperación unitarias con stock explícito,
pilas continuas y cinco clases de urgencia. La interfaz ofrece longitudes de 500
a 1000; el generador también admite casos pequeños para validar el motor.

Las clases `muy_baja`, `baja`, `media`, `alta` y `critica` proceden de puntuaciones
normales con cortes −1,5; −0,5; 0,5; 1,5. Media y desviación son configurables;
no hay cuotas de urgentes. La instancia guarda puntuaciones, semillas independientes,
frecuencias esperadas y observadas, geometría y cajas exactas. Los plazos son
sintéticos: cuando no se proporciona una base, se deriva de longitud y geometría
y se conserva la fórmula en los parámetros. No es un plazo operacional calibrado.

## Preparación secuencial

Cada grupo reserva unidades por ID de caja. Tras terminar la ola anterior, un
robot prepara físicamente el siguiente dentro del almacén: aproxima, toma, iza,
transporta, desciende y deposita bloqueadores en pilas legales. Los otros robots
conservan su estado y ocupación. La primera versión serializa la preparación con
un robot; la recuperación sigue usando la flota configurada.

La liberación depende de verificar el estado físico, sin costo fijo ni por pedido.
Los objetivos de una misma pila se recuperan de arriba hacia abajo. Si hay una
caja ajena entre dos objetivos, se apartan primero las cajas superiores necesarias.
Las dependencias físicas pueden limitar el orden de atención por urgencia.

La selección de cajas usa menos bloqueadores y desempate estable por coordenadas
e ID. Para reubicar se eligen columnas sin objetivos reservados, primero de menor
altura y después por distancia y coordenadas. Las cajas seleccionadas de una misma
columna respetan la dependencia de arriba hacia abajo. Un caso ya despejado solo
requiere verificar su estado; no se crean movimientos para forzar una demora.

Si no hay espacio o una ruta utilizable, la corrida conserva su motivo y estado
`inviable_preparacion`, sin tiempo final comparable. Los límites de ticks detienen
corridas incompletas y no representan una duración de preparación.

Los ticks son el reloj lógico de los movimientos; no equivalen a segundos reales
calibrados. El modelo anterior de preparación por costo se conserva para leer y
reproducir instancias v1 y sus resultados. Las dos versiones están identificadas
en el historial y no deben mezclarse como si usaran el mismo modelo físico.

## Reproducción por consola

Desde la raíz del simulador:

```powershell
& .\.venv\Scripts\python.exe -m experimentos generar --cantidad 1000 --semilla 42
& .\.venv\Scripts\python.exe -m experimentos simular --archivo output/instancias/ID.json --tamano 50 --regla urgentes_primero --ticks 100000
& .\.venv\Scripts\python.exe scripts/verificar_preparacion_fisica.py
```

El primer comando devuelve el nombre real del archivo que sustituye `ID.json`.
Para repetir el modelo anterior, generar sin `--cantidad` y cargar su instancia v1.
Los modelos físicos rechazan penalizaciones fijas o por pedido.

El piloto completo propuesto conserva 72 intentos: tres semillas por cada longitud
500/750/1000, cuatro tamaños (25/50/100/lista completa) y dos órdenes. Guarda las
entradas antes de evaluar estrategias y conserva los fallos:

```powershell
& .\.venv\Scripts\python.exe -m experimentos.piloto_fisico --salida output/piloto_fisico_NOMBRE_NUEVO
```

La carpeta de salida debe ser nueva para evitar sobrescribir evidencia. Se puede
reducir el piloto con `--cantidades`, `--semillas` y `--tamanos`. El caso de lista
completa se incluye siempre; algunas configuraciones pueden ser físicamente
inviables. El informe muestra completas/intentos junto a las medias de completas.

## Interfaz y persistencia

La configuración permite generar, inspeccionar por páginas y recuperar instancias.
El monitor muestra fase física, robot, caja, origen, destino y pedidos por página.
Los resultados conservan cumplimiento y tiempo de entrega por categoría.
El modo rápido del servicio avanza el reloj sin vincularlo a segundos de pantalla.

En v2, los eventos se consumen incrementalmente y se escriben por lotes. El estado
se guarda cada 50 ticks y al pausar o terminar; una caída abrupta puede perder el
trabajo posterior al último punto guardado. Recuperar un resultado no reanuda
automáticamente el proceso. Las corridas anteriores siguen consultables.

Consultas de detalle:

- `GET /snapshot?compacto=true`: resumen experimental sin toda la historia.
- `GET /experiments/runs/{id}?compacto=true`: entrada y resultado, sin eventos.
- `GET /experiments/runs/{id}/pedidos?pagina=1&tamano=50`: filtros `urgencia`,
  `estado` y `busqueda`.
- `GET /experiments/runs/{id}/eventos?desde=0&limite=100`: devuelve `items`,
  `siguiente` y `hay_mas`; usar el cursor devuelto para continuar.

La traza completa permanece en `output/corridas/{id}/eventos.jsonl`; las medidas
guardadas se pueden reconstruir desde ella. El modo v2 no duplica una película
completa del almacén en memoria para la exportación antigua.

También desactiva el registro duplicado por sesión del bus: el registro de corrida
conserva la traza principal y evita crear un hilo de escritura por tick. Esto no
altera el estado del bus ni la autorización de su escritor.

## Reparto de implementación

Se desplegaron tres agentes Luna: instancias/urgencias, movimiento/preparación y
API/interfaz. El agente principal integró el almacenamiento incremental, la consola,
el piloto y las verificaciones. Los cambios se realizan en el checkout existente;
no se publican commits ni se eliminan resultados previos.

## Verificación realizada el 10-nov

Pasaron 102 pruebas específicas, incluida la conservación de cajas por tick, pilas
sin huecos, rutas con dos robots, recuperación desde profundidad, preparación sin
espacio, espera de tráfico sin progreso, consulta de páginas y compatibilidad con
v1. La compilación de producción pasó. Desde el navegador se generaron 1000 pedidos,
se verificaron páginas y filtros, se completaron 1000/1000 en 37458 ticks y se
recuperó la corrida `493d26b81352407da86927e33ce8419a` del historial. Hubo 904 ticks
de preparación y 40 reubicaciones; todos los plazos sintéticos se cumplieron.

El piloto `output/piloto_fisico_2026-11-10_v2/` conserva 72/72 corridas completas.
Se auditaron 3.808.594 eventos: conservación de cajas, retiros desde arriba,
depósitos con soporte, pasos de robot, inventario final y medidas reconstruidas.
Repetir la auditoría con:

```powershell
& .\.venv\Scripts\python.exe scripts/auditar_piloto_fisico.py output/piloto_fisico_2026-11-10_v2
```

Los plazos automáticos fueron amplios y todas las clases cumplieron en el piloto:
no distinguen estrategias por incumplimiento. La sensibilidad de plazos y el estudio
de flotas grandes quedan para una evaluación posterior. El caso controlado sin
espacio sí verifica el almacenamiento de una preparación inviable.
