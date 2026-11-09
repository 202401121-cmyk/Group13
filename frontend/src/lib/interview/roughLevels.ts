/**
 * Rough answers and exact numbers (US-08). The weights live only in the table the backend exports: this reads it, and
 * nothing in the frontend keeps a copy. A button's label finds its level there, so "Good enough" is good_enough.
 */
import table from "@contracts/examples/rough_levels.json";
import type { RoughLevel } from "@/types/contracts/ReplyRequest";
import type { RoughLevelWeights } from "@/types/contracts/RoughLevelWeights";

export type { RoughLevel };

const WEIGHTS = (table as RoughLevelWeights).weights;

export const MIN_EXACT = 0;
export const MAX_EXACT = 10;
export const SLIDER_STEP = 0.5;

const IN_RANGE_WORDS = `a number from ${MIN_EXACT} to ${MAX_EXACT}`;
const PLAIN_NUMBER = /^\d*\.?\d+$/;

/** The level a reply stands for, or null if it isn't one of the table's. */
export function levelOf(reply: string): RoughLevel | null {
  const key = reply.trim().toLowerCase().replace(/\s+/g, "_");
  return Object.hasOwn(WEIGHTS, key) ? (key as RoughLevel) : null;
}

/** What a level weighs, from the table. */
export function weightOf(level: RoughLevel): number {
  return WEIGHTS[level]!;
}

/** The table in plain words, for the exact box. */
export function roughWeightsInWords(): string {
  const words = Object.entries(WEIGHTS).map(([level, weight]) => {
    const label = level.replace(/_/g, " ");
    return `${label.charAt(0).toUpperCase()}${label.slice(1)} is ${weight}`;
  });
  return `${words.join(", ")}.`;
}

export type ParsedExact = { ok: true; value: number } | { ok: false; message: string };

/** What the exact box holds. Plain digits only, so "1e1" is refused rather than read as 10. */
export function parseExactValue(raw: string): ParsedExact {
  const text = raw.trim();
  if (!text) return { ok: false, message: `Please enter ${IN_RANGE_WORDS}.` };
  const value = PLAIN_NUMBER.test(text) ? Number(text) : Number.NaN;
  if (Number.isNaN(value) || value < MIN_EXACT || value > MAX_EXACT) {
    return { ok: false, message: `That won't work. Please enter ${IN_RANGE_WORDS}.` };
  }
  return { ok: true, value };
}

/** The number an earlier answer holds, if that is all it holds. */
export function exactFromAnswer(value: string | null): number | null {
  if (value === null) return null;
  const parsed = parseExactValue(value);
  return parsed.ok ? parsed.value : null;
}
