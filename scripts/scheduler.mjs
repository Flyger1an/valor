const config = {
  appUrl: normalizeUrl(
    process.env.VALOR_APP_URL ??
      (process.env.REDIS_URL?.includes("redis:6379")
        ? "http://app:3000"
        : "http://127.0.0.1:3000"),
  ),
  intervalMs: numberFromEnv(process.env.SCHEDULER_INTERVAL_MS, 5 * 60_000),
  sendAlerts: process.env.SCHEDULER_SEND_ALERTS === "true",
  alertLimit: numberFromEnv(process.env.SCHEDULER_ALERT_LIMIT, 3),
  runOnce: process.env.SCHEDULER_RUN_ONCE === "true",
  runOnStart: process.env.SCHEDULER_RUN_ON_START !== "false",
};

let running = false;

log({
  service: "valor-scheduler",
  status: "ready",
  appUrl: config.appUrl,
  intervalMs: config.intervalMs,
  sendAlerts: config.sendAlerts,
  timestamp: new Date().toISOString(),
});

if (config.runOnStart || config.runOnce) {
  await runCycle();
}

if (!config.runOnce) {
  setInterval(runCycle, config.intervalMs);
}

async function runCycle() {
  if (running) {
    log({
      service: "valor-scheduler",
      status: "skipped",
      reason: "previous cycle still running",
      timestamp: new Date().toISOString(),
    });
    return;
  }

  running = true;
  try {
    const response = await fetch(`${config.appUrl}/api/ops/scheduler`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        sendAlerts: config.sendAlerts,
        alertLimit: config.alertLimit,
      }),
    });
    const body = await response.json().catch(() => null);

    log({
      service: "valor-scheduler",
      status: response.ok ? "cycle" : "error",
      httpStatus: response.status,
      result: body?.result ?? body,
      timestamp: new Date().toISOString(),
    });
  } catch (error) {
    log({
      service: "valor-scheduler",
      status: "error",
      error: error instanceof Error ? error.message : String(error),
      timestamp: new Date().toISOString(),
    });
  } finally {
    running = false;
  }
}

function normalizeUrl(value) {
  return value.replace(/\/$/, "");
}

function numberFromEnv(value, fallback) {
  if (!value) return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function log(event) {
  console.log(JSON.stringify(event));
}
