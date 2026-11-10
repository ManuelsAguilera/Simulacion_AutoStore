import pytest

from experimentos.instancias import generar_demo, inicializar


def terminar(sim, limite=1000):
    for _ in range(limite):
        if sim.ha_terminado():
            return
        sim.avanzar_tick()
    assert sim.ha_terminado(), "La instancia controlada no completó la lista"


@pytest.mark.parametrize("tamano", [1, 4, 12])
@pytest.mark.parametrize("regla", ["original", "urgentes_primero"])
@pytest.mark.parametrize("costos", [(0, 0), (2, 1)])
def test_olas_completan_sin_solapamiento_y_sin_reiniciar(tamano, regla, costos):
    instancia = generar_demo()
    sim = inicializar(instancia, opciones_olas={"tamano": tamano, "regla": regla,
                      "preparacion_fija": costos[0], "preparacion_por_pedido": costos[1]})
    grilla, despachador = sim._grilla, sim._despachador
    anteriores = {c.id_caja for c in grilla._celdas.values()}
    for _ in range(1000):
        assert sim._grilla is grilla and sim._despachador is despachador
        presentes = {c.id_caja for c in grilla._celdas.values()}
        assert presentes <= anteriores  # Ninguna caja se repone entre olas.
        anteriores = presentes
        if sim.ha_terminado():
            break
        sim.avanzar_tick()
    resultado = sim.plan_olas.resumen()
    assert resultado["completa"]
    assert len(sim.pedidos_completados) == len({p.id_pedido for p in sim.pedidos_completados}) == 12
    olas = resultado["olas"]
    assert len(olas) == 12 // tamano
    assert resultado["preparacion_total"] == len(olas) * costos[0] + 12 * costos[1]
    assert resultado["tiempo"] == resultado["preparacion_total"] + resultado["recuperacion_ticks"]
    for previa, siguiente in zip(olas, olas[1:]):
        assert siguiente["preparacion_inicio"] == previa["fin"]
        assert siguiente["liberacion"] == previa["fin"] + siguiente["preparacion_ticks"]
    for pedido in resultado["pedidos"]:
        assert pedido["liberacion"] < pedido["asignacion"] <= pedido["entrega"]
    if regla == "urgentes_primero":
        orden = [e["id_pedido"] for e in resultado["eventos"] if e["tipo"] == "solicitud_asignada"]
        assert orden[:3] == [s.pedido.id_pedido for s in instancia.solicitudes if s.urgencia == "urgente"]


def test_una_ola_sin_preparacion_reproduce_motor_original_tick_a_tick():
    instancia = generar_demo()
    base = inicializar(instancia)
    olas = inicializar(instancia, opciones_olas={"tamano": 12})
    for _ in range(500):
        a, b = base.bus.read_snapshot(), olas.bus.read_snapshot()
        assert (a.robots, a.grilla, a.pedidos, a.kpis) == (b.robots, b.grilla, b.pedidos, b.kpis)
        if base.ha_terminado():
            assert olas.ha_terminado()
            break
        base.avanzar_tick()
        olas.avanzar_tick()
    assert base.ola_completa() and olas.plan_olas.completa


def test_preparacion_avanza_reloj_sin_asignar_y_hay_pedidos_pendientes():
    sim = inicializar(generar_demo(), opciones_olas={"tamano": 4, "preparacion_fija": 5})
    inicial = sim.bus.read_snapshot()
    assert not sim.ha_terminado() and not inicial.pedidos.cola
    for _ in range(4):
        sim.avanzar_tick()
    actual = sim.bus.read_snapshot()
    r = sim.plan_olas.resumen()
    assert actual.tick == inicial.tick + 4
    assert actual.grilla == inicial.grilla and actual.robots == inicial.robots
    assert r["preparacion_total"] == 4 and r["tiempo_finalizacion"] is None
    assert all(p["asignacion"] is None for p in r["pedidos"])
    sim.avanzar_tick()
    assert len(sim.bus.read_snapshot().pedidos.cola) == 4
    assert not sim.ha_terminado()


def test_ultima_ola_menor_usa_costo_de_sus_pedidos():
    sim = inicializar(generar_demo(), opciones_olas={"tamano": 5, "preparacion_fija": 2,
                                                  "preparacion_por_pedido": 1})
    terminar(sim)
    r = sim.plan_olas.resumen()
    assert [len(o["pedidos"]) for o in r["olas"]] == [5, 5, 2]
    assert [o["preparacion_ticks"] for o in r["olas"]] == [7, 7, 4]


@pytest.mark.parametrize("opciones", [{"tamano": 0}, {"tamano": 1.5}, {"regla": "otra"},
                                      {"preparacion_fija": -1}, {"preparacion_por_pedido": 0.5}])
def test_rechaza_parametros_invalidos(opciones):
    with pytest.raises(ValueError):
        inicializar(generar_demo(), opciones_olas=opciones)


def test_api_expone_y_conserva_plan_al_reiniciar(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from api import server
    from api.loop_worker import SimulationLoop
    from bus_persistencia.bus.state_bus import StateBus
    bus = StateBus()
    loop = SimulationLoop(bus, lambda: None, tmp_path)
    monkeypatch.setattr(server, "bus", bus)
    monkeypatch.setattr(server, "loop", loop)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    with TestClient(server.app) as client:
        id = client.post("/experiments/instances/generate", json={}).json()["id"]
        url = f"/experiments/instances/{id}/load"
        assert client.post(url, json={"tamano": 0}).status_code == 422
        assert client.post(url, json={"tamano": 4,"adaptativa":True,"maximo":2}).status_code == 422
        assert len(client.get('/experiments/instances').json()['instancias']) == 1
        assert client.post(url, json={"tamano": 4, "regla": "urgentes_primero",
                                      "preparacion_fija": 2}).status_code == 200
        inicial = client.get("/snapshot").json()["experimento"]
        assert inicial["estado_ola"] == "preparando"
        while not loop.registro.terminal:
            loop.registro.avanzar()
        resultado = client.get("/snapshot").json()["experimento"]
        assert resultado["completa"] and len(resultado["olas"]) == 3
        assert resultado["urgentes_a_tiempo"] == 3
        with client.websocket_connect("/ws/state") as ws:
            assert ws.receive_json()["experimento"] == resultado
        assert client.post("/control/reset").status_code == 200
        assert client.get("/snapshot").json()["experimento"] == inicial
