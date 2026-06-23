import type { RelativeValueSignal } from "@/lib/domain/types";
import { round } from "@/lib/utils/math";

export interface SignalJournalEntry {
  id: string;
  fingerprint: string;
  signalId: string;
  kind: RelativeValueSignal["kind"];
  assetPair: string;
  venue: string;
  direction: RelativeValueSignal["direction"];
  opportunityScore: number;
  expectedEdgeBps: number;
  edgeDeltaBps: number;
  riskScore: number;
  liquidityScore: number;
  eligibleForPaperTrading: boolean;
  sightings: number;
  firstSeenAt: string;
  lastSeenAt: string;
  recordedAt: string;
}

export interface SignalJournalSnapshot {
  entries: SignalJournalEntry[];
  recordedAt: string;
  persistedSignals: number;
  paperEligible: number;
}

export function buildSignalJournal(input: {
  signals: RelativeValueSignal[];
  previous?: SignalJournalEntry[];
  timestamp: string;
  limit?: number;
}): SignalJournalSnapshot {
  const limit = input.limit ?? 16;
  const previousByFingerprint = new Map(
    (input.previous ?? []).map((entry) => [entry.fingerprint, entry]),
  );

  const entries = input.signals.slice(0, limit).map((signal) => {
    const fingerprint = journalFingerprint(signal);
    const previous = previousByFingerprint.get(fingerprint);
    const firstSeenAt = previous?.firstSeenAt ?? input.timestamp;
    const edgeDeltaBps = round(signal.expectedEdgeBps - (previous?.expectedEdgeBps ?? signal.expectedEdgeBps), 2);

    return {
      id: `journal:${fingerprint}:${input.timestamp}`,
      fingerprint,
      signalId: signal.id,
      kind: signal.kind,
      assetPair: signal.assetPair,
      venue: signal.venue,
      direction: signal.direction,
      opportunityScore: signal.opportunityScore,
      expectedEdgeBps: signal.expectedEdgeBps,
      edgeDeltaBps,
      riskScore: signal.riskScore,
      liquidityScore: signal.liquidityScore,
      eligibleForPaperTrading: signal.eligibleForPaperTrading,
      sightings: (previous?.sightings ?? 0) + 1,
      firstSeenAt,
      lastSeenAt: input.timestamp,
      recordedAt: input.timestamp,
    } satisfies SignalJournalEntry;
  });

  return {
    entries,
    recordedAt: input.timestamp,
    persistedSignals: entries.filter((entry) => entry.sightings >= 2).length,
    paperEligible: entries.filter((entry) => entry.eligibleForPaperTrading).length,
  };
}

export function journalFingerprint(signal: RelativeValueSignal): string {
  return `${signal.kind}:${signal.assetPair}:${signal.venue}:${signal.direction}`;
}