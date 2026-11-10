"""Regression tests for physical preparation, conservation, and run metrics."""
from collections import defaultdict

from bus_persistencia.models.state import Caja, Config, GrillaDimensions, Pedido, celdas_robot, punta
from experimentos.instancias import Instancia, Solicitud, inicializar
from experimentos.resultados import RegistroCorrida, leer
from tests.test_preparacion_fisica import make_sim


def _assert_piles_have_no_holes(sim):
    levels = defaultdict(set)
    for caja in sim._grilla.cajas:
        levels[(caja.x, caja.y)].add(caja.z)
    assert all(zs == set(range(len(zs))) for zs in levels.values())


def _assert_conserved(sim, expected_ids, delivered):
    boxes = list(sim._grilla.cajas)
    grid_ids = [c.id_caja for c in boxes]
    cargo_ids = [r.carga_id for r in sim.robots.values() if r.carga_id is not None]
    assert len(grid_ids) == len(set(grid_ids))
    assert len(cargo_ids) == len(set(cargo_ids))
    assert not set(grid_ids) & set(cargo_ids)
    assert not set(grid_ids) & delivered
    assert not set(cargo_ids) & delivered
    assert set(grid_ids) | set(cargo_ids) | delivered == expected_ids
    _assert_piles_have_no_holes(sim)

    occupied = []
    for robot in sim.robots.values():
        footprint = celdas_robot(robot)
        assert all(sim._grilla.en_superficie(x, y) for x, y in footprint)
        occupied.extend(footprint)
    assert len(occupied) == len(set(occupied))


def _advance_checked(sim, expected, delivered, known):
    before_grid = {c.id_caja: c for c in sim._grilla.cajas}
    before_cargo = {r.id: r.carga_id for r in sim.robots.values()}
    sim.avanzar_tick()
    eventos = sim.last_tick_eventos
    after_grid = {c.id_caja: c for c in sim._grilla.cajas}
    after_robots = sim.robots

    for event in eventos:
        if event["tipo"] == "preparacion_toma":
            caja = before_grid[event["caja"]]
            arriba = [c.z for c in before_grid.values() if (c.x, c.y) == (caja.x, caja.y)]
            assert caja.z == max(arriba)
            rid = event["robot"]
            assert after_robots[rid].carga_id == caja.id_caja
        elif event["tipo"] == "caja_recuperada":
            caja = before_grid[event["id_caja"]]
            assert (caja.x, caja.y, caja.z) == (event["x"], event["y"], event["z"])
            arriba = [c.z for c in before_grid.values() if (c.x, c.y) == (caja.x, caja.y)]
            assert caja.z == max(arriba)
            assert after_robots[event["robot_id"]].carga_id == caja.id_caja
        elif event["tipo"] == "preparacion_izado":
            assert after_robots[event["robot"]].carga_id == event["carga"]
        elif event["tipo"] == "preparacion_deposito":
            destino = tuple(event["a"])
            caja = after_grid[event["caja"]]
            assert (caja.x, caja.y, caja.z) == destino
            assert punta(after_robots[event["robot"]]) == destino[:2]
        elif event["tipo"] == "pedido_completado":
            rid = event["robot_id"]
            box_id = before_cargo[rid]
            assert box_id in known
            assert known[box_id].id_sku == event["id_sku"]
            delivered.add(box_id)

    _assert_conserved(sim, expected, delivered)


def _run_checked(sim, limit=10000):
    initial = {c.id_caja: c for c in sim._grilla.cajas}
    expected = set(initial)
    delivered = set()
    for _ in range(limit):
        if sim.plan_olas.completa or sim.plan_olas.fallo:
            break
        _advance_checked(sim, expected, delivered, initial)
    assert sim.plan_olas.completa or sim.plan_olas.fallo
    _assert_conserved(sim, expected, delivered)
    return sim.plan_olas.resumen(), initial, delivered


def _make_v2_case(cajas, pedidos, *, dims=(4, 4, 3), tamano=None):
    config = Config(GrillaDimensions(*dims), 1,
                    len(cajas) / (dims[0] * dims[1] * dims[2]), robots_este=1)
    solicitudes = tuple(
        Solicitud(Pedido(pid, sku, 1, "destino"), i, urgencia, None, 0.0)
        for i, (pid, sku, urgencia) in enumerate(pedidos)
    )
    instancia = Instancia(3, config, tuple(cajas), solicitudes, version=2,
                          parametros={"caso": "auditoria"})
    instancia.validar()
    return instancia, inicializar(instancia, opciones_olas={
        "tamano": tamano or len(pedidos), "modelo_preparacion": "fisica"})


