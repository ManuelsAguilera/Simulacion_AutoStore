"""Verificar medidas físicas, persistencia y API con límite de 60 segundos."""
from pathlib import Path
import subprocess
import sys

try:
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_resultados_experimentales.py",
                        "tests/test_olas_experimentales.py", "tests/test_instancias_experimentales.py",
                        "tests/test_api_bridge.py", "tests/test_run_sku_inventory.py", "-q",
                        "-o", "faulthandler_timeout=15"],
                       cwd=Path(__file__).resolve().parents[1], timeout=60)
except subprocess.TimeoutExpired:
    print("La verificación superó 60 segundos.", file=sys.stderr)
    raise SystemExit(124)
raise SystemExit(r.returncode)
