/**
 * US-23 (S5-5): decide's rules and order, repeated in the browser, on the cases the parity file has none of: versions,
 * a second rule in another unit, amounts written with commas or a scale word, the leave-out hint, decide's own order
 * when only a must-have changes, a tie, a missing score, and a result where decide showed nothing. Each expectation is
 * what backend/app/decide (rules.py, units.py, scoring.py) gives for the same input. Made-up products only.
 */
import { describe, expect, it } from "vitest";
import {
  checkRule,
  NO_SINGLE_CHANGE,
  quantity,
  refilter,
  relaxationHint,
} from "@/lib/results/refilter";
import { originalChoices, outcome, type Choices } from "@/lib/results/rerank";
import { rescore } from "@/lib/results/score";
import type { CheckedRead, RerankData, RerankRule } from "@/lib/results/types";

const rule = (
  r: Partial<RerankRule> & Pick<RerankRule, "attribute" | "operator" | "value">,
): RerankRule => ({
  id: `${r.attribute}_${r.operator}`,
  unit: null,
  sourceText: "the rule",
  active: true,
  ...r,
});

const read = (
  r: Partial<CheckedRead> & Pick<CheckedRead, "attribute" | "values">,
): CheckedRead => ({
  state: "known",
  unit: null,
  anyVersion: false,
  fromNames: 0,
  ...r,
});

describe("versions (a RAM or storage size its names state)", () => {
  const atLeast16 = rule({
    attribute: "ram_gb",
    operator: ">=",
    value: 16,
    unit: "GB",
    sourceText: "at least 16 GB RAM",
  });
  const versions = (values: number[], fromNames = 0, state = "known") =>
    read({ attribute: "ram_gb", values, unit: "gb", anyVersion: true, fromNames, state });

  it("passes when any version keeps the rule, one only a name states too", () => {
    expect(checkRule(atLeast16, versions([8, 16], 1)).verdict).toBe("pass");
  });

  it("fails when every version breaks it, naming each, smallest first", () => {
    expect(checkRule(atLeast16, versions([12, 8]))).toMatchObject({
      verdict: "fail",
      reason: 'It fails "at least 16 GB RAM" in every version we found: 8 GB and 12 GB',
    });
  });

  it("says when the one version it has comes only from a name", () => {
    expect(checkRule(atLeast16, versions([8], 1)).reason).toBe(
      'It fails "at least 16 GB RAM": it has 8 GB, going by the name a source gave it, not a spec page',
    );
  });

  it("can't tell when the claims disagree with only one side kept, and no version keeps the rule", () => {
    expect(checkRule(atLeast16, versions([8, 12], 1, "disputed"))).toMatchObject({
      verdict: "unknown",
      reason: "Sources disagree on its RAM: one says 8 GB",
    });
  });

  it("checks a rule that compares no size by the claims' value alone, as decide does", () => {
    const named = rule({
      attribute: "ram_gb",
      operator: "==",
      value: "16 GB DDR5",
      sourceText: "16 GB DDR5",
    });
    // 8 (claimed) then 16 (from a name): an equality on text is no size rule, so only the claim counts.
    expect(checkRule(named, versions([8, 16], 1)).reason).toBe(
      'We couldn\'t compare its RAM ("8 GB") with "16 GB DDR5"',
    );
  });
});

describe("a disputed value under a must-have", () => {
  const atLeast16 = rule({
    attribute: "ram_gb",
    operator: ">=",
    value: 16,
    sourceText: "at least 16 GB RAM",
  });
  const sides = (values: number[]) =>
    read({ attribute: "ram_gb", values, unit: "gb", state: "disputed" });

  it("fails when every side breaks it, naming each side", () => {
    expect(checkRule(atLeast16, sides([8, 12]))).toMatchObject({
      verdict: "fail",
      reason: 'It fails "at least 16 GB RAM": sources give 8 GB or 12 GB',
    });
  });

  it("can't tell when the sides split", () => {
    expect(checkRule(atLeast16, sides([8, 32]))).toMatchObject({
      verdict: "unknown",
      reason: "Sources disagree: 8 GB or 32 GB",
    });
  });

  it("passes when every side keeps it", () => {
    expect(checkRule(atLeast16, sides([16, 32])).verdict).toBe("pass");
  });
});

