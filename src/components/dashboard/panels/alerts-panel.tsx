import type { DashboardState } from "@/lib/dashboard/get-dashboard-state";
import { formatDateTime } from "@/lib/dashboard/format";

export function AlertsPanel(props: {
  alerts: DashboardState["alertEvents"];
  routingPreview: DashboardState["alertRoutingPreview"];
  deliveries: DashboardState["alertDeliveries"];
}) {
  return (
    <div className="alerts-grid">
      <div className="panel">
        <h3>Active Alert Events</h3>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Severity</th>
                <th>Title</th>
                <th>Source</th>
                <th>Impact</th>
              </tr>
            </thead>
            <tbody>
              {props.alerts.slice(0, 10).map((alert) => (
                <tr key={alert.id}>
                  <td>
                    <span className={`pill severity-pill-${alert.severity.toLowerCase()}`}>
                      {alert.severity}
                    </span>
                  </td>
                  <td>
                    <strong>{alert.title}</strong>
                    <span className="muted block">{alert.message}</span>
                  </td>
                  <td>{alert.source}</td>
                  <td>{alert.tradingImpact}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="panel">
        <h3>Routing Preview</h3>
        <div className="delivery-list">
          {props.routingPreview.map((result) => (
            <div key={result.alert.id} className="delivery-row">
              <div>
                <strong>{result.alert.severity}</strong>
                <span>{result.alert.title}</span>
              </div>
              <p>
                {result.suppressed
                  ? result.reasons.join(", ")
                  : result.deliveries
                      .map((delivery) => `${delivery.channel}:${delivery.destination}`)
                      .join(" / ")}
              </p>
            </div>
          ))}
        </div>
      </div>
      <div className="panel full-width">
        <h3>Delivery Log</h3>
        <DeliveryLog deliveries={props.deliveries} />
      </div>
    </div>
  );
}

function DeliveryLog({
  deliveries,
}: {
  deliveries: DashboardState["alertDeliveries"];
}) {
  if (deliveries.length === 0) {
    return <p className="muted">No delivery attempts recorded yet.</p>;
  }

  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Channel</th>
            <th>Status</th>
            <th>Destination</th>
            <th>Alert</th>
          </tr>
        </thead>
        <tbody>
          {deliveries.slice(0, 12).map((delivery) => (
            <tr key={delivery.id}>
              <td>{formatDateTime(delivery.attemptedAt)}</td>
              <td>{delivery.channel}</td>
              <td>
                <span className="pill">{delivery.status}</span>
              </td>
              <td>{delivery.destination}</td>
              <td>{delivery.alertId}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}