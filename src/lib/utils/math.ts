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