def test_conservacion_por_tick_en_pila_objetivo_bloqueador_objetivo():
    cajas = [Caja("OBJ-BAJO", "SKU-A", 1, 2, 2, 0),
             Caja("AJENA", "SKU-X", 1, 2, 2, 1),
             Caja("OBJ-ALTO", "SKU-B", 1, 2, 2, 2)]
    sim = make_sim(cajas, [("P-BAJO", "SKU-A", "media"),
                           ("P-ALTO", "SKU-B", "critica")], dims=(4, 4, 4), tamano=2)

    resultado, inicial, entregadas = _run_checked(sim)

    assert resultado["completa"]
    assert entregadas == {"OBJ-BAJO", "OBJ-ALTO"}
    assert set(inicial) == {"OBJ-BAJO", "AJENA", "OBJ-ALTO"}
    assert {c.id_caja for c in sim._grilla.cajas} == {"AJENA"}


def test_objetivo_de_ola_dos_puede_reubicarse_durante_ola_uno_y_se_recupera():
    cajas = [Caja("OBJ-OLA-1", "SKU-A", 1, 2, 2, 0),
             Caja("OBJ-OLA-2", "SKU-B", 1, 2, 2, 1)]
    sim = make_sim(cajas, [("P-OLA-1", "SKU-A", "media"),
                           ("P-OLA-2", "SKU-B", "critica")], dims=(4, 4, 3), tamano=1)
    inicial = {c.id_caja: c for c in sim._grilla.cajas}
    expected, delivered = set(inicial), set()
    primera_ola_terminada = False
    for _ in range(10000):
        if sim.plan_olas.completa or sim.plan_olas.fallo:
            break
        _advance_checked(sim, expected, delivered, inicial)
        if sim.plan_olas.olas[0]["fin"] is not None and not primera_ola_terminada:
            primera_ola_terminada = True
            caja_futura = next(c for c in sim._grilla.cajas if c.id_caja == "OBJ-OLA-2")
            assert caja_futura.id_sku == "SKU-B"
            assert (caja_futura.x, caja_futura.y, caja_futura.z) != (2, 2, 1)
    assert sim.plan_olas.completa
    assert delivered == {"OBJ-OLA-1", "OBJ-OLA-2"}
    assert any(e["tipo"] == "preparacion_deposito" and e["caja"] == "OBJ-OLA-2"
               for e in sim.plan_olas.eventos)


def test_espera_de_trafico_sin_cambio_fisico_agota_ventana(tmp_path, monkeypatch):
    instancia, sim = _make_v2_case(
        [Caja("T", "SKU-A", 1, 2, 2, 0), Caja("B", "SKU-X", 1, 2, 2, 1)],
        [("P", "SKU-A", "media")], dims=(4, 4, 3))
    registro = RegistroCorrida(instancia, sim, tmp_path, limite_ticks=100,
                               ventana_sin_entregas=3)

    def esperar_sin_avanzar():
        sim._eventos_pendientes.append({"tipo": "preparacion_espera_trafico", "robot": 0})
        return False

    monkeypatch.setattr(sim.plan_olas._preparador, "avanzar_tick", esperar_sin_avanzar)
    for _ in range(3):
        registro.avanzar()
    assert registro.terminal
    assert registro.estado == "sin_entregas"
    assert registro.sim.plan_olas.tiempo == 3

def test_tiempo_medio_guardado_coincide_con_servicio_en_vivo(tmp_path):
    cajas = [Caja("A", "SKU-A", 1, 2, 2, 0), Caja("B", "SKU-B", 1, 4, 4, 0)]
    instancia, sim = _make_v2_case(
        cajas, [("PA", "SKU-A", "media"), ("PB", "SKU-B", "media")],
        dims=(4, 4, 3), tamano=1)
    registro = RegistroCorrida(instancia, sim, tmp_path, limite_ticks=1000)
    for _ in range(10000):
        if registro.terminal:
            break
        registro.avanzar()
    assert registro.estado == "completa"

    vivo = sim.plan_olas.resumen()["servicio_por_categoria"]["media"]["tiempo_medio_entrega"]
    persistido = leer(tmp_path, registro.id)["resultado"]["servicio_por_categoria"]["media"]["tiempo_medio_entrega"]
    assert vivo == persistido
