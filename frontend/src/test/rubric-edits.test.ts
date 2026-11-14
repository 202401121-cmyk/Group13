/** The rubric editor's edits (US-09, US-10), applied as the backend applies them (docs/frontend-api.md). */
import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/client";
import { ALL_ZERO_NOTE, applyEdits } from "@/lib/interview/rubricEdits";
import type { EditableRubric } from "@/lib/interview/types";

const RULE = "ram_gb_min_1c18a881e7";

const base: EditableRubric = {
  rules: [{ id: RULE, words: "at least 16 GB RAM", active: true }],
  weights: [
    { key: "cpu_performance", title: "CPU performance", weight: 9, share: 0.6, origin: "answer" },
    { key: "battery_life", title: "Battery life", weight: 6, share: 0.4, origin: "standard" },
  ],
  exclusions: [{ kind: "brand", value: "Zenith", always: false }],
};

function refusal(run: () => unknown): ApiError {
  try {
    run();
  } catch (e) {
    if (e instanceof ApiError) return e;
    throw e;
  }
  throw new Error("the edit was not refused");
}

describe("applyEdits", () => {
  it("switching a rule keeps its words and flips it", () => {
    const { rubric } = applyEdits(base, [{ kind: "switch_rule", ruleId: RULE, active: false }]);
    expect(rubric.rules).toEqual([{ id: RULE, words: "at least 16 GB RAM", active: false }]);
    expect(base.rules[0]!.active).toBe(true); // the rubric handed in is never changed
    const back = applyEdits(rubric, [{ kind: "switch_rule", ruleId: RULE, active: true }]);
    expect(back.rubric.rules[0]!.active).toBe(true);
  });

  it("a weight edit is marked manual and the shares are recomputed", () => {
    const { rubric, note } = applyEdits(base, [
      { kind: "set_weight", key: "cpu_performance", weight: 4 },
    ]);
    expect(rubric.weights[0]).toMatchObject({ weight: 4, origin: "manual" });
    expect(rubric.weights[1]).toMatchObject({ weight: 6, origin: "standard" });
    expect(rubric.weights.map((w) => w.share)).toEqual([0.4, 0.6]);
    expect(note).toBeNull();
  });

  it("every weight at 0 uses the defaults and gives the note", () => {
    const edits = [
      { kind: "set_weight" as const, key: "cpu_performance", weight: 0 },
      { kind: "set_weight" as const, key: "battery_life", weight: 0 },
    ];
    const withDefaults = applyEdits(base, edits, { cpu_performance: 8, battery_life: 2 });
    expect(withDefaults.note).toBe(ALL_ZERO_NOTE);
    expect(withDefaults.rubric.weights.map((w) => [w.weight, w.origin])).toEqual([
      [8, "standard"],
      [2, "standard"],
    ]);
    expect(withDefaults.rubric.weights.map((w) => w.share)).toEqual([0.8, 0.2]);
    // Without the defaults (live mode, before the backend answers) the shares are 0, and the note still shows.
    const live = applyEdits(base, edits);
    expect(live.note).toBe(ALL_ZERO_NOTE);
    expect(live.rubric.weights.map((w) => w.share)).toEqual([0, 0]);
    expect(ALL_ZERO_NOTE).toBe(
      "Every weight was set to 0, so the usual weights are used until you raise one.",
    );
  });

  it("removing an exclusion matches kind and value, ignoring case", () => {
    const other = applyEdits(base, [
      { kind: "remove_exclusion", exclusion: { kind: "feature", value: "Zenith", always: false } },
    ]);
    expect(other.rubric.exclusions).toHaveLength(1); // another kind with the same words stays
    const gone = applyEdits(base, [
      { kind: "remove_exclusion", exclusion: { kind: "brand", value: "zenith", always: true } },
    ]);
    expect(gone.rubric.exclusions).toEqual([]);
  });

  it("adding an exclusion that is there already replaces it, so Always can change", () => {
    const { rubric } = applyEdits(base, [
      { kind: "add_exclusion", exclusion: { kind: "brand", value: "zenith", always: true } },
      { kind: "add_exclusion", exclusion: { kind: "brand", value: "Xiaomi", always: false } },
    ]);
    expect(rubric.exclusions).toEqual([
      { kind: "brand", value: "zenith", always: true },
      { kind: "brand", value: "Xiaomi", always: false },
    ]);
  });

  it("an unknown rule id is refused, naming it", () => {
    const rule = refusal(() =>
      applyEdits(base, [{ kind: "switch_rule", ruleId: "nope", active: false }]),
    );
    expect([rule.kind, rule.status, rule.message]).toEqual([
      "invalid",
      422,
      "No hard rule has the id nope.",
    ]);
    const key = refusal(() => applyEdits(base, [{ kind: "set_weight", key: "oops", weight: 3 }]));
    expect(key.message).toBe("No criterion is called oops.");
  });

  it("a weight outside 0 to 10 is refused", () => {
    for (const weight of [-1, 10.5, Number.NaN]) {
      const e = refusal(() =>
        applyEdits(base, [{ kind: "set_weight", key: "cpu_performance", weight }]),
      );
      expect(e.message).toBe("A weight must be a number from 0 to 10.");
    }
  });
});
