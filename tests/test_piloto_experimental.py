from experimentos.instancias import generar_escenario, inicializar
from experimentos.piloto import intervalo_pareado
import pytest


@pytest.mark.parametrize('perfil',['uniforme','concentrado'])
@pytest.mark.parametrize('profundidad',['superficial','mixta'])
def test_escenarios_son_factibles_y_reproducibles(perfil,profundidad):
    a=generar_escenario(100,perfil,profundidad)
    assert a==generar_escenario(100,perfil,profundidad)
    assert len(a.solicitudes)==32
    assert sum(s.urgencia=='urgente' for s in a.solicitudes)==8
    assert len(a.inventario)==(48 if profundidad=='mixta' else 32)
    a.validar()


def test_adaptacion_conserva_lista_y_usa_solo_ola_terminada():
    i=generar_escenario(200)
    s=inicializar(i,opciones_olas={'tamano':4,'adaptativa':True,
                                  'objetivo_ticks_por_pedido':1000,'regla':'urgentes_primero'})
    for _ in range(1500):
        if s.ha_terminado(): break
        s.avanzar_tick()
    r=s.plan_olas.resumen()
    assert r['completa']
    assert [len(o['pedidos']) for o in r['olas']]==[4,8,16,4]
    assert len(s.pedidos_completados)==len({p.id_pedido for p in s.pedidos_completados})==32
    for e in r['eventos']:
        if e['tipo']=='ajuste_tamano':
            previa=next(o for o in r['olas'] if o['fin']==e['tiempo'])
            assert e['observado']==(previa['fin']-previa['liberacion'])/len(previa['pedidos'])


def test_intervalo_de_diferencias_constantes():
    assert intervalo_pareado([3]*10)=={'n':10,'media':3,'ic95_bootstrap':[3,3]}
