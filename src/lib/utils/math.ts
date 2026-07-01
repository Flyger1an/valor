export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function round(value: number, digits = 2): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

export function pctChange(current: number, previous: number): number {
  if (previous === 0) return 0;
  return (current - previous) / previous;
}

export function mean(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

export function standardDeviation(values: number[]): number {
  if (values.length < 2) return 0;
  const avg = mean(values);
  const variance =
    values.reduce((sum, value) => sum + (value - avg) ** 2, 0) /
    (values.length - 1);
  return Math.sqrt(variance);
}

export function zScore(value: number, sample: number[]): number {
  const sd = standardDeviation(sample);
  if (sd === 0) return 0;
  return (value - mean(sample)) / sd;
}

/**
 * Mean-reversion half-life (hours) of a spread series, via an Ornstein-Uhlenbeck
 * / AR(1) fit: regress Δx_t on (x_{t-1} - mean); φ = 1 + slope; half-life =
 * -ln(2)/ln(φ) periods. Returns `fallbackHours` when the series is too short or
 * not mean-reverting (φ ∉ (0,1)). Capped at 14 days.
 */
export function meanReversionHalfLifeHours(
  series: number[],
  periodHours: number,
  fallbackHours: number,
): number {
  if (series.length < 8) return fallbackHours;
  const avg = mean(series);
  let num = 0;
  let den = 0;
  for (let t = 1; t < series.length; t++) {
    const xPrev = series[t - 1] - avg;
    const dx = series[t] - series[t - 1];
    num += xPrev * dx;
    den += xPrev * xPrev;
  }
  if (den === 0) return fallbackHours;
  const phi = 1 + num / den; // AR(1) coefficient
  if (phi <= 0 || phi >= 1) return fallbackHours;
  const hours = (-Math.log(2) / Math.log(phi)) * periodHours;
  if (!Number.isFinite(hours) || hours <= 0) return fallbackHours;
  return Math.min(hours, 24 * 14);
}

export type AdfConfidence = "90%" | "95%" | "99%" | "none";

export interface AugmentedDickeyFullerResult {
  /** t-statistic on the lag level coefficient (more negative ⇒ more stationary). */
  testStatistic: number;
  beta: number;
  n: number;
  confidence: AdfConfidence;
  isStationary: boolean;
}

const ADF_MIN_OBS = 12;

/** MacKinnon-style critical values for ADF with constant, no trend (approximate). */
const ADF_CRITICAL: Array<[number, number, number, number]> = [
  [25, -2.66, -3.0, -3.75],
  [50, -2.59, -2.93, -3.58],
  [100, -2.57, -2.89, -3.51],
  [250, -2.57, -2.88, -3.48],
];

function adfCriticalValue(
  sampleSize: number,
  significance: 0.1 | 0.05 | 0.01,
): number {
  const col = significance === 0.1 ? 1 : significance === 0.05 ? 2 : 3;
  const n = Math.max(sampleSize, ADF_MIN_OBS);
  if (n <= ADF_CRITICAL[0][0]) return ADF_CRITICAL[0][col];
  if (n >= ADF_CRITICAL[ADF_CRITICAL.length - 1][0]) {
    return ADF_CRITICAL[ADF_CRITICAL.length - 1][col];
  }
  for (let i = 0; i < ADF_CRITICAL.length - 1; i++) {
    const [n0, , ,] = ADF_CRITICAL[i];
    const [n1, c10, c5, c1] = ADF_CRITICAL[i + 1];
    if (n >= n0 && n <= n1) {
      const t = (n - n0) / (n1 - n0);
      const cv0 = significance === 0.1 ? c10 : significance === 0.05 ? c5 : c1;
      const prev = ADF_CRITICAL[i];
      const cvPrev =
        significance === 0.1 ? prev[1] : significance === 0.05 ? prev[2] : prev[3];
      return cvPrev + t * (cv0 - cvPrev);
    }
  }
  return ADF_CRITICAL[1][col];
}

/**
 * Augmented Dickey–Fuller test (constant, no trend): Δy_t = α + β·y_{t-1} + ε_t.
 * H0: unit root (β = 0). Reject when the t-stat on β is below the critical value.
 */
export function augmentedDickeyFullerTest(
  series: number[],
  significance: 0.1 | 0.05 | 0.01 = 0.05,
): AugmentedDickeyFullerResult {
  if (series.length < ADF_MIN_OBS) {
    return {
      testStatistic: 0,
      beta: 0,
      n: Math.max(0, series.length - 1),
      confidence: "none",
      isStationary: false,
    };
  }

  const yLag: number[] = [];
  const dy: number[] = [];
  for (let t = 1; t < series.length; t++) {
    yLag.push(series[t - 1]);
    dy.push(series[t] - series[t - 1]);
  }

  const n = dy.length;
  const yMean = mean(yLag);
  const dyMean = mean(dy);
  let xVar = 0;
  let cov = 0;
  for (let i = 0; i < n; i++) {
    const x = yLag[i] - yMean;
    xVar += x * x;
    cov += x * (dy[i] - dyMean);
  }
  if (xVar === 0) {
    return {
      testStatistic: 0,
      beta: 0,
      n,
      confidence: "none",
      isStationary: false,
    };
  }

  const beta = cov / xVar;
  const alpha = dyMean - beta * yMean;
  let sse = 0;
  for (let i = 0; i < n; i++) {
    const residual = dy[i] - alpha - beta * yLag[i];
    sse += residual * residual;
  }
  const mse = sse / Math.max(1, n - 2);
  const seBeta = Math.sqrt(mse / xVar);
  const testStatistic =
    seBeta === 0 ? 0 : round(beta / seBeta, 4);
  const critical = adfCriticalValue(n, significance);
  const isStationary = testStatistic < critical;
  const confidence: AdfConfidence = isStationary
    ? significance === 0.01
      ? "99%"
      : significance === 0.05
        ? "95%"
        : "90%"
    : "none";

  return { testStatistic, beta, n, confidence, isStationary };
}

export function annualizeDailySharpe(dailyReturns: number[]): number {
  const avg = mean(dailyReturns);
  const sd = standardDeviation(dailyReturns);
  if (sd === 0) return 0;
  return (avg / sd) * Math.sqrt(365);
}

export function sortinoRatio(dailyReturns: number[]): number | null {
  const downside = dailyReturns.filter((value) => value < 0);
  const downsideDeviation = standardDeviation(downside);
  // No downside deviation => Sortino is genuinely undefined (division by zero). Return null so callers
  // render it honestly ("∞ / no downside") rather than a magic 99 masquerading as a real ratio.
  if (downsideDeviation === 0) return dailyReturns.some((v) => v > 0) ? null : 0;
  return (mean(dailyReturns) / downsideDeviation) * Math.sqrt(365);
}
