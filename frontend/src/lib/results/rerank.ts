/**
 * The shopper's changes to a finished result, worked out in the browser from what it already holds (US-23, P-42,
 * P-47): new weights re-rank the shown products with decide's own score (score.ts), and a new budget or rule re-filters
 * every decided product from what decide's filter read (refilter.ts). Never a server call.
 *
 * - The products decide showed keep decide's scores for each criterion; only the weights change (DECIDED 1). Nothing
 *   is compared again: a product's compared and estimated scores are the ones decide gave.
 * - With the weights as decide had them, decide's own order stands (it ranks by chance of being first, which needs its
 *   simulations): a budget or rule change only takes products out or lets them in. With new weights, the order is the
 *   new score's, and a tie in score keeps decide's order.
 * - Each move is counted from decide's own order.
 * - A product a re-filter lets back in has no scores for these priorities (decide scored only the shown ones). It
 *   joins the end of the list with no rank number, as "Not scored for these priorities" (P-47).
 * - With every weight at 0 the order is not re-worked: the note says the usual weights are used (US-09).
 * - Back at the original weights and rules, nothing has changed: decide's own result stands, chance of being first
 *   and confidence included.
 */
import { checksFor, grouped, refilter, relaxationHint, type Budget } from "./refilter";
import { rescore } from "./score";
import type { RerankData } from "./types";

export interface Choices {
  /** Each criterion's slider, 0 to 10. */
  weights: Record<string, number>;
  budget: Budget | null;
  /** Each rule on or off, by its id. */
  rulesOn: Record<string, boolean>;
}

export interface ShownRow {
  productId: string;
  /** The score on these weights; null when nothing could be scored. */
  score: number | null;
  /** Places up since decide's order: positive up, negative down, 0 the same. */
  move: number;
}

export interface Outcome {
  /** False: the original weights and rules, so decide's own result stands. */
  changed: boolean;
  /** Every weight is 0: the usual weights are used and the order is decide's. */
  allZero: boolean;
  /** The scored products, best first. */
  shown: ShownRow[];
  /** Products a change let back in: no scores for these priorities (P-47). */
  letIn: string[];
  filteredOut: { productId: string; reasons: string[] }[];
  cantTell: { productId: string; reasons: string[]; attribute: string | null }[];
  /** With nothing shown: the change that lets the most products through. */
  hint: string | null;
}

export function originalChoices(data: RerankData): Choices {
  return {
    weights: Object.fromEntries(data.weights.map((w) => [w.criterionId, w.weight])),
    budget: data.budget,
    rulesOn: Object.fromEntries(data.rules.map((r) => [r.id, r.active])),
  };
}

function sameBudget(a: Budget | null, b: Budget | null): boolean {
  return (
    a === b ||
    (a !== null && b !== null && a.min === b.min && a.max === b.max && a.currency === b.currency)
  );
}

export function isOriginal(data: RerankData, choices: Choices): boolean {
  const first = originalChoices(data);
  return (
    data.weights.every((w) => choices.weights[w.criterionId] === first.weights[w.criterionId]) &&
    sameBudget(choices.budget, first.budget) &&
    data.rules.every((r) => choices.rulesOn[r.id] === first.rulesOn[r.id])
  );
}

export function outcome(data: RerankData, choices: Choices): Outcome {
  if (!data.canRefilter)
    choices = { ...choices, budget: data.budget, rulesOn: originalChoices(data).rulesOn };
  const decidedOrder = data.scored.map((s) => s.productId);
  const rules = data.rules.map((r) => ({ ...r, active: choices.rulesOn[r.id] ?? r.active }));
  const everyone = [...decidedOrder, ...data.held.map((h) => h.productId)];
  // Without what the filter read (a result from before 1.10.0), decide's own groups stand.
  const filtered = data.canRefilter
    ? refilter(data, choices.budget, rules, everyone)
    : new Map(
        everyone.map((id) => {
          const h = data.held.find((x) => x.productId === id);
          const held = h ? { group: h.group, reasons: h.reasons, attribute: null } : null;
          return [id, { held, blocked: new Set<string>() }] as const;
        }),
      );
  const stillShown = new Set(decidedOrder.filter((id) => !filtered.get(id)?.held));
  const letIn = data.held.map((h) => h.productId).filter((id) => !filtered.get(id)?.held);

  const weights = data.weights.map((w) => ({
    key: w.criterionId,
    weight: choices.weights[w.criterionId] ?? 0,
  }));
  const allZero = weights.every((w) => w.weight === 0);
  const sameWeights = data.weights.every((w) => (choices.weights[w.criterionId] ?? 0) === w.weight);
  const kept = data.scored.filter((s) => stillShown.has(s.productId));
  const original = new Map(decidedOrder.map((id, i) => [id, i]));
  const rescored = allZero ? [] : rescore(kept, weights);
  const scoreOf = new Map(rescored.map((r) => [r.productId, r.score]));
  const at = (id: string) => original.get(id) ?? Number.MAX_SAFE_INTEGER;
  const order: { productId: string; score: number | null }[] = allZero
    ? kept.map((s) => ({ productId: s.productId, score: null }))
    : sameWeights
      ? kept.map((s) => ({ productId: s.productId, score: scoreOf.get(s.productId) ?? null }))
      : [...rescored].sort(
          (a, b) =>
            (b.score ?? -Infinity) - (a.score ?? -Infinity) || at(a.productId) - at(b.productId),
        );
  const shown = order.map((row, i) => ({
    productId: row.productId,
    score: row.score,
    move: (original.get(row.productId) ?? i) - i,
  }));

  const held = everyone.flatMap((id) => {
    const h = filtered.get(id)?.held;
    return h ? [{ productId: id, ...h }] : [];
  });
  const nothingShown = shown.length === 0 && letIn.length === 0 && everyone.length > 0;
  const budget = choices.budget;
  return {
    changed: !isOriginal(data, choices),
    allZero,
    shown,
    letIn,
    filteredOut: held
      .filter((h) => h.group === "filtered_out")
      .map(({ productId, reasons }) => ({ productId, reasons })),
    cantTell: held
      .filter((h) => h.group === "cant_tell")
      .map(({ productId, reasons, attribute }) => ({ productId, reasons, attribute })),
    hint: nothingShown
      ? relaxationHint(
          everyone.map((id) => filtered.get(id)?.blocked ?? new Set<string>()),
          budget,
          rules.filter((r) => r.active),
          data.exclusions,
        )
      : null,
  };
}

export { moveText } from "./moves";

/** decide's own groups at the original budget and rules, for a check that the browser reads them as decide did. */
export function originalGroups(data: RerankData) {
  return Object.fromEntries(
    [...data.scored.map((s) => s.productId), ...data.held.map((h) => h.productId)].map((id) => [
      id,
      grouped(checksFor(data.checked[id], data.budget, data.rules)),
    ]),
  );
}
