import type { RiskState } from "@/lib/domain/types";

export function money(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value);
}

export function signedMoney(value: number): string {
  const formatted = money(Math.abs(value));
  return `${value >= 0 ? "+" : "-"}${formatted}`;
}

export function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function riskTone(
  state: RiskState,
): "good" | "bad" | "warn" | "info" | "neutral" {
  if (state === "Green") return "good";
  if (state === "Yellow") return "warn";
  if (state === "Red" || state === "Black") return "bad";
  return "neutral";
}