describe("units and amounts, as decide reads them", () => {
  it("compares a second rule in its own unit: 512 GB is at most 1 TB", () => {
    const most1tb = rule({ attribute: "storage_gb", operator: "<=", value: 1, unit: "TB" });
    expect(
      checkRule(most1tb, read({ attribute: "storage_gb", values: [512], unit: "gb" })).verdict,
    ).toBe("pass");
    expect(
      checkRule(most1tb, read({ attribute: "storage_gb", values: [2000], unit: "gb" })).verdict,
    ).toBe("fail");
  });

  it("reads a target with commas and a unit: 4500 mAh is at least 1,000 mAh", () => {
    const battery = rule({ attribute: "battery_mah", operator: ">=", value: "1,000 mAh" });
    expect(
      checkRule(battery, read({ attribute: "battery_mah", values: [4500], unit: "mah" })).verdict,
    ).toBe("pass");
  });

  it("never reads 80k on a price rule as 80: decide can't compare it, so it can't tell", () => {
    const price = { attribute: "price_inr", values: [84990], unit: "INR" };
    expect(
      checkRule(rule({ attribute: "price_inr", operator: "<=", value: "80k" }), read(price))
        .verdict,
    ).toBe("unknown");
    expect(
      checkRule(rule({ attribute: "price_inr", operator: "<=", value: "₹80,000" }), read(price))
        .verdict,
    ).toBe("fail");
  });

  it("reads quantities as units.quantity() does", () => {
    expect(quantity("16 GB")).toEqual({ number: 16, unit: "gb", plain: true });
    expect(quantity("Wi-Fi 6")).toMatchObject({ number: 6, plain: false });
    expect(quantity("16 GB + 8 GB")).toBeNull();
    expect(quantity("₹1.15 lakh")).toEqual({ number: 115000, unit: "inr", plain: true });
    expect(quantity("₹2m")).toBeNull();
    expect(quantity("up to ₹80,000")).toBeNull();
  });
});

describe("the hint", () => {
  it("names the leave-out that holds back the most, as decide's does", () => {
    const blocked = [new Set(["exclusion:0"]), new Set(["exclusion:0"]), new Set(["budget"])];
    expect(
      relaxationHint(
        blocked,
        { min: null, max: 50000, currency: "INR" },
        [],
        [{ kind: "brand", value: "Kestrel" }],
      ),
    ).toBe("Allowing Kestrel again would let 2 products through.");
  });

  it("keys each leave-out reason to its exclusion, a sub-brand's too", () => {
    const data = {
      checked: {},
      held: [
        { productId: "p1", group: "filtered_out" as const, reasons: ["You ruled out Kestrel"] },
        {
          productId: "p2",
          group: "filtered_out" as const,
          reasons: ["Zip is a Kestrel brand. You ruled out Kestrel"],
        },
      ],
      budget: null,
      rules: [],
      exclusions: [
        { kind: "feature", value: "curved" },
        { kind: "brand", value: "Kestrel" },
      ],
    };
    const got = refilter(data, null, [], ["p1", "p2"]);
    expect([...got.get("p1")!.blocked]).toEqual(["exclusion:1"]);
    expect([...got.get("p2")!.blocked]).toEqual(["exclusion:1"]);
  });

  it("says no single change helps when every product has two blocks", () => {
    const blocked = [new Set(["budget", "ram"]), new Set(["budget", "ram"])];
    expect(
      relaxationHint(blocked, { min: null, max: 1, currency: "INR" }, [
        rule({ id: "ram", attribute: "ram_gb", operator: ">=", value: 8 }),
      ]),
    ).toBe(NO_SINGLE_CHANGE);
  });
});

