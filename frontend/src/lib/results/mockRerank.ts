/**
 * Mock data for re-ranking and re-filtering in the browser (US-23), shaped as a live result's (liveAdapter.ts):
 * made-up criterion scores, a budget, rules and what the filter read. Nothing here is a real product or price.
 *
 * Each product's criterion scores are made so the shopper's own weights give back its shown score, and so moving a
 * weight moves products: the first products do best on the first criteria, the last ones on the last.
 */
import type { CheckedRead, ProductResult, RerankData, RerankRule, RubricItem } from "./types";

const TILT = 1.5; // big enough that one weight moved can turn the order round

export const MOCK_BUDGET = { min: null, max: 80000, currency: "INR" };
export const RAM_RULE: RerankRule = {
  id: "ram_gb_min_mock",
  attribute: "ram_gb",
  operator: ">=",
  value: 8,
  unit: "GB",
  sourceText: "at least 8 GB RAM",
  active: true,
};
export const TOUCH_RULE: RerankRule = {
  id: "has_touchscreen_mock",
  attribute: "has_touchscreen",
  operator: "==",
  value: true,
  unit: null,
  sourceText: "with a touchscreen",
  active: false,
};

const STATE: Record<string, string> = {
  verified: "known",
  disputed: "disputed",
  estimated: "estimated",
};

function scoredOf(products: ProductResult[], rubric: RubricItem[]): RerankData["scored"] {
  const total = rubric.reduce((a, r) => a + r.weight, 0);
  const shares = rubric.map((r) => (total > 0 ? r.weight / total : 1 / rubric.length));
  const centre = shares.reduce((a, s, i) => a + s * i, 0);
  const offsets = rubric.map((_, i) => i - centre);
  const widest = Math.max(0, ...offsets.map(Math.abs));
  return products.map((p, k) => {
    const shown = p.score == null ? null : p.score / 10;
    const lean = products.length > 1 ? TILT * ((2 * k) / (products.length - 1) - 1) : 0;
    // Only as far as its scores stay within 0 to 10: the shopper's own weights then give back its shown score exactly.
    const room = shown === null || widest === 0 ? 0 : Math.min(shown, 10 - shown) / widest;
    const tilt = Math.sign(lean) * Math.min(Math.abs(lean), room);
    return {
      productId: p.id,
      contributions: rubric.map((r, i) => {
        const row = p.criteria.find((c) => c.criterionId === r.criterionId);
        const score = shown === null ? null : shown + tilt * offsets[i]!;
        // An unknown value has an estimate from the other products, as decide gives it (DR-09).
        return {
          criterionKey: r.criterionId,
          score,
          state: STATE[row?.state ?? ""] ?? "estimated",
        };
      }),
    };
  });
}

function price(p: ProductResult): number | null {
  const prices = p.offers.flatMap((o) => (o.price == null ? [] : [o.price]));
  return prices.length ? Math.min(...prices) : null;
}

/**
 * The price decide's filter read for a product it showed. It reads the price from the sources' claims, not from store
 * offers, so a product shown with no offer still had one: the sourced-price scenario's ₹71,990 stands in for it.
 */
const SOURCED_PRICE = 71990;
const checkedPrice = (p: ProductResult) => price(p) ?? SOURCED_PRICE;

function reads(priceInr: number | null, ram: number | null, touch: boolean | null): CheckedRead[] {
  const read = (
    attribute: string,
    value: number | boolean | null,
    unit: string | null,
  ): CheckedRead => ({
    attribute,
    state: value === null ? "unknown" : "known",
    values: value === null ? [] : [value],
    unit: value === null ? null : unit,
    anyVersion: false,
    fromNames: 0,
  });
  return [
    read("price_inr", priceInr, "INR"),
    read("ram_gb", ram, "gb"),
    read("has_touchscreen", touch, null),
  ];
}

/** The budget decide used: ₹80,000, or the next ₹10,000 above the dearest product it showed. */
function budgetFor(products: ProductResult[]): RerankData["budget"] {
  const dearest = Math.max(0, ...products.flatMap((p) => price(p) ?? []));
  return dearest <= MOCK_BUDGET.max
    ? MOCK_BUDGET
    : { ...MOCK_BUDGET, max: Math.ceil(dearest / 10_000) * 10_000 };
}

/** The plain case: every shown product within the budget and the rule, none held back. */
export function mockRerank(products: ProductResult[], rubric: RubricItem[]): RerankData | null {
  if (rubric.length === 0 || !products.some((p) => p.score != null)) return null;
  return {
    weights: rubric.map((r) => ({ criterionId: r.criterionId, title: r.title, weight: r.weight })),
    scored: scoredOf(products, rubric),
    budget: budgetFor(products),
    rules: [RAM_RULE],
    checked: Object.fromEntries(products.map((p) => [p.id, reads(checkedPrice(p), 16, true)])),
    exclusions: [],
    held: [],
    others: [],
    canRefilter: true,
  };
}

/**
 * Products held back, each for a reason a change can lift or not: D is over the budget, E's RAM is unknown, and F
 * was left out by brand, which no budget or rule here lets back. B has no touchscreen, for the rule that is off.
 */
export function mockRefilter(
  products: ProductResult[],
  held: {
    product: ProductResult;
    price: number;
    ram: number | null;
    group: "filtered_out" | "cant_tell";
    reason: string;
  }[],
  rubric: RubricItem[],
): RerankData {
  const base = mockRerank(products, rubric)!;
  const touch = new Map(products.map((p, i) => [p.id, i !== 1]));
  return {
    ...base,
    rules: [RAM_RULE, TOUCH_RULE],
    exclusions: [{ kind: "brand", value: "Kestrel" }],
    checked: Object.fromEntries([
      ...products.map(
        (p, i) =>
          [p.id, reads(checkedPrice(p), i === 2 ? 32 : 16, touch.get(p.id) ?? true)] as const,
      ),
      ...held.map((h) => [h.product.id, reads(h.price, h.ram, true)] as const),
    ]),
    held: held.map((h) => ({ productId: h.product.id, group: h.group, reasons: [h.reason] })),
    others: held.map((h) => h.product),
  };
}
