from __future__ import annotations

from fastapi.testclient import TestClient

import api.server as server
from api.loop_worker import SimulationLoop
from bus_persistencia.bus.state_bus import StateBus


def test_generation_v2_defaults_and_legacy_compatibility(tmp_path, monkeypatch):
    isolated_bus = StateBus()
    isolated_loop = SimulationLoop(isolated_bus, on_tick=lambda: None, output_dir=tmp_path)
    monkeypatch.setattr(server, "bus", isolated_bus)
    monkeypatch.setattr(server, "loop", isolated_loop)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    with TestClient(server.app) as client:
        legacy = client.post("/experiments/instances/generate", json={})
        assert legacy.status_code == 200
        old_instance = legacy.json()["instancia"]
        assert old_instance["version"] == 1
        assert len(old_instance["solicitudes"]) == 12

        response = client.post("/experiments/instances/generate", json={
            "version": 2,
            "cantidad": 500,
            "productos": 20,
            "semilla": 42,
        })
        assert response.status_code == 200, response.text
        body = response.json()
        instance = body["instancia"]
        assert instance["version"] == 2
        assert len(instance["solicitudes"]) == 500
        assert len(instance["inventario"]) >= 500
        assert set(s["urgencia"] for s in instance["solicitudes"]) <= {
            "muy_baja", "baja", "media", "alta", "critica"
        }
        assert all("puntuacion" in s for s in instance["solicitudes"])
        assert set(instance["parametros"]["frecuencias_esperadas"]) == {
            "muy_baja", "baja", "media", "alta", "critica"
        }

        recovered = client.get(f"/experiments/instances/{body['id']}")
        assert recovered.status_code == 200
        assert recovered.json()["instancia"] == instance

        loaded = client.post(f"/experiments/instances/{body['id']}/load")
        assert loaded.status_code == 200, loaded.text
        run_id = loaded.json()["corrida_id"]

        snapshot = client.get("/snapshot?compacto=true").json()["experimento"]
        assert snapshot["total_pedidos"] == 500
        assert "pedidos" not in snapshot
        assert "eventos" not in snapshot
        assert len(snapshot["olas"]) <= 10

        compact = client.get(f"/experiments/runs/{run_id}?compacto=true")
        assert compact.status_code == 200
        assert compact.json()["eventos"] == []
        assert "pedidos" not in compact.json()["resultado"]

        page = client.get(f"/experiments/runs/{run_id}/pedidos?pagina=1&tamano=50")
        assert page.status_code == 200
        assert page.json()["total"] == 500
        assert len(page.json()["items"]) == 50
        clase = instance["solicitudes"][0]["urgencia"]
        filtrada = client.get(f"/experiments/runs/{run_id}/pedidos?pagina=1&tamano=50&urgencia={clase}")
        assert filtrada.status_code == 200
        assert all(p["urgencia"] == clase for p in filtrada.json()["items"])

        directorio = tmp_path / "corridas" / run_id
        with (directorio / "eventos.jsonl").open("a", encoding="utf-8") as archivo:
            archivo.write('{"tiempo":0,"tipo":"prueba_paginacion"}\n')
        events = client.get(f"/experiments/runs/{run_id}/eventos?desde=0&limite=1&tipo=prueba_paginacion")
        assert events.status_code == 200
        assert events.json()["items"][0]["tipo"] == "prueba_paginacion"

        incompatible = client.post(f"/experiments/instances/{body['id']}/load", json={
            "modelo_preparacion": "sintetica",
        })
        assert incompatible.status_code == 422


def test_v2_rechaza_dispersion_no_positiva():
    with TestClient(server.app) as client:
        response = client.post("/experiments/instances/generate", json={
            "version": 2,
            "cantidad": 500,
            "desviacion_urgencia": 0,
        })
    assert response.status_code == 422
    assert response.json()["detail"]