/** Three shown products in decide's order, Y first by chance of being first though X scores more, and W held. */
function chanceOrder(): RerankData {
  const scored = (id: string, a: number, b: number) => ({
    productId: id,
    contributions: [
      { criterionKey: "a", score: a, state: "known" },
      { criterionKey: "b", score: b, state: "known" },
    ],
  });
  const reads = (price: number, touch: boolean) => [
    read({ attribute: "price_inr", values: [price], unit: "INR" }),
    read({ attribute: "has_touchscreen", values: [touch] }),
  ];
  return {
    weights: [
      { criterionId: "a", title: "A", weight: 5 },
      { criterionId: "b", title: "B", weight: 5 },
    ],
    scored: [scored("Y", 6, 7), scored("X", 8, 7), scored("Z", 5, 5)],
    budget: { min: null, max: 80000, currency: "INR" },
    rules: [
      rule({
        id: "touch",
        attribute: "has_touchscreen",
        operator: "==",
        value: true,
        sourceText: "with a touchscreen",
        active: false,
      }),
    ],
    checked: { Y: reads(60000, true), X: reads(61000, true), Z: reads(62000, false) },
    exclusions: [],
    held: [],
    others: [],
    canRefilter: true,
  };
}

const change = (d: RerankData, edit: (c: Choices) => void) => {
  const c = structuredClone(originalChoices(d));
  edit(c);
  return outcome(d, c);
};

describe("decide's order", () => {
  it("stands when only a must-have changes: nothing moves but what the rule takes out", () => {
    const o = change(chanceOrder(), (c) => (c.rulesOn["touch"] = true));
    expect(o.shown.map((r) => [r.productId, r.move])).toEqual([
      ["Y", 0],
      ["X", 0],
    ]);
    expect(o.filteredOut.map((h) => h.productId)).toEqual(["Z"]);
  });

  it("gives way to the new score when the weights change", () => {
    const o = change(chanceOrder(), (c) => (c.weights = { a: 10, b: 0 }));
    expect(o.shown.map((r) => r.productId)).toEqual(["X", "Y", "Z"]);
    expect(o.shown[0]!.move).toBe(1);
  });

  it("keeps decide's order in a tie, though the product IDs sort the other way", () => {
    const d = chanceOrder();
    d.scored = [
      { productId: "mock-Z", contributions: d.scored[0]!.contributions },
      { productId: "mock-A", contributions: d.scored[0]!.contributions },
    ];
    d.checked = { "mock-Z": d.checked["Y"]!, "mock-A": d.checked["X"]! };
    const o = change(d, (c) => (c.weights = { a: 2, b: 9 }));
    expect(o.shown.map((r) => r.productId)).toEqual(["mock-Z", "mock-A"]);
  });
});

describe("a missing score", () => {
  it("is the other products' average, marked estimated, never 0 (DR-09)", () => {
    const [p1, p2] = [
      { criterionKey: "cpu", score: 7, state: "known" },
      { criterionKey: "battery", score: 9, state: "known" },
    ];
    const got = rescore(
      [
        { productId: "P1", contributions: [p1!, p2!] },
        {
          productId: "P2",
          contributions: [p1!, { criterionKey: "battery", score: null, state: "unknown" }],
        },
      ],
      [
        { key: "cpu", weight: 5 },
        { key: "battery", weight: 5 },
      ],
    );
    const p2score = got.find((g) => g.productId === "P2")!;
    expect(p2score.score).toBe(80);
    expect(p2score.coverage).toBeLessThan(1);
  });
});

describe("a result where decide showed nothing", () => {
  it("lets products back in, unscored, when the budget rises", () => {
    const d = chanceOrder();
    const price = { Y: "₹60,000", X: "₹61,000", Z: "₹62,000" } as Record<string, string>;
    d.held = d.scored.map((s) => ({
      productId: s.productId,
      group: "filtered_out" as const,
      reasons: [`${price[s.productId]} is above your ₹50,000 budget`],
    }));
    d.scored = [];
    d.budget = { min: null, max: 50000, currency: "INR" };
    const o = change(d, (c) => (c.budget = { min: null, max: 70000, currency: "INR" }));
    expect(o.shown).toEqual([]);
    expect(o.letIn).toEqual(["Y", "X", "Z"]);
    expect(o.hint).toBeNull();
  });
});
