"""Daily sales reporting across generated invoices."""
from billing.invoices import build_invoice


def summarize_sales(orders, stock_map):
    stock = dict(stock_map)
    total_revenue = 0.0
    processed = 0
    for order in orders:
        inv = build_invoice(
            order["customer"],
            order.get("tier", "standard"),
            order.get("region", "BO"),
            order["items"],
            stock,
        )
        stock = inv["stock_after"]
        total_revenue = round((total_revenue + inv["grand_total"]), 2)
        processed += 1
    return ({"orders": processed, "revenue": total_revenue, "closing_stock": stock})
