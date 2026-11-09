/**
 * The rough answers and the exact number (US-08). The weights live only in the table the backend exports, so this also
 * reads the source: a second copy anywhere in the frontend fails here at once.
 */
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import table from "@contracts/examples/rough_levels.json";
import { IMPORTANCE_REPLIES } from "@/lib/interview/questions";
import {
  exactFromAnswer,
  levelOf,
  parseExactValue,
  roughWeightsInWords,
  weightOf,
} from "@/lib/interview/roughLevels";

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory())
      return entry.name === "test" || entry.name === "contracts" ? [] : sourceFiles(path);
    return /\.tsx?$/.test(entry.name) ? [path] : [];
  });
}

describe("the table", () => {
  it("is the story's table: low 2, medium 5, high 8, good enough 3", () => {
    expect(table.weights).toEqual({ low: 2, medium: 5, high: 8, good_enough: 3 });
  });

  it("every reply button has a row in the exported table", () => {
    expect(IMPORTANCE_REPLIES).toEqual(["Low", "Medium", "High", "Good enough"]);
    for (const reply of IMPORTANCE_REPLIES) {
      expect(levelOf(reply), reply).not.toBeNull();
    }
  });

  it("a button's label gives its level", () => {
    expect(levelOf("Low")).toBe("low");
    expect(levelOf("Medium")).toBe("medium");
    expect(levelOf("High")).toBe("high");
    expect(levelOf("Good enough")).toBe("good_enough");
  });

  it("a reply that is not in the table has no level", () => {
    for (const reply of ["Yes, a lot", "Skip", "7", "", "constructor", "toString"]) {
      expect(levelOf(reply), reply).toBeNull();
    }
  });

  it("the words shown come from the table", () => {
    expect(roughWeightsInWords()).toBe("Low is 2, Medium is 5, High is 8, Good enough is 3.");
  });

  it("a level weighs what the table says", () => {
    expect(weightOf("low")).toBe(table.weights.low);
    expect(weightOf("good_enough")).toBe(table.weights.good_enough);
  });

  it("no file in src holds its own copy of the table", () => {
    // Words of the table next to a number, one line at a time. A confidence scale may use the same words.
    const copies = [
      /\b(?:low|medium|high)["']?\s*[:,=]\s*\d/i,
      /\bgood[ _]enough["']?\s*(?:[:,=]|is|maps to)\s*\d/i,
      /\[\s*2(?:\.0)?\s*,\s*5(?:\.0)?\s*,\s*8(?:\.0)?\s*(?:,\s*3(?:\.0)?\s*)?\]/,
    ];
    const files = sourceFiles("src");
    expect(files.length).toBeGreaterThan(20);
    for (const file of files) {
      const lines = readFileSync(file, "utf8").split("\n");
      for (const line of lines.filter((l) => !/Confidence/.test(l))) {
        for (const copy of copies) expect(copy.test(line), `${file}: ${line}`).toBe(false);
      }
    }
  });
});

describe("the exact value", () => {
  it.each([
    ["0", 0],
    ["10", 10],
    ["7", 7],
    ["7.5", 7.5],
    [" 7 ", 7],
    ["0.25", 0.25],
  ])("takes %j", (raw, value) => {
    expect(parseExactValue(raw)).toEqual({ ok: true, value });
  });

  it.each(["", "   ", "11", "-1", "10.5", "abc", "1e1", "NaN", "7,5", "Infinity"])(
    "refuses %j with a plain message",
    (raw) => {
      const parsed = parseExactValue(raw);
      expect(parsed.ok).toBe(false);
      if (!parsed.ok) expect(parsed.message).toMatch(/number from 0 to 10/);
    },
  );

  it("an earlier answer that was a number opens the exact box with it", () => {
    expect(exactFromAnswer("7.5")).toBe(7.5);
    expect(exactFromAnswer("0")).toBe(0);
  });

  it("an earlier rough, typed or missing answer does not", () => {
    for (const value of ["High", "Good enough", "under 1.5 kg", "11", null]) {
      expect(exactFromAnswer(value), String(value)).toBeNull();
    }
  });
});
