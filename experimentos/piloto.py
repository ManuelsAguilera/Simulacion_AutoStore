"""Piloto emparejado y evaluación separada. Ejecutar: python -m experimentos.piloto."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import random
import statistics
import time

from bus_persistencia.bus.state_bus import StateBus
from .instancias import generar_demo, generar_escenario, guardar, inicializar
from .resultados import RegistroCorrida, leer, version_codigo


class SinBitacoraDuplicada:
    """El registro experimental ya conserva todos los eventos de M2."""
    def buffer_event(self, *args): pass
    def flush_tick_async(self, *args): pass


def intervalo_pareado(diferencias):
    if not diferencias:
        return None
    rng = random.Random(20261110)
    medias = sorted(statistics.mean(rng.choices(diferencias,k=len(diferencias))) for _ in range(2000))
    return {"n":len(diferencias), "media":statistics.mean(diferencias), "ic95_bootstrap":[medias[49],medias[1949]]}


def ejecutar(salida):
    salida.mkdir(parents=True,exist_ok=False)
    protocolo = {"tipo":"piloto exploratorio sintético", "codigo":version_codigo(),
      "desarrollo":list(range(100,105)), "evaluacion":list(range(200,210)),
      "robustez": [300,301], "sensibilidad":[400,401], "esfuerzo":[500,501],
      "N":32,"B":[1,2,4,8,16,32],"urgentes":8,"plazo":250,"umbral_servicio":0.9,
      "preparacion_fija":2,"preparacion_por_pedido":1,"horizonte":2000,
      "ventana_sin_entregas":None,
      "adaptativa":{"inicio":4,"minimo":1,"maximo":32,
        "regla":"duplicar si recuperación/pedido <= objetivo; reducir a mitad si lo supera",
        "objetivo":"mediana recuperación/pedido del fijo seleccionado en desarrollo"},
      "plazos_sensibilidad":[100,250,400],
      "nota":"Umbral y plazos son supuestos sintéticos previos; no niveles operacionales validados"}
    (salida/'protocolo.json').write_text(json.dumps(protocolo,indent=2),encoding='utf-8')
    filas=[]
    def correr(instancia,etapa,tamano,regla='urgentes_primero',fija=2,por=1,adaptativa=False,objetivo=15,**factores):
        guardar(instancia,salida/'instancias')
        sim=inicializar(instancia,bus=StateBus(SinBitacoraDuplicada()),opciones_olas={
          'tamano':tamano,'regla':regla,'preparacion_fija':fija,'preparacion_por_pedido':por,
          'adaptativa':adaptativa,'objetivo_ticks_por_pedido':objetivo})
        registro=RegistroCorrida(instancia,sim,salida/'corridas',2000)
        inicio=time.perf_counter()
        try:
            while not registro.terminal: registro.avanzar()
        except Exception:
            if not registro.terminal: raise
        r=leer(salida/'corridas',registro.id)['resultado']
        fila={'etapa':etapa,'semilla':instancia.semilla,'instancia_id':instancia.id,
              'corrida_id':registro.id,'B':tamano,'regla':regla,'adaptativa':adaptativa,
              'fija':fija,'por_pedido':por,'segundos':time.perf_counter()-inicio,
              'estado':r['estado'],'entregados':r['entregados'],'total':r['total'],
              'tiempo':r['tiempo_finalizacion'],'preparacion':r['preparacion_total'],
              'servicio':r['cumplimiento_urgentes'],'excavaciones':r['movimientos_bloqueadores'],
              'robot_ticks_excavacion':r['robot_ticks_excavacion'],**factores}
        filas.append(fila)
        (salida/'indice.json').write_text(json.dumps(filas,indent=2),encoding='utf-8')
        if len(filas)%5==0: print(f"{len(filas)} corridas conservadas; {etapa} / {r['estado']}",flush=True)
        return fila
    # Demostración del plan con seis corridas de la misma lista.
    for regla in ('original','urgentes_primero'):
        for b in (1,4,12): correr(generar_demo(), 'demostracion',b,regla)
    for seed in protocolo['desarrollo']:
        instancia=generar_escenario(seed)
        for regla in ('original','urgentes_primero'):
            for b in protocolo['B']: correr(instancia,'desarrollo',b,regla)
    candidatos=[]
    for b in protocolo['B']:
        grupo=[f for f in filas if f['etapa']=='desarrollo' and f['B']==b and f['regla']=='urgentes_primero']
        completa=all(f['estado']=='completa' for f in grupo)
        servicio=completa and all(f['servicio']>=0.9 for f in grupo)
        if completa: candidatos.append((not servicio,statistics.mean(f['tiempo'] for f in grupo),b))
    if not candidatos: raise RuntimeError('No hay un fijo completo en desarrollo; revisar motor antes de evaluar')
    no_servicio,_,mejor=min(candidatos)
    base=[f for f in filas if f['etapa']=='desarrollo' and f['B']==mejor and f['regla']=='urgentes_primero']
    objetivo=statistics.median((f['tiempo']-f['preparacion'])/32 for f in base)
    seleccion={'B_fijo':mejor,'cumple_servicio_desarrollo':not no_servicio,'objetivo_adaptativo':objetivo,
               'semillas_usadas':protocolo['desarrollo']}
    (salida/'seleccion_congelada.json').write_text(json.dumps(seleccion,indent=2),encoding='utf-8')
    for seed in protocolo['evaluacion']:
        instancia=generar_escenario(seed)
        for b in sorted({mejor,32}): correr(instancia,'evaluacion',b)
        correr(instancia,'evaluacion',4,adaptativa=True,objetivo=objetivo)
    for seed in protocolo['robustez']:
        for perfil in ('uniforme','concentrado'):
            for profundidad in ('superficial','mixta'):
                instancia=generar_escenario(seed,perfil,profundidad)
                for b in (1,4,32): correr(instancia,'robustez',b,perfil=perfil,profundidad=profundidad)
    for seed in protocolo['sensibilidad']:
        for fija,por in ((0,0),(10,0)):
            for b in (1,4,32): correr(generar_escenario(seed),'sensibilidad',b,fija=fija,por=por)
    for seed in protocolo['esfuerzo']:
        for b in (4,32): correr(generar_escenario(seed,ocupacion=0.78,robots=3),'esfuerzo',b)
    dif=[]
    for seed in protocolo['evaluacion']:
        grupo=[f for f in filas if f['etapa']=='evaluacion' and f['semilla']==seed]
        fijo=next(f for f in grupo if not f['adaptativa'] and f['B']==mejor)
        adapt=next(f for f in grupo if f['adaptativa'])
        if fijo['estado']==adapt['estado']=='completa': dif.append(adapt['tiempo']-fijo['tiempo'])
    resumen={'total_corridas':len(filas),'estados':{s:sum(f['estado']==s for f in filas) for s in {f['estado'] for f in filas}},
             'seleccion':seleccion,'diferencia_adaptativa_menos_fija':intervalo_pareado(dif),
             'pares_evaluacion_completos':len(dif),
             'fallos_evaluacion':sum(f['estado']!='completa' for f in filas if f['etapa']=='evaluacion')}
    (salida/'resumen.json').write_text(json.dumps(resumen,indent=2),encoding='utf-8')
    graficar(salida,filas)
    print(json.dumps(resumen),flush=True)


def graficar(salida,filas):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(10,4),layout='constrained',sharey=True)
    for regla,ax in zip(('original','urgentes_primero'),axes):
        for seed in range(100,105):
            grupo=sorted([f for f in filas if f['etapa']=='desarrollo' and f['regla']==regla and f['semilla']==seed],key=lambda f:f['B'])
            ax.plot([f['B'] for f in grupo],[f['tiempo'] if f['estado']=='completa' else float('nan') for f in grupo],marker='o',label=str(seed))
        ax.set(xlabel='Solicitudes por ola',ylabel='Tiempo hasta última entrega (ticks)',title=regla.replace('_',' '))
        ax.grid(alpha=.2)
    axes[1].legend(title='Semilla',fontsize=8)
    fig.savefig(salida/'tiempo_por_tamano.png',dpi=180)
    fig.savefig(salida/'tiempo_por_tamano.pdf')
    plt.close(fig)
    fig,ax=plt.subplots(figsize=(7,4),layout='constrained')
    for adapt,etiqueta in ((False,'Fijo / lista completa'),(True,'Adaptativo')):
        grupo=[f for f in filas if f['etapa']=='evaluacion' and f['adaptativa']==adapt]
        ax.scatter([f['semilla'] for f in grupo],[f['tiempo'] for f in grupo],label=etiqueta)
    ax.set(xlabel='Semilla de evaluación nueva',ylabel='Tiempo total (ticks)')
    ax.legend(loc='upper center',bbox_to_anchor=(0.5,1.17),ncol=2);ax.grid(alpha=.2)
    fig.savefig(salida/'evaluacion_nueva.png',dpi=180);plt.close(fig)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--salida',type=Path,default=Path('output/piloto_2026-11-10'))
    ejecutar(parser.parse_args().salida)
