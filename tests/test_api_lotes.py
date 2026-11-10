import json
import time

from fastapi.testclient import TestClient

from api import server
from api.loop_worker import SimulationLoop
from bus_persistencia.bus.state_bus import StateBus
from experimentos.resultados import leer


def esperar_lote(client, identificador, timeout=15):
    vence = time.monotonic() + timeout
    terminales = {"completa", "cancelada", "interrumpida", "error"}
    while time.monotonic() < vence:
        response = client.get(f"/experiments/batches/{identificador}")
        assert response.status_code == 200
        lote = response.json()
        if lote["estado"] in terminales:
            return lote
        time.sleep(0.02)
    raise AssertionError(f"El lote {identificador} no terminó en {timeout}s")


def test_api_lotes_genera_compara_recupera_exporta_y_mapea_errores(tmp_path, monkeypatch):
    bus = StateBus()
    loop = SimulationLoop(bus, lambda: None, tmp_path)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(server, "bus", bus)
    monkeypatch.setattr(server, "loop", loop)

    with TestClient(server.app) as client:
        generada = client.post("/experiments/instances/generate", json={
            "version": 2, "cantidad": 12, "productos": 3, "semilla": 204,
        })
        assert generada.status_code == 200
        instancia_id = generada.json()["id"]
        instancia = generada.json()["instancia"]

        body = {"instancia_id": instancia_id, "nombre": None,
                "configuraciones": [{"tamano": 12, "regla": "original"}],
                "limite_ticks": 100000}
        invalido = {**body, "configuraciones": [{"tamano": 0, "regla": "original"}]}
        assert client.post("/experiments/batches", json=invalido).status_code == 422
        assert not list((tmp_path / "lotes").glob("*/lote.json")) if (tmp_path / "lotes").exists() else True

        inexistente = "f" * 32
        assert client.get("/experiments/batches/no-existe").status_code == 422
        assert client.get(f"/experiments/batches/{inexistente}").status_code == 404
        assert client.get(f"/experiments/batches/{inexistente}/export").status_code == 404
        assert client.post(f"/experiments/batches/{inexistente}/cancel").status_code == 404

        creada = client.post("/experiments/batches", json=body)
        assert creada.status_code == 201
        lote_id = creada.json()["id"]
        final = esperar_lote(client, lote_id)
        assert final["nombre"] == "Comparación de olas"
        assert final["estado"] == "completa"
        assert final["finalizadas"] == 1
        assert final["configuraciones"][0]["estado"] in {"completa", "inviable_preparacion"}
        assert final["configuraciones"][0]["resultado"]["estado"] == final["configuraciones"][0]["estado"]

        catalogo = client.get("/experiments/batches")
        assert catalogo.status_code == 200
        assert any(row["id"] == lote_id for row in catalogo.json()["lotes"])
        detalle = client.get(f"/experiments/batches/{lote_id}")
        assert detalle.status_code == 200 and detalle.json() == final
        exportado = client.get(f"/experiments/batches/{lote_id}/export")
        assert exportado.status_code == 200
        assert exportado.headers["content-type"].startswith("application/json")
        paquete = exportado.json()
        assert paquete["lote"] == final
        assert paquete["protocolo"]["instancia_id"] == instancia_id

        fila = final["configuraciones"][0]
        corrida = leer(tmp_path / "corridas", fila["corrida_id"])
        assert corrida["entrada"]["instancia"] == json.loads(json.dumps(instancia))
        assert corrida["entrada"]["lote_id"] == lote_id
        assert corrida["entrada"]["configuracion_id"] == fila["id"]
        assert corrida["resultado"]["lote_id"] == lote_id
        assert corrida["resultado"]["configuracion_id"] == fila["id"]

        gestor = server._gestor_lotes()
        original_crear = gestor.crear
        def ya_activo(*args, **kwargs):
            raise RuntimeError("Ya hay una comparación en ejecución")
        monkeypatch.setattr(gestor, "crear", ya_activo)
        conflicto = client.post("/experiments/batches", json=body)
        assert conflicto.status_code == 409
        monkeypatch.setattr(gestor, "crear", original_crear)

    loop.cerrar()
