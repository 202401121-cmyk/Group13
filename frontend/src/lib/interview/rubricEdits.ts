/**
 * The rubric editor's edits (US-09, US-10), applied by the rules the backend's rubric route uses (docs/frontend-api.md,
 * "Editing the rubric"). The editor uses this to show an edit at once, before the save comes back, and the mock
 * service uses it as its backend. In live mode the backend's answer always replaces what this worked out.
 */
import { ApiError } from "@/lib/api/client";
import type {
  EditAnswer,
  EditableRubric,
  ExclusionView,
  RubricEditView,
  WeightView,
} from "./types";

/** The sentence the backend sends in X-Rubric-Note when every weight is 0. */
export const ALL_ZERO_NOTE =
  "Every weight was set to 0, so the usual weights are used until you raise one.";

export const WEIGHT_RANGE = "A weight must be a number from 0 to 10.";

/** The rubric route's limits (shared/contracts): 20 edits a request, 30 leave-outs in all. */
export const MAX_EDITS = 20;
export const MAX_EXCLUSIONS = 30;

function refuse(message: string): never {
  throw new ApiError("invalid", message, 422);
}

/** One leave-out is another when the kind is the same and the words are, ignoring case. */
export function sameExclusion(a: ExclusionView, b: ExclusionView): boolean {
  return a.kind === b.kind && a.value.toLowerCase() === b.value.toLowerCase();
}

/** Each weight's share of them all, from 0 to 1. With every weight at 0 every share is 0. */
export function withShares(weights: WeightView[]): WeightView[] {
  const total = weights.reduce((sum, w) => sum + w.weight, 0);
  return weights.map((w) => ({ ...w, share: total > 0 ? w.weight / total : 0 }));
}

/**
 * The rubric with the edits applied in order, as a new object. An edit naming a rule or criterion the rubric hasn't
 * got, or a weight outside 0 to 10, refuses them all (ApiError "invalid", as the backend's 422). With every weight at
 * 0, `defaults` (the usual weights, when known) take their place, and the note says so.
 */
export function applyEdits(
  base: EditableRubric,
  edits: readonly RubricEditView[],
  defaults?: Readonly<Record<string, number>>,
): EditAnswer {
  let { rules, weights, exclusions } = base;
  for (const edit of edits) {
    switch (edit.kind) {
      case "switch_rule": {
        if (!rules.some((r) => r.id === edit.ruleId))
          refuse(`No hard rule has the id ${edit.ruleId}.`);
        rules = rules.map((r) => (r.id === edit.ruleId ? { ...r, active: edit.active } : r));
        break;
      }
      case "set_weight": {
        if (!Number.isFinite(edit.weight) || edit.weight < 0 || edit.weight > 10)
          refuse(WEIGHT_RANGE);
        if (!weights.some((w) => w.key === edit.key)) refuse(`No criterion is called ${edit.key}.`);
        weights = weights.map((w) =>
          w.key === edit.key ? { ...w, weight: edit.weight, origin: "manual" } : w,
        );
        break;
      }
      case "add_exclusion": {
        const added = { ...edit.exclusion };
        const at = exclusions.findIndex((e) => sameExclusion(e, added));
        exclusions =
          at >= 0 ? exclusions.map((e, i) => (i === at ? added : e)) : [...exclusions, added];
        break;
      }
      case "remove_exclusion": {
        exclusions = exclusions.filter((e) => !sameExclusion(e, edit.exclusion));
        break;
      }
    }
  }
  let note: string | null = null;
  if (weights.length > 0 && weights.every((w) => w.weight === 0)) {
    note = ALL_ZERO_NOTE;
    if (defaults) {
      weights = weights.map((w) => ({ ...w, weight: defaults[w.key] ?? 0, origin: "standard" }));
    }
  }
  return { rubric: { rules, weights: withShares(weights), exclusions }, note };
}
