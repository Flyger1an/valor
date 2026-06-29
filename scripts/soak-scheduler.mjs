const config = {
  appUrl: normalizeUrl(process.env.VALOR_APP_URL ?? "http://127.0.0.1:3000"),
  cycles: numberFromEnv(process.env.SOAK_CYCLES, 3),
  delayMs: numberFromEnv(process.env.SOAK_DELAY_MS, 2_000),
  sendAlerts: process.env.SCHEDULER_SEND_ALERTS === "true",
  alertLimit: numberFromEnv(process.env.SCHEDULER_ALERT_LIMIT, 3),
  opsSecret: process.env.VALOR_OPS_SECRET,
};

const results = [];

log({
  service: "valor-scheduler-soak",
  status: "started",
  appUrl: config.appUrl,
  cycles: config.cycles,
  delayMs: config.delayMs,
  timestamp: new Date().toISOString(),
});

for (let cycle = 1; cycle <= config.cycles; cycle += 1) {
  const result = await postJson(`${config.appUrl}/api/ops/scheduler`, {
    sendAlerts: config.sendAlerts,
    alertLimit: config.alertLimit,
  });
  results.push(result.body?.result ?? result.body);

  log({
    service: "valor-scheduler-soak",
    status: result.ok ? "cycle" : "error",
    cycle,
    httpStatus: result.status,
    result: result.body?.result ?? result.body,
    timestamp: new Date().toISOString(),
  });

  if (!result.ok || result.body?.result?.status !== "success") {
    process.exitCode = 1;
    break;
  }

  if (cycle < config.cycles) {
    await sleep(config.delayMs);
  }
}

const health = await getJson(`${config.appUrl}/api/ops/health`);
log({
  service: "valor-scheduler-soak",
  status: health.ok && process.exitCode !== 1 ? "passed" : "failed",
  cycles: results.length,
  health: health.body?.report ?? health.body,
  timestamp: new Date().toISOString(),
});

if (!health.ok) process.exitCode = 1;

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: opsHeaders({ "content-type": "application/json" }),
    body: JSON.stringify(body),
  });
  return {
    ok: response.ok,
    status: response.status,
    body: await response.json().catch(() => null),
  };
}

async function getJson(url) {
  const response = await fetch(url, {
    headers: opsHeaders(),
  });
  return {
    ok: response.ok,
    status: response.status,
    body: await response.json().catch(() => null),
  };
}

function normalizeUrl(value) {
  return value.replace(/\/$/, "");
}

function numberFromEnv(value, fallback) {
  if (!value) return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function opsHeaders(headers = {}) {
  if (!config.opsSecret) return headers;
  return {
    ...headers,
    "x-valor-ops-secret": config.opsSecret,
  };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function log(event) {
  console.log(JSON.stringify(event));
}
