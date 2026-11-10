from collections import Counter

import pytest

from bus_persistencia.models.state import Caja, Config, GrillaDimensions, Pedido
from experimentos.instancias import Instancia, Solicitud, inicializar


def make_sim(cajas, pedidos, *, dims=(4, 4, 3), robots=1, tamano=None):
    config = Config(GrillaDimensions(*dims), robots, len(cajas) / (dims[0]*dims[1]*dims[2]),
                    robots_este=robots)
    version = 2 if any(u in {"muy_baja","baja","media","alta","critica"}
                       for _,_,u in pedidos) else 1
    solicitudes = tuple(Solicitud(Pedido(pid, sku, 1, "destino"), i, urgencia, None)
                        if version == 1 else
                        Solicitud(Pedido(pid, sku, 1, "destino"), i, urgencia, None, 0.0)
                        for i, (pid, sku, urgencia) in enumerate(pedidos))
    instancia = Instancia(3, config, tuple(cajas), solicitudes, version=version,
                          parametros={"caso": "test"} if version == 2 else None)
    instancia.validar()
    return inicializar(instancia, opciones_olas={"tamano": tamano or len(pedidos),
                                                 "modelo_preparacion": "fisica"})


def run_until_stop(sim, limit=5000):
    for _ in range(limit):
        if sim.plan_olas.completa or sim.plan_olas.fallo:
            break
        sim.avanzar_tick()
    return sim.plan_olas.resumen()


def stack_target(blockers=1, x=2, y=2):
    return [Caja("T", "SKU", 1, x, y, 0)] + [
        Caja(f"B{i}", f"BLOCK{i}", 1, x, y, i+1) for i in range(blockers)
    ]


def test_objetivo_accesible_se_verifica_sin_teletransportar_cajas():
    sim = make_sim([Caja("T", "SKU", 1, 2, 2, 0)], [("P", "SKU", "normal")])
    inicio = {c.id_caja: (c.x,c.y,c.z) for c in sim._grilla.cajas}
    resultado = run_until_stop(sim)
    assert resultado["completa"]
    assert resultado["modelo_preparacion"] == "fisica"
    assert resultado["preparacion_fisica"]["fase"] == "completa"
    assert {c.id_caja for c in sim._grilla.cajas} == set(inicio) - {"T"}
    assert resultado["preparacion_total"] >= 1  # verificación del estado real


def test_reubica_bloqueador_con_etapas_y_conserva_identidad():
    sim = make_sim(stack_target(1), [("P", "SKU", "normal")])
    inventario = Counter(c.id_caja for c in sim._grilla.cajas)
    for _ in range(200):
        if sim.plan_olas.puede_procesar:
            break
        sim.avanzar_tick()
    eventos = sim.plan_olas.eventos
    fases = {e.get("fase") for e in eventos if e["tipo"] == "preparacion_movimiento"}
    assert "transporte" in fases
    assert any(e["tipo"] == "preparacion_toma" for e in eventos)
    assert any(e["tipo"] == "preparacion_deposito" for e in eventos)
    assert any(e["tipo"] == "excavacion" and e.get("fase") == "preparacion" for e in eventos)
    assert Counter(c.id_caja for c in sim._grilla.cajas) == inventario
    assert sim._grilla.get(2, 2, 0).id_caja == "T"
    assert sim._grilla.get(2, 2, 1) is None


def test_varios_bloqueadores_se_mueven_por_acciones_y_preparan_objetivo():
    sim = make_sim(stack_target(2), [("P", "SKU", "normal")])
    resultado = run_until_stop(sim)
    moves = [e for e in resultado["eventos"] if e["tipo"] == "excavacion" and e.get("fase") == "preparacion"]
    assert resultado["completa"] and len(moves) == 2
    assert {e["id_caja"] for e in moves} == {"B0", "B1"}


