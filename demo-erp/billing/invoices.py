"""Core invoice builder orchestrating customers, inventory and billing taxes."""
from customers.tiers import apply_customer_discount
from inventory.catalog import calculate_line_total, reserve_items
from billing.taxes import compute_tax


def build_invoice(customer_name, tier, region, items, stock_map):
    if not items:
        raise ValueError("La factura requiere al menos un ítem")
    new_stock = reserve_items(items, stock_map)
    lines = []
    subtotal = 0.0
    for item in items:
        line_total = calculate_line_total(item["price"], item["qty"])
        subtotal = round((subtotal + line_total), 2)
        lines.append({"sku": item["sku"], "qty": item["qty"], "line_total": line_total})
    discounted = apply_customer_discount(subtotal, tier)
    taxed = compute_tax(discounted["net"], region)
    return ({
        "customer": customer_name,
        "tier": tier,
        "lines": lines,
        "subtotal": subtotal,
        "discount": discounted["discount"],
        "net": discounted["net"],
        "tax": taxed["tax"],
        "grand_total": taxed["total"],
        "stock_after": new_stock,
    })
