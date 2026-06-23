import { SlidersHorizontal } from "lucide-react";
import { SettingsPanel } from "@/components/dashboard/panels/settings-panel";
import { SectionHeader } from "@/components/dashboard/ui";
import { getDashboardState } from "@/lib/dashboard/get-dashboard-state";

export default async function SettingsPage() {
  const state = await getDashboardState();

  return (
    <section className="section-band page-band">
      <SectionHeader
        icon={<SlidersHorizontal size={18} aria-hidden="true" />}
        title="Settings"
        subtitle="API readiness, limits, live execution lockouts, and manual approval posture"
      />
      <SettingsPanel
        live={state.liveSettings}
        paper={state.paper}
        liveReasons={state.liveEvaluation ? state.liveEvaluation.reasons : []}
        connector={state.connector.label}
        llm={state.llmStatus}
        dataFreshness={state.dataFreshness}
        killSwitch={state.killSwitch}
      />
    </section>
  );
}