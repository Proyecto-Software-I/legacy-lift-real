import unittest
from customers.tiers import apply_customer_discount, get_tier_rate


class CustomerTiersTests(unittest.TestCase):
    def test_vip_discount_calculation(self):
        result = apply_customer_discount(200.0, "vip")
        self.assertEqual(result["rate"], 0.15)
        self.assertEqual(result["discount"], 30.0)
        self.assertEqual(result["net"], 170.0)

    def test_standard_tier_defaults_to_zero(self):
        self.assertEqual(get_tier_rate(None), 0.0)
        res = apply_customer_discount(100.0, "unknown")
        self.assertEqual(res["net"], 100.0)

    def test_negative_subtotal_raises(self):
        with self.assertRaises(ValueError):
            apply_customer_discount(-10.0, "gold")
