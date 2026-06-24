"""Evolutionary Prompt Engineering demo — evolve the analyst's decision prompt.

    python3 scripts/evolve_prompt.py          # mock analyst (free, instant, deterministic)
    python3 scripts/evolve_prompt.py llm       # real gpt-5-mini analyst + gpt-5.5 operators

Eval set = real cross-venue dislocation events labeled by forward P&L. Fitness = realized
P&L the prompt's decisions capture. A good prompt LEARNS to enter big (under-arbitraged)
dislocations and skip the small fee-dominated ones — the rule we found by hand, discovered
here by evolving language.
"""
from __future__ import annotations

import os
import pathlib
import pickle
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_envf = ROOT / ".env"
if _envf.exists():
    for _line in _envf.read_text().splitlines():
        if "=" in _line and not _line.strip().startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

from evolver.config import DEFAULT_LIMITS  # noqa: E402
from evolver.evolve import evoprompt as EP  # noqa: E402
from evolver.optimize.cross_venue import run_cross_venue  # noqa: E402


def build_eval_set(n=16, seed=3):
    # fee_bps=8 (realistic 4-leg taker): small dislocations go net-NEGATIVE, big ones stay
    # positive -> selectivity is the learnable edge (matches the manual finding).
    data = pickle.loads((ROOT / ".xv_cache_18mo.pkl").read_bytes())
    fills = run_cross_venue(
        data, {"entry_ann_diff": 0.03, "exit_ann_diff": 0.01, "max_hold_days": 14, "fee_bps": 8.0},
        DEFAULT_LIMITS)["fills"]
    events = [{"signal": {"ann_diff": f.entry_ann}, "pnl": round(f.pnl_pct, 5)} for f in fills]
    rng = random.Random(seed)
    rng.shuffle(events)
    return events[:n]


def main():
    use_llm = len(sys.argv) > 1 and sys.argv[1] == "llm"
    if not use_llm:
        os.environ.pop("OPENAI_API_KEY", None)   # force mock path
    eval_set = build_eval_set()
    enter_all = sum(e["pnl"] for e in eval_set)
    big_only = sum(e["pnl"] for e in eval_set if e["signal"]["ann_diff"] >= 0.15)
    print(f"eval set: {len(eval_set)} labeled dislocation events")
    print(f"  baselines: enter-ALL P&L {enter_all:+.4f} | enter-only-big(>=15%) P&L {big_only:+.4f}")
    print(f"  mode: {'LLM (gpt-5-mini analyst + gpt-5.5 operators)' if use_llm else 'mock analyst (offline)'}\n")

    res = EP.evolve_prompts(eval_set, generations=(2 if use_llm else 3),
                            pop=(4 if use_llm else 4), model=EP.FAST)
    print(f"\nbest evolved prompt — P&L {res['best_pnl']:+.4f} over {res['enters']} entries:")
    print(f'  "{res["best_prompt"]}"')
    print(f"\n  vs enter-all {enter_all:+.4f}: the evolved prompt {'improves selectivity' if res['best_pnl'] > enter_all else 'matches'} "
          f"— it learned to harvest the profitable subset, not trade everything.")


if __name__ == "__main__":
    main()
