import { LimitRow } from "@/components/dashboard/ui";
import type { PaperPortfolio } from "@/lib/domain/types";
import { money, signedMoney } from "@/lib/dashboard/format";

export function PaperPanel({ paper }: { paper: PaperPortfolio }) {
  return (
    <div className="paper-grid">
      <div className="panel">
        <h3>Positions</h3>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Pair</th>
                <th>Venue</th>
                <th>Direction</th>
                <th>Notional</th>
                <th>Mark PnL</th>
              </tr>
            </thead>
            <tbody>
              {paper.positions.map((position) => (
                <tr key={position.id}>
                  <td className="mono strong">{position.assetPair}</td>
                  <td>{position.venue}</td>
                  <td>{position.direction.replaceAll("_", " ")}</td>
                  <td>{money(position.notionalUsd)}</td>
                  <td className={position.markPnlUsd >= 0 ? "good-text" : "bad-text"}>
                    {signedMoney(position.markPnlUsd)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="panel">
        <h3>Risk Enforcement</h3>
        <div className="limit-list">
          <LimitRow label="Max Position" value={money(paper.riskLimits.maxPositionUsd)} />
          <LimitRow
            label="Portfolio Notional"
            value={`${(paper.riskLimits.maxPortfolioNotionalPct * 100).toFixed(0)}%`}
          />
          <LimitRow
            label="Max Signal Risk"
            value={paper.riskLimits.maxSignalRiskScore.toString()}
          />
          <LimitRow
            label="Min Liquidity"
            value={paper.riskLimits.minLiquidityScore.toString()}
          />
          <LimitRow
            label="Rejected Signals"
            value={paper.rejectedSignals.length.toString()}
          />
        </div>
      </div>
    </div>
  );
}