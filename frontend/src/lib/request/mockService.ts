/**
 * Mock adapter for the request service. Rules-based parsing only — no live
 * sources are contacted. Replace with a real adapter in service.ts.
 *
 * Mock conventions:
 * - Text containing "network error" fails once, then succeeds on retry.
 * - Very short requests (< 20 chars) are marked as read by the backup reader.
 */
import type { Brief, FollowUp, RequestService, UnderstandInput, UnderstandResponse } from "./types";

const PRODUCTS: { re: RegExp; name: string }[] = [
  { re: /\b(laptop|notebook|macbook)s?\b/i, name: "Laptop" },
  { re: /\b(earbuds?|earphones?|tws)\b/i, name: "Earbuds" },
  { re: /\bheadphones?\b/i, name: "Headphones" },
  { re: /\b(pram|stroller)s?\b/i, name: "Pram" },
  { re: /\b(smart ?watch|watch)(es)?\b/i, name: "Smartwatch" },
  { re: /\b(phone|smartphone|mobile)s?\b/i, name: "Phone" },
  { re: /\b(skincare|skin care|moisturi[sz]er|serum|sunscreen)\b/i, name: "Skincare" },
  { re: /\b(handbag|purse|tote)s?\b/i, name: "Handbag" },
  { re: /\b(health|bp monitor|massager|oximeter)\b/i, name: "Health product" },
  { re: /\b(tv|television)s?\b/i, name: "TV" },
];

const RECIPIENT_RE =
  /\bfor (?:my )?(mom|mum|mother|dad|father|wife|husband|sister|brother|friend|girlfriend|boyfriend)\b/i;
const AMBIGUOUS_RE = /\b(something|gift|present|anything)\b/i;
const TERM = String.raw`(?=,|;|\.| and | but | under | below | within | not | with |$)`;

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export function detectProduct(text: string): string | null {
  return PRODUCTS.find((p) => p.re.test(text))?.name ?? null;
}

function toAmount(num: string | undefined, unit?: string): number {
  const n = parseFloat((num ?? "").replace(/,/g, ""));
  const u = unit?.toLowerCase();
  if (u === "k") return Math.round(n * 1000);
  if (u === "l" || u === "lakh" || u === "lakhs") return Math.round(n * 100000);
  return Math.round(n);
}

const AMT = String.raw`(?:₹|rs\.?|inr)?\s?([\d,]+(?:\.\d+)?)\s?(k|lakhs?|l)?\b`;

export function parseBudget(text: string): Brief["budget"] {
  const between = new RegExp(String.raw`between\s+${AMT}\s+(?:and|to|-)\s+${AMT}`, "i").exec(text);
  if (between)
    return { min: toAmount(between[1], between[2]), max: toAmount(between[3], between[4]) };
  const upper = new RegExp(
    String.raw`(?:under|below|less than|up ?to|max(?:imum)?|within)\s+${AMT}`,
    "i",
  ).exec(text);
  if (upper) return { min: null, max: toAmount(upper[1], upper[2]) };
  const lower = new RegExp(
    String.raw`(?:above|over|more than|at least|from)\s+${AMT}(?!\s?(?:gb|tb|mp|mah|hz|inch))`,
    "i",
  ).exec(text);
  if (lower && /₹|rs|inr|k\b|lakh/i.test(lower[0]))
    return { min: toAmount(lower[1], lower[2]), max: null };
  return null;
}

export function parseRequest(text: string, productHint?: string): Brief | null {
  const product = (productHint && detectProduct(productHint)) || detectProduct(text);
  if (!product) return null;

  let usedFor: string | null = null;
  const recipient = RECIPIENT_RE.exec(text);
  if (recipient) usedFor = `Gift for ${(recipient[1] ?? "").toLowerCase()}`;
  else {
    const m = new RegExp(String.raw`\bfor (?:a |an |the |my )?([a-z][a-z ]*?)${TERM}`, "i").exec(
      text,
    );
    if (m && !/^\d/.test(m[1] ?? "")) usedFor = cap((m[1] ?? "").trim());
  }

  const mustHaves = Array.from(
    text.matchAll(
      new RegExp(
        String.raw`\b(at least [^,;]+?|not too [^,;]+?|with [^,;]+?|must have [^,;]+?|minimum [^,;]+?)${TERM}`,
        "gi",
      ),
    ),
  )
    .map((m) => (m[1] ?? "").replace(/^(with|must have)\s+/i, "").trim())
    .filter(Boolean);

  const exclusions = Array.from(
    text.matchAll(/\b(?:no|without|avoid|don't want|do not want)\s+([^,;.]+?)(?=,|;|\.| and |$)/gi),
  ).map((m) => (m[1] ?? "").trim());

  const budget = parseBudget(text);
  const region = /₹|\brs\b|inr|\d\s?k\b|lakh/i.test(text) ? "India" : null;

  return {
    product,
    usedFor,
    budget,
    region,
    mustHaves,
    exclusions,
    source: text.trim().length < 20 ? "fallback" : "model",
  };
}

function followUpFor(text: string): FollowUp {
  const r = RECIPIENT_RE.exec(text)?.[1]?.toLowerCase();
  if (r && /mom|mum|mother|wife|sister|girlfriend/.test(r))
    return {
      question: `What kind of thing are you thinking of for your ${r}?`,
      options: ["Skincare", "Phone", "Handbag", "Health product"],
    };
  if (r)
    return {
      question: `What kind of thing are you thinking of for your ${r}?`,
      options: ["Smartwatch", "Phone", "Headphones", "Health product"],
    };
  return {
    question: "What kind of product do you have in mind?",
    options: ["Headphones", "Smartwatch", "Skincare"],
  };
}

export function resolve(input: UnderstandInput): UnderstandResponse {
  const { text, clarification } = input;
  if (clarification !== undefined) {
    const brief = parseRequest(text, clarification);
    return brief && detectProduct(clarification)
      ? { kind: "understood", brief }
      : { kind: "examples" };
  }
  const brief = parseRequest(text);
  if (brief) return { kind: "understood", brief };
  if (RECIPIENT_RE.test(text) || AMBIGUOUS_RE.test(text))
    return { kind: "follow-up", followUp: followUpFor(text) };
  return { kind: "examples" };
}

export function createMockService(latency = 700): RequestService {
  const failedOnce = new Set<string>();
  return {
    async understand(input) {
      await delay(latency);
      if (/network error/i.test(input.text) && !failedOnce.has(input.text)) {
        failedOnce.add(input.text);
        throw new Error("We couldn't reach ShopSense. Check your connection and try again.");
      }
      return resolve(input);
    },
    async saveBrief(brief) {
      await delay(latency / 2);
      return brief;
    },
  };
}
