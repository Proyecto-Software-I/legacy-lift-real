import unittest
from billing.invoices import build_invoice
from billing.taxes import compute_tax
from reports.daily_summary import summarize_sales


class BillingInvoicesTests(unittest.TestCase):
    def test_compute_tax_bo(self):
        taxed = compute_tax(100.0, "BO")
        self.assertEqual(taxed["tax"], 13.0)
        self.assertEqual(taxed["total"], 113.0)

    def test_build_invoice_vip_updates_stock(self):
        inv = build_invoice(
            "ACME",
            "vip",
            "BO",
            [{"sku": "A1", "price": 100.0, "qty": 2}],
            {"A1": 5},
        )
        self.assertEqual(inv["subtotal"], 200.0)
        self.assertEqual(inv["discount"], 30.0)
        self.assertEqual(inv["net"], 170.0)
        self.assertEqual(inv["tax"], 22.1)
        self.assertEqual(inv["grand_total"], 192.1)
        self.assertEqual(inv["stock_after"]["A1"], 3)

    def test_build_invoice_empty_items_rejected(self):
        with self.assertRaises(ValueError):
            build_invoice("ACME", "vip", "BO", [], {"A1": 5})

    def test_daily_summary_multiple_orders(self):
        res = summarize_sales(
            [
                {"customer": "Uno", "tier": "gold", "region": "EXPORT", "items": [{"sku": "S1", "price": 50.0, "qty": 2}]},
                {"customer": "Dos", "tier": "standard", "region": "US", "items": [{"sku": "S2", "price": 100.0, "qty": 1}]},
            ],
            {"S1": 4, "S2": 2},
        )
        self.assertEqual(res["orders"], 2)
        self.assertEqual(res["revenue"], 197.0)
        self.assertEqual(res["closing_stock"], {"S1": 2, "S2": 1})
