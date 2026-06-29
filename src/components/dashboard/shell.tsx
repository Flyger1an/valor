import { Database, TerminalSquare } from "lucide-react";
import { DashboardNavLinks } from "@/components/dashboard/nav-links";
import { RiskBadge } from "@/components/dashboard/ui";
import { LivePulse } from "@/components/live-pulse";
import { LogoutButton } from "@/components/logout-button";
import type { DashboardState } from "@/lib/dashboard/get-dashboard-state";

export function DashboardShell(props: {
  state: DashboardState;
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <TerminalSquare size={22} aria-hidden="true" />
          <div>
            <p className="brand-name">Valor</p>
            <p className="brand-subtitle">Private RV + Risk Intel</p>
          </div>
        </div>
        <DashboardNavLinks />
        <div className="connector-box">
          <Database size={16} aria-hidden="true" />
          <div>
            <span>{props.state.connector.label}</span>
            <strong>
              {props.state.connector.needsApiKey ? "Key required" : "Local-ready"}
            </strong>
          </div>
        </div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">{props.subtitle}</p>
            <h1>{props.title}</h1>
          </div>
          <div className="topbar-actions">
            <RiskBadge risk={props.state.risk} />
            <LogoutButton />
          </div>
        </header>
        <LivePulse />
        {props.children}
      </div>
    </main>
  );
}
