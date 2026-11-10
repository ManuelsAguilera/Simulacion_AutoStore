"""La preparación de pedidos conserva la ocupación y respeta la demanda."""

from types import SimpleNamespace

from bus_persistencia.models.state import Caja, Config, GrillaDimensions, Pedido
from motor.grilla import Grilla
from motor.run import _asegurar_cajas_para_skus


def test_asegurar_skus_reemplaza_cajas_sin_reutilizarlas():
    grilla = Grilla(Config(GrillaDimensions(3, 3, 1), robots=3, ocupacion_inicial=0))
    for indice, sku in enumerate(("SKU-A", "SKU-X", "SKU-X")):
        grilla.agregar(Caja(f"C{indice}", sku, 1, 2 + indice, 2, 0))

    pedidos = [
        Pedido("P1", "SKU-A", 1, "Tienda"),
        Pedido("P2", "SKU-A", 1, "Tienda"),
        Pedido("P3", "SKU-B", 1, "Tienda"),
    ]
    sim = SimpleNamespace(_grilla=grilla, pedidos_cola=pedidos)

    _asegurar_cajas_para_skus(sim)

    cajas = list(grilla._celdas.values())
    assert len(cajas) == 3
    assert {c.id_caja for c in cajas} == {"C0", "C1", "C2"}
    assert sorted(c.id_sku for c in cajas) == ["SKU-A", "SKU-A", "SKU-B"]
    assert {(c.x, c.y, c.z) for c in cajas} == {(2, 2, 0), (3, 2, 0), (4, 2, 0)}
