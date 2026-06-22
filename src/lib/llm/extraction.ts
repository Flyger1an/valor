import type { ExtractedRiskItem } from "@/lib/llm/types";

export function extractRiskItemsLocally(text: string): ExtractedRiskItem[] {
  const lower = text.toLowerCase();
  const items: ExtractedRiskItem[] = [];

  if (/(exploit|hack|compromise|bridge)/i.test(text)) {
    items.push({
      severity: lower.includes("confirmed") || lower.includes("loss") ? "CRITICAL" : "WATCH",
      title: "Security incident candidate",
      summary: text.slice(0, 280),
      affectedAssets: extractAssets(text),
      affectedVenues: extractVenues(text),
      confidence: 0.68,
    });
  }

  if (/(depeg|below peg|0\.99|0\.98|stablecoin)/i.test(text)) {
    items.push({
      severity: /(0\.98|0\.97|below 0\.99|severe)/i.test(text)
        ? "CRITICAL"
        : "WATCH",
      title: "Stablecoin peg-risk candidate",
      summary: text.slice(0, 280),
      affectedAssets: extractAssets(text),
      affectedVenues: extractVenues(text),
      confidence: 0.62,
    });
  }

  if (/(withdrawal|paused|maintenance|status page|outage)/i.test(text)) {
    items.push({
      severity: /(paused|outage|halted|suspended)/i.test(text)
        ? "CRITICAL"
        : "WATCH",
      title: "Venue operations candidate",
      summary: text.slice(0, 280),
      affectedAssets: extractAssets(text),
      affectedVenues: extractVenues(text),
      confidence: 0.64,
    });
  }

  return items;
}

export function structuredExtractionPrompt(rawText: string): string {
  return `Extract risk intelligence from the text below. Return only JSON with this shape:
{
  "items": [
    {
      "severity": "INFO|WATCH|TRADEABLE|CRITICAL|BLACK",
      "title": "short title",
      "summary": "source-grounded summary",
      "affectedAssets": ["BTC"],
      "affectedVenues": ["coinbase"],
      "confidence": 0.0
    }
  ]
}

Rules:
- Do not infer insider information.
- Do not recommend trades.
- Mark uncertain items WATCH.
- Never include secrets, balances, addresses, account identifiers, or API keys.

Text:
${rawText.slice(0, 12000)}`;
}

function extractAssets(text: string): string[] {
  return [...new Set(text.match(/\b(BTC|ETH|SOL|USDC|USDT|DAI)\b/g) ?? [])];
}

function extractVenues(text: string): string[] {
  return [
    ...new Set(
      text.match(/\b(coinbase|kraken|binance|okx|deribit|uniswap)\b/gi) ?? [],
    ),
  ].map((venue) => venue.toLowerCase());
}
