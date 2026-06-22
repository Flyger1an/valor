import { ShieldAlert } from "lucide-react";
import { SectionHeader, formatDateTime } from "@/components/dashboard/ui";
import { getDashboardState } from "@/lib/dashboard/get-dashboard-state";

export default async function RiskPage() {
  const state = await getDashboardState();

  return (
    <section className="section-band page-band">
      <SectionHeader
        icon={<ShieldAlert size={18} aria-hidden="true" />}
        title="Risk Intel"
        subtitle="Unified alert timeline with active trading restrictions"
      />
      <div className="risk-grid">
        {state.risk.activeAlerts.map((alert) => (
          <article key={alert.id} className={`alert-row severity-${alert.severity}`}>
            <div>
              <span className="tag">{alert.category}</span>
              <h3>{alert.title}</h3>
              <p>{alert.explanation}</p>
            </div>
            <div className="alert-meta">
              <strong>{alert.severity}</strong>
              <span>{formatDateTime(alert.timestamp)}</span>
              <span>{alert.source}</span>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}