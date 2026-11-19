"""Tax rules per region."""

REGION_TAX = {
    "BO": 0.13,
    "CL": 0.19,
    "US": 0.07,
    "EXPORT": 0.0,
}


def compute_tax(amount, region="BO"):
    if amount < 0:
        raise ValueError("El monto gravable no puede ser negativo")
    rate = REGION_TAX.get((region or "BO").upper(), 0.13)
    tax = round((amount * rate), 2)
    return ({"region": (region or "BO").upper(), "rate": rate, "tax": tax, "total": round((amount + tax), 2)})
