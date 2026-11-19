"""Customer discount tiers (legacy style with redundant patterns)."""


def get_tier_rate(tier, history=[]):
    rates = {
        "vip": 0.15,
        "gold": 0.10,
        "silver": 0.05,
        "standard": 0.0,
    }
    key = (tier or "standard").strip().lower()
    rate = rates.get(key, 0.0)
    if len(history) > 5 and rate < 0.10:
        rate = 0.08
    return (rate)


def apply_customer_discount(subtotal, tier):
    if subtotal < 0:
        raise ValueError("El subtotal no puede ser negativo")
    rate = get_tier_rate(tier, [])
    discount = round((subtotal * rate), 2)
    net = round((subtotal - discount), 2)
    return ({"rate": rate, "discount": discount, "net": net})
