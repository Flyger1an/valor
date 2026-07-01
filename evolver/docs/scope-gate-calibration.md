# Scope — is the gate mis-calibrated for our (retail) regime?

**The worry (from the user):** our gate is calibrated for institutional-grade confidence, and may be
systematically rejecting small-but-real, capacity-constrained edges that are exactly the retail niche.

**The danger:** loosening the gate is the precise failure mode the gate exists to prevent. So this scope
refuses any "just relax it" move and instead asks the sharp question, then answers it with a forward,
risk-free **measurement** — never a production relaxation.

---

## The actual gate (grounded in `scripts/research_tick.py:636`)

Seven clauses must ALL hold, plus `CONFIRM=2` across non-overlapping windows ≥7 days apart:

```python
passed = (osr > min_osr        # 1  OOS holdout Sharpe > per-family economic floor
          and op < 0.05        # 2  bootstrap p-value < 5%
          and on >= min_n      # 3  OOS trade count >= per-family floor
          and o2 > 0           # 4  survives 2× costs
          and dho > 0.95       # 5  deflated (multiplicity-corrected) Sharpe > 95%
          and npos >= 0.75·nb  # 6  parameter stability: 75% of neighbours positive
          and pbo < 0.5)       # 7  prob. of backtest overfitting < 50%
```

## Tunable (decision-theoretic) vs sacrosanct (anti-self-fooling)

| clause | protects | verdict |
|---|---|---|
| `op < 0.05` | false-positive tolerance α (bootstrap) | **TUNABLE — a real knob** |
| `dho > 0.95` | same α, on the multiplicity-corrected Sharpe | **TUNABLE — a real knob** |
| `min_osr`, `min_n` | economic floor, sample floor | already per-family-adapted; not the culprit |
| `o2 > 0` (2× cost) | execution-cost robustness | **SACROSANCT** (and see headwind) |
| stability (6), `pbo` (7), the DSR *deflation mechanism* | overfitting / fragility | **SACROSANCT — moving these = self-deception** |

**The entire legitimate question reduces to two numbers: α = `0.05` and `0.95`.** They encode "how
confident must we be before we believe it" — which genuinely depends on the loss function. An institution
sizing $100M needs a tiny false-positive rate; a paper-first operator with bounded size and ongoing
forward CONFIRM has a less asymmetric loss and *might* rationally tolerate a higher α.

## Why it can't be settled by argument

The decision-theory pull (our loss function tolerates higher α) **collides** with the Bayesian pull: our
prior that *no edge exists* is strong, and against a strong "no edge" prior a higher α floods us with
false positives. That collision depends on the true P(edge) and forward-decay, which we don't know — so
it can only be settled **forward, empirically.**

## The honest headwind (don't paper over)

Small edges live in **illiquid corners where our costs are higher, not lower.** The `2× cost` clause —
the one most likely to kill a marginal edge — is therefore *correctly* harsh for us specifically and must
never move. The retail-capacity thesis cuts both ways.

---

## The experiment — a shadow bar, adjudicated forward

- **Phase A** ⬜→🔄 — each cycle, compute a *second* verdict `shadow_passed` with ONLY the two α knobs
  relaxed (`op < EVOLVER_SHADOW_P=0.15`, `dho > EVOLVER_SHADOW_DSR=0.80`); **every overfitting/robustness
  clause frozen identical.** It NEVER creates a candidate, alerts, or trades. It logs the **marginal band**
  (passes relaxed, fails strict) to a calibration ledger (`EVOLVER_SHADOW_LEDGER`). Pure telemetry; the
  production `passed`/candidate path is byte-for-byte unchanged.
- **Phase B** ⬜ — run marginal-band genomes through the SAME CONFIRM machinery, but *stricter* (relaxation
  on the historical axis repaid on the forward axis).
- **Phase C** ⬜ — after ~1–2 months: do marginal genomes CONFIRM forward, or evaporate? Compare confirm
  rates strict-vs-shadow. Evidence, not opinion.
- **Phase D** ⬜ — ONLY if C shows real over-rejection: propose a calibrated, evidence-based α change, back
  to the user. Never automatic.

## Guardrails (non-negotiable)

- Production `passed`, candidate alerts, and the queue stay **byte-for-byte unchanged**.
- Only the two α thresholds relax in the shadow copy; PBO, 2× cost, stability, the DSR deflation
  mechanism, and min_n are **frozen**.
- Nothing off the shadow bar ever trades. It is a measurement.

## The other half — capacity (separate, additive, lower priority)

The gate has **no liquidity/capacity model** — it can't tell a real edge in a fillable corner from
small-sample noise. The proper fix is *additive* (estimate fillable notional per candidate from venue
depth/volume), not a loosening. Deferred — we have zero candidates to size.

---

### Build log
- 2026-06-30 — scope written; **Phase A** building (shadow-logging only, zero behaviour change).
