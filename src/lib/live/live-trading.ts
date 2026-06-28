import type {
  Asset,
  LiveTradeEvaluation,
  LiveTradingSettings,
  RelativeValueSignal,
  SystemTrustVerdict,
  Venue,
} from "@/lib/domain/types";

export function readLiveTradingSettings(
  env: NodeJS.ProcessEnv = process.env,
): LiveTradingSettings {
  return {
    enabled:
      env.LIVE_TRADING_ENABLED === "true" || env.ENABLE_LIVE_TRADING === "true",
    dryRun: env.LIVE_TRADING_DRY_RUN !== "false",
    manualConfirmationRequired: env.REQUIRE_MANUAL_LIVE_CONFIRMATION !== "false",
    killSwitchActive: env.LIVE_KILL_SWITCH !== "false",
    maxTradeUsd: numberFromEnv(env.LIVE_MAX_TRADE_USD, 250),
    dailyLossLimitUsd: numberFromEnv(env.LIVE_DAILY_LOSS_LIMIT_USD, 100),
    maxLeverage: numberFromEnv(env.LIVE_MAX_LEVERAGE, 1),
    venueAllowlist: listFromEnv<Venue>(env.LIVE_ALLOWED_VENUES, [
      "coinbase",
      "kraken",
    ]),
    assetAllowlist: listFromEnv<Asset>(env.LIVE_ALLOWED_ASSETS, [
      "BTC",
      "ETH",
      "USDC",
      "USD",
    ]),
  };
}

export function evaluateLiveTradeRequest(input: {
  signal: RelativeValueSignal;
  requestedNotionalUsd: number;
  settings?: LiveTradingSettings;
  manualConfirmation: boolean;
  currentDailyPnlUsd: number;
  systemTrust?: SystemTrustVerdict;
}): LiveTradeEvaluation {
  const settings = input.settings ?? readLiveTradingSettings();
  const reasons: string[] = [];

  if (!settings.enabled) {
    reasons.push("ENABLE_LIVE_TRADING is not true.");
  }

  if (settings.killSwitchActive) {
    reasons.push("Live kill switch is active.");
  }

  if (input.systemTrust?.blocksLiveTrading) {
    reasons.push(`System trust blocks live trading: ${input.systemTrust.summary}`);
  }

  if (settings.manualConfirmationRequired && !input.manualConfirmation) {
    reasons.push("Manual confirmation is required.");
  }

  if (settings.maxLeverage > 1) {
    reasons.push("Max leverage must remain 1x for this private MVP.");
  }

  if (input.requestedNotionalUsd > settings.maxTradeUsd) {
    reasons.push(
      `Requested notional $${input.requestedNotionalUsd} exceeds live max $${settings.maxTradeUsd}.`,
    );
  }

  if (input.currentDailyPnlUsd <= -Math.abs(settings.dailyLossLimitUsd)) {
    reasons.push("Daily loss limit has been reached.");
  }

  if (!input.signal.eligibleForLiveTrading) {
    reasons.push("Signal is not marked eligible for live trading.");
  }

  const [base, quote] = input.signal.assetPair.split("/") as [Asset, Asset | undefined];
  if (!settings.assetAllowlist.includes(base) || (quote && !settings.assetAllowlist.includes(quote))) {
    reasons.push("Signal contains an asset outside the live allowlist.");
  }

  const venueAllowed = settings.venueAllowlist.some((venue) =>
    input.signal.venue.includes(venue),
  );
  if (!venueAllowed) {
    reasons.push("Signal venue is outside the live allowlist.");
  }

  return {
    allowed: reasons.length === 0,
    dryRun: settings.dryRun,
    reasons,
    auditLabel:
      reasons.length === 0
        ? "live.trade_attempt.allowed"
        : "live.trade_attempt.blocked",
  };
}

function numberFromEnv(value: string | undefined, fallback: number): number {
  if (!value) return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function listFromEnv<T extends string>(value: string | undefined, fallback: T[]): T[] {
  if (!value) return fallback;
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean) as T[];
}
