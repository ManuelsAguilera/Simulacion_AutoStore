"""Informe descriptivo reproducible a partir del índice y las trazas del piloto."""
import argparse
from collections import Counter
from pathlib import Path
import json
import statistics

from .resultados import leer


def analizar(carpeta):
    filas=json.loads((carpeta/'indice.json').read_text(encoding='utf-8'))
    resumen=json.loads((carpeta/'resumen.json').read_text(encoding='utf-8'))
    protocolo=json.loads((carpeta/'protocolo.json').read_text(encoding='utf-8'))
    evaluacion=[f for f in filas if f['etapa']=='evaluacion']
    medias={a:statistics.mean(f['tiempo'] for f in evaluacion if f['adaptativa']==a and f['estado']=='completa')
            for a in (False,True)}
    sin_costo=[f for f in filas if f['etapa']=='sensibilidad' and f['fija']==f['por_pedido']==0]
    iguales=all(len({f['tiempo'] for f in sin_costo if f['semilla']==seed and f['estado']=='completa'})==1
                for seed in protocolo['sensibilidad'])
    lectura=(f"En las instancias nuevas, el fijo seleccionado tarda en promedio {medias[False]:.1f} ticks "
             f"y la regla variable {medias[True]:.1f}. "
             'Las cifras de servicio se presentan junto con el tiempo para evaluar el compromiso. ')
    if iguales:
        lectura+='Al eliminar el costo de preparación, los tamaños comparados tardan lo mismo en el control de sensibilidad. No se observa una mejora física atribuible al tamaño en esa línea base de un robot.'
    lineas=['# Piloto de olas con urgencia: resultados del 10-nov-2026','',
      'Resultados exploratorios de solicitudes sintéticas en el modelo AutoStore. '
      'El protocolo se guardó antes de ejecutar; los parámetros del tamaño variable '
      'se congelaron usando únicamente las cinco instancias de desarrollo. '
      'Las diez instancias de evaluación tienen otras semillas. Los plazos y el '
      'umbral de 90 % son supuestos de este piloto, no una condición de servicio validada en Forus.','',
      f"Se conservaron **{len(filas)} corridas**. Estados: {dict(Counter(f['estado'] for f in filas))}.",
      'Las corridas incompletas conservan pedidos pendientes y eventos y no reciben un tiempo de finalización.','',
      lectura,'',
      '## Desarrollo: seis tamaños y dos reglas','',
      'Cinco listas de 32 solicitudes, cuatro SKU y ocho urgentes con plazo 250 ticks; '
      'inventario superficial y un robot ESTE. Dentro de cada instancia se conserva '
      'la lista, el inventario, las urgencias y FIFO interno. La preparación secuencial '
      'cuesta dos ticks por ola más uno por solicitud.','',
      '| Formación | Tamaño | Completas | Tiempo medio de completas | Cumplimiento urgente medio |',
      '|---|---:|---:|---:|---:|']
    for regla in ('original','urgentes_primero'):
        for b in protocolo['B']:
            g=[f for f in filas if f['etapa']=='desarrollo' and f['regla']==regla and f['B']==b]
            c=[f for f in g if f['estado']=='completa']
            media=f"{statistics.mean(f['tiempo'] for f in c):.1f}" if c else '—'
            servicio=statistics.mean(f['servicio'] for f in g)
            lineas.append(f"| {regla.replace('_',' ')} | {b} | {len(c)}/{len(g)} | {media} ticks | {servicio:.1%} |")
    s=resumen['seleccion']
    lineas+=['',f"Se seleccionó **B={s['B_fijo']}**, con urgentes primero. "
      f"Cumple el umbral en todas las instancias de desarrollo: {'sí' if s['cumple_servicio_desarrollo'] else 'no'}. "
      f"La regla variable comienza con cuatro solicitudes y usa {s['objetivo_adaptativo']:.3f} ticks "
      'de recuperación por pedido como objetivo: duplica el tamaño si la ola previa cumple '
      'ese valor y lo reduce a la mitad si lo supera, entre 1 y 32. Solo utiliza información '
      'de una ola ya terminada; el orden de solicitudes es el mismo que para los fijos.','',
      '![Curvas emparejadas por instancia](tiempo_por_tamano.png)','',
      '## Evaluación con instancias nuevas','',
      '| Estrategia | Completas | Tiempo medio de completas | Cumplimiento urgente medio |',
      '|---|---:|---:|---:|']
    for adapt,nombre in ((False,'Fijo seleccionado / lista completa'),(True,'Variable')):
        g=[f for f in filas if f['etapa']=='evaluacion' and f['adaptativa']==adapt]
        c=[f for f in g if f['estado']=='completa']
        media=f"{statistics.mean(f['tiempo'] for f in c):.1f}" if c else '—'
        lineas.append(f"| {nombre} | {len(c)}/{len(g)} | {media} ticks | {statistics.mean(f['servicio'] for f in g):.1%} |")
    if s['B_fijo']==32:
        lineas+=['','El fijo seleccionado coincide con liberar toda la lista; se ejecutó una sola vez '
                   'por instancia y sirve como ambas referencias.']
    intervalo=resumen['diferencia_adaptativa_menos_fija']
    lineas+=['',f"Diferencia emparejada (variable menos fijo), en {intervalo['n']} pares completos: "
      f"**{intervalo['media']:.1f} ticks**, intervalo bootstrap del 95 % "
      f"{intervalo['ic95_bootstrap'][0]:.1f} a {intervalo['ic95_bootstrap'][1]:.1f} ticks. "
      'Una diferencia positiva implica que la variable tarda más. Este intervalo describe '
      'la variabilidad de las instancias sintéticas examinadas; no mide incertidumbre '
      'sobre la operación real ni cubre la selección de todas las familias de reglas posibles.','',
      '![Evaluación separada](evaluacion_nueva.png)','',
      '## Profundidad y mezcla de productos','',
      'Dos semillas por bloque; en profundidad mixta se añade un bloqueador sobre '
      'la mitad de las cajas solicitadas. La mezcla concentrada asigna 26 de 32 '
      'solicitudes al mismo SKU. Dentro de cada bloque se comparan entradas idénticas.','',
      '| Mezcla | Profundidad | Tamaño | Completas | Tiempo medio completo | Movimientos bloqueadores medios |',
      '|---|---|---:|---:|---:|---:|']
    for perfil in ('uniforme','concentrado'):
        for profundidad in ('superficial','mixta'):
            for b in (1,4,32):
                g=[f for f in filas if f['etapa']=='robustez' and f['perfil']==perfil and f['profundidad']==profundidad and f['B']==b]
                c=[f for f in g if f['estado']=='completa']
                media=f"{statistics.mean(f['tiempo'] for f in c):.1f}" if c else '—'
                lineas.append(f"| {perfil} | {profundidad} | {b} | {len(c)}/{len(g)} | {media} | {statistics.mean(f['excavaciones'] for f in g):.1f} |")
    lineas+=['','## Sensibilidad a los supuestos','',
      '| Preparación fija / por pedido | Tamaño | Completas | Tiempo medio completo |',
      '|---|---:|---:|---:|']
    for fija,por in ((0,0),(10,0)):
        for b in (1,4,32):
            g=[f for f in filas if f['etapa']=='sensibilidad' and f['B']==b and f['fija']==fija and f['por_pedido']==por]
            c=[f for f in g if f['estado']=='completa']
            media=f"{statistics.mean(f['tiempo'] for f in c):.1f}" if c else '—'
            lineas.append(f"| {fija} / {por} | {b} | {len(c)}/{len(g)} | {media} |")
    lineas+=['','Los plazos siguientes estaban declarados como sensibilidad en el protocolo. '
      'Se recalcula cumplimiento sobre las mismas entregas de evaluación, pues la '
      'regla utiliza clase de urgencia y no el valor del plazo. La selección se hizo '
      'siempre con el plazo original de 250 ticks.','',
      '| Plazo | Referencia fija: urgentes a tiempo | Variable: urgentes a tiempo |',
      '|---:|---:|---:|']
    for plazo in protocolo['plazos_sensibilidad']:
        porcentajes=[]
        for adapt in (False,True):
            pedidos=[p for f in filas if f['etapa']=='evaluacion' and f['adaptativa']==adapt
                     for p in leer(carpeta/'corridas',f['corrida_id'])['resultado']['pedidos'] if p['urgencia']=='urgente']
            porcentajes.append(sum(p['entrega'] is not None and p['entrega']<=plazo for p in pedidos)/len(pedidos))
        lineas.append(f"| {plazo} | {porcentajes[0]:.1%} | {porcentajes[1]:.1%} |")
    lineas+=['','## Prueba de esfuerzo separada','',
      'Se probaron dos instancias con ocupación objetivo del 78 % (149 de 192 '
      'celdas: 77,6 % efectivo), con tres robots N/E/O y '
      'tamaños 4 y 32. El horizonte común fue 2000 ticks. Esta condición difiere '
      'de la línea base; sus fallos no prueban por sí solos una capacidad máxima de ola.','',
      '| Semilla | Tamaño | Estado | Entregados | Tiempo final | Corrida |',
      '|---:|---:|---|---:|---:|---|']
    for f in filas:
        if f['etapa']=='esfuerzo':
            lineas.append(f"| {f['semilla']} | {f['B']} | {f['estado']} | {f['entregados']}/32 | {f['tiempo'] if f['tiempo'] is not None else '—'} | {f['corrida_id']} |")
    for f in filas:
        if f['etapa']=='esfuerzo' and f['estado']!='completa':
            doc=leer(carpeta/'corridas',f['corrida_id'])
            entregas=[e['tiempo'] for e in doc['eventos'] if e['tipo']=='pedido_completado']
            lineas+=['',f"En la semilla {f['semilla']}, B={f['B']}, la última entrega ocurrió en el "
              f"tick {max(entregas) if entregas else 'ninguno'} y no hubo nuevas entregas hasta el horizonte. "
              'El último estado físico y los eventos se conservan para revisar coordinación y rutas. '
              'En estos controles la lista completa sí terminó; el fallo apareció con una ola menor. '
              'Esto no reproduce una relación general entre olas grandes y atasco.']
    lineas+=['','## Alcance de la conclusión','',
      'La comparación principal utiliza un robot, solicitudes unitarias y datos sintéticos. '
      'Con esa flota el tamaño puede afectar principalmente el costo externo de preparación. '
      'El control de costo cero permite distinguir ese efecto de una mejora física. '
      'Las urgencias cambian el orden externo de las solicitudes; su efecto se estudia '
      'por separado del tamaño. Una ventaja de toda la lista o una desventaja de la '
      'regla variable no demuestra que otras reglas o instalaciones se comporten igual.','',
      'Los controles de profundidad verifican movimientos representados por el motor, '
      'no tiempos mecánicos calibrados. Las corridas de alta ocupación conservan sus '
      'trazas para investigar causas; alcanzar el horizonte no identifica un atasco '
      'operacional. No hay validación con datos de Forus.','',
      'El protocolo, la selección congelada, el índice y los registros completos '
      'permiten reconstruir las cifras. Dos intentos de lanzamiento anteriores '
      'fallaron por retención de archivos al actualizar checkpoints en Windows; '
      'se conservaron aparte y se añadió un reintento acotado. El conjunto analizado '
      'es el de la carpeta v3, después de esa corrección.']
    path=carpeta/'informe_piloto.md'
    path.write_text('\n'.join(lineas)+'\n',encoding='utf-8')
    return path


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('carpeta',type=Path)
    print(analizar(parser.parse_args().carpeta))
