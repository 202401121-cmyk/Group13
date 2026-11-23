/**
 * Decide's weighted score, in the browser (US-23, P-42): the one place outside the backend a product is scored, so the
 * shopper can move a weight and see the order change with no server call. It does exactly what
 * backend/app/decide/scoring.py does for the weighted ranking, step by step and in the same order of operations, so the
 * numbers match to the last bit (src/test/parity.test.ts checks 30 cases the real decide code scored):
 *
 * - the slider weights are normalised as the backend normalises them (contracts/rubric.py normalize): each over their
 *   sum, or equal shares when they are all 0;
 * - only the criteria some shown product has a score on count, their shares renormalised over them (_weights): equal
 *   shares when those add up to 0;
 * - each contribution's score is the one decide gave: a source's opinion, a compared value or an estimate (DR-09). An
 *   estimate stays an estimate, and an unknown criterion is left out, never scored 0;
 * - score = min(100, Σ score × share × 10), rounded to 2 places as Python rounds (half to even on the exact value);
 * - coverage, the share of the score backed by Known or Disputed values, as _coverage works it out;
 * - order: score, then coverage, high first, then product id.
 *
 * No simulation runs here: chance of being first and confidence come only from the backend (DECIDED 4).
 */

export interface ScoredContribution {
  criterionKey: string;
  /** Null for a criterion with no score (unknown); never 0 for one. */
  score: number | null;
  /** The value's state as decide gave it: "known", "disputed", "estimated" or "unknown". */
  state: string;
}

export interface ScoreInput {
  productId: string;
  contributions: ScoredContribution[];
}

export interface WeightInput {
  key: string;
  /** The slider's 0 to 10. */
  weight: number;
}

export interface Scored {
  productId: string;
  /** Null when no shown product has a score on any criterion: nothing is ranked (S3-10b). */
  score: number | null;
  coverage: number;
}

const TINY = 1e-9;
const EVIDENCE = new Set(["known", "disputed"]);

function sum(values: number[]): number {
  let total = 0;
  for (const v of values) total += v; // left to right, as Python 3.11's sum()
  return total;
}

/** contracts/rubric.py normalize: each weight over their sum; all zero gives equal shares. */
export function normalizeWeights(weights: WeightInput[]): Map<string, number> {
  const total = sum(weights.map((w) => w.weight));
  return new Map(weights.map((w) => [w.key, total === 0 ? 1 / weights.length : w.weight / total]));
}

/** The shares over the criteria some shown product has a score on (scoring._weights). */
function presentShares(normalized: Map<string, number>, present: string[]): Map<string, number> {
  const total = sum(present.map((k) => normalized.get(k) ?? 0));
  return new Map(
    present.map((k) => [k, total <= 0 ? 1 / present.length : (normalized.get(k) ?? 0) / total]),
  );
}

/**
 * Every shown product scored on these weights, best first, as decide's weighted ranking would score and order them.
 * `products` come in decide's order; with nothing to rank on they keep it, as decide lists them by name then.
 */
export function rescore(products: ScoreInput[], weights: WeightInput[]): Scored[] {
  const keys = weights.map((w) => w.key);
  const present = keys.filter((k) =>
    products.some((p) => p.contributions.some((c) => c.criterionKey === k && c.score !== null)),
  );
  if (present.length === 0)
    return products.map((p) => ({ productId: p.productId, score: null, coverage: 0 }));
  const shares = presentShares(normalizeWeights(weights), present);
  // A product with no score on any counted criterion (never one decide shows) has nothing to rank on: it stays
  // after every scored product, in the order given, and is never scored 0.
  const unknown = (p: ScoreInput) =>
    present.every((k) => !p.contributions.some((c) => c.criterionKey === k && c.score !== null));
  // DR-09: a missing score is the average of the other shown products' on that criterion, never 0 (decide never
  // sends one for a product it showed; an older or odd result might).
  const peers = (key: string, productId: string) =>
    products.flatMap((p) =>
      p.productId === productId
        ? []
        : p.contributions.flatMap((c) =>
            c.criterionKey === key && c.score !== null ? [c.score] : [],
          ),
    );
  const scored = products
    .filter((p) => !unknown(p))
    .map((p) => scoreOne(p, keys, shares, (key) => peers(key, p.productId)));
  scored.sort(
    (a, b) =>
      b.score! - a.score! ||
      b.coverage - a.coverage ||
      (a.productId < b.productId ? -1 : a.productId > b.productId ? 1 : 0),
  );
  return [
    ...scored,
    ...products.filter(unknown).map((p) => ({ productId: p.productId, score: null, coverage: 0 })),
  ];
}

function scoreOne(
  product: ScoreInput,
  keys: string[],
  shares: Map<string, number>,
  peers: (key: string) => number[],
): Scored {
  const own = new Map(product.contributions.map((c) => [c.criterionKey, c]));
  const rows = keys.map((key) => {
    const share = shares.get(key);
    const c = own.get(key);
    if (share === undefined)
      return { score: null as number | null, share: 0, contribution: 0, state: "unknown" };
    if (!c || c.score === null) {
      const others = peers(key);
      const score = pyRound(sum(others) / others.length, 2); // a counted criterion: some other product has it
      return { score, share, contribution: pyRound(score * share, 4), state: "estimated" };
    }
    return { score: c.score, share, contribution: pyRound(c.score * share, 4), state: c.state };
  });
  const exact = sum(rows.map((r) => (r.score ?? 0) * r.share));
  const evidenced = rows.filter((r) => EVIDENCE.has(r.state));
  const total = sum(rows.map((r) => r.contribution));
  const share =
    total > TINY
      ? sum(evidenced.map((r) => r.contribution)) / total
      : sum(evidenced.map((r) => r.share));
  return {
    productId: product.productId,
    score: pyRound(Math.min(100, exact * 10), 2),
    coverage: pyRound(Math.min(1, Math.max(0, share)), 3),
  };
}

/** A double as `mantissa × 2^exponent`, exactly. */
function decompose(x: number): [bigint, number] {
  const view = new DataView(new ArrayBuffer(8));
  view.setFloat64(0, x);
  const bits = view.getBigUint64(0);
  const exponent = Number((bits >> 52n) & 0x7ffn);
  const fraction = bits & ((1n << 52n) - 1n);
  return exponent === 0 ? [fraction, -1074] : [fraction | (1n << 52n), exponent - 1075];
}

/**
 * Python's round(x, digits): the nearest multiple of 10^-digits to x's exact binary value, a tie going to the even
 * one, given as the double nearest that decimal. JavaScript's toFixed rounds an exact tie up, so 72.125 would become
 * 72.13 where decide writes 72.12.
 */
export function pyRound(x: number, digits: number): number {
  if (!Number.isFinite(x) || x === 0) return x;
  const [mantissa, exponent] = decompose(Math.abs(x));
  if (exponent >= 0) return x; // a whole number already
  const k = BigInt(-exponent);
  const scaled = mantissa * 10n ** BigInt(digits); // |x| × 10^digits × 2^k, exactly
  const floor = scaled >> k;
  const rest = scaled - (floor << k);
  const half = 1n << (k - 1n);
  const n = rest > half || (rest === half && (floor & 1n) === 1n) ? floor + 1n : floor;
  const rounded = Number(n) / 10 ** digits;
  return x < 0 ? -rounded : rounded;
}
