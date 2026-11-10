from pathlib import Path
import json

import pytest

from bus_persistencia.models.state import Caja, Config, GrillaDimensions, Pedido
from experimentos.instancias import Instancia, Solicitud, generar_demo, inicializar
from experimentos.resultados import RegistroCorrida, leer, listar, reconstruir


def caso_fisico(bloqueador=False):
    cajas = [Caja("OBJ", "SKU-A", 1, 3, 3, 0)]
    if bloqueador:
        cajas.append(Caja("BLOQ", "SKU-B", 1, 3, 3, 1))
    return Instancia(1, Config(GrillaDimensions(3, 3, 2), 1, len(cajas)*100/18, robots_este=1),
                     tuple(cajas), (Solicitud(Pedido("P1", "SKU-A", 1, "D"), 0, "urgente", 100),))


def correr(registro):
    for _ in range(1000):
        if registro.terminal:
            return
        registro.avanzar()
    pytest.fail("La corrida no finalizó dentro del límite de prueba")


@pytest.mark.parametrize("bloqueador", [False, True])
def test_evento_fisico_coincide_con_caja_y_posicion(tmp_path, bloqueador):
    instancia = caso_fisico(bloqueador)
    sim = inicializar(instancia, opciones_olas={"tamano": 1})
    registro = RegistroCorrida(instancia, sim, tmp_path, 100)
    movimientos = 0
    for _ in range(100):
        if registro.terminal:
            break
        previo = {c.id_caja: c for c in sim.bus.read_snapshot().grilla}
        registro.avanzar()
        actual = {c.id_caja: c for c in sim.bus.read_snapshot().grilla}
        for e in sim.last_tick_eventos:
            if e["tipo"] == "excavacion":
                movimientos += 1
                assert e["id_caja"] == "BLOQ" and e["id_pedido"] == "P1"
                a, b = previo[e["id_caja"]], actual[e["id_caja"]]
                assert [a.x, a.y, a.z] == e["de"]
                assert [b.x, b.y, b.z] == e["a"]
    doc = leer(tmp_path, registro.id)
    r = doc["resultado"]
    assert r["estado"] == "completa" and r["entregados"] == 1
    assert movimientos == r["movimientos_bloqueadores"] == int(bloqueador)
    assert r["robot_ticks_excavacion"] == r["ticks_con_excavacion"] == int(bloqueador)
    assert r["tiempo_finalizacion"] == (12 if bloqueador else 11)
    assert reconstruir(instancia, doc["eventos"])["pedidos"] == r["pedidos"]
    p = r["pedidos"][0]
    assert p["tiempo_total"] == p["espera_previa_liberacion"] + p["espera_asignacion"] + p["tiempo_desde_asignacion"]


@pytest.mark.parametrize("tipo", ["escort", "espacio"])
def test_espera_excavacion_no_cuenta_como_trabajo_activo(tmp_path, monkeypatch, tipo):
    instancia = caso_fisico(True)
    sim = inicializar(instancia, opciones_olas={"tamano": 1})
    planner = sim._despachador.escort_planner
    if tipo == "escort":
        monkeypatch.setattr(planner, "planificar", lambda *args: None)
    else:
        monkeypatch.setattr(planner, "mover_escort_un_paso", lambda *args: None)
    registro = RegistroCorrida(instancia, sim, tmp_path, 15)
    correr(registro)
    r = leer(tmp_path, registro.id)["resultado"]
    assert r["estado"] == "horizonte_agotado" and r["tiempo_finalizacion"] is None
    assert r[f"robot_ticks_espera_{tipo}"] > 0
    assert r["movimientos_bloqueadores"] == r["robot_ticks_excavacion"] == 0


def test_trabajo_paralelo_no_se_suma_como_duracion():
    eventos = [{"tipo": "excavacion", "tiempo": 3, "robot_id": r} for r in (0, 1)]
    r = reconstruir(caso_fisico(), eventos)
    assert r["robot_ticks_excavacion"] == 2 and r["ticks_con_excavacion"] == 1


def test_checkpoint_reintenta_bloqueo_transitorio(tmp_path, monkeypatch):
    from experimentos.resultados import _json
    original = Path.replace
    intentos = []
    def reemplazar(path, destino):
        intentos.append(1)
        if len(intentos) < 3:
            raise PermissionError("archivo retenido temporalmente")
        return original(path, destino)
    monkeypatch.setattr(Path, "replace", reemplazar)
    _json(tmp_path / "estado.json", {"estado":"completa"})
    assert len(intentos) == 3
    assert json.loads((tmp_path / "estado.json").read_text()) == {"estado":"completa"}


def test_no_sobrescribe_y_reconstruye_resultados(tmp_path):
    instancia = generar_demo()
    ids = []
    primero = None
    for _ in range(2):
        sim = inicializar(instancia, opciones_olas={"tamano": 4, "regla": "urgentes_primero",
                                                  "preparacion_fija": 2})
        registro = RegistroCorrida(instancia, sim, tmp_path, 500)
        correr(registro)
        ids.append(registro.id)
        if primero is None:
            primero = (tmp_path / registro.id / "estado.json").read_bytes()
    assert ids[0] != ids[1]
    assert (tmp_path / ids[0] / "estado.json").read_bytes() == primero
    assert len(listar(tmp_path)) == 2
    for id in ids:
        doc = leer(tmp_path, id)
        r = doc["resultado"]
        assert r["estado"] == "completa" and r["entregados"] == r["total"] == 12
        assert r["urgentes_a_tiempo"] == 3 and r["cumplimiento_urgentes"] == 1
        assert reconstruir(instancia, doc["eventos"])["pedidos"] == r["pedidos"]
        assert doc["entrada"]["instancia"] == json.loads(json.dumps(instancia.to_dict()))
        assert len(doc["entrada"]["codigo"]["sha256_fuentes"]) == 64


