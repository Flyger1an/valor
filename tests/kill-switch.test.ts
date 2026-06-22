import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { FileKillSwitchStore } from "@/lib/kill-switch/kill-switch";

let dir: string | null = null;

afterEach(() => {
  if (dir) rmSync(dir, { recursive: true, force: true });
  dir = null;
});

describe("file kill switch store", () => {
  it("persists BLACK halt state across store instances", () => {
    dir = mkdtempSync(join(tmpdir(), "valor-kill-"));
    const path = join(dir, "kill-switch.json");
    const first = new FileKillSwitchStore(path);

    first.activate({
      reason: "test halt",
      actor: "telegram",
      now: new Date("2026-06-22T12:00:00.000Z"),
    });

    const second = new FileKillSwitchStore(path);
    const state = second.read();

    expect(state.active).toBe(true);
    expect(state.reason).toBe("test halt");
    expect(state.dashboardResetRequired).toBe(true);
  });
});
