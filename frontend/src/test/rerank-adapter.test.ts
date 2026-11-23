/**
 * US-23: the live adapter carries what the browser re-ranks and re-filters from, all from the result itself, with no
 * new request (S5-5 DECIDED 9). The contract's own example result.
 */
import { describe, expect, it } from "vitest";
import searchExample from "@contracts/examples/search_result.json";
import { resultFromSearch } from "@/lib/results/liveAdapter";
import { outcome, originalChoices } from "@/lib/results/rerank";
import type { RubricItem } from "@/lib/results/types";
import type { SearchResult } from "@/types/contracts/SearchResult";

const example = searchExample as unknown as SearchResult;
const rubric: RubricItem[] = example.request.rubric.weights.map((w) => ({
  criterionId: w.key,
  title: w.key,
  weight: w.weight,
}));

describe("the live result's re-rank data", () => {
  it("holds the rubric, decide's scores, its filter's reads and a card for each held product", () => {
    const data = resultFromSearch(example, rubric).rerank!;
    const decision = example.decision!;
    expect(data.weights.map((w) => [w.criterionId, w.weight])).toEqual(
      example.request.rubric.weights.map((w) => [w.key, w.weight]),
    );
    expect(data.scored.map((s) => s.productId)).toEqual(
      [...decision.ranked].sort((a, b) => a.rank - b.rank).map((r) => r.product_id),
    );
    expect(data.scored[0]!.contributions[0]).toEqual({
      criterionKey: decision.ranked[0]!.contributions[0]!.criterion_key,
      score: decision.ranked[0]!.contributions[0]!.score,
      state: decision.ranked[0]!.contributions[0]!.state,
    });
    expect(data.budget).toEqual({ min: null, max: 70000, currency: "INR" });
    expect(data.rules.map((r) => [r.id, r.sourceText, r.active])).toEqual(
      example.request.rubric.hard_rules.map((r) => [r.id, r.source_text, r.active]),
    );
    expect(Object.values(data.checked).flat()).toHaveLength(decision.checked_values.length);
    expect(data.held.map((h) => h.productId)).toEqual(decision.filtered.map((f) => f.product_id));
    expect(data.others.map((o) => [o.id, o.rank, o.score])).toEqual(
      decision.filtered.map((f) => [f.product_id, null, null]),
    );
    expect(data.canRefilter).toBe(true);
  });

  it("a result from before 1.10.0 re-ranks but never re-filters: decide's groups stand", () => {
    const old = structuredClone(example);
    old.decision!.checked_values = [];
    const data = resultFromSearch(old, rubric).rerank!;
    expect(data.canRefilter).toBe(false);
    const o = outcome(data, {
      ...originalChoices(data),
      budget: { min: null, max: 1, currency: "INR" },
    });
    expect(o.shown).toHaveLength(data.scored.length); // a budget of ₹1 changes nothing it can't read
    expect([...o.filteredOut, ...o.cantTell].map((h) => h.productId).sort()).toEqual(
      data.held.map((h) => h.productId).sort(),
    );
  });

  it("carries the rubric's leave-outs, for the hint", () => {
    expect(resultFromSearch(example, rubric).rerank!.exclusions).toEqual(
      example.request.rubric.exclusions.map((e) => ({ kind: e.kind, value: e.value })),
    );
  });

  it("when decide showed nothing, still carries what its filter read, so a change can let products in", () => {
    const none = structuredClone(example);
    none.decision!.ranked = [];
    const data = resultFromSearch(none, rubric).rerank!;
    expect(data.scored).toEqual([]);
    expect(data.canRefilter).toBe(true);
    const rule = data.rules[0]!.id;
    const o = outcome(data, { ...originalChoices(data), rulesOn: { [rule]: false } });
    expect(o.letIn).toEqual(["prd_vela_pro_14"]); // its RAM was unknown; Aurora is still over the budget
  });

  it("nothing scored gives nothing to re-rank", () => {
    const unscored = structuredClone(example);
    for (const r of unscored.decision!.ranked) r.score = null;
    expect(resultFromSearch(unscored, rubric).rerank).toBeNull();
  });
});
