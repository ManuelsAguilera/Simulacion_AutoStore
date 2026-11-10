"""Verificación acotada: devuelve 124 si las pruebas exceden 45 segundos."""
from pathlib import Path
import subprocess
import sys

try:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_instancias_experimentales.py",
         "tests/test_api_bridge.py", "tests/test_run_sku_inventory.py", "-q",
         "-o", "faulthandler_timeout=15"],
        cwd=Path(__file__).resolve().parents[1], timeout=45,
    )
except subprocess.TimeoutExpired:
    print("La verificación superó 45 segundos.", file=sys.stderr)
    raise SystemExit(124)
raise SystemExit(result.returncode)
