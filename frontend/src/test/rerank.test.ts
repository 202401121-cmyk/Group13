/**
 * US-23 (S5-5): what a change to a finished result does, worked out in the browser from what it already holds.
 * Made-up products only (the mock "refilter" and "success" scenarios). Parity with decide itself is in parity.test.ts.
 */
import { describe, expect, it } from "vitest";
import { buildMockResult } from "@/lib/results/mockResults";
import { MOCK_SCENARIOS } from "@/lib/results/mockService";
import {
  isOriginal,
  moveText,
  originalChoices,
  originalGroups,
  outcome,
  type Choices,
} from "@/lib/results/rerank";
import { NO_SINGLE_CHANGE } from "@/lib/results/refilter";
import { rescore } from "@/lib/results/score";
import type { RerankData, ResearchInput } from "@/lib/results/types";

const input: ResearchInput = {
  sessionId: "sess_000000000000000000000000",
  category: "laptop",
  rubric: [
    { criterionId: "cpu", title: "CPU speed", weight: 8 },
    { criterionId: "battery", title: "Battery life", weight: 6 },
    { criterionId: "keys", title: "Key comfort", weight: 4 },
  ],
  mustHaves: [],
  exclusions: [],
};

function data(scenario = "refilter"): RerankData {
  return buildMockResult(input, scenario).rerank!;
}

const ids = (d: RerankData) => ({
  A: d.scored[0]!.productId,
  B: d.scored[1]!.productId,
  C: d.scored[2]!.productId,
  D: d.held[0]!.productId,
  E: d.held[1]!.productId,
  F: d.held[2]!.productId,
});

function change(d: RerankData, edit: (c: Choices) => void): Choices {
  const c = structuredClone(originalChoices(d));
  edit(c);
  return c;
}

describe("as decided", () => {
  it("reads every held product back as decide held it, and changes nothing", () => {
    const d = data();
    const { D, E, F } = ids(d);
    const groups = originalGroups(d);
    expect(groups[D]?.reasons).toEqual(["₹84,990 is above your ₹80,000 budget"]);
    expect(groups[E]).toEqual({
      group: "cant_tell",
      reasons: [`We couldn't find its RAM, so we can't check "at least 8 GB RAM"`],
      attribute: "ram_gb",
    });
    expect(groups[F]).toBeNull(); // a leave-out: not in what the filter read
    const o = outcome(d, originalChoices(d));
    expect(o.changed).toBe(false);
    expect(o.filteredOut.map((h) => h.productId).sort()).toEqual([D, F].sort());
    expect(o.filteredOut.find((h) => h.productId === F)?.reasons).toEqual([
      "You ruled out Kestrel",
    ]);
    expect(o.cantTell.map((h) => h.productId)).toEqual([E]);
  });
});

describe("re-filtering", () => {
  it("a lower budget holds back what costs more, with decide's own sentence", () => {
    const d = data();
    const { A, B, C } = ids(d);
    const o = outcome(
      d,
      change(d, (c) => (c.budget = { ...c.budget!, max: 70000 })),
    );
    expect(o.changed).toBe(true);
    expect(o.shown.map((r) => r.productId)).toEqual([A]);
    expect(o.filteredOut.find((h) => h.productId === B)?.reasons).toEqual([
      "₹71,990 is above your ₹70,000 budget",
    ]);
    expect(o.filteredOut.find((h) => h.productId === C)?.reasons).toEqual([
      "₹75,490 is above your ₹70,000 budget",
    ]);
  });

  it("a budget below every price shows the hint: the change that lets the most through", () => {
    const d = data();
    const o = outcome(
      d,
      change(d, (c) => (c.budget = { ...c.budget!, max: 50000 })),
    );
    expect(o.shown).toEqual([]);
    expect(o.letIn).toEqual([]);
    // A, B, C and D are held by the budget alone; E also by its unknown RAM, F also by its brand
    expect(o.hint).toBe("Removing your ₹50,000 budget would let 4 products through.");
  });

  it("with no single change to help, the hint says so", () => {
    const d = data();
    const o = outcome(
      d,
      change(d, (c) => {
        c.budget = { ...c.budget!, max: 50000 };
        c.rulesOn = Object.fromEntries(Object.keys(c.rulesOn).map((k) => [k, true]));
      }),
    );
    expect(o.shown).toEqual([]);
    expect(o.hint).toBe("Removing your ₹50,000 budget would let 3 products through."); // B also lacks a touchscreen
    expect(NO_SINGLE_CHANGE).toMatch(/^No single change/);
  });

  it("a higher budget lets a product back in, unscored, after every scored one (P-47)", () => {
    const d = data();
    const { A, B, C, D } = ids(d);
    const o = outcome(
      d,
      change(d, (c) => (c.budget = { ...c.budget!, max: 90000 })),
    );
    expect(o.letIn).toEqual([D]);
    expect(o.shown.map((r) => r.productId)).toEqual([A, B, C]); // D is never ranked among them
    expect(o.filteredOut.map((h) => h.productId)).not.toContain(D);
  });

  it("a rule switched on holds back what breaks it", () => {
    const d = data();
    const { B } = ids(d);
    const o = outcome(
      d,
      change(d, (c) => (c.rulesOn["has_touchscreen_mock"] = true)),
    );
    expect(o.filteredOut.find((h) => h.productId === B)?.reasons).toEqual([
      `It fails "with a touchscreen": it doesn't`,
    ]);
  });

  it("unknown stays can't tell, never a pass, whatever the budget; only switching its rule off lets it in", () => {
    const d = data();
    const { E } = ids(d);
    const richer = outcome(
      d,
      change(d, (c) => (c.budget = { ...c.budget!, max: 200000 })),
    );
    expect(richer.cantTell.map((h) => h.productId)).toEqual([E]);
    expect(richer.letIn).not.toContain(E);
    const off = outcome(
      d,
      change(d, (c) => (c.rulesOn["ram_gb_min_mock"] = false)),
    );
    expect(off.letIn).toEqual([E]);
  });
});

