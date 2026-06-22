import { BellRing } from "lucide-react";
import { AlertsPanel } from "@/components/dashboard/panels/alerts-panel";
import { SectionHeader } from "@/components/dashboard/ui";
import { getDashboardState } from "@/lib/dashboard/get-dashboard-state";

export default async function AlertsPage() {
  const state = await getDashboardState();

  return (
    <section className="section-band page-band">
      <SectionHeader
        icon={<BellRing size={18} aria-hidden="true" />}
        title="Alerts"
        subtitle="Severity-based routing with dedupe, quiet hours, SMS fallback, and dry-run delivery previews"
      />
      <AlertsPanel
        alerts={state.alertEvents}
        routingPreview={state.alertRoutingPreview}
        deliveries={state.alertDeliveries}
      />
    </section>
  );
}