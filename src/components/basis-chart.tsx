import type { HistoricalPoint } from "@/lib/domain/types";

interface BasisChartProps {
  history: HistoricalPoint[];
  label?: string;
}

export function BasisChart({ history, label = "BTC spot/perp basis proxy" }: BasisChartProps) {
  if (history.length < 2) {
    return <p className="muted">Insufficient history for basis chart.</p>;
  }

  const width = 720;
  const height = 180;
  const padding = 18;
  const basisSeries = history.map((point) => ((point.perpPrice - point.spotPrice) / point.spotPrice) * 10_000);
  const min = Math.min(...basisSeries);
  const max = Math.max(...basisSeries);
  const range = Math.max(max - min, 1);
  const step = (width - padding * 2) / Math.max(history.length - 1, 1);
  const zeroY =
    height - padding - ((0 - min) / range) * (height - padding * 2);

  const path = basisSeries
    .map((value, index) => {
      const x = padding + index * step;
      const y = height - padding - ((value - min) / range) * (height - padding * 2);
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");

  return (
    <svg
      className="equity-chart basis-chart"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={label}
    >
      <title>{label}</title>
      <line
        x1={padding}
        x2={width - padding}
        y1={zeroY}
        y2={zeroY}
        className="chart-zero-line"
      />
      <path d={path} className="chart-line basis-line" />
      <text x={padding} y={16}>
        {max.toFixed(1)} bps
      </text>
      <text x={padding} y={height - 6}>
        {min.toFixed(1)} bps
      </text>
    </svg>
  );
}