"""Pruebas de preparación física, lotes y regresiones, con límite global."""
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

raiz = Path(__file__).resolve().parents[1]
archivos = ["tests/test_instancias_v2.py", "tests/test_preparacion_fisica.py",
            "tests/test_lotes.py", "tests/test_api_lotes.py",
            "tests/test_auditoria_fisica.py",
            "tests/test_resultados_v2.py", "tests/test_api_v2.py",
            "tests/test_instancias_experimentales.py", "tests/test_olas_experimentales.py",
            "tests/test_resultados_experimentales.py", "tests/test_piloto_experimental.py",
            "tests/test_api_bridge.py", "tests/test_run_sku_inventory.py",
            "bus_persistencia/tests/test_state_bus.py"]
try:
    resultado = subprocess.run([sys.executable, "-m", "pytest", *archivos, "-q", "--tb=short",
        "--basetemp", str(raiz / "output" / f"pytest_fisico_{uuid4().hex}"),
        "-o", "faulthandler_timeout=20"], cwd=raiz, timeout=180)
except subprocess.TimeoutExpired:
    print("La verificación superó 180 segundos; revisar la prueba bloqueada.", file=sys.stderr)
    raise SystemExit(124)
raise SystemExit(resultado.returncode)
