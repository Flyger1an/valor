import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { closeDb } from "@/db/client";
import { GET as statusGet } from "@/app/api/ops/status/route";
import { POST as refreshPost } from "@/app/api/ops/refresh/route";
import { POST as killSwitchPost } from "@/app/api/ops/kill-switch/route";
import { LocalStateStore } from "@/lib/state/local-store";

const TEST_DB = `/tmp/valor-api-test-${process.pid}.sqlite`;

beforeEach(() => {
  process.env.DATABASE_URL = `file:${TEST_DB}-${Date.now()}`;
  process.env.VALOR_DISABLE_SQLITE = "false";
  process.env.ENABLE_PUBLIC_MARKET_FETCH = "false";
  process.env.KILL_SWITCH_STATE_PATH = `/tmp/valor-kill-${process.pid}.json`;
  closeDb();
});

afterEach(() => {
  closeDb();
});

describe("ops api routes", () => {
  it("status route returns live summary", async () => {
    await refreshPost();
    const response = await statusGet();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.ok).toBe(true);
    expect(body.signalCount).toBeGreaterThan(0);
    expect(body.riskState).toBeTruthy();
  });

  it("refresh route persists market state", async () => {
    const response = await refreshPost();
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.ok).toBe(true);
    expect(body.markets).toBeGreaterThan(0);
    expect(body.signals).toBeGreaterThan(0);

    const store = new LocalStateStore();
    const state = store.read();
    expect(state.data).toBeDefined();
    expect(state.signals?.length).toBeGreaterThan(0);
    expect(state.risk).toBeDefined();
  });

  it("kill switch route updates persisted state", async () => {
    await refreshPost();

    const response = await killSwitchPost(
      new Request("http://localhost/api/ops/kill-switch", {
        method: "POST",
        body: JSON.stringify({
          action: "activate",
          reason: "integration test",
        }),
      }),
    );
    const body = await response.json();

    expect(response.status).toBe(200);
    expect(body.ok).toBe(true);
    expect(body.state.active).toBe(true);

    const store = new LocalStateStore();
    expect(store.read().killSwitch?.active).toBe(true);
  });
});