def test_limites_y_error_quedan_guardados(tmp_path, monkeypatch):
    instancia = generar_demo()
    for estado, limite, ventana in [("horizonte_agotado", 4, 1), ("sin_entregas", 100, 1)]:
        sim = inicializar(instancia, opciones_olas={"preparacion_fija": 5})
        registro = RegistroCorrida(instancia, sim, tmp_path, limite, ventana)
        correr(registro)
        r = leer(tmp_path, registro.id)["resultado"]
        assert r["estado"] == estado and r["tiempo_finalizacion"] is None
        assert r["entregados"] == 0
    sim = inicializar(instancia, opciones_olas={})
    registro = RegistroCorrida(instancia, sim, tmp_path)
    def fallar():
        raise RuntimeError("fallo controlado")
    monkeypatch.setattr(sim, "avanzar_tick", fallar)
    with pytest.raises(RuntimeError):
        registro.avanzar()
    r = leer(tmp_path, registro.id)["resultado"]
    assert r["estado"] == "error" and "fallo controlado" in r["motivo"]


def test_api_historial_recuperable_tras_nuevo_controlador(tmp_path, monkeypatch):
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
        r = client.post(f"/experiments/instances/{id}/load", json={"limite_ticks": 1}).json()
        corrida = r["corrida_id"]
        loop.registro.avanzar()
        assert client.post("/policy", json={"policy": "PRIORIDAD_POSICION"}).status_code == 409
        doc = client.get(f"/experiments/runs/{corrida}").json()
        assert doc["resultado"]["estado"] == "horizonte_agotado"
        client.post("/control/reset")
        assert client.get("/snapshot").json()["corrida"]["id"] != corrida
    nuevo = SimulationLoop(bus, lambda: None, tmp_path)
    monkeypatch.setattr(server, "loop", nuevo)
    with TestClient(server.app) as client:
        assert client.get(f"/experiments/runs/{corrida}").json() == doc
        estados = {r["estado"] for r in client.get("/experiments/runs").json()["corridas"]}
        assert estados == {"horizonte_agotado", "interrumpida"}
        assert client.get("/experiments/runs/incorrecto").status_code == 422


def test_api_finaliza_y_guarda_horizonte_en_hilo(tmp_path, monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from api import server
    from api.loop_worker import SimulationLoop, VELOCIDAD_INTERVALOS
    from bus_persistencia.bus.state_bus import StateBus
    bus = StateBus()
    loop = SimulationLoop(bus, lambda: None, tmp_path)
    monkeypatch.setattr(server, "bus", bus)
    monkeypatch.setattr(server, "loop", loop)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    monkeypatch.setitem(VELOCIDAD_INTERVALOS, 1, 0.001)
    with TestClient(server.app) as client:
        id = client.post("/experiments/instances/generate", json={}).json()["id"]
        corrida = client.post(f"/experiments/instances/{id}/load",
                              json={"limite_ticks": 2}).json()["corrida_id"]
        assert client.post("/control/play").status_code == 200
        limite = time.monotonic() + 3
        while loop.status == "RUNNING" and time.monotonic() < limite:
            time.sleep(0.01)
        assert loop.status == "INCOMPLETE"
        doc = client.get(f"/experiments/runs/{corrida}").json()
        assert doc["resultado"]["estado"] == "horizonte_agotado"
        assert doc["resultado"]["tiempo"] == 2
        assert client.post("/control/play").status_code == 409
    assert leer(tmp_path / "corridas", corrida) == doc


def test_historial_incluye_pilotos_completos_y_permite_cargar_sus_instancias(tmp_path, monkeypatch):
    from experimentos.instancias import guardar
    from fastapi.testclient import TestClient
    from api import server
    from api.loop_worker import SimulationLoop
    from bus_persistencia.bus.state_bus import StateBus
    carpeta=tmp_path/'piloto_control'
    instancia=generar_demo(43)
    guardar(instancia,carpeta/'instancias')
    registro=RegistroCorrida(instancia,inicializar(instancia,opciones_olas={}),carpeta/'corridas',1)
    registro.avanzar()
    bus=StateBus()
    monkeypatch.setattr(server,'OUTPUT_DIR',tmp_path)
    monkeypatch.setattr(server,'bus',bus)
    monkeypatch.setattr(server,'loop',SimulationLoop(bus,lambda:None,tmp_path))
    with TestClient(server.app) as client:
        assert client.get('/experiments/runs').json()['corridas']==[]
        (carpeta/'resumen.json').write_text('{}')
        assert len(client.get('/experiments/runs').json()['corridas'])==1
        assert client.get(f'/experiments/runs/{registro.id}').json()['resultado']['estado']=='horizonte_agotado'
        assert client.get('/experiments/instances').json()['instancias'][0]['id']==instancia.id
        assert client.post(f'/experiments/instances/{instancia.id}/load').status_code==200
