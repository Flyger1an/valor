import { CircleDollarSign } from "lucide-react";
import { PaperPanel } from "@/components/dashboard/panels/paper-panel";
import { SectionHeader } from "@/components/dashboard/ui";
import { getDashboardState } from "@/lib/dashboard/get-dashboard-state";

export default async function PaperPage() {
  const state = await getDashboardState();

  return (
    <section className="section-band page-band">
      <SectionHeader
        icon={<CircleDollarSign size={18} aria-hidden="true" />}
        title="Paper Trading"
        subtitle="Simulated fills with signal attribution and risk-limit enforcement"
      />
      <PaperPanel paper={state.paper} />
    </section>
  );
}