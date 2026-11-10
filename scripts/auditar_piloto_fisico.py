"""Audita conservación y tiempos de un piloto físico sin cargar toda su traza."""
import argparse
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experimentos.instancias import Instancia, inicializar
from experimentos.resultados import MedidasIncrementales
from bus_persistencia.models.state import punta


def auditar(carpeta):
    filas = json.loads((carpeta / "indice.json").read_text(encoding="utf-8"))
    hashes, eventos_total = Counter(), 0
    for fila in filas:
        directorio = carpeta / "corridas" / fila["corrida_id"]
        entrada = json.loads((directorio / "entrada.json").read_text(encoding="utf-8"))
        resultado = json.loads((directorio / "estado.json").read_text(encoding="utf-8"))
        instancia = Instancia.from_dict(entrada["instancia"])
        medidas = MedidasIncrementales(instancia)
        conocidas = {c.id_caja: asdict(c) for c in instancia.inventario}
        almacen = dict(conocidas)
        carga, entregadas, pedidos = {}, set(), set()
        sim = inicializar(instancia, opciones_olas=entrada["opciones"])
        robots = {rid: [r.x, r.y, r.orientacion] for rid, r in sim.robots.items()}

        def retirar(cid, rid):
            assert rid not in carga, (fila, "Robot ya cargado", rid)
            caja = almacen.pop(cid)
            assert not any(c["x"] == caja["x"] and c["y"] == caja["y"] and c["z"] > caja["z"]
                           for c in almacen.values()), (fila, "Se retiró una caja bajo otra", cid)
            carga[rid] = cid

        with (directorio / "eventos.jsonl").open(encoding="utf-8") as archivo:
            for linea in archivo:
                e = json.loads(linea)
                if e["tiempo"] > resultado["tiempo"]:
                    break
                eventos_total += 1
                medidas.consumir([e])
                tipo = e["tipo"]
                if tipo in {"movimiento", "preparacion_movimiento"}:
                    rid = e.get("robot_id", e.get("robot"))
                    x, y, ori = robots[rid]
                    assert abs(x-e["x"])+abs(y-e["y"]) == 1, (fila, "Salto de robot", e)
                    robots[rid] = [e["x"], e["y"], ori]
                elif tipo == "preparacion_toma":
                    retirar(e["caja"], e["robot"])
                elif tipo == "preparacion_deposito":
                    cid, rid = e["caja"], e["robot"]
                    assert carga.pop(rid) == cid
                    x, y, z = e["a"]
                    col = [c for c in almacen.values() if (c["x"], c["y"]) == (x,y)]
                    assert z == len(col), (fila, "Depósito sin soporte", e)
                    rx, ry, ori = robots[rid]
                    desplazamiento = {"E": (1,0), "O": (-1,0), "N": (0,-1)}[ori]
                    assert (rx+desplazamiento[0], ry+desplazamiento[1]) == (x,y)
                    almacen[cid] = {**conocidas[cid], "x": x, "y": y, "z": z}
                elif tipo == "caja_recuperada":
                    retirar(e["id_caja"], e["robot_id"])
                elif tipo == "pedido_completado":
                    cid = carga.pop(e["robot_id"])
                    assert e["id_pedido"] not in pedidos and cid not in entregadas
                    assert conocidas[cid]["id_sku"] == e["id_sku"]
                    pedidos.add(e["id_pedido"])
                    entregadas.add(cid)
        assert set(almacen) | set(carga.values()) | entregadas == set(conocidas)
        assert not (set(almacen) & set(carga.values()) or set(almacen) & entregadas)
        final = {c["id_caja"]: c for c in resultado["ultimo_estado"]["inventario"]}
        assert final == almacen, (fila, "Estado final de inventario distinto de traza")
        assert {r["id"]:r["carga_id"] for r in resultado["ultimo_estado"]["robots"] if r["carga_id"]} == carga
        reconstruidas = medidas.resumen()
        for clave in ("pedidos", "entregados", "total", "servicio_por_categoria",
                      "movimientos_preparacion", "movimientos_recuperacion"):
            assert reconstruidas[clave] == resultado[clave], (fila, clave)
        assert len(pedidos) == resultado["entregados"]
        if resultado["estado"] == "completa":
            assert len(pedidos) == len(instancia.solicitudes)
            assert resultado["tiempo_finalizacion"] == max(p["entrega"] for p in resultado["pedidos"])
        else:
            assert resultado["tiempo_finalizacion"] is None
        hashes[entrada["codigo"]["sha256_fuentes"]] += 1
    informe = {"corridas_auditadas": len(filas), "eventos_revisados": eventos_total,
        "conservacion_y_trazas": "verificadas", "huellas_fuentes_registradas": dict(hashes),
        "nota": "Las huellas completas pueden variar por mejoras de instrumentación; cada corrida conserva su entrada."}
    (carpeta / "auditoria.json").write_text(json.dumps(informe, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(informe, ensure_ascii=False))
    return informe


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("carpeta", type=Path)
    auditar(parser.parse_args().carpeta)
