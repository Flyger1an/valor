export function dataAgeMs(generatedAt: string, now = Date.now()): number {
  const parsed = Date.parse(generatedAt);
  if (!Number.isFinite(parsed)) return Number.POSITIVE_INFINITY;
  return Math.max(0, now - parsed);
}

export function isDataStale(
  generatedAt: string,
  maxAgeMs = Number(process.env.VALOR_STALE_DATA_MS ?? 300_000),
  now = Date.now(),
): boolean {
  return dataAgeMs(generatedAt, now) > maxAgeMs;
}

export function formatDataAge(generatedAt: string, now = Date.now()): string {
  const ageMs = dataAgeMs(generatedAt, now);
  if (!Number.isFinite(ageMs)) return "unknown age";
  if (ageMs < 60_000) return `${Math.round(ageMs / 1000)}s ago`;
  if (ageMs < 3_600_000) return `${Math.round(ageMs / 60_000)}m ago`;
  return `${(ageMs / 3_600_000).toFixed(1)}h ago`;
}