"""Two-stage gate: evolve PROPOSES (cheap, daily), confirm DISPOSES (expensive, honest).

Tonight's lesson, encoded: a daily-validated genome is NOT a candidate until it also clears
recent-period significance AND execution-realistic (intraday) significance with breadth. This
is the exact manual check that caught the cross-venue genome being borderline (intraday p=0.094)
rather than the clean pass its daily DSR implied. The engine proposes; this stage refuses.
"""
from __future__ import annotations


def confirm(genomes, validate, log=print):
    """genomes: gate-passing scorecards. validate(params) -> dict with recent_sharpe/recent_p,
    intraday_sharpe/intraday_p, breadth. Returns the survivors as (genome, validation)."""
    survivors = []
    for g in genomes:
        v = validate(g.params)
        ok = (v.get("recent_p", 1.0) < 0.05 and v.get("intraday_p", 1.0) < 0.05
              and v.get("intraday_sharpe", -9) > 0 and v.get("breadth", 0.0) >= 0.6)
        log(f"  confirm {g.params}:")
        log(f"    daily-recent  sharpe {v.get('recent_sharpe'):+.3f} (p {v.get('recent_p'):.3f})")
        log(f"    intraday-exec sharpe {v.get('intraday_sharpe'):+.3f} (p {v.get('intraday_p'):.3f}) "
            f"| breadth {v.get('breadth'):.2f}  ->  {'CONFIRMED ✅' if ok else 'rejected (not yet tradeable)'}")
        if ok:
            survivors.append((g, v))
    return survivors
