const SECRET_PATTERNS = [
  /\b(?:sk|pk|rk|xoxb|ghp|gho|twilio|telegram)_[A-Za-z0-9_-]{12,}\b/gi,
  /\b[A-Za-z0-9_-]{32,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b/g,
  /\b0x[a-fA-F0-9]{16,}\b/g,
  /\b(?:bc1|[13])[a-zA-HJ-NP-Z0-9]{25,}\b/g,
  /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/g,
];

export function redactSensitiveText(input: string): string {
  return SECRET_PATTERNS.reduce(
    (text, pattern) => text.replace(pattern, "[REDACTED]"),
    input,
  ).replace(/\b(balance|cash|equity):\s*\$?[0-9,]+(?:\.\d+)?/gi, "$1: [REDACTED]");
}

export function formatAlertMessage(input: {
  severity: string;
  title: string;
  message: string;
  tradingImpact: string;
}): string {
  return redactSensitiveText(
    `[${input.severity}] ${input.title}\n${input.message}\nTrading impact: ${input.tradingImpact}`,
  );
}
