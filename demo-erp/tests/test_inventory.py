import unittest
from inventory.catalog import calculate_line_total, check_stock, reserve_items


class InventoryCatalogTests(unittest.TestCase):
    def test_calculate_line_total(self):
        self.assertEqual(calculate_line_total(19.99, 2), 39.98)

    def test_check_stock_sufficient_and_insufficient(self):
        ok = check_stock("SKU-1", 3, {"SKU-1": 5})
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["remaining"], 2)
        fail = check_stock("SKU-1", 6, {"SKU-1": 5})
        self.assertFalse(fail["ok"])

    def test_reserve_items_raises_on_shortage(self):
        with self.assertRaises(ValueError):
            reserve_items([{"sku": "SKU-1", "qty": 10}], {"SKU-1": 4})