describe("re-ranking", () => {
  it("moving a weight re-orders, and each product says how far it moved", () => {
    const d = data("success");
    const [a, b, c] = d.scored.map((s) => s.productId);
    const o = outcome(
      d,
      change(d, (x) => (x.weights = { cpu: 0, battery: 0, keys: 10 })),
    );
    expect(o.shown.map((r) => r.productId)).toEqual([c, b, a]);
    expect(o.shown.map((r) => moveText(r.move))).toEqual(["Up 2", "Same", "Down 2"]);
  });

  it("every weight at 0 keeps decide's order, with nothing moved", () => {
    const d = data("success");
    const o = outcome(
      d,
      change(d, (x) => (x.weights = { cpu: 0, battery: 0, keys: 0 })),
    );
    expect(o.allZero).toBe(true);
    expect(o.shown.map((r) => r.productId)).toEqual(d.scored.map((s) => s.productId));
    expect(o.shown.every((r) => r.move === 0 && r.score === null)).toBe(true);
  });

  it("a tie in score keeps decide's tie order", () => {
    const d = data("success");
    const twin = structuredClone(d.scored[0]!);
    const tied: RerankData = {
      ...d,
      scored: [d.scored[0]!, { ...twin, productId: "mock-Z" }, ...d.scored.slice(1)],
      checked: { ...d.checked, "mock-Z": d.checked[d.scored[0]!.productId]! },
    };
    for (const weights of [
      { cpu: 10, battery: 0, keys: 0 },
      { cpu: 1, battery: 9, keys: 3 },
    ]) {
      const o = outcome(
        tied,
        change(tied, (x) => (x.weights = weights)),
      );
      const order = o.shown.map((r) => r.productId);
      expect(order.indexOf(d.scored[0]!.productId)).toBe(order.indexOf("mock-Z") - 1);
    }
  });

  it("moving a slider back to where it was is no change: decide's own result stands", () => {
    const d = data("success");
    const moved = change(d, (x) => (x.weights["cpu"] = 2));
    expect(isOriginal(d, moved)).toBe(false);
    moved.weights["cpu"] = originalChoices(d).weights["cpu"]!;
    expect(isOriginal(d, moved)).toBe(true);
    expect(outcome(d, moved).changed).toBe(false);
  });

  it("a product with every value unknown never jumps above a scored one", () => {
    const products = [
      {
        productId: "p_unknown",
        contributions: [{ criterionKey: "cpu", score: null, state: "unknown" }],
      },
      { productId: "p_low", contributions: [{ criterionKey: "cpu", score: 1, state: "known" }] },
    ];
    const got = rescore(products, [{ key: "cpu", weight: 10 }]);
    expect(got).toEqual([
      { productId: "p_low", score: 10, coverage: 1 },
      { productId: "p_unknown", score: null, coverage: 0 },
    ]);
  });

  it("fifty products re-rank well within a frame", () => {
    const d = buildMockResult(input, "many").rerank!;
    expect(d.scored).toHaveLength(50);
    const started = performance.now();
    for (let i = 0; i <= 10; i += 1)
      outcome(
        d,
        change(d, (x) => (x.weights["cpu"] = i)),
      );
    expect((performance.now() - started) / 11).toBeLessThan(50);
  });
});

describe("mock data", () => {
  it("in every scenario, the products decide showed pass its own budget and rules", () => {
    for (const { id } of MOCK_SCENARIOS) {
      const d = buildMockResult(input, id).rerank;
      if (!d) continue;
      const shown = outcome(d, originalChoices(d)).shown.map((r) => r.productId);
      expect(shown, id).toEqual(d.scored.map((s) => s.productId));
    }
  });

  it("gives back each product's shown score at the shopper's own weights", () => {
    for (const scenario of ["success", "refilter", "many"]) {
      const r = buildMockResult(input, scenario);
      const d = r.rerank!;
      const weights = d.weights.map((w) => ({ key: w.criterionId, weight: w.weight }));
      const byId = new Map(rescore(d.scored, weights).map((x) => [x.productId, x.score]));
      for (const p of r.products) expect(byId.get(p.id), `${scenario} ${p.name}`).toBe(p.score);
    }
  });
});
