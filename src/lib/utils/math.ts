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

export type AdfConfidence = "90%" | "95%" | "99%" | "none";

export interface AugmentedDickeyFullerResult {
  testStatistic: number;
  beta: number;
  n: number;
  confidence: AdfConfidence;
  isStationary: boolean;
}

const ADF_MIN_OBS = 12;
const ADF_CRITICAL_VALUES: Array<[number, number, number, number]> = [
  [25, -2.66, -3.0, -3.75],
  [50, -2.59, -2.93, -3.58],
  [100, -2.57, -2.89, -3.51],
  [250, -2.57, -2.88, -3.48],
];

function adfCriticalValue(
  sampleSize: number,
  significance: 0.1 | 0.05 | 0.01,
): number {
  const column = significance === 0.1 ? 1 : significance === 0.05 ? 2 : 3;
  const n = Math.max(sampleSize, ADF_MIN_OBS);

  if (n <= ADF_CRITICAL_VALUES[0][0]) return ADF_CRITICAL_VALUES[0][column];
  if (n >= ADF_CRITICAL_VALUES[ADF_CRITICAL_VALUES.length - 1][0]) {
    return ADF_CRITICAL_VALUES[ADF_CRITICAL_VALUES.length - 1][column];
  }

  for (let i = 0; i < ADF_CRITICAL_VALUES.length - 1; i++) {
    const previous = ADF_CRITICAL_VALUES[i];
    const next = ADF_CRITICAL_VALUES[i + 1];
    const n0 = previous[0];
    const n1 = next[0];

    if (n >= n0 && n <= n1) {
      const t = (n - n0) / (n1 - n0);
      return previous[column] + t * (next[column] - previous[column]);
    }
  }

  return ADF_CRITICAL_VALUES[1][column];
}

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
  for (let i = 1; i < series.length; i++) {
    yLag.push(series[i - 1]);
    dy.push(series[i] - series[i - 1]);
  }

  const n = dy.length;
  const yMean = mean(yLag);
  const dyMean = mean(dy);
  let xVariance = 0;
  let covariance = 0;

  for (let i = 0; i < n; i++) {
    const x = yLag[i] - yMean;
    xVariance += x * x;
    covariance += x * (dy[i] - dyMean);
  }

  if (xVariance === 0) {
    return {
      testStatistic: 0,
      beta: 0,
      n,
      confidence: "none",
      isStationary: false,
    };
  }

  const beta = covariance / xVariance;
  const alpha = dyMean - beta * yMean;
  let squaredError = 0;

  for (let i = 0; i < n; i++) {
    const residual = dy[i] - alpha - beta * yLag[i];
    squaredError += residual * residual;
  }

  const meanSquaredError = squaredError / Math.max(1, n - 2);
  const betaStdError = Math.sqrt(meanSquaredError / xVariance);
  const testStatistic = betaStdError === 0 ? 0 : round(beta / betaStdError, 4);
  const criticalValue = adfCriticalValue(n, significance);
  const isStationary = testStatistic < criticalValue;
  const confidence: AdfConfidence = isStationary
    ? significance === 0.01
      ? "99%"
      : significance === 0.05
        ? "95%"
        : "90%"
    : "none";

  return {
    testStatistic,
    beta,
    n,
    confidence,
    isStationary,
  };
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
