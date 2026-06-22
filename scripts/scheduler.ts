import { refreshAndPersistMarketState } from "../src/lib/ops/recompute";

const intervalMs = Number(process.env.VALOR_SCHEDULER_INTERVAL_MS ?? 60_000);
const jitterMs = Number(process.env.VALOR_SCHEDULER_JITTER_MS ?? 5_000);

async function tick() {
  const startedAt = Date.now();
  try {
    const { connector, data, computed } = await refreshAndPersistMarketState();
    console.log(
      JSON.stringify({
        service: "valor-scheduler",
        status: "tick_ok",
        connector: connector.label,
        markets: data.markets.length,
        signals: computed.signals.length,
        riskState: computed.risk.state,
        durationMs: Date.now() - startedAt,
        timestamp: new Date().toISOString(),
      }),
    );
  } catch (error) {
    console.error(
      JSON.stringify({
        service: "valor-scheduler",
        status: "tick_error",
        error: error instanceof Error ? error.message : "unknown error",
        durationMs: Date.now() - startedAt,
        timestamp: new Date().toISOString(),
      }),
    );
  }
}

function scheduleNext() {
  const delay = intervalMs + Math.floor(Math.random() * jitterMs);
  setTimeout(async () => {
    await tick();
    scheduleNext();
  }, delay);
}

console.log(
  JSON.stringify({
    service: "valor-scheduler",
    status: "ready",
    intervalMs,
    jitterMs,
    timestamp: new Date().toISOString(),
  }),
);

void tick();
scheduleNext();