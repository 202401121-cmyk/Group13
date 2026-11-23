/**
 * US-23 (S5-5 DECIDED 2): the browser's re-rank scores exactly as decide does. Every case in fixtures/parity.json was
 * written by backend/scripts/export_parity_cases.py: decide's own shown products and contributions, new slider
 * weights, and the real decide code's weighted ranking with them. The same order, and scores within 1e-6.
 */
import { describe, expect, it } from "vitest";
import parity from "./fixtures/parity.json";
import { pyRound, rescore, type ScoreInput } from "@/lib/results/score";
import { refilter, relaxationHint, type Budget } from "@/lib/results/refilter";
import type { CheckedRead, RerankRule } from "@/lib/results/types";

interface Case {
  id: string;
  weights: { key: string; weight: number }[];
  products: {
    product_id: string;
    contributions: { criterion_key: string; score: number | null; state: string }[];
  }[];
  expected: { product_id: string; score: number | null; coverage: number }[];
}

const cases = (parity as { cases: Case[] }).cases;

function inputs(c: Case): ScoreInput[] {
  return c.products.map((p) => ({
    productId: p.product_id,
    contributions: p.contributions.map((x) => ({
      criterionKey: x.criterion_key,
      score: x.score,
      state: x.state,
    })),
  }));
}

describe("parity with decide's weighted score", () => {
  it("has the 30 cases the backend wrote", () => {
    expect(cases).toHaveLength(30);
    expect(
      cases.filter((c) => c.expected.some((e) => e.score !== null)).length,
    ).toBeGreaterThanOrEqual(15);
  });

  it.each(cases.map((c) => [c.id, c] as const))("%s: same order, scores within 1e-6", (_id, c) => {
    const got = rescore(inputs(c), c.weights);
    expect(got.map((g) => g.productId)).toEqual(c.expected.map((e) => e.product_id));
    got.forEach((g, i) => {
      const want = c.expected[i]!;
      if (want.score === null) expect(g.score).toBeNull();
      else expect(Math.abs(g.score! - want.score)).toBeLessThanOrEqual(1e-6);
      expect(Math.abs(g.coverage - want.coverage)).toBeLessThanOrEqual(1e-6);
    });
  });
});

describe("Python's rounding", () => {
  it.each([
    [72.125, 2, 72.12], // an exact binary half goes to even, where toFixed would give 72.13
    [72.375, 2, 72.38],
    [0.5, 0, 0],
    [1.5, 0, 2],
    [2.675, 2, 2.67], // 2.675 is a little under its decimal, so it rounds down, as in Python
    [-72.125, 2, -72.12],
    [1.53415, 4, 1.5341], // just under its decimal, as Python finds it
    [100, 2, 100],
  ])("round(%s, %s) is %s", (x, digits, want) => {
    expect(pyRound(x, digits)).toBe(want);
  });
});

// --- the filter: decide's own groups and reasons, with the budget and rules changed (DECIDED 6) -------------------

interface WireMoney {
  amount: number;
  currency: string;
}
interface WireBudget {
  min: WireMoney | null;
  max: WireMoney | null;
}
interface FilterCase {
  id: string;
  budget: WireBudget | null;
  exclusions: { kind: string; value: string }[];
  rules: {
    id: string;
    attribute: string;
    operator: string;
    value: number | string | boolean | string[];
    unit: string | null;
    source_text: string;
    active: boolean;
  }[];
  shown: string[];
  held: { product_id: string; group: "filtered_out" | "cant_tell"; reasons: string[] }[];
  checked_values: {
    product_id: string;
    attribute: string;
    state: string;
    values: (number | string | boolean)[];
    unit: string | null;
    any_version: boolean;
    from_names: number;
  }[];
  changes: {
    change: string;
    budget: WireBudget | null;
    rules_on: Record<string, boolean>;
    shown: string[];
    held: FilterCase["held"];
    hint: string | null;
  }[];
}

const filterCases = (parity as unknown as { filter_cases: FilterCase[] }).filter_cases;

function budgetOf(b: WireBudget | null): Budget | null {
  if (!b) return null;
  return {
    min: b.min?.amount ?? null,
    max: b.max?.amount ?? null,
    currency: (b.max ?? b.min)!.currency,
  };
}

function dataOf(f: FilterCase) {
  const checked: Record<string, CheckedRead[]> = {};
  for (const v of f.checked_values)
    (checked[v.product_id] ??= []).push({
      attribute: v.attribute,
      state: v.state,
      values: v.values,
      unit: v.unit,
      anyVersion: v.any_version,
      fromNames: v.from_names,
    });
  const rules: RerankRule[] = f.rules.map((r) => ({
    id: r.id,
    attribute: r.attribute,
    operator: r.operator,
    value: r.value,
    unit: r.unit,
    sourceText: r.source_text,
    active: r.active,
  }));
  const held = f.held.map((h) => ({ productId: h.product_id, group: h.group, reasons: h.reasons }));
  return { checked, rules, held, budget: budgetOf(f.budget), exclusions: f.exclusions };
}

describe("parity with decide's filter", () => {
  it("covers every brief and recorded run, with the budget moved both ways and each rule switched", () => {
    expect(filterCases).toHaveLength(10);
    expect(filterCases.flatMap((f) => f.changes).length).toBeGreaterThanOrEqual(30);
  });

  it.each(
    filterCases.flatMap((f) => f.changes.map((c) => [`${f.id}: ${c.change}`, f, c] as const)),
  )("%s: the same groups and reasons", (_id, f, change) => {
    const data = dataOf(f);
    const rules = data.rules.map((r) => ({ ...r, active: change.rules_on[r.id] ?? r.active }));
    const everyone = [...f.shown, ...f.held.map((h) => h.product_id)];
    const got = refilter(data, budgetOf(change.budget), rules, everyone);
    const shown = everyone.filter((id) => !got.get(id)!.held).sort();
    const held = everyone
      .filter((id) => got.get(id)!.held)
      .sort()
      .map((id) => ({
        product_id: id,
        group: got.get(id)!.held!.group,
        reasons: got.get(id)!.held!.reasons,
      }));
    expect(shown).toEqual(change.shown);
    expect(held).toEqual(change.held);
    if (change.hint) {
      const blocked = everyone.map((id) => got.get(id)!.blocked);
      expect(
        relaxationHint(
          blocked,
          budgetOf(change.budget),
          rules.filter((r) => r.active),
          data.exclusions,
        ),
      ).toBe(change.hint);
    }
  });
});
