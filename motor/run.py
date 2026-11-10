"""motor/run.py — Standalone runner para simulación M2 sin UI ni Omniverse.

Uso:
    python -m motor.run --policy fifo --ticks 100
    python -m motor.run --policy prioridad_posicion --ticks 200 --seed 42
    python -m motor.run --compare  # ejecuta ambas políticas y genera reporte
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from bus_persistencia.bus.state_bus import StateBus
from bus_persistencia.models.state import Caja, KPISet, ModoTurno, Pedido, PoliticaPicking, Robot
from bus_persistencia.persistence.config_loader import load_config
from bus_persistencia.persistence.execution_metadata import (
    ExecutionMetadata,
    MetadataStore,
    create_execution_metadata,
    file_hash,
)
from bus_persistencia.persistence.ola_loader import load_ola
from bus_persistencia.persistence.session_logger import SessionLogger
from motor.dashboard import RICH_AVAILABLE, mostrar_tick_realtime
from motor.simulador import AutoStoreSimulator

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"


_POOL_SKUS = [f"SKU-{chr(65 + i)}" for i in range(10)]  # SKU-A .. SKU-J
_POOL_DESTINOS = [
    "Tienda_01", "Tienda_02", "Tienda_03", "Tienda_04", "Tienda_05",
    "Ecommerce_01", "Ecommerce_02", "Ecommerce_03",
]


def generar_ola_aleatoria(gx: int, gy: int, seed: int) -> list[Pedido]:
    """Genera una ola de pedidos aleatoria proporcional al tamaño de la grilla.

    Cantidad de pedidos: max(4, gx * gy // 5).
    SKUs elegidos del pool SKU-A..SKU-J; cantidades entre 1 y 3.
    Determinista para la misma seed.
    """
    rng = random.Random(seed)
    n_pedidos = max(4, (gx * gy) // 5)
    pedidos: list[Pedido] = []
    for i in range(1, n_pedidos + 1):
        pedidos.append(Pedido(
            id_pedido=f"P{i:04d}",
            id_sku=rng.choice(_POOL_SKUS),
            cantidad=rng.randint(1, 3),
            destino=rng.choice(_POOL_DESTINOS),
        ))
    return pedidos


def _asegurar_cajas_para_skus(sim: AutoStoreSimulator) -> None:
    """Sustituye cajas sobrantes por SKUs demandados sin cambiar la ocupación."""
    if not sim.pedidos_cola:
        return

    demanda_por_sku: dict[str, int] = {}
    for p in sim.pedidos_cola:
        demanda_por_sku[p.id_sku] = demanda_por_sku.get(p.id_sku, 0) + 1

    cajas_existentes = list(sim._grilla._celdas.values())
    existentes_por_sku: dict[str, int] = {}
    for c in cajas_existentes:
        existentes_por_sku[c.id_sku] = existentes_por_sku.get(c.id_sku, 0) + 1

    # Consumir primero SKUs no pedidos; después, excedentes de SKUs pedidos.
    disponibles = sorted(
        cajas_existentes,
        key=lambda c: (c.id_sku in demanda_por_sku, c.x, c.y, c.z, c.id_caja),
    )
    for sku in sorted(demanda_por_sku):
        faltan = demanda_por_sku[sku] - existentes_por_sku.get(sku, 0)
        for caja in disponibles[:]:
            if faltan <= 0:
                break
            origen = caja.id_sku
            if existentes_por_sku[origen] <= demanda_por_sku.get(origen, 0):
                continue
            sim._grilla.agregar(replace(caja, id_sku=sku, cantidad=1))
            disponibles.remove(caja)
            existentes_por_sku[origen] -= 1
            existentes_por_sku[sku] = existentes_por_sku.get(sku, 0) + 1
            faltan -= 1

        if faltan > 0:
            print(f"  Aviso: no hay cajas para reemplazar SKU {sku}; faltan {faltan}")

def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def ejecutar_sesion(
    config_path: str | Path,
    ola_path: str | Path,
    politica: str,
    output_dir: str | Path = OUTPUT_DIR,
    session_name: str | None = None,
    seed: int = 42,
    max_ticks: int = 100,
    verbose: bool = True,
    realtime: bool = False,
    delay_ms: float = 0,
    use_rich: bool = True,
    use_ansi_clear: bool = True,
    show_matrix: bool = False,
    random_ola: bool = False,
) -> dict:
    """Ejecuta una sesión de simulación completa y retorna resultados."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    if session_name is None:
        session_name = f"{politica}_s{seed}"

    # --- Cargar datos ---
    config_result = load_config(config_path)
    if not config_result.is_valid:
        raise SystemExit(f"Error en config: {config_result.errors}")

    if random_ola:
        gx = config_result.data.grilla.x
        gy = config_result.data.grilla.y
        pedidos = generar_ola_aleatoria(gx, gy, seed)
    else:
        ola_result = load_ola(ola_path)
        if not ola_result.is_valid:
            raise SystemExit(f"Error en ola: {ola_result.errors}")
        pedidos = ola_result.data

    # --- Configurar bus ---
    logger = SessionLogger(output_dir=output, session_name=session_name)
    bus = StateBus(session_logger=logger)
    bus.set_config(config_result.data)
    bus.set_pedidos_cola(pedidos)
    bus.set_policy(politica)

    # --- Configurar metadata ---
    if random_ola:
        meta = ExecutionMetadata(
            nombre_ejecucion=session_name, semilla=seed,
            modo="diurno", politica=politica,
            config_path=str(config_path), data_path="random",
            config_hash=file_hash(config_path), data_hash=f"random_seed_{seed}",
        )
    else:
        meta = create_execution_metadata(
            session_name, seed, "diurno", politica,
            config_path, ola_path,
        )

    # --- Inicializar simulador ---
    sim = AutoStoreSimulator(bus)
    sim.inicializar_desde_bus(seed=seed)

    # --- Asegurar cajas para los SKUs de pedidos ---
    _asegurar_cajas_para_skus(sim)

    logger.write_metadata_header(meta.to_dict())

    if verbose:
        n_robots = len(sim.robots)
        n_pedidos = len(sim.pedidos_cola)
        print(f"  Grilla: {sim._grilla.config.grilla.x}×{sim._grilla.config.grilla.y}×{sim._grilla.config.grilla.z}")
        print(f"  Cajas iniciales: {sim._grilla.total_cajas}")
        print(f"  Robots: {n_robots}")
        print(f"  Pedidos: {n_pedidos}" + (" (aleatorios)" if random_ola else ""))
        print(f"  Política: {politica}")
        print(f"  Semilla: {seed}")
        print()

    # --- Ciclo de simulación ---
    t_start = time.perf_counter()
    if realtime:
        if RICH_AVAILABLE and use_rich:
            print("  [EN VIVO] Modo Rich (^C para detener)")
        else:
            print("  [EN VIVO] Modo ANSI (^C para detener)")
        if delay_ms > 0:
            print(f"  Delay: {delay_ms:.0f}ms entre ticks")
        print()
        time.sleep(0.5)

    for t in range(1, max_ticks + 1):
        sim.avanzar_tick()

        if realtime:
            mostrar_tick_realtime(
                sim, t, max_ticks,
                use_rich=use_rich,
                use_ansi_clear=use_ansi_clear,
                show_matrix=show_matrix,
            )
            if delay_ms > 0:
                time.sleep(delay_ms / 1000)
        elif verbose and t % 10 == 0:
            k = sim.kpis
            print(f"  Tick {t:>4} | TSP={k.TSP:6.2f}%  MTRP={k.MTRP:7.2f}  TBR={k.TBR:6.2f}%  IOG={k.IOG:5.1f}%")

        if sim.ha_terminado():
            if verbose:
                print(f"  [+] Sesión completada en {t} ticks")
            break

    elapsed = time.perf_counter() - t_start

    # --- Finalizar ---
    logger.flush_session()

    snap = bus.read_snapshot()
    meta.kpis_finales = snap.kpis.as_dict()
    MetadataStore(output / "metadata").save(meta)

    resultado = {
        "session_name": session_name,
        "tick_final": snap.tick,
        "pedidos_completados": len(snap.pedidos.completados),
        "pedidos_totales": len(snap.pedidos.cola) + len(snap.pedidos.completados),
        "KPIs": snap.kpis.as_dict(),
        "politica": politica,
        "seed": seed,
        "tiempo_seg": elapsed,
        "output_dir": str(output),
    }

    if verbose:
        print()
        print("  -------- RESULTADOS --------")
        print(f"  Ticks:        {resultado['tick_final']}")
        print(f"  Pedidos:      {resultado['pedidos_completados']}/{resultado['pedidos_totales']}")
        print(f"  Tiempo:       {elapsed:.2f}s")
        print()
        print(f"  {'KPI':<8} {'Valor':<12}")
        print(f"  {'--------':<8} {'------------':<12}")
        for k, v in snap.kpis.as_dict().items():
            print(f"  {k:<8} {v:<12.2f}")
        print()
        print(f"  Salida: {output / f'sesion_{session_name}.csv'}")

    return resultado


def generar_reporte_comparativo(
    resultado_a: dict,
    resultado_b: dict,
    output_dir: str | Path = OUTPUT_DIR,
) -> Path:
    """Genera reporte_comp.csv comparando dos ejecuciones."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "reporte_comp.csv"

    kpis_a = resultado_a["KPIs"]
    kpis_b = resultado_b["KPIs"]
    nombre_a = f"{resultado_a['politica']}_s{resultado_a['seed']}"
    nombre_b = f"{resultado_b['politica']}_s{resultado_b['seed']}"

    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["KPI", "FIFO_75", "Prioridad_90", "Delta_%"])
        for kpi_name in ("TSP", "TPCP", "MTRP", "IOG", "TR", "TI", "TBR"):
            va = kpis_a.get(kpi_name, 0.0)
            vb = kpis_b.get(kpi_name, 0.0)
            delta = ((vb - va) / va * 100) if va != 0 else 0.0
            sign = "+" if delta >= 0 else ""
            w.writerow([kpi_name, f"{va:.2f}", f"{vb:.2f}", f"{sign}{delta:.2f}%"])

    print(f"  Reporte: {path}")
    return path


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="AutoStore Simulator — Motor M2 (standalone, sin UI ni Omniverse)",
    )
    p.add_argument("--config", default=str(DATA_DIR / "config.json"),
                    help="Ruta a config.json")
    p.add_argument("--ola", default=str(DATA_DIR / "ola.csv"),
                    help="Ruta a ola.csv")
    p.add_argument("--output", default=str(OUTPUT_DIR),
                    help="Directorio de salida")
    p.add_argument("--ticks", type=int, default=100,
                    help="Máximo de ticks a simular")
    p.add_argument("--seed", type=int, default=42,
                    help="Semilla para reproducibilidad")
    p.add_argument("--policy", default="fifo",
                    choices=["fifo", "prioridad_posicion"],
                    help="Política de picking")
    p.add_argument("--compare", action="store_true",
                    help="Ejecuta ambas políticas y genera reporte comparativo")
    p.add_argument("--quiet", action="store_true",
                    help="Modo silencioso (solo muestra resultado final)")
    p.add_argument("--realtime", "-r", action="store_true",
                    help="Modo en vivo: muestra cada tick con estado de robots")
    p.add_argument("--delay", type=float, default=0,
                    help="Milisegundos de espera entre ticks en modo realtime (default: 0)")
    p.add_argument("--no-rich", action="store_true",
                    help="Usa ANSI puro en lugar de rich (más ligero)")
    p.add_argument("--no-clear", action="store_true",
                    help="No limpia la pantalla entre ticks (DEBUG)")
    p.add_argument("--matrix", action="store_true",
                    help="Incluye vista de matriz 2D de la grilla en tiempo real")
    p.add_argument("--random", action="store_true",
                    help="Genera ola de pedidos aleatoria (proporcional a la grilla, usa --seed)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # realtime implica verbose, ignorar --quiet
    if args.realtime:
        args.quiet = False

    use_rich = not args.no_rich and RICH_AVAILABLE
    use_ansi_clear = not args.no_clear
    show_matrix = args.matrix
    random_ola = args.random

    if args.compare:
        print("=== Ejecutando Demo FIFO (75%) ===")
        print()
        res_a = ejecutar_sesion(
            args.config, args.ola,
            "fifo",
            output_dir=args.output,
            session_name="fifo_s75",
            seed=args.seed,
            max_ticks=args.ticks,
            verbose=not args.quiet,
            realtime=args.realtime,
            delay_ms=args.delay,
            use_rich=use_rich,
            use_ansi_clear=use_ansi_clear,
            show_matrix=show_matrix,
            random_ola=random_ola,
        )
        print()
        print("=== Ejecutando Demo Prioridad (90%) ===")
        print()
        res_b = ejecutar_sesion(
            args.config, args.ola,
            "prioridad_posicion",
            output_dir=args.output,
            session_name="prioridad_s90",
            seed=args.seed,
            max_ticks=args.ticks,
            verbose=not args.quiet,
            realtime=args.realtime,
            delay_ms=args.delay,
            use_rich=use_rich,
            use_ansi_clear=use_ansi_clear,
            show_matrix=show_matrix,
            random_ola=random_ola,
        )
        print()
        print("=== Generando reporte comparativo ===")
        generar_reporte_comparativo(res_a, res_b, args.output)
        print()
        print("  Demo completada. Revisar output/ para resultados.")
    else:
        politica = args.policy
        ejecutar_sesion(
            args.config, args.ola,
            politica,
            output_dir=args.output,
            session_name=f"{args.policy}_s{args.seed}",
            seed=args.seed,
            max_ticks=args.ticks,
            verbose=not args.quiet,
            realtime=args.realtime,
            delay_ms=args.delay,
            use_rich=use_rich,
            use_ansi_clear=use_ansi_clear,
            show_matrix=show_matrix,
            random_ola=random_ola,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
