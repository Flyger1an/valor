import { FileClock } from "lucide-react";
import { ActionLog, AuditPanel } from "@/components/dashboard/panels/audit-panel";
import { SectionHeader } from "@/components/dashboard/ui";
import { getDashboardState } from "@/lib/dashboard/get-dashboard-state";

export default async function AuditPage() {
  const state = await getDashboardState();

  return (
    <section className="section-band page-band">
      <SectionHeader
        icon={<FileClock size={18} aria-hidden="true" />}
        title="Audit"
        subtitle="Important refreshes, generated signals, alerts, backtests, and trade events"
      />
      <AuditPanel events={state.auditEvents} />
      <ActionLog entries={state.actionLog} />
    </section>
  );
}