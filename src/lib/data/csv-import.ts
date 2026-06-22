import type { MarketSnapshot, Venue, Asset } from "@/lib/domain/types";

export interface CsvImportResult {
  rows: MarketSnapshot[];
  rejected: Array<{ line: number; reason: string }>;
}

export function parseMarketCsv(csv: string): CsvImportResult {
  const [headerLine, ...lines] = csv.trim().split(/\r?\n/);
  const headers = splitCsvLine(headerLine).map((header) => header.trim());
  const required = [
    "timestamp",
    "venue",
    "base",
    "quote",
    "instrumentType",
    "price",
    "volume24hUsd",
  ];
  const missing = required.filter((name) => !headers.includes(name));
  if (missing.length > 0) {
    return {
      rows: [],
      rejected: [{ line: 1, reason: `Missing required columns: ${missing.join(", ")}` }],
    };
  }

  const rows: MarketSnapshot[] = [];
  const rejected: CsvImportResult["rejected"] = [];

  lines.forEach((line, index) => {
    const lineNumber = index + 2;
    const cells = splitCsvLine(line);
    const record = Object.fromEntries(
      headers.map((header, columnIndex) => [header, cells[columnIndex] ?? ""]),
    );

    const price = Number(record.price);
    const volume24hUsd = Number(record.volume24hUsd);

    if (!Number.isFinite(price) || price <= 0) {
      rejected.push({ line: lineNumber, reason: "price must be positive" });
      return;
    }

    if (!Number.isFinite(volume24hUsd) || volume24hUsd < 0) {
      rejected.push({ line: lineNumber, reason: "volume24hUsd must be non-negative" });
      return;
    }

    rows.push({
      id: record.id || `manual-${lineNumber}`,
      timestamp: record.timestamp,
      venue: record.venue as Venue,
      base: record.base as Asset,
      quote: record.quote as Asset,
      instrumentType:
        record.instrumentType === "perp" || record.instrumentType === "spot"
          ? record.instrumentType
          : "spot",
      price,
      markPrice: optionalNumber(record.markPrice),
      indexPrice: optionalNumber(record.indexPrice),
      fundingRate8h: optionalNumber(record.fundingRate8h),
      openInterestUsd: optionalNumber(record.openInterestUsd),
      volume24hUsd,
      volatility30d: optionalNumber(record.volatility30d) ?? 0.5,
      change24hPct: optionalNumber(record.change24hPct) ?? 0,
      orderBook: {
        bid: optionalNumber(record.bid) ?? price,
        ask: optionalNumber(record.ask) ?? price,
        bidDepthUsd: optionalNumber(record.bidDepthUsd) ?? 0,
        askDepthUsd: optionalNumber(record.askDepthUsd) ?? 0,
        spreadBps: optionalNumber(record.spreadBps) ?? 0,
      },
    });
  });

  return { rows, rejected };
}

function optionalNumber(value: string | undefined): number | undefined {
  if (value === undefined || value.trim() === "") return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function splitCsvLine(line: string): string[] {
  const cells: string[] = [];
  let current = "";
  let inQuotes = false;

  for (const char of line) {
    if (char === '"') {
      inQuotes = !inQuotes;
    } else if (char === "," && !inQuotes) {
      cells.push(current);
      current = "";
    } else {
      current += char;
    }
  }

  cells.push(current);
  return cells;
}
