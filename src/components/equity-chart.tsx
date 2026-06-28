import type { EquityPoint } from "@/lib/domain/types";

interface EquityChartProps {
  points: EquityPoint[];
}

export function EquityChart({ points }: EquityChartProps) {
  const width = 720;
  const height = 190;
  const padding = 18;
  const values = points.map((point) => point.equity);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(max - min, 1);
  const step = (width - padding * 2) / Math.max(points.length - 1, 1);
  const path = points
    .map((point, index) => {
      const x = padding + index * step;
      const y = height - padding - ((point.equity - min) / range) * (height - padding * 2);
      return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(" ");
  const area = `${path} L ${width - padding} ${height - padding} L ${padding} ${
    height - padding
  } Z`;

  return (
    <svg
      className="equity-chart"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="Backtest equity curve"
    >
      <path d={area} className="chart-area" />
      <path d={path} className="chart-line" />
      <line x1={padding} x2={width - padding} y1={height - padding} y2={height - padding} />
      <text x={padding} y={20}>
        ${max.toLocaleString()}
      </text>
      <text x={padding} y={height - 6}>
        ${min.toLocaleString()}
      </text>
    </svg>
  );
}
