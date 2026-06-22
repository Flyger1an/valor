import { Activity } from "lucide-react";
import type { AuditEvent } from "@/lib/domain/types";
import type { DashboardState } from "@/lib/dashboard/get-dashboard-state";
import { formatDateTime } from "@/lib/dashboard/format";

export function AuditPanel({ events }: { events: AuditEvent[] }) {
  return (
    <div className="audit-list">
      {events.slice(0, 14).map((event) => (
        <article key={event.id} className="audit-row">
          <div className="audit-icon">
            <Activity size={15} aria-hidden="true" />
          </div>
          <div>
            <span className="mono">{event.action}</span>
            <p>{event.summary}</p>
          </div>
          <time>{formatDateTime(event.timestamp)}</time>
        </article>
      ))}
    </div>
  );
}

export function ActionLog({
  entries,
}: {
  entries: DashboardState["actionLog"];
}) {
  if (entries.length === 0) return null;

  return (
    <div className="panel action-log-panel">
      <h3>Action Log</h3>
      <div className="delivery-list">
        {entries.slice(0, 10).map((entry) => (
          <div key={entry.id} className="delivery-row">
            <div>
              <strong>{entry.action}</strong>
              <span>{entry.status}</span>
            </div>
            <p>{entry.message}</p>
          </div>
        ))}
      </div>
    </div>
  );
}