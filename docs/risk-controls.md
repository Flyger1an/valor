# Risk Controls

The MVP risk engine lives in `src/lib/risk/risk-engine.ts`.

## Market Risk State

- `Green`: no material alerts.
- `Yellow`: active cautions; paper trading can continue.
- `Red`: serious research-only environment; live trading remains blocked and paper size stays capped.
- `Black`: severe state; new paper trades are blocked and live trading must remain impossible.

## Covered Alerts

- Exchange withdrawal or reserve stress
- Stablecoin depeg
- Security advisories
- Sudden liquidity collapse
- Funding and open-interest extremes
- Cross-venue dislocations
- Chain congestion and fee spikes

## Live Guardrails

The live guardrail interface lives in `src/lib/live/live-trading.ts`. By default it blocks because:

- `ENABLE_LIVE_TRADING` is false.
- `LIVE_KILL_SWITCH` is true.
- Manual confirmation is required.
- All generated signals have `eligibleForLiveTrading=false`.
- Max leverage defaults to 1x.

No exchange executor exists in the MVP.
