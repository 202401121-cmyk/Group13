/**
 * The guard behind "Where this comes from": only the latest opening's answer is shown. An answer that arrives
 * after the drawer was opened again for something else, or after it was closed, is dropped.
 */
import { describe, expect, it } from "vitest";
import { latestOnly } from "@/lib/results/view";

describe("latestOnly", () => {
  it("the current opening's answer is used", () => {
    const answers = latestOnly();
    const token = answers.next();
    expect(answers.isCurrent(token)).toBe(true);
  });

  it("an answer for an earlier opening is ignored", () => {
    const answers = latestOnly();
    const first = answers.next();
    const second = answers.next();
    expect(answers.isCurrent(first)).toBe(false);
    expect(answers.isCurrent(second)).toBe(true);
  });

  it("an answer that arrives after closing is ignored", () => {
    const answers = latestOnly();
    const token = answers.next();
    answers.cancel();
    expect(answers.isCurrent(token)).toBe(false);
  });

  it("opening again after closing works as before", () => {
    const answers = latestOnly();
    const before = answers.next();
    answers.cancel();
    const after = answers.next();
    expect(answers.isCurrent(before)).toBe(false);
    expect(answers.isCurrent(after)).toBe(true);
  });

  it("each guard keeps its own count", () => {
    const a = latestOnly();
    const b = latestOnly();
    const token = a.next();
    b.next();
    b.next();
    expect(a.isCurrent(token)).toBe(true);
  });
});
