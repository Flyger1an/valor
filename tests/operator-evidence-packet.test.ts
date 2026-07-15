import { describe, expect, it } from "vitest";
import { buildDashboardState } from "@/lib/dashboard/build-dashboard";
import {
  buildOperatorEvidencePacket,
  formatOperatorEvidenceMarkdown,
} from "@/lib/reports/operator-evidence-packet";

describe("operator evidence packet", () => {
  it("builds a redacted operator packet from current dashboard evidence", async () => {
    const state = await buildDashboardState();
    const packet = buildOperatorEvidencePacket(state);

    expect(packet.id).toContain("operator-evidence:");
    expect(packet.title).toBe("Valor v0.2 Operator Evidence Packet");
    expect(packet.guardrail).toContain("not live execution approval");
    expect(packet.readiness.status).toBe(state.tinyLiveReadiness.status);
    expect(packet.controls.systemTrust).toContain(state.systemTrust.status);
    expect(packet.evidence.paper).toContain("ledger event");
    expect(packet.evidence.evolverRecoveryPlan).toContain(
      state.evolverEvidence.recoveryPlan.status,
    );
    expect(packet.evidence.evolverRecoveryWatchdog).toContain(
      state.evolverRecoveryWatchdog.posture,
    );
    expect(packet.attestations.some((item) => item.includes("No live exchange executor"))).toBe(true);
  });

  it("formats the packet as a reviewable markdown memo", async () => {
    const state = await buildDashboardState();
    const packet = buildOperatorEvidencePacket(state);
    const markdown = formatOperatorEvidenceMarkdown(packet);

    expect(markdown).toContain("# Valor v0.2 Operator Evidence Packet");
    expect(markdown).toContain("## Readiness");
    expect(markdown).toContain("## Controls");
    expect(markdown).toContain("## Next Actions");
    expect(markdown).toContain("Evolver recovery plan");
    expect(markdown).toContain("Evolver recovery watchdog");
    expect(markdown).toContain(packet.decision);
    expect(markdown).toContain("not live execution approval");
  });
});
