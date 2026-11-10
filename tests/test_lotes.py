from threading import Event, Thread
import json
import shutil
import time

import pytest

from api.loop_worker import SimulationLoop
from bus_persistencia.bus.state_bus import StateBus
from bus_persistencia.models.state import Config, GrillaDimensions
from experimentos.instancias import generar_instancia
from experimentos.lotes import GestorLotes
from experimentos.resultados import RegistroCorrida, leer


def esperar_estado(gestor, identificador, estados, timeout=20):
    vence = time.monotonic() + timeout
    while time.monotonic() < vence:
        lote = gestor.leer(identificador)
        if lote["estado"] in estados:
            return lote
        time.sleep(0.02)
    pytest.fail(f"El lote {identificador} no llegó a {sorted(estados)}")


def cuatro_configuraciones():
    return [
        {"tamano": 4, "regla": "original"},
        {"tamano": 4, "regla": "urgentes_primero"},
        {"tamano": 12, "regla": "original"},
        {"tamano": 12, "regla": "urgentes_primero"},
    ]


def test_lote_compara_cuatro_configuraciones_misma_instancia_y_se_recupera(tmp_path):
    instancia = generar_instancia(semilla=130, cantidad=12, productos=3)
    gestor = GestorLotes(tmp_path)
    configuraciones = cuatro_configuraciones()

    creado = gestor.crear(instancia, configuraciones, nombre="Comparación controlada",
                          limite_ticks=100000)
    final = esperar_estado(gestor, creado["id"], {"completa", "error"})

    assert final["estado"] == "completa"
    assert final["total_configuraciones"] == 4
    assert final["finalizadas"] == 4
    assert final["actual"] is None
    assert [fila["id"] for fila in final["configuraciones"]] == ["C01", "C02", "C03", "C04"]
    assert [(fila["tamano"], fila["regla"]) for fila in final["configuraciones"]] == [
        (4, "original"), (4, "urgentes_primero"),
        (12, "original"), (12, "urgentes_primero"),
    ]
    assert all(fila["estado"] == "completa" and fila["resultado"]["estado"] == "completa"
               for fila in final["configuraciones"])

    lote_dir = tmp_path / "lotes" / creado["id"]
    assert json.loads((lote_dir / "lote.json").read_text(encoding="utf-8")) == final
    protocolo = json.loads((lote_dir / "protocolo.json").read_text(encoding="utf-8"))
    assert protocolo["instancia_id"] == instancia.id
    assert protocolo["configuraciones"] == configuraciones

    corrida_ids = [fila["corrida_id"] for fila in final["configuraciones"]]
    assert len(set(corrida_ids)) == 4
    entradas = []
    for fila in final["configuraciones"]:
        corrida = leer(tmp_path / "corridas", fila["corrida_id"])
        entrada, resultado = corrida["entrada"], corrida["resultado"]
        assert entrada["instancia_id"] == instancia.id
        assert entrada["instancia"] == json.loads(json.dumps(instancia.to_dict()))
        assert entrada["lote_id"] == resultado["lote_id"] == creado["id"]
        assert entrada["configuracion_id"] == resultado["configuracion_id"] == fila["id"]
        assert resultado["estado"] == "completa"
        assert resultado["tiempo_finalizacion"] is not None
        entradas.append(entrada["instancia"])
    assert all(e == entradas[0] for e in entradas[1:])

    catalogo = gestor.listar()
    assert any(item["id"] == creado["id"] and item["estado"] == "completa" for item in catalogo)
    gestor.cerrar()
    nuevo_gestor = GestorLotes(tmp_path)
    assert nuevo_gestor.leer(creado["id"]) == final
    assert any(item["id"] == creado["id"] for item in nuevo_gestor.listar())
    nuevo_gestor.cerrar()


@pytest.mark.parametrize("configuraciones", [
    [],
    [{"tamano": 0, "regla": "original"}],
    [{"tamano": -1, "regla": "original"}],
    [{"tamano": 13, "regla": "original"}],
    [{"tamano": 4, "regla": "aleatoria"}],
    [{"tamano": 4, "regla": "original"}, {"tamano": 4, "regla": "original"}],
])
def test_configuraciones_invalidas_no_crean_archivos(tmp_path, configuraciones):
    instancia = generar_instancia(semilla=131, cantidad=12, productos=3)
    gestor = GestorLotes(tmp_path)
    with pytest.raises(ValueError):
        gestor.crear(instancia, configuraciones)
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]
    gestor.cerrar()


def test_rechaza_mas_de_24_configuraciones_antes_de_persistir(tmp_path):
    instancia = generar_instancia(semilla=132, cantidad=30, productos=4)
    configuraciones = [{"tamano": tamano, "regla": regla}
                       for tamano in range(1, 14)
                       for regla in ("original", "urgentes_primero")]
    gestor = GestorLotes(tmp_path)
    with pytest.raises(ValueError):
        gestor.crear(instancia, configuraciones)
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]
    gestor.cerrar()


