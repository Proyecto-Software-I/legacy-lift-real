"""Main entrypoint for Legacy ERP Billing demo."""
from reports.daily_summary import summarize_sales


def run_demo():
    stock = {"SKU-100": 10, "SKU-200": 5}
    orders = [
        {"customer": "ACME SA", "tier": "vip", "region": "BO", "items": [{"sku": "SKU-100", "price": 100.0, "qty": 2}]},
        {"customer": "Nova Ltda", "tier": "standard", "region": "EXPORT", "items": [{"sku": "SKU-200", "price": 50.0, "qty": 1}]},
    ]
    return summarize_sales(orders, stock)


if __name__ == "__main__":
    print(run_demo())
