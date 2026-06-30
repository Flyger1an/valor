# Valor Vol-Premium (Deribit Options) — Build Roadmap

**Goal:** harvest the **variance risk premium** (realized vol persistently below implied vol) via
**delta-hedged short-vol on Deribit**, judged by the SAME honest gate as every other family — and never
place a live order. The premium is the most-documented, most-persistent edge in finance, but it is
**crash-prone** (short vol has a fat left tail), so this build must honestly capture the tail and the
hedging frictions, and the gate must demand the edge survives net of them.

**Why it's the big one (#4):** the other structural families — #1 OI-reversion, #2 funding-carry,
#3 liquidation-prints (all shipped + soaking) — reuse the engine's spot-return machinery. Options
can't: they need net-new plumbing the engine has never had — a Deribit connector AND a delta-hedged
P&L / risk model. The **gate itself is reusable** once the strategy emits `(ts, return)`; it's the
plumbing in front of the gate that's greenfield.

**Non-goals (same discipline as the rest of Valor):**
- No live execution — Deribit **testnet only**, no live code path.
- No LLM authority over risk, sizing, execution, or kill switches.
- No **naked** short vol — delta-hedged and tail-aware only.
- No scaling claims from sample data or a single regime.
- **Stop at the human-authorization gate** before any real key or capital. Promotion is human-gated.

**Status at a glance:** Phase 0 ⬜ · 1 ⬜ · 2 ⬜ · 3 ⬜ · 4 ⬜ · 5 ⬜ · 6 ⬜ — *not started.*
(⬜ todo · 🔄 in progress · ✅ done — flip each as work lands; record the commit hash.)

---

## Phase 0 — Observe-first: Deribit data probe ⬜
**Purpose:** confirm the data exists and is usable BEFORE writing plumbing — the discipline that caught
the OKX OI 16:00-UTC alignment, the OKX liquidation `instFamily` param, the Gate funding +1s offset,
and the `universe_vol` crash. Assume nothing; parse against the real response.
**Work:**
- Probe the live Deribit public API from BOTH the sandbox and the droplet (geo + reachability).
- Confirm availability + depth: instruments / option chains, per-strike mark IV, the **DVOL** index,
  the underlying index price, historical option marks (how far back?), funding, order-book spreads.
- Observe the real shapes (fields, units, time conventions).
**Acceptance gates:**
- Reachable from the droplet (not geo-blocked).
- IV surface + a usable history depth confirmed (or a documented data gap + vendor fallback noted).
- A reusable `venue_probe`-style report committed.

## Phase 1 — Deribit connector ⬜ (`evolver/data/deribit.py`)
**Purpose:** a pure-stdlib connector (no numpy on the box) for the vol-premium family's data.
**Work:**
- Fetch: option chain (strikes/expiries), mark IV per option, underlying index, DVOL, historical
  marks/closes, funding.
- A `data_feed_check`-style smoke test, reusable on the droplet.
**Acceptance gates:**
- Connector returns real data, verified live.
- Smoke test passes on the droplet.

## Phase 2 — Options pricing + delta-hedged P&L model ⬜ (the net-new core)
**Purpose:** the honest options backtest engine — the part the spot families never needed.
**Work:**
- Black-Scholes pricing + greeks (delta / gamma / vega / theta), pure-Python.
- A delta-hedged simulator: short an option (or straddle), rehedge to delta-neutral at a configurable
  interval along the underlying path, accrue P&L = vega-weighted (implied − realized) vol spread −
  hedging slippage − fees − gamma cost. **Honest tail capture** (the position's drawdown in a vol spike).
**Acceptance gates:**
- Unit tests vs known Black-Scholes values.
- A synthetic check: the simulator recovers a planted (implied − realized) premium **net of frictions**,
  AND shows the loss in a vol-spike scenario (the tail is real, not hidden).

## Phase 3 — Vol-premium family + gate validation ⬜ (`evolver/optimize/vol_premium.py`)
**Purpose:** a family the gate can judge, validated by the same surface-rate methodology.
**Work:**
- A delta-hedged short-vol family (params: tenor, moneyness, rehedge interval, entry IV-rank threshold,
  exit) emitting `(ts, return)`; wire into the family registry.
- Synthetic thesis test (surface-rate over K cycles): plant a real premium (realized < implied
  persistently) → gate surfaces in the majority; plant no premium / fat-tail noise → gate rejects
  (**never on noise**).
**Acceptance gates:**
- Surface-rate test PASS (planted majority, noise 0/K).
- Survives 2x-cost stress AND a tail/drawdown guard (clears net of crash risk, not just average return).
- Existing families' noise rejection unchanged; `test_core` + `test_stats` green.

## Phase 4 — Demo/testnet-locked Deribit executor ⬜ (`evolver/execution/deribit_executor.py`)
**Purpose:** the dormant-but-ready execution layer, locked to testnet (like okx/oanda executors).
**Work:**
- `test.deribit.com`-locked: place / cancel / positions / flatten / reconcile, with `--check` /
  `--selftest`. No live code path.
**Acceptance gates:**
- Testnet-locked, verified — no implementation can reach the live venue.
- `--selftest` passes.

## Phase 5 — Deploy as a parallel hunt + forward shadow ⬜
**Purpose:** run it as an independent hunt (separate state / queue / multiplicity), exactly like the
Gate venue — never disturbing the running OKX / Gate / FX soak.
**Work:**
- A `deribit-research-runner` (own registry + state + datasets) and a `deribit-shadow-runner` (forward
  track via `candidate_shadow`). Compose services + dataset paths. Forward-feedback wired.
- Observe-first on deploy: confirm the connector works on the box, the family runs end-to-end.
**Acceptance gates:**
- Deployed; first real-data cycle runs clean (honest reject, or a CONFIRM'd candidate Telegram-alerts).
- The existing hunts are untouched (separate runner, separate multiplicity).

## Phase 6 — Soak + readiness (human gate) ⬜
**Purpose:** give the vol premium a fair, honest test across regimes, and decide go/no-go — no real capital.
**Work:**
- Run unattended; accumulate the delta-hedged track record across a **vol regime change** (calm AND a
  vol spike — short vol's whole story is in the tail). Weekly digest + Telegram alerts.
- A go/no-go readiness review (like v0.2's tiny-live-readiness): does it clear the gate AND survive a
  real vol spike in the forward shadow?
**Acceptance gates:**
- ≥ several weeks of forward results spanning a vol regime change.
- An honest go/no-go memo; positive after costs AND tail stress.
- **Nothing promoted to real capital without explicit human sign-off. The build stops here.**

---

**Honest prior (carry it the whole way):** the variance risk premium is the realest target we have —
but it pays you to *insure against crashes*, so it works until it doesn't, and a thin retail
delta-hedged book eats real hedging frictions. The gate's job is to demand the edge survives net of
those frictions AND a tail event before anything surfaces. The most likely honest outcome is still
"not clearable for this setup" — a valid result, not a failure.

## Build log
_(dated entries appended as phases land — newest last)_
- 2026-06-29 — roadmap created; Phase 0 next (Deribit observe-first probe).
