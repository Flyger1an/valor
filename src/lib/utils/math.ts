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

export function annualizeDailySharpe(dailyReturns: number[]): number {
  const avg = mean(dailyReturns);
  const sd = standardDeviation(dailyReturns);
  if (sd === 0) return 0;
  return (avg / sd) * Math.sqrt(365);
}

export function sortinoRatio(dailyReturns: number[]): number {
  const downside = dailyReturns.filter((value) => value < 0);
  const downsideDeviation = standardDeviation(downside);
  if (downsideDeviation === 0) return dailyReturns.some((v) => v > 0) ? 99 : 0;
  return (mean(dailyReturns) / downsideDeviation) * Math.sqrt(365);
}
