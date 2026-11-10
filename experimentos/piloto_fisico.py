"""Piloto versionado de preparación física, con entradas congeladas y fallos visibles."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import statistics
import time

from bus_persistencia.bus.state_bus import StateBus
from .instancias import generar_instancia, guardar, inicializar
from .piloto import SinBitacoraDuplicada
from .resultados import RegistroCorrida, _json, leer_resumen, version_codigo


def ejecutar(salida, cantidades=(500, 750, 1000), semillas=(610, 611, 612), tamanos=(25, 50, 100),
             limite_ticks=100000, incluir_lista_completa=True):
    salida = Path(salida)
    salida.mkdir(parents=True, exist_ok=False)
    protocolo = {"version": 2, "modelo_preparacion": "fisica", "codigo": version_codigo(),
        "cantidades": list(cantidades), "semillas": list(semillas), "tamanos": list(tamanos),
        "incluir_lista_completa": incluir_lista_completa, "reglas": ["original", "urgentes_primero"],
        "generador": {"productos": 20, "ocupacion": 0.65, "robots": 1, "z": 4,
                      "media_urgencia": 0, "desviacion_urgencia": 1, "plazo_base": "automatico_por_geometria_y_N"},
        "limite_ticks": limite_ticks, "intervalo_guardado": 500, "umbral_servicio_altas_criticas": 0.9,
        "nota": "Parámetros sintéticos exploratorios; sin calibración operacional. No excluir fallos."}
    _json(salida / "protocolo.json", protocolo)
    # Guardar todas las entradas antes de evaluar cualquiera de las estrategias.
    instancias = [generar_instancia(cantidad=n, semilla=semilla) for n in cantidades for semilla in semillas]
    for instancia in instancias:
        guardar(instancia, salida / "instancias")
    filas = []
    for instancia in instancias:
        n = len(instancia.solicitudes)
        for tamano in sorted(set(tamanos) | ({n} if incluir_lista_completa else set())):
            for regla in protocolo["reglas"]:
                sim = inicializar(instancia, bus=StateBus(SinBitacoraDuplicada()),
                    opciones_olas={"tamano": tamano, "regla": regla, "modelo_preparacion": "fisica"})
                registro = RegistroCorrida(instancia, sim, salida / "corridas", limite_ticks=limite_ticks,
                                            intervalo_guardado=protocolo["intervalo_guardado"])
                inicio = time.perf_counter()
                try:
                    while not registro.terminal:
                        registro.avanzar()
                except Exception as exc:
                    if not registro.terminal:
                        registro.guardar("error", f"{type(exc).__name__}: {exc}")
                resultado = leer_resumen(salida / "corridas", registro.id)["resultado"]
                traza = registro.directorio / "eventos.jsonl"
                filas.append({"corrida_id": registro.id, "instancia_id": instancia.id,
                    "N": n, "semilla": instancia.semilla, "B": tamano, "regla": regla,
                    "estado": resultado["estado"], "motivo": resultado["motivo"],
                    "entregados": resultado["entregados"], "tiempo": resultado["tiempo_finalizacion"],
                    "ultimo_tick": resultado["tiempo"], "preparacion": resultado["preparacion_total"],
                    "movimientos_preparacion": resultado["movimientos_preparacion"],
                    "movimientos_recuperacion": resultado["movimientos_recuperacion"],
                    "servicio": resultado["cumplimiento_urgentes"],
                    "servicio_por_categoria": resultado["servicio_por_categoria"],
                    "segundos_equipo": time.perf_counter() - inicio, "bytes_traza": traza.stat().st_size})
                _json(salida / "indice.json", filas)
                print(f"{len(filas)}: N={n}, B={tamano}, {regla}: {resultado['estado']} "
                      f"{resultado['entregados']}/{n}; {filas[-1]['segundos_equipo']:.2f}s", flush=True)
    estados = dict(Counter(f["estado"] for f in filas))
    resumen = {"version": 2, "total": len(filas), "estados": estados,
               "segundos_equipo": sum(f["segundos_equipo"] for f in filas), "protocolo": protocolo}
    _json(salida / "resumen.json", resumen)
    grupos = defaultdict(list)
    for fila in filas:
        grupos[(fila["N"], fila["B"], fila["regla"])].append(fila)
    texto = ["# Piloto de preparación física", "", "Instancias sintéticas reproducibles; preparación secuencial dentro del almacén.",
             "Los ticks expresan el reloj del modelo, sin equivalencia calibrada a segundos reales.", "",
             f"Intentos: {len(filas)}. Estados: {estados}.", "",
             "| N | B | Orden | Completas/intentos | Tiempo medio completas | Preparación media completas |",
             "|---:|---:|---|---:|---:|---:|"]
    for (n, b, regla), grupo in sorted(grupos.items()):
        completas = [f for f in grupo if f["estado"] == "completa"]
        media = f"{statistics.mean(f['tiempo'] for f in completas):.1f}" if completas else "—"
        prep = f"{statistics.mean(f['preparacion'] for f in completas):.1f}" if completas else "—"
        texto.append(f"| {n} | {b} | {regla} | {len(completas)}/{len(grupo)} | {media} | {prep} |")
    texto += ["", "Las medias excluyen incompletas y deben interpretarse junto con la proporción de fallos.",
              "Tres semillas constituyen un piloto exploratorio; no permiten una recomendación operacional.",
              "`indice.json` conserva motivos, servicio por clase, costo de ejecución y tamaño de trazas.",
              "Las entradas y sus huellas están en `instancias/`; los eventos y estados en `corridas/`."]
    (salida / "informe_piloto.md").write_text("\n".join(texto) + "\n", encoding="utf-8")
    return resumen


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--salida", type=Path, required=True)
    parser.add_argument("--cantidades", nargs="+", type=int, default=[500, 750, 1000])
    parser.add_argument("--semillas", nargs="+", type=int, default=[610, 611, 612])
    parser.add_argument("--tamanos", nargs="+", type=int, default=[25, 50, 100])
    parser.add_argument("--ticks", type=int, default=100000)
    args = parser.parse_args()
    ejecutar(args.salida, tuple(args.cantidades), tuple(args.semillas), tuple(args.tamanos), args.ticks)
