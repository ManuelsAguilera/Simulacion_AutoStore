# Cuarta etapa: interfaz de experimentos

Abrir `http://localhost:8100/config` con los servicios de Docker iniciados.
La vista de configuración permite generar una lista de 12 pedidos sintéticos
con semilla, urgentes y plazo, o recuperar una lista guardada. La tabla permite
revisar ID, producto, urgencia y plazo antes de iniciar.

La segunda columna configura tamaño y orden de formación, costos sintéticos
de preparación, horizonte y ventana opcional sin entregas. La opción de tamaño
variable permite fijar mínimo, máximo y objetivo de recuperación por pedido.
Los valores de esa regla son parámetros experimentales; el piloto congela los
seleccionados para su evaluación separada.

`Cargar e iniciar corrida` abre el monitor. Se muestran el reloj lógico en ticks,
preparación acumulada, servicio urgente, estado y tiempos de cada ola y de cada
solicitud. Los controles permiten pausar, continuar y comenzar otra corrida con
las mismas opciones. El reinicio conserva el registro anterior. La grilla muestra
el estado físico actual; los indicadores heredados con unidades no verificadas
se ocultan en la vista experimental.

En `Datos Estadísticos` se puede consultar el historial, actualizarlo y seleccionar
hasta dos corridas para revisar sus resultados y desplegar los tiempos por pedido.
Las corridas incompletas muestran su causa y carecen de tiempo de finalización.
Si las instancias son distintas aparece una indicación para evitar atribuir sus
diferencias solo al tamaño de ola. El historial vuelve a consultarse al recargar
la página y proviene de los registros del servicio, no del almacenamiento del
navegador. La configuración y las exportaciones anteriores se conservan plegadas.

Se comprobó por navegador: generación de lista, ejecución completa con olas de
tamaño 4, 12/12 pedidos en 166 ticks y 3/3 urgentes a tiempo; selección de dos
resultados de la misma instancia y recuperación del historial después de recargar.
Se inspeccionó visualmente la comparación. Es un control funcional de una sola
instancia, no una evaluación de eficacia general.

La compilación `npm run build` produce la aplicación. Se desactivó únicamente
la incorporación de fuentes remotas en el proceso de compilación para que
construir no dependa de Google Fonts; el navegador utiliza los estilos y las
fuentes de respaldo definidos por la aplicación. El código continúa en la rama
local de trabajo hasta que se autorice su publicación.
