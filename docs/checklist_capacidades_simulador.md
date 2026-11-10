# Checklist de capacidades del simulador AutoStore

## Propósito

Usar esta lista para determinar qué puede hacer el simulador actual y qué podría ampliarse razonablemente para el seminario. No se presupone que una capacidad exista por estar descrita en un informe o plan: registrar evidencia en el código, pruebas existentes o una ejecución reproducible.

Software de referencia: repositorio de simulación `samirabsaa/Simulacion_AutoStore`. Antes de revisar, registrar la rama y versión exacta inspeccionadas.

## Cómo registrar cada capacidad

Para cada punto, completar:

- **Estado actual:** Verificado / Parcial / No existe / No comprobado.
- **Evidencia:** ruta de archivo y clase, función, prueba o ejecución que lo demuestra.
- **Extensión posible:** cambio requerido, dependencias y esfuerzo aproximado (bajo / medio / alto).
- **Decisión de alcance:** incluir / dejar como trabajo futuro / pendiente.

No marcar una función como verificada solo porque exista una clase o un nombre de política: comprobar qué comportamiento ejecuta.

## A. Modelo y estado del sistema

| Revisar si el software… | Estado | Evidencia / notas |
|---|---|---|
| Representa bins apilados por columnas y permite consultar qué bins bloquean al solicitado. | [ ] | |
| Representa celdas ocupadas y libres, y actualiza correctamente su estado cuando un bin se mueve. | [ ] | |
| Modela el movimiento horizontal de robots sobre la grilla y la extracción/descenso de bins. | [ ] | |
| Permite conocer pedidos, SKU, frecuencia de demanda y relación entre productos de un mismo pedido. | [ ] | |
| Mantiene un estado inicial reproducible entre políticas y réplicas. | [ ] | |
| Permite configurar la ocupación inicial y el tamaño de la flota sin editar manualmente el motor. | [ ] | |

## B. Asignación inicial ABC

| Revisar si el software… | Estado | Evidencia / notas |
|---|---|---|
| Calcula la frecuencia de demanda por SKU o por bin desde los pedidos de entrada. | [ ] | |
| Permite ordenar SKU por frecuencia y clasificarlos en grupos A, B y C. | [ ] | |
| Permite configurar los umbrales de las clases A/B/C y deja registrada la regla usada. | [ ] | |
| Asigna bins a ubicaciones iniciales según la clase, sin crear colisiones ni exceder capacidad. | [ ] | |
| Distingue una asignación solo por frecuencia de una asignación que también usa afinidad entre SKU. | [ ] | |
| Puede agrupar SKU que suelen aparecer juntos en pedidos, si se decide incluir afinidad. | [ ] | |
| Mantiene la asignación estática durante la corrida, como establece el diseño actual. | [ ] | |

**Aclaración:** ABC clasifica los productos por frecuencia de demanda. La afinidad es otro criterio: identifica productos que suelen solicitarse juntos. No asumir que implementar ABC ya incorpora afinidad.

## C. Reubicación de bloqueadores (BRP)

| Revisar si el software… | Estado | Evidencia / notas |
|---|---|---|
| Detecta todos los bins sobre el objetivo y puede moverlos antes de recuperar el bin solicitado. | [ ] | |
| Encuentra celdas libres válidas y puede elegir la más cercana al trayecto del robot. | [ ] | |
| Define qué hacer si no hay una celda libre válida o si varias opciones tienen el mismo costo. | [ ] | |
| Evita colocar un bloqueador en una columna que vuelva a bloquear el pedido actual. | [ ] | |
| Actualiza correctamente el origen y destino del bin, el estado de la grilla y la tarea del robot. | [ ] | |
| Coordina el robot que extrae con el que transporta/reubica un bloqueador. | [ ] | |
| Completa excavaciones con varios bloqueadores y detecta ciclos, espera o bloqueo permanente. | [ ] | |

## D. Políticas y arquitectura de integración

| Revisar si el software… | Estado | Evidencia / notas |
|---|---|---|
| Permite seleccionar FIFO y prioridad por proximidad mediante una interfaz común de políticas. | [ ] | |
| Permite añadir una política aleatoria y políticas ABC, BRP e integrada sin duplicar el motor. | [ ] | |
| Puede ejecutar ABC solo, BRP solo y ABC + BRP con el mismo estado inicial para separar sus efectos. | [ ] | |
| Requiere cambios locales al despachador para ejecutar BRP y cuáles son esos cambios concretos. | [ ] | |
| Permite mantener intacta la lógica general de rutas y despacho, limitando los cambios a la coordinación local requerida por BRP. | [ ] | |
| Permite reposicionar bins durante la operación y, de ser así, qué estado, eventos y costos necesitaría la extensión. | [ ] | |
| Permite reposicionamiento con horizonte rodante; si no, qué algoritmo, datos futuros y costo de cómputo exigiría incorporarlo. | [ ] | |

