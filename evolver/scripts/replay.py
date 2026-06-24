"""Replay a signals.jsonl through the core loop (deterministic) -> KPI report.

Builds a paper track record fast for Phase 2, and produces the exact input the
optimizer (Optuna walk-forward) consumes. Reuses run_strategy so replay and
optimization can never diverge. Pure stdlib (no LLM, no network).

    python3 scripts/replay.py --signals /tmp/backfill_signals.jsonl
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evolver.config import DEFAULT_LIMITS, DEFAULT_STRATEGY  # noqa: E402
from evolver.optimize.backtest import run_strategy            # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", required=True)
    args = ap.parse_args()
    signals = [json.loads(line) for line in pathlib.Path(args.signals).read_text().splitlines() if line.strip()]
    print(f"replaying {len(signals)} signals through the core loop (deterministic)...")
    res = run_strategy(signals, DEFAULT_STRATEGY, DEFAULT_LIMITS)
    print(json.dumps(res["kpis"], indent=2))
    print(f"\nfinal equity: ${res['equity']:,.2f} | halt: {res['halt']}")


if __name__ == "__main__":
    main()
