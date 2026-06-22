import { AlertTriangle, ShieldCheck } from "lucide-react";
import type { MarketRiskState, RelativeValueSignal, RiskState } from "@/lib/domain/types";
import { formatDateTime, riskTone } from "@/lib/dashboard/format";

export function SectionHeader(props: {
  icon: React.ReactNode;
  title: string;
  subtitle: string;
}) {
  return (
    <div className="section-header">
      <div className="section-title">
        {props.icon}
        <h2>{props.title}</h2>
      </div>
      <p>{props.subtitle}</p>
    </div>
  );
}

export function RiskBadge({ risk }: { risk: MarketRiskState }) {
  const Icon = risk.state === "Green" ? ShieldCheck : AlertTriangle;
  return (
    <div className={`risk-badge risk-${risk.state.toLowerCase()}`}>
      <Icon size={17} aria-hidden="true" />
      <span>{risk.state}</span>
      <strong>{risk.activeAlerts.length} alerts</strong>
    </div>
  );
}

export function MetricTile(props: {
  label: string;
  value: string;
  sub: string;
  tone: "good" | "bad" | "warn" | "info" | "neutral";
}) {
  return (
    <div className={`metric-tile tone-${props.tone}`}>
      <span>{props.label}</span>
      <strong>{props.value}</strong>
      <small>{props.sub}</small>
    </div>
  );
}

export function OpportunityRow({ signal }: { signal: RelativeValueSignal }) {
  return (
    <div className="opportunity-row">
      <div>
        <span className="mono strong">{signal.assetPair}</span>
        <p>{signal.venue}</p>
      </div>
      <div className="score-stack">
        <strong>{signal.opportunityScore.toFixed(1)}</strong>
        <span>{signal.expectedEdgeBps.toFixed(1)} bps</span>
      </div>
    </div>
  );
}

export function LimitRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="status-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function StatusBoolean(props: {
  label: string;
  value: boolean;
  safeWhenFalse?: boolean;
}) {
  const good = props.safeWhenFalse ? !props.value : props.value;
  return (
    <div className="status-row">
      <span>{props.label}</span>
      <strong className={good ? "good-text" : "bad-text"}>
        {props.value ? "Yes" : "No"}
      </strong>
    </div>
  );
}

export { formatDateTime, riskTone };