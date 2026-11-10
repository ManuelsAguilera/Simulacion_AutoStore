# Primera etapa: instancia reproducible

La implementación continúa con olas y tiempos de preparación en
[la segunda etapa](experimentos_etapa2.md). Los límites indicados aquí describen
la entrega de la primera etapa.

La instancia de demostración contiene 12 cajas en posiciones explícitas, tres
SKU repetidos y 12 recuperaciones unitarias identificadas. Tres solicitudes son
urgentes y tienen un plazo sintético de 100 ticks; ese plazo es un ejemplo
configurable, no una condición de servicio validada. Se conserva el orden original.
La grilla es 6×6×2, con una capa de cajas y un robot ESTE para una primera
recuperación controlada. No representa todavía la flota ni la ocupación de Forus.

Desde la raíz, usando el entorno del proyecto:

```powershell
& .\.venv\Scripts\python.exe -m experimentos generar
& .\.venv\Scripts\python.exe -m experimentos comprobar --archivo output/instancias/7ab48968e77a8c692e40.json --ticks 500
& .\.venv\Scripts\python.exe scripts/verificar_etapa1.py
```

Con semilla 42 se completaron las 12 entregas en 148 ticks transcurridos desde
la inicialización. Es una prueba de funcionamiento de una sola ola, no un
experimento de tamaños ni una medida de urgencia. La generación no usa datos
reales ni modifica SKU después de cargar los pedidos. El inventario se valida
antes de iniciar el motor; el stock se cuenta en cajas unitarias por SKU.

Los archivos incluyen versión del formato, semilla, configuración, inventario,
solicitudes y urgencias. Su identificador depende del contenido. Generar con los
mismos parámetros produce el mismo archivo; recargarlo conserva los datos y
rechaza stock insuficiente, IDs repetidos, posiciones inválidas y urgentes sin
plazo. Las etiquetas se mantienen en la capa experimental; el modelo compartido
`Pedido` no cambió.

La API agrega estas operaciones:

| Método y ruta | Uso |
|---|---|
| `POST /experiments/instances/generate` | Generar y guardar. Cuerpo opcionalmente parametrizado: `{"semilla":42,"urgentes":3,"plazo_tick":100}`. |
| `GET /experiments/instances/{id}` | Recuperar la lista completa, sus urgencias y el inventario desde disco. |
| `POST /experiments/instances/{id}/load` | Cargar esa instancia en el motor, con FIFO interno y su inventario explícito. |

Después se usan `/snapshot` y los controles habituales de inicio, pausa y reinicio.
El reinicio conserva el inventario de la instancia. El almacenamiento por defecto
es `output/instancias/`; el montaje de Docker conserva esta carpeta en el equipo.
Tras actualizar la imagen de API, reconstruirla con `docker compose up -d --build`.

La verificación acotada cubre generación/recarga, entrega única de los 12 IDs,
inventario final, rechazo de entradas inválidas, endpoints, avance, pausa, reinicio
y recuperación con un nuevo controlador. Se verificaron 15 pruebas. La primera
ejecución restringida quedó detenida al crear el socket local de asyncio; con las
conexiones locales permitidas las pruebas de API pasaron. El script detiene la
verificación a los 45 segundos si ese bloqueo reaparece.

La interfaz todavía usa sus pantallas anteriores. La selección visual de estas
instancias, las olas sucesivas, el costo de preparación, la atención efectiva por
urgencia y el historial de resultados de corridas corresponden a las siguientes
etapas. Este almacenamiento conserva entradas, no resultados experimentales.
