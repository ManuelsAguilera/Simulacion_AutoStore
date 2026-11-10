import json

import pytest

from experimentos.instancias import generar_instancia, inicializar
from experimentos.resultados import (RegistroCorrida, leer, leer_resumen,
                                     paginar_eventos, paginar_pedidos, reconstruir)


def test_checkpoint_incremental_y_paginas_reconcilian(tmp_path):
    instancia = generar_instancia(semilla=91, cantidad=8, productos=2, x=4, y=4, z=2)
    sim = inicializar(instancia, opciones_olas={"tamano": 4, "regla": "urgentes_primero"})
    registro = RegistroCorrida(instancia, sim, tmp_path, limite_ticks=3000)
    primer_guardado = (registro.directorio / "estado.json").read_bytes()
    for _ in range(10):
        registro.avanzar()
    assert registro.eventos == []  # v2 no duplica toda la traza en memoria
    if not registro.terminal:
        assert (registro.directorio / "estado.json").read_bytes() == primer_guardado
    while not registro.terminal:
        registro.avanzar()
    documento = leer(tmp_path, registro.id)
    resumen = documento["resultado"]
    assert resumen["estado"] == "completa", resumen.get("motivo")
    assert resumen["modelo_preparacion"] == "fisica"
    assert resumen["entregados"] == 8
    assert reconstruir(instancia, documento["eventos"])["pedidos"] == resumen["pedidos"]
    assert leer_resumen(tmp_path, registro.id)["eventos"] == []
    assert sum(c["total"] for c in resumen["servicio_por_categoria"].values()) == 8
    pagina = paginar_pedidos(tmp_path, registro.id, pagina=2, tamano=3)
    assert pagina["total"] == 8 and len(pagina["items"]) == 3
    assert all(p["ola"] in {1, 2} for p in pagina["items"])
    assert paginar_pedidos(tmp_path, registro.id, estado="pendiente")["total"] == 0
    recuperados, cursor = [], 0
    while True:
        lote = paginar_eventos(tmp_path, registro.id, desde=cursor, limite=7)
        recuperados.extend(lote["items"])
        cursor = lote["siguiente"]
        if not lote["hay_mas"]:
            break
    assert recuperados == documento["eventos"]
    for funcion, kwargs in [(paginar_pedidos, {"pagina": 0}), (paginar_eventos, {"desde": -1})]:
        with pytest.raises(ValueError):
            funcion(tmp_path, registro.id, **kwargs)


def test_pausa_vuelca_eventos_y_no_inventa_tiempo_final(tmp_path):
    instancia = generar_instancia(semilla=92, cantidad=20)
    registro = RegistroCorrida(instancia, inicializar(instancia, opciones_olas={"tamano": 5}), tmp_path)
    for _ in range(3):
        registro.avanzar()
    registro.guardar("pausada")
    doc = leer(tmp_path, registro.id)
    assert doc["resultado"]["tiempo"] == 3
    assert doc["resultado"]["estado"] == "pausada"
    assert doc["resultado"]["tiempo_finalizacion"] is None
    assert all(e["tiempo"] <= 3 for e in doc["eventos"])
    with (registro.directorio / "eventos.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"tipo": "incompleto", "tiempo": 4}))
    assert leer(tmp_path, registro.id) == doc


def test_v2_no_duplica_logger_por_tick_y_conserva_snapshot():
    from bus_persistencia.bus.state_bus import StateBus

    class Logger:
        def __init__(self): self.eventos = 0
        def buffer_event(self, *args): self.eventos += 1
        def flush_tick_async(self, *args): self.eventos += 1

    logger = Logger()
    bus = StateBus(logger)
    sim = inicializar(generar_instancia(cantidad=8), bus, {"tamano": 4})
    for _ in range(4):
        sim.avanzar_tick()
    assert logger.eventos == 0
    assert bus.read_snapshot().tick == sim.tick == 5
    assert sim.last_tick_eventos


def test_preparacion_inviable_conserva_cajas_y_motivo(tmp_path):
    from bus_persistencia.models.state import Caja, Config, GrillaDimensions, Pedido
    from experimentos.instancias import Instancia, Solicitud
    cajas = (Caja("A", "A", 1, 2, 2, 0), Caja("X", "X", 1, 2, 2, 1),
             Caja("B", "B", 1, 3, 2, 0))
    solicitudes = tuple(Solicitud(Pedido(k, k, 1, "D"), i, "media", 100, 0.0)
                        for i, k in enumerate(("A", "B")))
    instancia = Instancia(1, Config(GrillaDimensions(2, 1, 2), 1, 75, robots_este=1),
                          cajas, solicitudes, version=2, parametros={})
    registro = RegistroCorrida(instancia, inicializar(instancia, opciones_olas={"tamano": 2}), tmp_path)
    registro.avanzar()
    doc = leer(tmp_path, registro.id)["resultado"]
    assert doc["estado"] == "inviable_preparacion"
    assert "espacio" in doc["motivo"]
    assert doc["tiempo_finalizacion"] is None
    assert len(doc["ultimo_estado"]["inventario"]) == 3
    assert doc["entregados"] == 0
