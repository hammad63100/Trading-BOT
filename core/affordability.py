"""Account-currency affordability checks; no broker or trading side effects."""
import math


def affordability(equity, risk_pct, minimum_volume, loss_per_lot, cost_per_lot=0.0):
    values = (equity, risk_pct, minimum_volume, loss_per_lot)
    if not all(math.isfinite(x) and x > 0 for x in values) or not math.isfinite(cost_per_lot) or cost_per_lot < 0:
        raise ValueError('Invalid affordability inputs')
    minimum_loss = minimum_volume * (loss_per_lot + cost_per_lot)
    budget = equity * risk_pct
    return dict(affordable=minimum_loss <= budget, budget=budget,
                minimum_loss=minimum_loss, minimum_equity=minimum_loss / risk_pct,
                minimum_risk_pct=minimum_loss / equity)
