"use client";

import type { ReactNode } from "react";
import { AlertOctagon, BellRing, LineChart, RefreshCcw, Send } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

type ActionKey = "refresh" | "backtest" | "paper" | "alert" | "kill";

const ACTIONS: Array<{
  key: ActionKey;
  label: string;
  icon: ReactNode;
  endpoint: string;
  body?: Record<string, unknown>;
  dangerous?: boolean;
}> = [
  {
    key: "refresh",
    label: "Refresh Data",
    icon: <RefreshCcw size={15} aria-hidden="true" />,
    endpoint: "/api/ops/refresh",
  },
  {
    key: "backtest",
    label: "Run Backtest",
    icon: <LineChart size={15} aria-hidden="true" />,
    endpoint: "/api/ops/backtest",
  },
  {
    key: "paper",
    label: "Open Paper Trades",
    icon: <Send size={15} aria-hidden="true" />,
    endpoint: "/api/ops/paper-trade",
  },
  {
    key: "alert",
    label: "Send First Alert",
    icon: <BellRing size={15} aria-hidden="true" />,
    endpoint: "/api/ops/send-alert",
  },
  {
    key: "kill",
    label: "Activate Kill Switch",
    icon: <AlertOctagon size={15} aria-hidden="true" />,
    endpoint: "/api/ops/kill-switch",
    body: { action: "activate", reason: "Manual dashboard activation" },
    dangerous: true,
  },
];

export function OperationalControls() {
  const router = useRouter();
  const [busy, setBusy] = useState<ActionKey | null>(null);
  const [result, setResult] = useState<string>("No action run in this browser session.");

  async function runAction(action: (typeof ACTIONS)[number]) {
    if (action.dangerous) {
      const confirmed = window.confirm(
        "Activate BLACK kill switch? This persists locally and blocks live trading flows.",
      );
      if (!confirmed) return;
    }

    setBusy(action.key);
    try {
      const response = await fetch(action.endpoint, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(action.body ?? {}),
      });
      const json = await response.json();
      setResult(JSON.stringify(json, null, 2));
      router.refresh();
    } catch (error) {
      setResult(error instanceof Error ? error.message : "Unknown action error");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="ops-panel">
      <div className="ops-buttons">
        {ACTIONS.map((action) => (
          <button
            key={action.key}
            className={action.dangerous ? "ops-button dangerous-action" : "ops-button"}
            type="button"
            disabled={busy !== null}
            onClick={() => runAction(action)}
          >
            {busy === action.key ? <RefreshCcw size={15} aria-hidden="true" /> : action.icon}
            {busy === action.key ? "Running" : action.label}
          </button>
        ))}
      </div>
      <pre className="ops-result">{result}</pre>
    </div>
  );
}
