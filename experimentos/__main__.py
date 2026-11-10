"""Generar y comprobar la instancia de la primera etapa."""
import argparse
import json
from pathlib import Path

from .instancias import cargar, generar_demo, generar_instancia, guardar, inicializar
from .resultados import RegistroCorrida, leer, listar

parser = argparse.ArgumentParser()
parser.add_argument("accion", choices=["generar", "comprobar", "simular", "historial", "resultado"])
parser.add_argument("--carpeta", type=Path, default=Path("output/instancias"))
parser.add_argument("--archivo", type=Path)
parser.add_argument("--semilla", type=int, default=42)
parser.add_argument("--ticks", type=int, default=100000)
parser.add_argument("--cantidad", type=int, help="Formato v2: 1–1000; interfaz ofrece 500–1000")
parser.add_argument("--productos", type=int, default=20)
parser.add_argument("--ocupacion", type=float, default=0.65)
parser.add_argument("--robots", type=int, default=1)
parser.add_argument("--media-urgencia", type=float, default=0)
parser.add_argument("--desviacion-urgencia", type=float, default=1)
parser.add_argument("--plazo-base", type=int)
parser.add_argument("--modelo-preparacion", choices=["fisica", "sintetica"])
parser.add_argument("--tamano", type=int, default=4)
parser.add_argument("--regla", choices=["original", "urgentes_primero"], default="original")
parser.add_argument("--preparacion-fija", type=int, default=0)
parser.add_argument("--preparacion-por-pedido", type=int, default=0)
parser.add_argument("--resultados", type=Path, default=Path("output/corridas"))
parser.add_argument("--ventana-sin-entregas", type=int)
parser.add_argument("--corrida-id")
args = parser.parse_args()
if args.ticks < 1:
    parser.error("--ticks debe ser positivo")
if args.accion == "generar":
    instancia = (generar_demo(args.semilla) if args.cantidad is None else
                 generar_instancia(semilla=args.semilla, cantidad=args.cantidad,
                    productos=args.productos, ocupacion=args.ocupacion, robots=args.robots,
                    media_urgencia=args.media_urgencia, desviacion_urgencia=args.desviacion_urgencia,
                    plazo_base=args.plazo_base))
    print(guardar(instancia, args.carpeta))
elif args.accion == "historial":
    print(json.dumps(listar(args.resultados), ensure_ascii=False))
elif args.accion == "resultado":
    if args.corrida_id is None:
        parser.error("resultado requiere --corrida-id")
    print(json.dumps(leer(args.resultados, args.corrida_id), ensure_ascii=False))
else:
    if args.archivo is None:
        parser.error("comprobar y simular requieren --archivo")
    instancia = cargar(args.archivo)
    opciones = {"tamano": args.tamano, "regla": args.regla,
                "preparacion_fija": args.preparacion_fija,
                "preparacion_por_pedido": args.preparacion_por_pedido}
    if args.modelo_preparacion is not None:
        opciones["modelo_preparacion"] = args.modelo_preparacion
    sim = inicializar(instancia, opciones_olas=opciones if args.accion == "simular" else None)
    if args.accion == "simular":
        registro = RegistroCorrida(instancia, sim, args.resultados, args.ticks, args.ventana_sin_entregas)
        try:
            while not registro.terminal:
                registro.avanzar()
        except Exception:
            print(json.dumps({"corrida_id": registro.id, **leer(args.resultados, registro.id)["resultado"]}))
            raise SystemExit(1)
        print(json.dumps({"corrida_id": registro.id, **leer(args.resultados, registro.id)["resultado"]}))
        raise SystemExit(0 if registro.estado == "completa" else 2)
    inicio = sim.tick
    for _ in range(args.ticks):
        if sim.ola_completa():
            break
        sim.avanzar_tick()
    completa = sim.ola_completa()
    print(json.dumps({"experimento": sim.plan_olas.resumen() if sim.plan_olas else None,
                      "instancia": instancia.id, "estado": "completa" if completa else "horizonte_agotado",
                      "entregados": len(sim.pedidos_completados), "total": len(instancia.solicitudes),
                      "ticks_transcurridos": sim.tick - inicio}))
    raise SystemExit(0 if completa else 2)
