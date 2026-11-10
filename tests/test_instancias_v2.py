from dataclasses import replace
import json

import pytest

from experimentos.instancias import (
    URGENCIAS,
    Instancia,
    cargar,
    generar_demo,
    generar_instancia,
    guardar,
    inicializar,
    prioridad_urgencia,
)


@pytest.mark.parametrize("cantidad", [500, 750, 1000])
def test_v2_genera_guarda_y_recarga_listas_largas(cantidad, tmp_path):
    original = generar_instancia(semilla=81, cantidad=cantidad)
    archivo = guardar(original, tmp_path)
    recargada = cargar(archivo)

    assert original.version == recargada.version == 2
    assert original == recargada
    assert len(recargada.solicitudes) == cantidad
    assert len({s.pedido.id_pedido for s in recargada.solicitudes}) == cantidad
    assert json.loads(json.dumps(recargada.to_dict())) == json.loads(
        archivo.read_text(encoding="utf-8"))
    assert len(recargada.inventario) >= cantidad
    assert len(recargada.inventario) < recargada.config.grilla.capacidad_total
    assert recargada.parametros["columna_reserva"]
    assert set(recargada.parametros["frecuencias_observadas"]) == set(URGENCIAS)
    assert set(recargada.parametros["frecuencias_esperadas"]) == set(URGENCIAS)

    alturas = {}
    for caja in recargada.inventario:
        alturas.setdefault((caja.x, caja.y), set()).add(caja.z)
    assert all(zs == set(range(len(zs))) for zs in alturas.values())
    stock = {}
    for caja in recargada.inventario:
        stock[caja.id_sku] = stock.get(caja.id_sku, 0) + 1
    demanda = {}
    for solicitud in recargada.solicitudes:
        demanda[solicitud.pedido.id_sku] = demanda.get(solicitud.pedido.id_sku, 0) + 1
    assert all(stock[sku] >= n for sku, n in demanda.items())


def test_v2_tiene_cinco_clases_puntuacion_y_frecuencias_sin_cuotas():
    instancia = generar_instancia(semilla=2, cantidad=20, productos=4)
    assert all(s.urgencia in URGENCIAS and s.puntuacion is not None
               for s in instancia.solicitudes)
    assert sum(instancia.parametros["frecuencias_observadas"].values()) == 20
    assert sum(instancia.parametros["frecuencias_esperadas"].values()) == pytest.approx(20)
    # No se fuerzan conteos predeterminados ni una clase específica por muestra.
    assert instancia.parametros["frecuencias_observadas"] == {
        clase: sum(s.urgencia == clase for s in instancia.solicitudes) for clase in URGENCIAS
    }
    assert prioridad_urgencia("muy_baja") == 0
    assert prioridad_urgencia("critica") == 4
    assert prioridad_urgencia(instancia.solicitudes[0]) == URGENCIAS.index(instancia.solicitudes[0].urgencia)
    assert prioridad_urgencia("normal") == prioridad_urgencia("media")
    assert prioridad_urgencia("urgente") == prioridad_urgencia("critica")


def test_secuencias_aleatorias_independientes_y_reproducibles():
    base = generar_instancia(semilla=15, cantidad=120)
    cambia_urgencia = generar_instancia(semilla=15, cantidad=120, media_urgencia=1.4)
    otra_semilla = generar_instancia(semilla=16, cantidad=120)
    assert base == generar_instancia(semilla=15, cantidad=120)
    assert base.inventario == cambia_urgencia.inventario
    assert [s.pedido.id_sku for s in base.solicitudes] == [s.pedido.id_sku for s in cambia_urgencia.solicitudes]
    assert [s.puntuacion for s in base.solicitudes] != [s.puntuacion for s in cambia_urgencia.solicitudes]
    assert (base.inventario, base.solicitudes) != (otra_semilla.inventario, otra_semilla.solicitudes)


def test_perfil_concentrado_admite_un_solo_producto():
    instancia = generar_instancia(cantidad=30, productos=1, perfil="concentrado")
    assert {s.pedido.id_sku for s in instancia.solicitudes} == {"SKU-001"}
    assert sum(c.id_sku == "SKU-001" for c in instancia.inventario) >= 30


def test_version_uno_conserva_serializacion_y_constructores_anteriores():
    original = generar_demo(42)
    doc = original.to_dict()
    assert doc["version"] == 1
    assert "parametros" not in doc
    assert all("puntuacion" not in s for s in doc["solicitudes"])
    assert Instancia.from_dict(doc) == original
    assert Instancia(original.semilla, original.config, original.inventario,
                     original.solicitudes).to_dict() == doc
    assert all(s.puntuacion is None for s in original.solicitudes)


def test_v1_con_opciones_olas_vacias_sigue_creando_plan():
    sim = inicializar(generar_demo(), opciones_olas={})
    assert sim.plan_olas is not None


@pytest.mark.parametrize("cambios", [
    {"cantidad": 0}, {"cantidad": 1001}, {"productos": 0}, {"x": 1},
    {"y": 1}, {"z": 0}, {"ocupacion": 1.0}, {"ocupacion": 0},
    {"robots": 0}, {"desviacion_urgencia": 0}, {"plazo_base": 0},
    {"perfil": "normal"}, {"x": 2, "y": 2, "z": 1, "cantidad": 10},
])
def test_rechaza_parametros_o_dimensiones_no_factibles(cambios):
    parametros = {"cantidad": 20}
    parametros.update(cambios)
    with pytest.raises(ValueError):
        generar_instancia(**parametros)


def test_validador_v2_rechaza_huecos_y_stock_insuficiente():
    instancia = generar_instancia(cantidad=20)
    caja_superior = next(c for c in instancia.inventario if c.z == 1)
    base = next(c for c in instancia.inventario
                if (c.x, c.y) == (caja_superior.x, caja_superior.y) and c.z == 0)
    con_hueco = replace(instancia, inventario=tuple(c for c in instancia.inventario if c != base))
    with pytest.raises(ValueError, match="huecos"):
        con_hueco.validar()
    solicitud = instancia.solicitudes[0]
    sin_stock = replace(instancia, solicitudes=(replace(
        solicitud, pedido=replace(solicitud.pedido, id_sku="SKU-AUSENTE")),
        *instancia.solicitudes[1:]))
    with pytest.raises(ValueError, match="Sin inventario"):
        sin_stock.validar()


def test_plazos_sinteticos_se_endurecen_con_clase():
    instancia = generar_instancia(cantidad=200, plazo_base=100)
    plazos_por_clase = {}
    for solicitud in instancia.solicitudes:
        plazos_por_clase.setdefault(solicitud.urgencia, set()).add(solicitud.plazo_tick)
    presentes = [clase for clase in URGENCIAS if clase in plazos_por_clase]
    assert all(len(plazos_por_clase[clase]) == 1 for clase in presentes)
    assert all(next(iter(plazos_por_clase[a])) >= next(iter(plazos_por_clase[b]))
               for a, b in zip(presentes, presentes[1:]))
