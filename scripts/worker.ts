import { refreshAndPersistMarketState } from "../src/lib/ops/recompute";

const intervalMs = Number(process.env.VALOR_REFRESH_INTERVAL_MS ?? 300_000);

async function runRefresh() {
  const startedAt = Date.now();
  try {
    const { connector, data, computed } = await refreshAndPersistMarketState();
    console.log(
      JSON.stringify({
        service: "valor-worker",
        status: "refresh_ok",
        connector: connector.label,
        markets: data.markets.length,
        signals: computed.signals.length,
        riskState: computed.risk.state,
        generatedAt: data.generatedAt,
        durationMs: Date.now() - startedAt,
        timestamp: new Date().toISOString(),
      }),
    );
  } catch (error) {
    console.error(
      JSON.stringify({
        service: "valor-worker",
        status: "refresh_error",
        error: error instanceof Error ? error.message : "unknown error",
        durationMs: Date.now() - startedAt,
        timestamp: new Date().toISOString(),
      }),
    );
  }
}

console.log(
  JSON.stringify({
    service: "valor-worker",
    status: "ready",
    refreshIntervalMs: intervalMs,
    timestamp: new Date().toISOString(),
  }),
);

void runRefresh();
setInterval(() => {
  void runRefresh();
}, intervalMs);