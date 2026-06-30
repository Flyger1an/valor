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

**Status at a glance:** Phase 0 ✅ · 1 ✅ · 2 ✅ · 3 ⬜ · 4 ⬜ · 5 ⬜ · 6 ⬜ — *Phase 3 (vol-premium family + gate validation) next.*
(⬜ todo · 🔄 in progress · ✅ done — flip each as work lands; record the commit hash.)

---

## Phase 0 — Observe-first: Deribit data probe ✅ (`scripts/deribit_probe.py`)
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

**✅ Found (2026-06-29, `scripts/deribit_probe.py`, verified live on sandbox AND droplet — 200, no
geo-block):** `btc_usd` index live; **868 live BTC options** across 12 expiries with `mark_iv` + full
greeks (delta/gamma/vega/theta/rho); **DVOL daily history ~1000 days (~2.7yr)** + hourly recent (the
implied side); **BTC price daily history ~1096 days (3yr)** (the realized side). Both span multiple vol
regimes → an honest DVOL-vs-realized backtest is feasible now, with greeks ready for the delta-hedged
version. **Data feasibility CONFIRMED.**

## Phase 1 — Deribit connector ✅ (`evolver/data/deribit.py`)
**Purpose:** a pure-stdlib connector (no numpy on the box) for the vol-premium family's data.
**Work:**
- Fetch: option chain (strikes/expiries), mark IV per option, underlying index, DVOL, historical
  marks/closes, funding.
- A `data_feed_check`-style smoke test, reusable on the droplet.
**Acceptance gates:**
- Connector returns real data, verified live.
- Smoke test passes on the droplet.

**✅ Done (2026-06-29, `evolver/data/deribit.py`):** pure-stdlib connector — `dvol_history` (implied,
~1000d), `price_history` (realized, ~1000d), `option_chain` (868 live), `option_ticker` (mark_iv +
greeks), `index_price`. Verified live on every function for **BTC AND ETH**; same endpoints Phase 0
confirmed reachable from the droplet. **Functional-not-scaffold check:** on 33 months of live data the
variance premium runs **+6.0 mean / +9.2 median vol pts, positive 71% of days, −31.5 worst-case tail** —
the edge is real, the crash risk is real, neither hidden.

## Phase 2 — Options pricing + delta-hedged P&L model ✅ (`evolver/optimize/vol_pnl.py`) — the net-new core
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

**✅ Done (2026-06-29, `evolver/optimize/vol_pnl.py` + `tests/test_vol_pnl.py`, 6/6 pass):** pure-Python
Black-Scholes (price + delta/gamma/vega/theta — matched to textbook + put-call parity) and a
`delta_hedged_straddle_pnl` simulator that sells an ATM straddle, rehedges along the real path, and
accrues P&L net of fees/slippage/option-spread. Verified behaviors: **profits when realized < implied,
loses in a vol spike, ~0 when implied = realized** (no free money). **Functional-not-scaffold proof:**
ran the actual strategy on 33 months of LIVE Deribit data (33 non-overlapping 30d trades, daily-rehedged,
net of all frictions) → **mean +1.02%/trade, annualized Sharpe ~1.0, 58% win, worst −7.2%.** The gross
+6 vol-pt premium collapses to a modest NET ~1.0 Sharpe once frictions bite (honest); delta-hedging
contained the raw −31.5 tail to −7.2% net. Whether that net survives the gate (small n=33, the tail) is
Phase 3's job — this proves the machine is real, not whether the edge clears.

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
- 2026-06-29 — roadmap created.
- 2026-06-29 — **Phase 0 ✅** (`scripts/deribit_probe.py`): Deribit reachable from sandbox + droplet (no
  geo-block); DVOL ~2.7yr daily + 868 options w/ greeks + ~3yr price history — data feasibility
  confirmed. **Phase 1 (Deribit connector) next.**
- 2026-06-29 — **Phase 1 ✅** (`evolver/data/deribit.py`): pure-stdlib connector verified live (BTC+ETH).
  Confirmed the edge is REAL on 33mo of live data (variance premium +6.0 mean / +9.2 median vol pts, 71%
  positive, −31.5 tail) — functional, not scaffold. **Phase 2 (BS pricing + delta-hedged P&L) next.**
- 2026-06-29 — **Phase 2 ✅** (`evolver/optimize/vol_pnl.py`, 6/6 tests): BS matches textbook; simulator
  profits on quiet vol / loses on spike / ~0 when implied=realized. Real-data run (33 live trades, net
  of frictions): **mean +1.02%/trade, Sharpe ~1.0, 58% win, −7.2% worst** — gross +6 → modest net ~1.0,
  hedging contained the tail. Machine real + honest; gate verdict is Phase 3. **Phase 3 (family + gate) next.**
