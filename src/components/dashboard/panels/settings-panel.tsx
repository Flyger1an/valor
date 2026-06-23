import { BookOpenCheck, Bot, KeyRound, LockKeyhole } from "lucide-react";
import { LimitRow, StatusBoolean } from "@/components/dashboard/ui";
import type { DashboardState } from "@/lib/dashboard/get-dashboard-state";
import type { LlmRuntimeStatus } from "@/lib/llm/status";
import type { LiveTradingSettings, PaperPortfolio } from "@/lib/domain/types";
import { money } from "@/lib/dashboard/format";

export function SettingsPanel(props: {
  live: LiveTradingSettings;
  paper: PaperPortfolio;
  liveReasons: string[];
  connector: string;
  llm: LlmRuntimeStatus;
  dataFreshness: DashboardState["dataFreshness"];
  killSwitch: DashboardState["killSwitch"];
}) {
  const apiStatuses = [
    ["Exchange OHLCV", props.connector],
    ["Stablecoin Pegs", "CoinGecko live path with fixture fallback"],
    ["News / RSS", "Advisory model active; RSS connector not yet wired"],
    ["CSV Import", "Parser available for manual snapshots"],
  ];

  return (
    <div className="settings-grid">
      <div className="panel">
        <h3>
          <KeyRound size={15} aria-hidden="true" />
          Data Sources
        </h3>
        <div className="status-list">
          {apiStatuses.map(([label, value]) => (
            <div key={label} className="status-row">
              <span>{label}</span>
              <strong>{value}</strong>
            </div>
          ))}
        </div>
      </div>
      <div className="panel">
        <h3>
          <LockKeyhole size={15} aria-hidden="true" />
          Live Guardrails
        </h3>
        <div className="status-list">
          <StatusBoolean label="Enabled" value={props.live.enabled} safeWhenFalse />
          <StatusBoolean label="Dry Run" value={props.live.dryRun} />
          <StatusBoolean
            label="Manual Approval"
            value={props.live.manualConfirmationRequired}
          />
          <StatusBoolean
            label="Kill Switch Active"
            value={props.live.killSwitchActive}
            safeWhenFalse={false}
          />
          <LimitRow label="Max Trade" value={money(props.live.maxTradeUsd)} />
          <LimitRow
            label="Daily Loss Limit"
            value={money(props.live.dailyLossLimitUsd)}
          />
          <LimitRow label="Max Leverage" value={`${props.live.maxLeverage}x`} />
        </div>
      </div>
      <div className="panel">
        <h3>
          <Bot size={15} aria-hidden="true" />
          Data Freshness
        </h3>
        <div className="status-list">
          <LimitRow label="Generated" value={props.dataFreshness.generatedAt} />
          <LimitRow label="Age" value={props.dataFreshness.ageLabel} />
          <StatusBoolean label="Stale" value={props.dataFreshness.stale} safeWhenFalse />
        </div>
      </div>
      <div className="panel full-width">
        <h3>
          <Bot size={15} aria-hidden="true" />
          LLM API Plug
        </h3>
        <div className="status-list">
          <StatusBoolean label="LLM_API_ENABLED" value={props.llm.enabled} />
          <StatusBoolean label="API key present" value={props.llm.hasApiKey} />
          <StatusBoolean label="Live calls configured" value={props.llm.configured} />
          <LimitRow label="Runtime mode" value={props.llm.mode} />
          <LimitRow label="Model" value={props.llm.model ?? "not configured"} />
          <LimitRow label="Base URL" value={props.llm.baseUrl} />
          <LimitRow label="Authority" value="RAG/extraction/explanation only" />
        </div>
        <p className="setup-hint">{props.llm.setupHint}</p>
      </div>
      <div className="panel full-width">
        <h3>
          <LockKeyhole size={15} aria-hidden="true" />
          Persisted Kill Switch
        </h3>
        <div className="status-list">
          <StatusBoolean
            label="Active"
            value={props.killSwitch?.active ?? false}
            safeWhenFalse
          />
          <LimitRow label="Reason" value={props.killSwitch?.reason ?? "Not active"} />
          <LimitRow
            label="Activated By"
            value={props.killSwitch?.activatedBy ?? "n/a"}
          />
        </div>
      </div>
      <div className="panel full-width">
        <h3>
          <BookOpenCheck size={15} aria-hidden="true" />
          Live Attempt Evaluation
        </h3>
        <div className="blocked-reasons">
          {props.liveReasons.map((reason) => (
            <span key={reason}>{reason}</span>
          ))}
        </div>
      </div>
    </div>
  );
}