## E. Demanda, ocupación y flota

| Revisar si el software… | Estado | Evidencia / notas |
|---|---|---|
| Genera una demanda sintética constante y reproducible con semilla configurable. | [ ] | |
| Permite probar distintos perfiles fijos de demanda (por ejemplo, uniforme y sesgado), manteniendo cada perfil constante durante una corrida. | [ ] | |
| Puede representar demanda que cambia durante una corrida; distinguir esto de comparar perfiles fijos. | [ ] | |
| Configura ocupaciones de 50 %, 70 % y 90 % y confirma la ocupación efectiva resultante. | [ ] | |
| Configura 4 y 6 robots; mide el tiempo de una corrida para decidir si 8 es viable. | [ ] | |
| Permite ejecutar solo el turno diurno y describe qué faltaría para validar el turno nocturno. | [ ] | |

**No confundir:** probar varias mezclas fijas de demanda no requiere necesariamente reposicionamiento dinámico. Hacer que la demanda cambie dentro de una corrida es una capacidad adicional.

## F. Indicadores y validez de comparación

| Revisar si el software… | Estado | Evidencia / notas |
|---|---|---|
| Calcula el tiempo de ciclo desde la llegada del pedido hasta su entrega, no desde la asignación de tarea. | [ ] | |
| Reinicia o delimita correctamente el contador de throughput entre corridas/modos. | [ ] | |
| Calcula y valida el indicador de bloqueos/interferencia (TBR) con una definición explícita. | [ ] | |
| Conserva los otros indicadores disponibles, aunque solo tres sean de análisis principal. | [ ] | |
| Registra resultados por política, escenario, semilla y réplica para poder rastrear cada dato. | [ ] | |
| Puede exportar tiempos de ciclo, throughput y bloqueos con granularidad suficiente para análisis estadístico. | [ ] | |

## G. Ejecutor experimental y costo de cómputo

| Revisar si el software… | Estado | Evidencia / notas |
|---|---|---|
| Ejecuta las seis políticas sobre escenarios equivalentes y las mismas semillas. | [ ] | |
| Ejecuta varias réplicas por configuración y registra fallos sin descartarlos silenciosamente. | [ ] | |
| Tiene un límite de ticks y un criterio para reportar una corrida incompleta o bloqueada. | [ ] | |
| Puede completar 36 configuraciones × 10 réplicas en el tiempo disponible. | [ ] | |
| Permite estimar cuánto aumentaría el costo al añadir demanda variable, ocho robots o reposicionamiento dinámico. | [ ] | |
| Produce una salida reproducible que pueda analizarse con intervalos de confianza y pruebas estadísticas. | [ ] | |

## H. Resultado de la revisión de viabilidad

Al terminar, completar esta tabla:

| Función | ¿Existe y está verificada? | Cambio necesario si falta | Esfuerzo | Recomendación |
|---|---|---|---|---|
| Asignación ABC por frecuencia | | | | |
| Afinidad entre SKU | | | | |
| BRP de celda libre cercana | | | | |
| Coordinación local para BRP | | | | |
| Comparación de ABC, BRP y ABC + BRP | | | | |
| Perfiles fijos de demanda | | | | |
| Demanda variable dentro de una corrida | | | | |
| Ocupaciones 50/70/90 % | | | | |
| Flotas de 4/6/8 robots | | | | |
| Turno diurno / turno nocturno | | | | |
| Indicadores TPCP, TR y TBR válidos | | | | |
| Ejecución de réplicas y análisis estadístico | | | | |
| Reposicionamiento dinámico / horizonte rodante | | | | |

## Decisión de alcance posterior

- **Incluir:** la función existe o requiere un cambio acotado, con evidencia y costo asumible.
- **Dejar para trabajo futuro:** requiere una capacidad nueva, datos adicionales o un costo que no cabe en el seminario.
- **Pendiente:** no hay evidencia suficiente; no describirla como capacidad disponible hasta verificarla.

La revisión solo evalúa viabilidad. No modifica el simulador ni confirma que un resultado experimental ya se haya obtenido.
