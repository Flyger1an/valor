import { Bot } from "lucide-react";
import { AnalystCopilot } from "@/components/analyst-copilot";
import { SectionHeader } from "@/components/dashboard/ui";
import { getDashboardState } from "@/lib/dashboard/get-dashboard-state";

export default async function AnalystPage() {
  const state = await getDashboardState();

  return (
    <section className="section-band page-band">
      <SectionHeader
        icon={<Bot size={18} aria-hidden="true" />}
        title="Analyst"
        subtitle="RAG, structured extraction, and explanation only; deterministic controls remain authoritative"
      />
      <AnalystCopilot
        configured={state.llmStatus.configured}
        model={state.llmStatus.model}
      />
    </section>
  );
}