def test_horizonte_de_una_incluye_resultado_incompleto_y_sin_tiempo_final(tmp_path):
    instancia = generar_instancia(semilla=133, cantidad=12, productos=3)
    gestor = GestorLotes(tmp_path)
    lote = gestor.crear(instancia, [{"tamano": 1, "regla": "original"}], limite_ticks=1)
    final = esperar_estado(gestor, lote["id"], {"completa", "error"})

    assert final["estado"] == "completa"
    fila = final["configuraciones"][0]
    assert fila["estado"] == "horizonte_agotado"
    assert fila["resultado"]["estado"] == "horizonte_agotado"
    assert fila["resultado"]["tiempo_finalizacion"] is None
    gestor.cerrar()


def test_cancelar_no_espera_dentro_de_avanzar_y_omite_pendientes(tmp_path, monkeypatch):
    instancia = generar_instancia(semilla=134, cantidad=20, productos=4)
    gestor = GestorLotes(tmp_path)
    entrando = Event()
    liberar = Event()
    original = RegistroCorrida.avanzar
    una_vez = Event()

    def avanzar_bloqueado(registro):
        if not una_vez.is_set():
            una_vez.set()
            entrando.set()
            liberar.wait(10)
            return
        return original(registro)

    monkeypatch.setattr(RegistroCorrida, "avanzar", avanzar_bloqueado)
    lote = gestor.crear(instancia, cuatro_configuraciones(), limite_ticks=100000)
    assert entrando.wait(5), "El lote no entró a una configuración activa"
    salida = {}
    llamada_termino = Event()

    def cancelar():
        salida.update(gestor.cancelar(lote["id"]))
        llamada_termino.set()

    hilo_cancelacion = Thread(target=cancelar, daemon=True)
    hilo_cancelacion.start()
    termino_sin_liberar = llamada_termino.wait(1.0)
    liberar.set()
    hilo_cancelacion.join(5)
    try:
        assert termino_sin_liberar, "cancelar esperó mientras avanzar estaba bloqueado"
        assert not hilo_cancelacion.is_alive()
        cancelado = esperar_estado(gestor, lote["id"], {"cancelada"})
        assert salida["estado"] == "cancelando"
        assert cancelado["estado"] == "cancelada"
        rows = cancelado["configuraciones"]
        actuales = [r for r in rows if r["estado"] == "interrumpida"]
        omitidas = [r for r in rows if r["estado"] == "omitida"]
        assert len(actuales) == 1
        assert len(omitidas) == 3
        assert cancelado["finalizadas"] == 1
    finally:
        liberar.set()
        gestor.cerrar()


def test_lote_no_modifica_un_simulation_loop_independiente(tmp_path):
    bus = StateBus()
    loop = SimulationLoop(bus, lambda: None, output_dir=tmp_path / "interactivo")
    config = Config(GrillaDimensions(4, 4, 3), 1, 0.1, robots_este=1)
    loop.configurar(config, seed=92, pedidos_demandados=2)
    before = bus.read_snapshot()
    instancia = generar_instancia(semilla=135, cantidad=12, productos=3)
    gestor = GestorLotes(tmp_path / "lote")
    lote = gestor.crear(instancia, [{"tamano": 12, "regla": "original"}], limite_ticks=100000)
    final = esperar_estado(gestor, lote["id"], {"completa", "error"})
    after = bus.read_snapshot()
    assert final["estado"] == "completa"
    assert after == before
    gestor.cerrar()
    loop.cerrar()


def test_reiniciar_gestor_markea_lote_en_ejecucion_interrumpido(tmp_path, monkeypatch):
    instancia = generar_instancia(semilla=136, cantidad=12, productos=3)
    gestor = GestorLotes(tmp_path)
    entrando = Event()
    liberar = Event()
    original = RegistroCorrida.avanzar

    def dejar_en_ejecucion(registro):
        entrando.set()
        liberar.wait(10)
        return

    monkeypatch.setattr(RegistroCorrida, "avanzar", dejar_en_ejecucion)
    lote = gestor.crear(instancia, cuatro_configuraciones(), limite_ticks=100000)
    assert entrando.wait(5)

    # Una segunda raíz simula disco tras caída: copia solo los checkpoints ya
    # persistidos, mientras el trabajador original permanece detenido.
    recuperacion = tmp_path / "recuperacion"
    shutil.copytree(tmp_path / "lotes", recuperacion / "lotes")
    shutil.copytree(tmp_path / "corridas", recuperacion / "corridas")
    nuevo_gestor = GestorLotes(recuperacion)
    try:
        reabierto = nuevo_gestor.leer(lote["id"])
        assert reabierto["estado"] == "interrumpida"
        assert reabierto["finalizadas"] == 1
        assert all(r["estado"] != "completa" for r in reabierto["configuraciones"])
    finally:
        liberar.set()
        monkeypatch.setattr(RegistroCorrida, "avanzar", original)
        gestor.cancelar(lote["id"])
        gestor.cerrar()
        nuevo_gestor.cerrar()
