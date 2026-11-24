/**
 * US-33 (S5-12 DECIDED 2): plain words at about Grade 8. The grade is the backend's own Flesch-Kincaid function
 * (backend/app/explain/readability.py, S4-8), repeated exactly; the numbers below are what it gives. Every fixed
 * question, its "why", and every fixed message the screens show (errors, notices, the interview's own lines) is at
 * grade 9 or under. Each page's own words are checked on the real pages by e2e/quality/reading-level.spec.ts.
 */
import { describe, expect, it } from "vitest";
import { MEMORY_MESSAGES, CONFIRM_FIRST } from "@/lib/account/memory";
import { MESSAGES } from "@/lib/api/client";
import {
  CRITERIA_FIXTURES,
  criteriaFor,
  DONE_MESSAGE,
  keptNote,
  rephraseMessage,
  SKIPPED_NOTE,
} from "@/lib/interview/mockService";
import { ALREADY_ANSWERED, questionFor } from "@/lib/interview/questions";
import { ALL_ZERO_NOTE, WEIGHT_RANGE } from "@/lib/interview/rubricEdits";
import { forgottenHereOnly, forgottenNotice } from "@/lib/interview/useInterview";
import { GRADE_LIMIT, grade, syllables, wordCount } from "@/lib/readability";

describe("the grade, as the backend works it out", () => {
  it.each([
    ["How much does battery life matter to you?", 3.755],
    ["Sources disagree on its price: one says 68,990 rupees.", 5.23],
    ["We couldn't reach ShopSense. Check your connection and try again.", 2.88],
    [
      "The comprehensive evaluation methodology incorporates heterogeneous considerations.",
      36.025714,
    ],
    ["Fixes, boxes and sources worked fine.", 4.45],
    ["", 0],
    ["#1 8 GB %", -3.4],
  ])("%s: %f", (text, expected) => {
    expect(grade(text)).toBeCloseTo(expected, 5);
  });

  it("counts syllables the same way", () => {
    const words = ["life", "sources", "worked", "fixes", "yellow", "the", "beautiful", "ShopSense"];
    expect(words.map(syllables)).toEqual([1, 2, 1, 2, 2, 1, 4, 2]);
  });
});

/** Every text of 8 words or more above grade 9, with its grade: none is the aim. */
function hard(texts: string[]): string[] {
  return texts
    .filter((t) => wordCount(t) >= 8 && grade(t) > GRADE_LIMIT)
    .map((t) => `${grade(t).toFixed(1)}: ${t}`);
}

describe("fixed words at grade 9 or under", () => {
  it("every question the interview asks, and why it asks it", () => {
    const criteria = [
      ...Object.values(CRITERIA_FIXTURES).flat(),
      ...criteriaFor({
        product: null,
        usedFor: null,
        budget: null,
        region: null,
        mustHaves: [],
        exclusions: [],
        source: "model",
      }).criteria,
    ];
    const texts = criteria.flatMap((c) => {
      const q = questionFor(c);
      return [q.question!, q.why!];
    });
    expect(texts.length).toBeGreaterThan(20);
    expect(hard(texts)).toEqual([]);
  });

  it("every fixed message: errors, notices and the interview's own lines", () => {
    const texts = [
      ...Object.values(MESSAGES),
      ...Object.values(MEMORY_MESSAGES),
      CONFIRM_FIRST,
      ALREADY_ANSWERED,
      DONE_MESSAGE,
      SKIPPED_NOTE,
      keptNote("Battery life"),
      rephraseMessage("Battery life"),
      ALL_ZERO_NOTE,
      WEIGHT_RANGE,
      forgottenNotice("Battery life"),
      forgottenHereOnly("Battery life"),
    ].filter((t): t is string => typeof t === "string");
    expect(hard(texts)).toEqual([]);
  });
});
