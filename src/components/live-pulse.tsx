"use client";

import { RefreshCcw, Radio } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

interface StatusPayload {
  generatedAt: string;
  connector: string;
  riskState: string;
  riskScore: number;
  signalCount: number;
  paperEligible: number;
  dataAgeLabel?: string;
  liveMarketCount?: number;
  fixtureMarketCount?: number;
  llmMode?: string;
}

export function LivePulse() {
  const router = useRouter();
  const [status, setStatus] = useState<StatusPayload | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pullStatus = useCallback(async (soft = true) => {
    try {
      const response = await fetch("/api/ops/status", {
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`status ${response.status}`);
      const body = (await response.json()) as StatusPayload & { ok: boolean };
      setStatus(body);
      setError(null);
      if (!soft) router.refresh();
    } catch (pullError) {
      setError(pullError instanceof Error ? pullError.message : "status unavailable");
    }
  }, [router]);

  const hardRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const response = await fetch("/api/ops/refresh", {
        method: "POST",
      });
      if (!response.ok) throw new Error(`refresh ${response.status}`);
      await pullStatus(false);
    } catch (refreshError) {
      setError(
        refreshError instanceof Error ? refreshError.message : "refresh failed",
      );
    } finally {
      setRefreshing(false);
    }
  }, [pullStatus]);

  useEffect(() => {
    void pullStatus(true);
    const interval = window.setInterval(() => {
      void pullStatus(true);
    }, 30_000);
    return () => window.clearInterval(interval);
  }, [pullStatus]);

  return (
    <div className="live-pulse">
      <div className="live-pulse-main">
        <span className="live-dot" aria-hidden="true" />
        <Radio size={14} aria-hidden="true" />
        <strong>Live pulse</strong>
        <span className="live-pulse-meta">
          {status
            ? `${status.riskState} · ${status.signalCount} signals · ${status.paperEligible} paper-eligible`
            : "Connecting"}
        </span>
        {status ? (
          <span className="live-pulse-time">
            {status.connector} · {status.dataAgeLabel ?? formatTime(status.generatedAt)} ·{" "}
            {status.liveMarketCount ?? 0} live / {status.fixtureMarketCount ?? 0} fixture · LLM{" "}
            {status.llmMode ?? "offline"}
          </span>
        ) : null}
        {error ? <span className="live-pulse-error">{error}</span> : null}
      </div>
      <button
        type="button"
        className="live-pulse-button"
        disabled={refreshing}
        onClick={() => void hardRefresh()}
      >
        <RefreshCcw size={14} className={refreshing ? "spin" : undefined} aria-hidden="true" />
        {refreshing ? "Recomputing" : "Hard refresh"}
      </button>
    </div>
  );
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}
