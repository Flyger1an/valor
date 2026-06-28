const targetUrl = process.env.TARGET_URL ?? "http://127.0.0.1:3000";
const cdpPort = process.env.CDP_PORT ?? "9333";
const screenshotPath =
  process.env.SCREENSHOT_PATH ?? "/private/tmp/valor-dashboard.png";
const viewportWidth = Number(process.env.VIEWPORT_WIDTH ?? 1440);
const viewportHeight = Number(process.env.VIEWPORT_HEIGHT ?? 1200);

const tabs = await fetch(`http://127.0.0.1:${cdpPort}/json`).then((response) =>
  response.json(),
);
const tab =
  tabs.find((candidate) => candidate.url.includes(new URL(targetUrl).host)) ??
  tabs[0];

if (!tab) {
  throw new Error("No Chrome DevTools tab found.");
}

const ws = new WebSocket(tab.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  ws.addEventListener("open", resolve, { once: true });
  ws.addEventListener("error", reject, { once: true });
});

let id = 0;
const pending = new Map();
const observed = {
  exceptions: [],
  consoleErrors: [],
  logErrors: [],
};

ws.addEventListener("message", (event) => {
  const payload = JSON.parse(event.data);

  if (payload.id && pending.has(payload.id)) {
    const { resolve, reject } = pending.get(payload.id);
    pending.delete(payload.id);

    if (payload.error) {
      reject(new Error(JSON.stringify(payload.error)));
    } else {
      resolve(payload.result);
    }
    return;
  }

  if (payload.method === "Runtime.exceptionThrown") {
    observed.exceptions.push(
      payload.params.exceptionDetails?.text ?? "Runtime exception",
    );
  }

  if (
    payload.method === "Runtime.consoleAPICalled" &&
    ["error", "warning"].includes(payload.params.type)
  ) {
    observed.consoleErrors.push(
      payload.params.args
        ?.map((arg) => arg.value ?? arg.description ?? "")
        .join(" "),
    );
  }

  if (
    payload.method === "Log.entryAdded" &&
    ["error", "warning"].includes(payload.params.entry?.level)
  ) {
    observed.logErrors.push(payload.params.entry.text);
  }
});

function send(method, params = {}) {
  const messageId = ++id;
  ws.send(JSON.stringify({ id: messageId, method, params }));
  return new Promise((resolve, reject) =>
    pending.set(messageId, { resolve, reject }),
  );
}

await send("Runtime.enable");
await send("Page.enable");
await send("Log.enable");
await send("Emulation.setDeviceMetricsOverride", {
  width: viewportWidth,
  height: viewportHeight,
  deviceScaleFactor: 1,
  mobile: viewportWidth < 700,
});
await send("Page.navigate", { url: targetUrl });

for (let attempt = 0; attempt < 80; attempt += 1) {
  const ready = await send("Runtime.evaluate", {
    expression:
      "JSON.stringify({ ready: document.readyState, textLength: document.body?.innerText?.trim().length || 0 })",
    returnByValue: true,
  });
  const state = JSON.parse(ready.result.value);

  if (state.ready === "complete" && state.textLength > 500) {
    break;
  }

  await new Promise((resolve) => setTimeout(resolve, 250));
}

const pageInfoResult = await send("Runtime.evaluate", {
  expression: `JSON.stringify({
    title: document.title,
    hasContent: document.body.innerText.trim().length > 500,
    textLength: document.body.innerText.trim().length,
    hasNextOverlay: Boolean(document.querySelector('[data-nextjs-dialog], #nextjs__container_errors, .nextjs-toast-errors-parent')),
    sections: ['Overview','Signals','Risk Intel','Alerts','Analyst','Backtests','Paper Trading','Settings','Audit'].filter((label) => document.body.innerText.includes(label)),
    buttons: Array.from(document.querySelectorAll('button')).map((button) => button.innerText || button.getAttribute('title')).slice(0, 12),
    firstText: document.body.innerText.trim().slice(0, 800)
  })`,
  returnByValue: true,
});

const screenshot = await send("Page.captureScreenshot", {
  format: "png",
  captureBeyondViewport: true,
  fromSurface: true,
});

await import("node:fs").then((fs) =>
  fs.writeFileSync(screenshotPath, Buffer.from(screenshot.data, "base64")),
);

ws.close();

console.log(
  JSON.stringify(
    {
      page: JSON.parse(pageInfoResult.result.value),
      observed,
      screenshotPath,
    },
    null,
    2,
  ),
);
