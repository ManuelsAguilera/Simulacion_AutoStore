# Piloto y evaluación separada

Preparar las dependencias de análisis con el intérprete del proyecto:

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements-analisis.txt
& .\.venv\Scripts\python.exe -m experimentos.piloto --salida output/piloto_nuevo
& .\.venv\Scripts\python.exe -m experimentos.analizar output/piloto_nuevo
```

La carpeta de salida debe ser nueva. El protocolo se guarda antes de iniciar
y cada corrida conserva entrada, eventos y último resultado. El índice se
actualiza después de cada corrida y el proceso informa avance cada cinco.
Los errores del motor registrados no se descartan; un problema de almacenamiento
que impida conservar evidencia detiene el lote.

El protocolo usa seis demostraciones de 12 pedidos; 60 corridas de desarrollo
(cinco instancias de 32 pedidos, seis tamaños, dos reglas); diez instancias nuevas
para comparar el fijo seleccionado, toda la lista y una regla variable; 24 controles
de profundidad/mezcla, 12 de costo de preparación y cuatro de alta ocupación. Si
el mejor fijo coincide con toda la lista, se comparte esa referencia en evaluación
y no se duplican corridas. Los plazos de 250 ticks y servicio mínimo del 90 % son
supuestos sintéticos fijados antes de comparar, no parámetros operacionales.

La regla variable empieza en cuatro solicitudes; duplica el siguiente tamaño si
el tiempo de recuperación por pedido de la ola previa no supera el objetivo,
y lo reduce a la mitad si lo supera, dentro de 1–32. El objetivo se calcula con
la mediana del fijo seleccionado en desarrollo y se guarda antes de las instancias
nuevas. No usa entregas futuras ni se reajusta para ganar en evaluación.

`protocolo.json`, `seleccion_congelada.json`, `indice.json` y `resumen.json`
documentan las decisiones. `informe_piloto.md` y las figuras se generan a partir de
los registros. Se informa la diferencia emparejada variable menos fijo y un intervalo
bootstrap condicional a las instancias sintéticas. Las incompletas se presentan
por separado con su causa y proporción; no tienen un tiempo total comparable.

Las carpetas completas `output/piloto_*/` con `resumen.json` son consultables por
la API del historial junto con las corridas interactivas. Sus instancias también
pueden recuperarse desde la interfaz. Un lote que no llegó a producir resumen
permanece en disco para diagnóstico y no se presenta como un piloto terminado.

El primer conjunto de trabajo está en `output/piloto_2026-11-10_v3/`. Los dos
intentos anteriores se conservaron después de una denegación transitoria al
reemplazar checkpoints en Windows. El registro ahora reintenta de forma acotada
ese reemplazo; otros errores de almacenamiento siguen propagándose.
