"use client";

import { ArrowDownUp, ChevronDown, ChevronUp } from "lucide-react";
import { useMemo, useState } from "react";
import type { RelativeValueSignal } from "@/lib/domain/types";

type SortKey =
  | "opportunityScore"
  | "expectedEdgeBps"
  | "confidence"
  | "riskScore"
  | "liquidityScore"
  | "assetPair";

interface SignalsTableProps {
  signals: RelativeValueSignal[];
}

export function SignalsTable({ signals }: SignalsTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>("opportunityScore");
  const [direction, setDirection] = useState<"asc" | "desc">("desc");

  const sortedSignals = useMemo(() => {
    return [...signals].sort((a, b) => {
      const aValue = a[sortKey];
      const bValue = b[sortKey];
      const multiplier = direction === "asc" ? 1 : -1;

      if (typeof aValue === "string" && typeof bValue === "string") {
        return aValue.localeCompare(bValue) * multiplier;
      }

      return ((aValue as number) - (bValue as number)) * multiplier;
    });
  }, [direction, signals, sortKey]);

  function toggleSort(nextKey: SortKey) {
    if (nextKey === sortKey) {
      setDirection((current) => (current === "asc" ? "desc" : "asc"));
      return;
    }

    setSortKey(nextKey);
    setDirection(nextKey === "assetPair" ? "asc" : "desc");
  }

  return (
    <div className="table-wrap">
      <table className="data-table signals-table">
        <thead>
          <tr>
            <SortableHeader
              label="Pair"
              column="assetPair"
              sortKey={sortKey}
              direction={direction}
              onSort={toggleSort}
            />
            <th>Signal</th>
            <th>Direction</th>
            <SortableHeader
              label="Opp"
              column="opportunityScore"
              sortKey={sortKey}
              direction={direction}
              onSort={toggleSort}
            />
            <SortableHeader
              label="Edge"
              column="expectedEdgeBps"
              sortKey={sortKey}
              direction={direction}
              onSort={toggleSort}
            />
            <SortableHeader
              label="Conf"
              column="confidence"
              sortKey={sortKey}
              direction={direction}
              onSort={toggleSort}
            />
            <SortableHeader
              label="Risk"
              column="riskScore"
              sortKey={sortKey}
              direction={direction}
              onSort={toggleSort}
            />
            <SortableHeader
              label="Liq"
              column="liquidityScore"
              sortKey={sortKey}
              direction={direction}
              onSort={toggleSort}
            />
            <th>Mode</th>
            <th>Explanation</th>
          </tr>
        </thead>
        <tbody>
          {sortedSignals.map((signal) => (
            <tr key={signal.id}>
              <td className="mono strong">{signal.assetPair}</td>
              <td>
                <span className="tag">{labelSignal(signal.kind)}</span>
                <span className="muted block">{signal.venue}</span>
              </td>
              <td className="direction-cell">{labelDirection(signal.direction)}</td>
              <td className="metric-number">{signal.opportunityScore.toFixed(1)}</td>
              <td className="metric-number">{signal.expectedEdgeBps.toFixed(1)} bps</td>
              <td className="metric-number">{(signal.confidence * 100).toFixed(0)}%</td>
              <td className={signal.riskScore >= 70 ? "metric-number danger" : "metric-number"}>
                {signal.riskScore.toFixed(1)}
              </td>
              <td className="metric-number">{signal.liquidityScore.toFixed(1)}</td>
              <td>
                <span className={signal.eligibleForPaperTrading ? "pill ok" : "pill muted-pill"}>
                  Paper
                </span>
                <span className="pill blocked">Live off</span>
              </td>
              <td className="explain-cell">{signal.explanation}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SortableHeader(props: {
  label: string;
  column: SortKey;
  sortKey: SortKey;
  direction: "asc" | "desc";
  onSort: (column: SortKey) => void;
}) {
  const active = props.sortKey === props.column;
  const Icon = !active ? ArrowDownUp : props.direction === "asc" ? ChevronUp : ChevronDown;

  return (
    <th>
      <button
        className={active ? "sort-button active" : "sort-button"}
        onClick={() => props.onSort(props.column)}
        title={`Sort by ${props.label}`}
        type="button"
      >
        <span>{props.label}</span>
        <Icon size={14} aria-hidden="true" />
      </button>
    </th>
  );
}

function labelSignal(kind: RelativeValueSignal["kind"]): string {
  return kind
    .split("_")
    .map((part) => part[0].toUpperCase() + part.slice(1))
    .join(" ");
}

function labelDirection(direction: RelativeValueSignal["direction"]): string {
  return direction.replaceAll("_", " ");
}
