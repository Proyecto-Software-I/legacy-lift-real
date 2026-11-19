"""Inventory catalog and stock checks."""


def calculate_line_total(unit_price, quantity):
    if unit_price < 0 or quantity <= 0:
        raise ValueError("Precio y cantidad deben ser positivos")
    return (round((unit_price * quantity), 2))


def check_stock(sku, requested, stock_map):
    available = stock_map.get(sku, 0)
    if requested <= 0:
        raise ValueError("Cantidad solicitada inválida")
    enough = available >= requested
    remaining = available - requested if enough else available
    return ({"sku": sku, "available": available, "ok": enough, "remaining": remaining})


def reserve_items(items, stock_map):
    updated = dict(stock_map)
    for item in items:
        sku = item["sku"]
        qty = item["qty"]
        status = check_stock(sku, qty, updated)
        if not status["ok"]:
            raise ValueError(f"Stock insuficiente para {sku}")
        updated[sku] = status["remaining"]
    return (updated)