def test_dos_objetivos_en_una_columna_se_recuperan_en_dependencia_top_down():
    cajas = [Caja("LOW", "SKU-A", 1, 2, 2, 0), Caja("HIGH", "SKU-B", 1, 2, 2, 1)]
    sim = make_sim(cajas, [("PLOW", "SKU-A", "normal"), ("PHIGH", "SKU-B", "normal")])
    resultado = run_until_stop(sim)
    assert resultado["completa"]
    finales = [e["id_pedido"] for e in resultado["eventos"] if e["tipo"] == "solicitud_entregada"]
    assert finales.index("PHIGH") < finales.index("PLOW")


@pytest.mark.parametrize(("z","expected"), [(0,3),(2,1)])
def test_recuperacion_iza_por_nivel_segun_profundidad(z, expected):
    cajas = [Caja("T", "SKU", 1, 2, 2, z)]
    if z == 0:
        cajas.extend([Caja("B1", "B1", 1, 2, 2, 1), Caja("B2", "B2", 1, 2, 2, 2)])
    else:
        cajas.extend([Caja("F0", "F0", 1, 2, 2, 0), Caja("F1", "F1", 1, 2, 2, 1)])
    sim = make_sim(cajas, [("P", "SKU", "normal")])
    r = run_until_stop(sim)
    izados = [e for e in r["eventos"] if e["tipo"] == "recuperacion_izado"]
    assert r["completa"] and len(izados) == expected


def test_dos_chunks_conservan_estado_y_reservas_de_caja():
    cajas = [Caja("T1", "S1", 1, 2, 2, 0), Caja("B1", "B1", 1, 2, 2, 1),
             Caja("T2", "S2", 1, 4, 4, 0), Caja("B2", "B2", 1, 4, 4, 1)]
    sim = make_sim(cajas, [("P1", "S1", "media"), ("P2", "S2", "critica")], tamano=1)
    r = run_until_stop(sim)
    assert r["completa"] and len(r["olas"]) == 2
    assert r["olas"][1]["preparacion_inicio"] == r["olas"][0]["fin"]
    assert len({p["id"] for p in r["pedidos"]}) == 2
    assert r["servicio_por_categoria"]["critica"]["entregados"] == 1
    assert sum(e["tipo"] == "excavacion" and e.get("fase") == "preparacion" for e in r["eventos"]) == 2


def test_sin_espacio_falla_con_motivo_y_no_libera_ola():
    cajas = [Caja("T", "SKU", 1, 2, 2, 0), Caja("B", "BLOCK", 1, 2, 2, 1)]
    sim = make_sim(cajas, [("P", "SKU", "normal")], dims=(1,1,2))
    resultado = run_until_stop(sim)
    assert not resultado["completa"]
    assert resultado["fallo"] and "espacio" in resultado["fallo"].lower()
    assert resultado["olas"][0]["liberacion"] is None
    assert resultado["tiempo_finalizacion"] is None


def test_transporte_largo_toma_mas_ticks_que_corto():
    base = stack_target(1)
    corto = make_sim(base, [("P", "SKU", "normal")], dims=(4,4,3))
    # Llenar todas las columnas salvo una esquina lejana; sus pilas son contiguas.
    usados = {(c.x,c.y,c.z) for c in base}
    llenado = list(base)
    n = 0
    for y in range(2,6):
        for x in range(2,6):
            if (x,y) in {(2,2),(5,5)}:
                continue
            for z in range(3):
                if (x,y,z) not in usados:
                    llenado.append(Caja(f"F{n}", f"F{n}", 1, x,y,z)); n+=1
    largo = make_sim(llenado, [("P", "SKU", "normal")], dims=(4,4,3))
    rc, rl = run_until_stop(corto), run_until_stop(largo)
    assert rc["preparacion_total"] < rl["preparacion_total"]


def test_dos_robots_se_conservan_y_no_comparten_celda():
    sim = make_sim(stack_target(1), [("P", "SKU", "normal")], robots=2)
    ubicaciones = [set()]
    for _ in range(500):
        if sim.plan_olas.completa or sim.plan_olas.fallo:
            break
        sim.avanzar_tick()
        celdas = []
        from bus_persistencia.models.state import celdas_robot
        for robot in sim.robots.values():
            celdas.extend(celdas_robot(robot))
        assert len(celdas) == len(set(celdas))
    assert len(sim.robots) == 2
    assert sim.plan_olas.completa
