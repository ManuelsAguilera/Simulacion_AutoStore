from dataclasses import replace
import json

import pytest

from experimentos.instancias import Instancia, cargar, generar_demo, guardar, inicializar


def test_guardar_recargar_y_repetir_entregas(tmp_path):
    original = generar_demo(42)
    path = guardar(original, tmp_path)
    assert guardar(original, tmp_path) == path
    assert cargar(path) == original == generar_demo(42)
    assert len({s.pedido.id_sku for s in original.solicitudes}) == 3
    assert sum(s.urgencia == "urgente" for s in original.solicitudes) == 3
    resultados = []
    for instancia in (original, cargar(path)):
        sim = inicializar(instancia)
        assert tuple(sim.bus.read_snapshot().grilla) == original.inventario
        inicio = sim.tick
        for _ in range(500):
            if sim.ola_completa():
                break
            sim.avanzar_tick()
        assert sim.ola_completa()
        ids = [p.id_pedido for p in sim.pedidos_completados]
        assert len(ids) == len(set(ids)) == 12
        assert set(ids) == {s.pedido.id_pedido for s in original.solicitudes}
        assert not sim.bus.read_snapshot().grilla
        resultados.append((sim.tick - inicio, ids))
    assert resultados[0] == resultados[1]


def test_sin_stock_se_rechaza_antes_de_simular():
    instancia = generar_demo()
    with pytest.raises(ValueError, match="Sin inventario"):
        inicializar(replace(instancia, inventario=instancia.inventario[:-1]))


@pytest.mark.parametrize("cambio", ["id", "posicion", "urgencia", "plazo", "cantidad"])
def test_rechaza_instancias_invalidas(cambio):
    obj = generar_demo().to_dict()
    if cambio == "id":
        obj["solicitudes"][1]["pedido"]["id_pedido"] = "P001"
    elif cambio == "posicion":
        obj["inventario"][0]["x"] = 0
    elif cambio == "urgencia":
        obj["solicitudes"][0]["urgencia"] = "desconocida"
    elif cambio == "plazo":
        obj["solicitudes"][0].update(urgencia="urgente", plazo_tick=None)
    else:
        obj["solicitudes"][0]["pedido"]["cantidad"] = 2
    with pytest.raises(ValueError):
        Instancia.from_dict(obj)


def test_api_genera_recupera_carga_y_reinicia(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from api import server
    from api.loop_worker import SimulationLoop
    from bus_persistencia.bus.state_bus import StateBus

    bus = StateBus()
    loop = SimulationLoop(bus, on_tick=lambda: None, output_dir=tmp_path)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(server, "bus", bus)
    monkeypatch.setattr(server, "loop", loop)
    with TestClient(server.app) as client:
        response = client.post("/experiments/instances/generate", json={"semilla": 42})
        assert response.status_code == 200
        doc = response.json()
        url = f'/experiments/instances/{doc["id"]}'
        assert client.get(url).json() == doc
        assert client.post(url + "/load").status_code == 200
        initial = bus.read_snapshot()
        assert len(initial.pedidos.cola) == len(initial.grilla) == 12
        assert client.get("/snapshot").status_code == 200
        assert client.post("/control/speed", json={"velocidad": 5}).status_code == 200
        assert client.post("/control/play").status_code == 200
        import time
        limite = time.monotonic() + 3
        while bus.read_snapshot().tick == initial.tick and time.monotonic() < limite:
            time.sleep(0.01)
        assert bus.read_snapshot().tick > initial.tick
        assert client.post("/control/pause").status_code == 200
        assert client.post("/control/reset").status_code == 200
        assert bus.read_snapshot().grilla == initial.grilla
        assert bus.read_snapshot().pedidos == initial.pedidos
        assert client.post("/experiments/instances/generate", json={"urgentes": 13}).status_code == 422
        assert client.get("/experiments/instances/no-valido").status_code == 400
    # El resultado se recupera del archivo, sin caché de instancias en la API.
    nuevo_loop = SimulationLoop(bus, on_tick=lambda: None, output_dir=tmp_path)
    monkeypatch.setattr(server, "loop", nuevo_loop)
    with TestClient(server.app) as client:
        assert client.get(url).json() == doc
        assert client.post(url + "/load").status_code == 200
    # Alterar stock en disco debe fallar antes de cargar el motor.
    archivo = tmp_path / "instancias" / f'{doc["id"]}.json'
    data = json.loads(archivo.read_text(encoding="utf-8"))
    data["inventario"].pop()
    archivo.write_text(json.dumps(data), encoding="utf-8")
    with TestClient(server.app) as client:
        assert client.post(url + "/load").status_code == 422
